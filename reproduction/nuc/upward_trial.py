#!/usr/bin/env python3
"""ACTIVE attended upward or continuous manual SpaceMouse diagnostics.

Uses the previously verified FR3 impedance launch and its isolated target topic.
Input is JSON on stdin, telemetry is JSON on stdout. No gripper or recovery API.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque

from attended_hold import stop_group
from fr3_kinematics import chain_from_urdf, check_upward_direction, checked_jacobian
from observe_hold import sample_from_message
from translation_guard import (TranslationGuard, TrialWindow, MAX_AGE, TARGET_RADIUS,
                               SPEED, READY_WAIT_SECONDS, ACTIVE_SECONDS, check_joints)

TARGET_TOPIC = "/hil_serl_preflight/unused_equilibrium_pose"
WORKSPACE = "/opt/venv/franka-0.18.0/franka_catkin_ws"


def emit(**data):
    data["server_time"] = time.monotonic()
    print(json.dumps(data, allow_nan=False), flush=True)


def read_start_state():
    # Audited upstream example: Robot constructor + 101 read callbacks, no control.
    result = subprocess.run(
        [WORKSPACE + "/libfranka/build/examples/echo_robot_state", "172.16.0.1"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    states = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    state = states[-1]
    if state["robot_mode"] != "Idle" or state["current_errors"] or state["last_motion_errors"]:
        raise ValueError("read-only precheck: robot is not idle and error-free")
    transform = state["O_T_EE"]
    return {"q": state["q"], "xyz": transform[12:15],
            "rotation": [transform[i] for i in (0, 1, 2, 4, 5, 6, 8, 9, 10)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-attended-upward-trial", action="store_true")
    mode.add_argument("--execute-attended-manual", action="store_true")
    parser.add_argument("--no-trial-bounds", action="store_true",
                        help="manual only: remove trial workspace and session timers")
    parser.add_argument("--official-input", action="store_true")
    parser.add_argument("--pilot-gate", action="store_true")
    parser.add_argument("--official-home", action="store_true")
    parser.add_argument("--home-switch-probe", action="store_true",
                        help="five-second diagnostic: switch using current joints as the goal; no manual input")
    parser.add_argument("--speed-scale", type=float, choices=(1., 1.5, 2.), default=1.)
    parser.add_argument("--translation-scale", type=float, choices=(1., 1.5, 1.6, 2.))
    parser.add_argument("--rotation-scale", type=float, choices=(1., 1.5, 2., 3.))
    parser.add_argument("--rotation-response", choices=("standard", "fast", "responsive"), default="standard")
    args = parser.parse_args()
    manual = args.execute_attended_manual
    free = args.no_trial_bounds
    official = args.official_input
    if free and not manual:
        parser.error("--no-trial-bounds requires manual mode")
    if official and not (manual and free):
        parser.error("--official-input requires manual mode without trial bounds")
    if args.official_home and not args.pilot_gate:
        parser.error("--official-home requires --pilot-gate")
    if args.home_switch_probe and not args.official_home:
        parser.error("--home-switch-probe requires --official-home")
    if args.pilot_gate and not args.official_input:
        parser.error("--pilot-gate requires --official-input")
    if args.speed_scale != 1. and not official:
        parser.error("--speed-scale requires --official-input")
    if args.translation_scale is not None and not official:
        parser.error("--translation-scale requires --official-input")
    if args.rotation_scale is not None and not official:
        parser.error("--rotation-scale requires --official-input")
    if args.rotation_response != "standard" and not official:
        parser.error("--rotation-response requires --official-input")
    translation_scale = args.speed_scale if args.translation_scale is None else args.translation_scale
    rotation_scale = args.speed_scale if args.rotation_scale is None else args.rotation_scale
    if (os.environ.get("ROS_MASTER_URI") != "http://127.0.0.1:11321"
            or os.environ.get("ROS_LOG_DIR") != "/hil-serl-state/logs/ros"):
        parser.error("unexpected runtime isolation")

    home_build = None
    if args.official_home:
        from official_home import activate_home_controller
        home_build = activate_home_controller()
    rotation_build = None
    if args.rotation_response == 'responsive':
        from rotation_controller import activate_rotation_controller
        rotation_build = activate_rotation_controller()
    import numpy as np
    import roslaunch
    import rospy
    import yaml
    from franka_msgs.msg import FrankaState
    from geometry_msgs.msg import PoseStamped
    from scipy.spatial.transform import Rotation
    from dynamic_reconfigure.client import Client
    if manual:
        from manual_guard import (ManualGuard, WORKSPACE_RADIUS, ORIENTATION_RADIUS,
                                  SESSION_SECONDS, IDLE_SECONDS, LINEAR_SPEED, ANGULAR_SPEED,
                                  ACTION_PERIOD, POSITION_ACTION_SCALE, ROTATION_ACTION_SCALE,
                                  ACTION_JOINT_SPEED)

    os.umask(0o007)
    scripts = Path(__file__).parent
    run = Path("/hil-serl-state/logs") / (
        ("manual-" if manual else "upward-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + str(os.getpid())
    )
    run.mkdir()
    outcome = {"run_directory": str(run), "target_limit_mm": 1000 * TARGET_RADIUS,
               "target_speed_mm_s": 1000 * SPEED, "ready_wait_seconds": READY_WAIT_SECONDS,
               "active_seconds": ACTIVE_SECONDS, "active_timer_started": False}
    if manual:
        outcome = {"run_directory": str(run), "mode": "manual_six_axis",
                   "workspace_radius_mm": 1000 * WORKSPACE_RADIUS,
                   "orientation_radius_deg": float(np.rad2deg(ORIENTATION_RADIUS)),
                   "target_speed_mm_s": 1000 * LINEAR_SPEED,
                   "angular_speed_deg_s": float(np.rad2deg(ANGULAR_SPEED)),
                   "session_seconds": SESSION_SECONDS, "idle_seconds": IDLE_SECONDS}
        outcome["pilot_gate"] = args.pilot_gate
        outcome["official_home"] = args.official_home
        if rotation_build:
            outcome['rotation_controller_build'] = rotation_build
        if home_build:
            outcome['home_controller_build'] = home_build
        outcome["trial_bounds"] = not free
        outcome["input_mode"] = "upstream_axes_no_enable_button" if official else "held_left_button"
        if official:
            outcome.update(response_profile="ram_measured_step" if args.speed_scale == translation_scale == rotation_scale == 1. else "measured_step_scaled", target_speed_mm_s=None,
                           angular_speed_deg_s=None, action_hz=1 / ACTION_PERIOD,
                           speed_scale=args.speed_scale,
                           translation_scale=translation_scale,
                           rotation_response=args.rotation_response,
                           rotation_filter_coefficient=.02 if rotation_build else .005,
                           rotation_scale=rotation_scale,
                           rotation_joint_speed_rad_s=.55 if rotation_scale > 2 else ACTION_JOINT_SPEED,
                           position_action_scale_mm=1000 * POSITION_ACTION_SCALE * translation_scale,
                           rotation_action_scale_rad=ROTATION_ACTION_SCALE * rotation_scale,
                           predicted_joint_speed_limit_rad_s=.55 if rotation_scale > 2 else ACTION_JOINT_SPEED,
                           translation_predicted_joint_speed_limit_rad_s=ACTION_JOINT_SPEED)
        if free:
            outcome.update(workspace_radius_mm=None, orientation_radius_deg=None,
                           session_seconds=None, idle_seconds=None)
    controller = subscriber = publisher = pilot = None
    shared, lock = {}, threading.Lock()
    recent_states = deque(maxlen=100)
    sample = None
    exit_code = 1

    def interrupted(signum, frame):
        raise KeyboardInterrupt("signal %s" % signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)

    def read_inputs():
        try:
            while True:
                line = sys.stdin.readline(2049)
                if not line:
                    raise ValueError("input connection closed")
                if len(line) > 2048 or not line.endswith("\n"):
                    raise ValueError("invalid input framing")
                packet = json.loads(line)
                with lock:
                    shared["packet"] = packet
                    shared["packet_received"] = time.monotonic()
        except Exception as error:
            with lock:
                shared["input_error"] = str(error)

    def receive(message):
        try:
            sample = sample_from_message(message, time.monotonic())
            with lock:
                previous = shared.get("sample")
                if previous and sample["stamp"] <= previous["stamp"]:
                    raise ValueError("robot state timestamp did not advance")
                shared["sample"] = sample
                recent_states.append(sample)
        except Exception as error:
            with lock:
                shared["state_error"] = str(error)

    def snapshot():
        with lock:
            data = dict(shared)
        if data.get("input_error") or data.get("state_error"):
            raise ValueError(data.get("input_error") or data["state_error"])
        if controller is not None and controller.poll() is not None:
            raise ValueError("controller launcher exited")
        return data

    threading.Thread(target=read_inputs, daemon=True).start()
    try:
        emit(phase="precheck", **outcome)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 11321))
        cfg = roslaunch.config.ROSLaunchConfig()
        roslaunch.xmlloader.XmlLoader().load(
            str(scripts / "fr3_hold.launch"), cfg, argv=["robot_ip:=172.16.0.1"], verbose=False
        )
        chain, lower, upper = chain_from_urdf(cfg.params["/robot_description"].value)
        initial = read_start_state()
        outcome["read_only_initial_state"] = initial
        if manual:
            ManualGuard(initial, lower, upper, trial_bounds=not free, official_input=official,
                        speed_scale=args.speed_scale, translation_scale=args.translation_scale, rotation_scale=args.rotation_scale)
            checked_jacobian(chain, initial)
        else:
            TranslationGuard(initial, lower, upper)
            derivative = check_upward_direction(chain, initial)
            check_joints(np.array(initial["q"]) + derivative * TARGET_RADIUS, lower, upper, 0.0015)
            outcome["predicted_joint_delta"] = (derivative * TARGET_RADIUS).tolist()
        with (run / "controller.log").open("x") as log:
            controller = subprocess.Popen(
                ["bash", str(scripts / "launch_hold.sh"), "--execute-attended-hold"],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                snapshot()
                emit(phase="starting")
                try:
                    response = subprocess.run(
                        ["rosservice", "call", "/controller_manager/list_controllers"],
                        capture_output=True, text=True, timeout=2,
                    )
                except subprocess.TimeoutExpired:
                    continue
                if response.returncode == 0:
                    states = {c["name"]: c["state"] for c in yaml.safe_load(response.stdout)["controller"]}
                    if all(states.get(name) == "running" for name in (
                        "franka_state_controller", "cartesian_impedance_controller"
                    )):
                        break
                time.sleep(0.1)
            else:
                raise ValueError("controllers did not become ready")

            rospy.init_node("hil_serl_manual" if manual else "hil_serl_upward_trial",
                            disable_signals=True, disable_rosout=True)
            expected_type = ('hil_serl_rotation/ResponsiveCartesianImpedanceController' if rotation_build
                             else 'serl_franka_controllers/CartesianImpedanceController')
            if rospy.get_param('/cartesian_impedance_controller/type') != expected_type:
                raise ValueError('unexpected Cartesian controller type')
            outcome['cartesian_controller_type'] = expected_type
            parameter_node = "/cartesian_impedance_controllerdynamic_reconfigure_compliance_param_node"
            config_client = Client(parameter_node, timeout=3)
            original_config = config_client.get_configuration(timeout=3)
            outcome["original_compliance"] = original_config
            if original_config is None:
                raise ValueError("cannot read active compliance settings")
            for axis in ("x", "y", "z"):
                for sign in ("", "neg_"):
                    if original_config["translational_clip_" + sign + axis] != 0.005:
                        raise ValueError("unexpected translation error clipping")
            if original_config["translational_Ki"] != 0 or original_config["rotational_Ki"] != 0:
                raise ValueError("unexpected integral gains")
            compliance = {
                "translational_stiffness": 1000.0, "translational_damping": 63.0,
            }
            if manual:
                compliance = {"translational_stiffness": 2000.0, "translational_damping": 89.0,
                              "rotational_stiffness": 50.0, "rotational_damping": 14.0}
                if official:
                    # Pinned RAM COMPLIANCE_PARAM gains; retain this deployment's
                    # existing error clips, zero integral gains and collision settings.
                    compliance.update(rotational_stiffness=150.0, rotational_damping=7.0)
                    if args.rotation_response in ("fast", "responsive"):
                        # More rotational restoring torque at the same target offset.
                        # Increase damping alongside stiffness; keep all error clips,
                        # input scales and guard thresholds unchanged.
                        compliance.update(rotational_stiffness=300.0, rotational_damping=10.0)
                for axis in ("x", "y", "z"):
                    for sign in ("", "neg_"):
                        if original_config["rotational_clip_" + sign + axis] != 0.03:
                            raise ValueError("unexpected rotational error clipping")
            new_config = config_client.update_configuration(compliance)
            if any(new_config[k] != v for k, v in compliance.items()):
                raise ValueError("compliance update was not confirmed")
            outcome["trial_compliance"] = new_config
            config_client.close()
            emit(phase="configured", compliance=compliance, **outcome)
            subscriber = rospy.Subscriber(
                "/franka_state_controller/franka_states", FrankaState, receive, queue_size=1
            )
            deadline = time.monotonic() + 3
            while "sample" not in snapshot() and time.monotonic() < deadline:
                emit(phase="waiting_for_state")
                time.sleep(0.02)
            sample = snapshot().get("sample")
            if sample is None:
                raise ValueError("no robot state")
            if manual:
                guard = ManualGuard(sample, lower, upper, trial_bounds=not free, official_input=official,
                                    speed_scale=args.speed_scale, translation_scale=args.translation_scale, rotation_scale=args.rotation_scale)
                guard.check_state(sample, time.monotonic(), checked_jacobian(chain, sample))
            else:
                guard = TranslationGuard(sample, lower, upper)
                guard.check_state(sample, time.monotonic())
                check_upward_direction(chain, sample)
            if args.pilot_gate:
                from pilot_motion import PilotMotion
                if args.official_home:
                    from official_home import OfficialHomeMotion, JointHome, ROSSwitcher, HOME_Q
                    goal = list(sample['q']) if args.home_switch_probe else HOME_Q
                    from official_home import MINIMUM_HOME_SECONDS
                    from custom_home import CustomHomeStore
                    pilot = OfficialHomeMotion(guard, chain, JointHome(ROSSwitcher(goal), goal,
                        minimum_duration=0. if args.home_switch_probe else MINIMUM_HOME_SECONDS),
                        custom_store=CustomHomeStore('/hil-serl-state/custom_home.json'))
                    outcome['home_switch_probe'] = args.home_switch_probe
                    outcome['joint_goal'] = list(goal)
                else:
                    pilot = PilotMotion(guard)
            code, message, state = rospy.get_master().getSystemState()
            forbidden = {TARGET_TOPIC, "/cartesian_impedance_controller/equilibrium_pose"}
            if code != 1 or any(topic in forbidden and nodes for topic, nodes in state[0]):
                raise ValueError("unexpected target publisher or ROS master error")
            subscribers = dict(state[1]).get(TARGET_TOPIC, [])
            if "/franka_control" not in subscribers:
                raise ValueError("target topic does not reach the Franka control node")
            outcome["target_subscribers"] = subscribers
            publisher = rospy.Publisher(TARGET_TOPIC, PoseStamped, queue_size=1, latch=False)
            deadline = time.monotonic() + 3
            while not publisher.get_num_connections() and time.monotonic() < deadline:
                snapshot()
                emit(phase="waiting_for_target_connection")
                time.sleep(0.02)
            if not publisher.get_num_connections():
                raise ValueError("target subscriber did not connect")

            quat = Rotation.from_matrix(np.array(sample["rotation"]).reshape(3, 3, order="F")).as_quat().tolist()
            handshake_deadline = time.monotonic() + 2
            while True:
                emit(phase="ready", armed=False, target_up_mm=0, actual_up_mm=0,
                     q4=sample["q"][3], **({"pilot": pilot.status()} if pilot else {}))
                time.sleep(0.02)
                data = snapshot()
                packet = data.get("packet")
                if packet and 0 <= time.monotonic() - packet["server_time"] <= MAX_AGE:
                    break
                if time.monotonic() > handshake_deadline:
                    raise ValueError("SpaceMouse did not complete ready handshake")
            window = TrialWindow(time.monotonic())
            session_deadline = time.monotonic() + SESSION_SECONDS if manual else None
            last_operator_activity = time.monotonic()
            last_step, last_seq = time.monotonic(), -1
            sent_target = list(guard.origin)
            sent_quat = list(quat)
            outcome["motion_origin"] = sample
            outcome["published_targets"] = 0
            probe_started = time.monotonic()
            with (run / "samples.jsonl").open("x") as samples:
                while free or time.monotonic() < (session_deadline if manual else window.deadline):
                    now, data = time.monotonic(), snapshot()
                    if rospy.is_shutdown():
                        raise ValueError("ROS shut down")
                    sample = data["sample"]
                    if args.home_switch_probe:
                        if np.max(np.abs(np.asarray(sample['q'])-goal)) > .02:
                            raise ValueError('same-position switch probe drift exceeded 0.02 rad')
                        elapsed = now-probe_started
                        if elapsed > 5:
                            raise ValueError('same-position switch probe exceeded five seconds')
                        if pilot.last_home_id is not None and pilot.mode == 'locked':
                            outcome['home_switch_probe_complete'] = True
                            break
                    if args.official_home:
                        pilot.check_state(sample, now)
                    elif manual:
                        guard.check_state(sample, now, checked_jacobian(chain, sample))
                    else:
                        guard.check_state(sample, now)
                        check_upward_direction(chain, sample)
                    packet = data.get("packet")
                    if packet is None or now - data["packet_received"] > MAX_AGE:
                        raise ValueError("SpaceMouse input stream stale")
                    if args.home_switch_probe:
                        packet = dict(packet, axes=[0.]*6, enable=False,
                            pilot=dict(id=1 if elapsed < 1 else 2,
                                       action='lock' if elapsed < 1 else 'home', connected=True))
                    if packet["seq"] != last_seq:
                        if manual:
                            target, quat = (pilot.step(packet, now, now - last_step, sample) if pilot
                                            else guard.step(packet, now, now - last_step))
                            if packet["enable"]:
                                last_operator_activity = now
                            if not free and now - last_operator_activity >= IDLE_SECONDS:
                                outcome["idle_timeout"] = True
                                break
                        else:
                            target = guard.step(packet, now, now - last_step)
                            window.observe(guard.armed, now)
                            outcome["active_timer_started"] = window.started_at is not None
                        last_step, last_seq = now, packet["seq"]
                        joint_active = args.official_home and pilot.joint.phase != "idle"
                        if not joint_active and (target != sent_target or quat != sent_quat):
                            msg = PoseStamped()
                            msg.header.frame_id = "fr3_link0"
                            msg.header.stamp = rospy.Time.now()
                            msg.pose.position.x, msg.pose.position.y, msg.pose.position.z = target
                            (msg.pose.orientation.x, msg.pose.orientation.y,
                             msg.pose.orientation.z, msg.pose.orientation.w) = quat
                            publisher.publish(msg)
                            sent_target = list(target)
                            sent_quat = list(quat)
                            outcome["published_targets"] += 1
                    samples.write(json.dumps({"state": sample, "target": sent_target,
                                              "target_quaternion": sent_quat,
                                              "input": packet, "armed": guard.armed,
                                              **({"pilot": pilot.status()} if pilot else {})}) + "\n")
                    telemetry = dict(phase="ready", armed=guard.armed,
                         target_up_mm=1000 * (sent_target[2] - guard.origin[2]),
                         actual_up_mm=1000 * (sample["xyz"][2] - guard.origin[2]),
                         left_button=packet["enable"], input_z=packet["axes"][2],
                         seconds_remaining=None if free else round(max(0, (session_deadline if manual else window.deadline) - now), 1),
                         q4=sample["q"][3])
                    if manual:
                        telemetry.update(mode="manual_six_axis", limit_reason=guard.limit_reason,
                            input_mode=outcome["input_mode"],
                            actual_delta_mm=(1000 * (guard.measured - guard.origin)).tolist(),
                            rotation_delta_deg=float(np.rad2deg((guard.measured_rotation * guard.origin_rotation.inv()).magnitude())))
                    else:
                        telemetry["active_timer_started"] = window.started_at is not None
                    if pilot:
                        telemetry["pilot"] = pilot.status()
                    emit(**telemetry)
                    time.sleep(0.02)
            outcome["completed"] = True
            exit_code = 0
    except (Exception, KeyboardInterrupt) as error:
        outcome["error"] = type(error).__name__ + ": " + str(error)
        outcome['failure_state'] = sample
        outcome['traceback'] = traceback.format_exc()
        with lock:
            outcome['recent_states'] = list(recent_states)
        if str(error) == "operator stop":
            outcome["operator_stopped"] = True
            exit_code = 0
    finally:
        if pilot:
            outcome["pilot"] = pilot.status()
        outcome["movement_commanded"] = outcome.get("published_targets", 0) > 0
        if outcome.get("completed"):
            outcome["verdict"] = ("window_finished; review actual displacement" if
                outcome["movement_commanded"] else "window_finished_without_motion_input")
            if manual:
                outcome["verdict"] = "manual_session_finished; review actual displacement"
        try:
            emit(phase="stopping", **outcome)
        except BrokenPipeError:
            pass
        if publisher is not None:
            publisher.unregister()
        if subscriber is not None:
            subscriber.unregister()
        try:
            stop_group(controller)
        except Exception as error:
            outcome["cleanup_error"] = str(error)
            exit_code = 1
        if rospy.core.is_initialized():
            rospy.signal_shutdown("trial finished")
        outcome["controller_exit_code"] = None if controller is None else controller.poll()
        (run / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
        emit(phase="stopped", **outcome)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
