#!/usr/bin/env python3
"""Observe an already-running controller; never command or stop the robot.

Exit 0 means a finite, fresh observation was collected, not that hardware is
safe or that a motion test is approved. The caller must assess the metrics.
"""

import argparse
import json
import math
import sys
import threading
import time


def vector(message, name, length):
    values = [float(value) for value in getattr(message, name)]
    if len(values) != length or not all(math.isfinite(value) for value in values):
        raise ValueError("invalid " + name)
    return values


def active_errors(message):
    return [name for name in message.__slots__ if getattr(message, name)]


def sample_from_message(message, received_at):
    transform = vector(message, "O_T_EE", 16)
    success = float(message.control_command_success_rate)
    stamp = float(message.header.stamp.to_sec())
    if not all(math.isfinite(value) for value in (success, stamp, received_at)):
        raise ValueError("non-finite timestamp or success rate")
    if not 0 <= success <= 1:
        raise ValueError("invalid success rate")
    return {
        "received_at": received_at,
        "stamp": stamp,
        "xyz": transform[12:15],
        "rotation": [transform[i] for i in (0, 1, 2, 4, 5, 6, 8, 9, 10)],
        "q": vector(message, "q", 7),
        "dq": vector(message, "dq", 7),
        "tau": vector(message, "tau_J", 7),
        "success": success,
        "mode": int(message.robot_mode),
        "current_errors": active_errors(message.current_errors),
        "last_motion_errors": active_errors(message.last_motion_errors),
    }


def summarize(samples, ended_at, max_gap=0.5):
    if len(samples) < 2:
        raise ValueError("need at least two state messages")
    first, last = samples[0], samples[-1]
    intervals = [b["received_at"] - a["received_at"] for a, b in zip(samples, samples[1:])]
    if any(interval <= 0 for interval in intervals) or ended_at < last["received_at"]:
        raise ValueError("invalid receipt ordering")
    if any(b["stamp"] <= a["stamp"] for a, b in zip(samples, samples[1:])):
        raise ValueError("robot message timestamps did not advance")
    largest_gap = max(intervals + [ended_at - last["received_at"]])
    if largest_gap > max_gap:
        raise ValueError("state stream gap %.3f s exceeds %.3f s" % (largest_gap, max_gap))

    def distance(a, b):
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def angle(sample):
        trace = sum(x * y for x, y in zip(first["rotation"], sample["rotation"]))
        return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0))))

    span = last["received_at"] - first["received_at"]
    return {
        "observation_complete": True,
        "sample_count": len(samples),
        "received_span_s": span,
        "received_hz": (len(samples) - 1) / span,
        "largest_receive_gap_s": largest_gap,
        "last_message_age_s": ended_at - last["received_at"],
        "tcp_net_drift_mm": 1000 * distance(first["xyz"], last["xyz"]),
        "tcp_max_deviation_mm": 1000 * max(distance(first["xyz"], s["xyz"]) for s in samples),
        "rotation_max_deviation_deg": max(angle(s) for s in samples),
        "joint_span_rad": [max(s["q"][i] for s in samples) - min(s["q"][i] for s in samples) for i in range(7)],
        "max_joint_speed_rad_s": max(abs(v) for s in samples for v in s["dq"]),
        "max_torque_change_norm_nm": max(distance(first["tau"], s["tau"]) for s in samples),
        "min_control_command_success_rate": min(s["success"] for s in samples),
        "robot_modes": sorted({s["mode"] for s in samples}),
        "current_errors": sorted({e for s in samples for e in s["current_errors"]}),
        "last_motion_errors": sorted({e for s in samples for e in s["last_motion_errors"]}),
        "verdict": "metrics_only; operator review required",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--first-message-timeout", type=float, default=5.0)
    parser.add_argument("--max-gap", type=float, default=0.5)
    args = parser.parse_args()
    for value in (args.duration, args.first_message_timeout, args.max_gap):
        if not math.isfinite(value) or value <= 0:
            parser.error("durations must be finite and positive")

    import rospy
    from franka_msgs.msg import FrankaState

    samples, failures = [], []
    lock, ready = threading.Lock(), threading.Event()

    def receive(message):
        received_at = time.monotonic()
        try:
            sample = sample_from_message(message, received_at)
            with lock:
                samples.append(sample)
        except (ValueError, TypeError, AttributeError) as error:
            with lock:
                failures.append(str(error))
        ready.set()

    rospy.init_node("hil_serl_hold_observer", anonymous=True, disable_rosout=True)
    subscriber = rospy.Subscriber(
        "/franka_state_controller/franka_states", FrankaState, receive, queue_size=1
    )
    try:
        if not ready.wait(args.first_message_timeout):
            raise ValueError("no FrankaState received before timeout")
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and not rospy.is_shutdown():
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        with lock:
            ended_at = time.monotonic()
            if failures:
                raise ValueError("invalid state message: " + failures[0])
            result = summarize(list(samples), ended_at, args.max_gap)
        if rospy.is_shutdown():
            raise ValueError("ROS shut down during observation")
        code, status, state = rospy.get_master().getSystemState()
        if code != 1:
            raise ValueError("cannot inspect ROS publishers: " + status)
        targets = {
            "/hil_serl_preflight/unused_equilibrium_pose",
            "/cartesian_impedance_controller/equilibrium_pose",
        }
        publishers = {topic: nodes for topic, nodes in state[0] if topic in targets and nodes}
        if publishers:
            raise ValueError("unexpected target publishers: " + json.dumps(publishers))
        result["target_publishers_at_end"] = publishers
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, rospy.ROSException) as error:
        print(json.dumps({"observation_complete": False, "error": str(error)}))
        return 1
    finally:
        subscriber.unregister()
        rospy.signal_shutdown("observation finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
