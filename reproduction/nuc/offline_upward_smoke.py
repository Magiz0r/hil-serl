#!/usr/bin/env python3
"""Exercise upward trial messaging and cleanup using a synthetic ROS robot only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).parent
MANUAL = '--manual' in sys.argv
FREE = '--no-trial-bounds' in sys.argv
OFFICIAL = '--official-input' in sys.argv
MODE_FLAGS = (['--manual'] if MANUAL else []) + (['--no-trial-bounds'] if FREE else []) + (['--official-input'] if OFFICIAL else [])
Q = [-.286408, -.959036, .12647, -2.7367 if MANUAL else -2.74571, .145695, 1.83043, .615604]


def require_offline():
    if {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}:
        raise RuntimeError('requires network-none container')


def model():
    import roslaunch
    from fr3_kinematics import chain_from_urdf, pose_and_jacobian
    cfg = roslaunch.config.ROSLaunchConfig()
    roslaunch.xmlloader.XmlLoader().load(str(ROOT / 'fr3_hold.launch'), cfg,
                                       argv=['robot_ip:=172.16.0.1'], verbose=False)
    chain, lower, upper = chain_from_urdf(cfg.params['/robot_description'].value)
    transform, jacobian = pose_and_jacobian(chain, Q)
    return chain, transform


def synthetic_robot():
    import numpy as np
    import rospy
    from scipy.spatial.transform import Rotation
    from controller_manager_msgs.msg import ControllerState
    from controller_manager_msgs.srv import ListControllers, ListControllersResponse
    from franka_msgs.msg import FrankaState
    from geometry_msgs.msg import PoseStamped
    from dynamic_reconfigure.server import Server
    from serl_franka_controllers.cfg import compliance_paramConfig
    from fr3_kinematics import pose_and_jacobian
    from upward_trial import TARGET_TOPIC
    master = subprocess.Popen(['roscore', '-p', '11321'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
    try:
        time.sleep(1.2)
        rospy.init_node('franka_control', disable_rosout=True)
        config_server = Server(compliance_paramConfig, lambda config, level: config,
            namespace='/cartesian_impedance_controllerdynamic_reconfigure_compliance_param_node')
        chain, transform = model()
        q, target = np.array(Q), transform[:3, 3].copy()
        orientation = transform[:3, :3].copy()

        def receive(message):
            target[:] = [message.pose.position.x, message.pose.position.y, message.pose.position.z]
            quaternion = message.pose.orientation
            orientation[:] = Rotation.from_quat([quaternion.x, quaternion.y, quaternion.z, quaternion.w]).as_matrix()

        def controllers(request):
            return ListControllersResponse(controller=[
                ControllerState(name=name, state='running') for name in
                ('franka_state_controller', 'cartesian_impedance_controller')])

        service = rospy.Service('/controller_manager/list_controllers', ListControllers, controllers)
        subscriber = rospy.Subscriber(TARGET_TOPIC, PoseStamped, receive, queue_size=1)
        publisher = rospy.Publisher('/franka_state_controller/franka_states', FrankaState, queue_size=1)
        while not rospy.is_shutdown():
            transform, jacobian = pose_and_jacobian(chain, q)
            error = np.r_[target - transform[:3, 3],
                          Rotation.from_matrix(orientation @ transform[:3, :3].T).as_rotvec()]
            q += np.linalg.pinv(jacobian) @ error
            transform, _ = pose_and_jacobian(chain, q)
            message = FrankaState()
            message.header.stamp = rospy.Time.now()
            message.q = q.tolist()
            message.O_T_EE = transform.flatten(order='F').tolist()
            message.control_command_success_rate = 1.0
            message.robot_mode = FrankaState.ROBOT_MODE_MOVE
            publisher.publish(message)
            time.sleep(1 / 30)
    finally:
        master.terminate()
        master.wait(timeout=5)


def synthetic_trial():
    import upward_trial
    chain, transform = model()
    upward_trial.read_start_state = lambda: {
        'q': Q, 'xyz': transform[:3, 3].tolist(),
        'rotation': transform[:3, :3].flatten(order='F').tolist()}
    original = subprocess.Popen

    def launch(args, *a, **kw):
        if args[0] == 'bash' and str(args[1]).endswith('/launch_hold.sh'):
            args = [sys.executable, __file__, '--robot'] + MODE_FLAGS
        return original(args, *a, **kw)

    subprocess.Popen = launch
    sys.argv = [str(ROOT / 'upward_trial.py'), '--execute-attended-manual' if MANUAL else '--execute-attended-upward-trial'] + (['--no-trial-bounds'] if FREE else []) + (['--official-input'] if OFFICIAL else [])
    return upward_trial.main()


def check_scenario(disconnect):
    process = subprocess.Popen([sys.executable, __file__, '--trial'] + MODE_FLAGS, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    seq, ready_count, result = 0, 0, None
    try:
        for line in process.stdout:
            message = json.loads(line)
            if message['phase'] == 'stopping':
                continue
            if message['phase'] == 'stopped':
                result = message
                break
            if message['phase'] == 'ready':
                ready_count += 1
            if ready_count >= 24 and disconnect:
                process.stdin.close()
                continue
            seq += 1
            axes = [0, 0, 1 if ready_count >= 8 else 0]
            if MANUAL:
                axes += [0, 0, .3 if ready_count >= 8 else 0]
            packet = dict(seq=seq, server_time=message['server_time'], axes=axes,
                          enable=False if OFFICIAL else ready_count >= 4, stop=ready_count >= 24)
            process.stdin.write(json.dumps(packet) + '\n')
            process.stdin.flush()
        code = process.wait(timeout=15)
        stderr = process.stderr.read()
        assert result, stderr
        assert result.get('published_targets', 0) >= 3, result
        assert code == (1 if disconnect else 0), (code, result, stderr)
        assert 'cleanup_error' not in result, result
        if MANUAL:
            assert result['mode'] == 'manual_six_axis', result
            assert result['session_seconds'] == (None if FREE else 600), result
            assert result['trial_bounds'] is not FREE, result
            if OFFICIAL:
                assert result['input_mode'] == 'upstream_axes_no_enable_button', result
                assert result['response_profile'] == 'ram_measured_step', result
                assert result['action_hz'] == 10, result
                assert result['trial_compliance']['rotational_stiffness'] == 150, result
                assert result['trial_compliance']['rotational_damping'] == 7, result
        if disconnect:
            assert 'input connection closed' in result['error'], result
        else:
            assert result['operator_stopped'], result
        print(json.dumps({'mode': 'manual_six_axis' if MANUAL else 'upward_only',
                          'trial_bounds': not FREE,
                          'official_input': OFFICIAL,
                          'offline_scenario': 'disconnect' if disconnect else 'stop_packet',
                          'passed': True, 'published_targets': result['published_targets'],
                          'controller_exit_code': result['controller_exit_code']}), flush=True)
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    require_offline()
    if '--robot' in sys.argv:
        synthetic_robot()
    elif '--trial' in sys.argv:
        sys.exit(synthetic_trial())
    else:
        # Each scenario gets its own network namespace; ROS TCP TIME_WAIT from
        # an earlier master must not be mistaken for a live external master.
        check_scenario('--disconnect' in sys.argv)
