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
PILOT = '--pilot-gate' in sys.argv
OFFICIAL_HOME = '--official-home' in sys.argv
INTERRUPT_HOME = '--interrupt-home' in sys.argv
CUSTOM_HOME = '--custom-home' in sys.argv
SPEED_SCALE = float(sys.argv[sys.argv.index('--speed-scale') + 1]) if '--speed-scale' in sys.argv else 1.
TRANSLATION_SCALE = float(sys.argv[sys.argv.index('--translation-scale') + 1]) if '--translation-scale' in sys.argv else None
ROTATION_SCALE = float(sys.argv[sys.argv.index('--rotation-scale') + 1]) if '--rotation-scale' in sys.argv else None
ROTATION_RESPONSE = sys.argv[sys.argv.index('--rotation-response') + 1] if '--rotation-response' in sys.argv else 'standard'
MODE_FLAGS = (['--manual'] if MANUAL else []) + (['--no-trial-bounds'] if FREE else []) + (['--official-input'] if OFFICIAL else []) + ['--speed-scale', str(SPEED_SCALE)]
MODE_FLAGS += ['--rotation-response', ROTATION_RESPONSE]
if PILOT:
    MODE_FLAGS += ['--pilot-gate']
if OFFICIAL_HOME:
    MODE_FLAGS += ['--official-home']
if CUSTOM_HOME:
    assert OFFICIAL_HOME and PILOT
    MODE_FLAGS += ['--custom-home']
if TRANSLATION_SCALE is not None:
    MODE_FLAGS += ['--translation-scale', str(TRANSLATION_SCALE)]
if ROTATION_SCALE is not None:
    MODE_FLAGS += ['--rotation-scale', str(ROTATION_SCALE)]
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
    from controller_manager_msgs.srv import (ListControllers, ListControllersResponse, LoadController, LoadControllerResponse, SwitchController, SwitchControllerResponse, UnloadController, UnloadControllerResponse)
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
        rospy.set_param('/cartesian_impedance_controller/type',
            'hil_serl_rotation/ResponsiveCartesianImpedanceController' if ROTATION_RESPONSE=='responsive'
            else 'serl_franka_controllers/CartesianImpedanceController')
        config_server = Server(compliance_paramConfig, lambda config, level: config,
            namespace='/cartesian_impedance_controllerdynamic_reconfigure_compliance_param_node')
        chain, transform = model()
        q, target = np.array(Q), transform[:3, 3].copy()
        orientation = transform[:3, :3].copy()

        def receive(message):
            target[:] = [message.pose.position.x, message.pose.position.y, message.pose.position.z]
            quaternion = message.pose.orientation
            orientation[:] = Rotation.from_quat([quaternion.x, quaternion.y, quaternion.z, quaternion.w]).as_matrix()

        controller_states={'franka_state_controller':'running','cartesian_impedance_controller':'running'}
        joint_origin,joint_started=q.copy(),None
        joint_goal=q.copy()
        home_controllers=('hil_serl_official_home_controller','hil_serl_custom_home_controller')
        switch_until=0.
        def controllers(request):
            return ListControllersResponse(controller=[ControllerState(name=name,state=state)
                for name,state in controller_states.items()])
        def load(request):
            assert request.name in home_controllers
            controller_states[request.name]='initialized'
            return LoadControllerResponse(ok=True)
        def unload(request):
            assert request.name==home_controllers[1]
            assert controller_states[request.name] in ('initialized','stopped')
            del controller_states[request.name]
            return UnloadControllerResponse(ok=True)
        def switch(request):
            nonlocal joint_origin,joint_started,joint_goal,switch_until
            assert request.strictness==2
            assert len(request.start_controllers)==len(request.stop_controllers)==1
            start,stop=request.start_controllers[0],request.stop_controllers[0]
            assert controller_states[start] in ('initialized','stopped') and controller_states[stop]=='running'
            controller_states[stop]='stopped';controller_states[start]='running'
            switch_until=time.monotonic()+.08
            if start in home_controllers:
                joint_goal=np.array(rospy.get_param('/'+start+'/target_joint_positions'))
                joint_origin=q.copy();joint_started=time.monotonic()
                from official_home import HOME_Q
                assert rospy.get_param('/'+home_controllers[0]+'/target_joint_positions')==HOME_Q
            else:
                pose,_=pose_and_jacobian(chain,q)
                target[:]=pose[:3,3];orientation[:]=pose[:3,:3]
            return SwitchControllerResponse(ok=True)

        service = rospy.Service('/controller_manager/list_controllers', ListControllers, controllers)
        loader=rospy.Service('/controller_manager/load_controller',LoadController,load)
        unloader=rospy.Service('/controller_manager/unload_controller',UnloadController,unload)
        switcher=rospy.Service('/controller_manager/switch_controller',SwitchController,switch)
        subscriber = rospy.Subscriber(TARGET_TOPIC, PoseStamped, receive, queue_size=1)
        publisher = rospy.Publisher('/franka_state_controller/franka_states', FrankaState, queue_size=1)
        while not rospy.is_shutdown():
            transform, jacobian = pose_and_jacobian(chain, q)
            error = np.r_[target - transform[:3, 3],
                          Rotation.from_matrix(orientation @ transform[:3, :3].T).as_rotvec()]
            if any(controller_states.get(name)=='running' for name in home_controllers):
                from home_profile import PROFILE, plan_duration
                seconds=plan_duration(float(np.max(np.abs(joint_goal-joint_origin))))
                s=max(0.,min(1.,(time.monotonic()-joint_started-PROFILE['hold_seconds'])/seconds))
                fraction=s*s*s*(10+s*(-15+6*s))
                q[:]=joint_origin+fraction*(joint_goal-joint_origin)
            else:
                q += np.linalg.pinv(jacobian) @ error
            transform, _ = pose_and_jacobian(chain, q)
            message = FrankaState()
            message.header.stamp = rospy.Time.now()
            message.q = q.tolist()
            message.q_d = q.tolist()
            message.O_T_EE = transform.flatten(order='F').tolist()
            message.control_command_success_rate = 1.0
            message.robot_mode = FrankaState.ROBOT_MODE_MOVE
            if time.monotonic()<switch_until:
                # Real FR3 also exposes Move with a zero initialized rate.
                message.robot_mode = (FrankaState.ROBOT_MODE_IDLE if
                    switch_until-time.monotonic()>.04 else FrankaState.ROBOT_MODE_MOVE)
                message.control_command_success_rate = 0.
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
    sys.argv += ['--speed-scale', str(SPEED_SCALE)]
    sys.argv += ['--rotation-response', ROTATION_RESPONSE]
    if TRANSLATION_SCALE is not None:
        sys.argv += ['--translation-scale', str(TRANSLATION_SCALE)]
    if ROTATION_SCALE is not None:
        sys.argv += ['--rotation-scale', str(ROTATION_SCALE)]
    if PILOT:
        sys.argv += ['--pilot-gate']
    if OFFICIAL_HOME:
        sys.argv += ['--official-home']
    return upward_trial.main()


def check_scenario(disconnect):
    process = subprocess.Popen([sys.executable, __file__, '--trial'] + MODE_FLAGS, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    seq, ready_count, result = 0, 0, None
    stop_count = (130 if INTERRUPT_HOME else 850) if OFFICIAL_HOME else 260 if PILOT else 24
    if CUSTOM_HOME and INTERRUPT_HOME:stop_count=190
    modes_seen = set()
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
            if ready_count >= stop_count and disconnect:
                process.stdin.close()
                continue
            seq += 1
            axes = [0, 0, 1 if ready_count >= 8 else 0]
            if MANUAL:
                axes += [0, 0, .3 if ready_count >= 8 else 0]
            packet = dict(seq=seq, server_time=message['server_time'], axes=axes,
                          enable=False if OFFICIAL else ready_count >= 4, stop=ready_count >= stop_count)
            if PILOT:
                identifier, action = ((0, 'lock') if ready_count < 4 else
                    (1, 'lock' if OFFICIAL_HOME else 'set_home') if ready_count < 8 else
                    (2, 'start') if ready_count < 32 else
                    (3, 'lock') if ready_count < 45 else (4, 'home'))
                if CUSTOM_HOME:
                    identifier,action=((0,'lock') if ready_count<4 else
                        (1,'set_custom_home') if ready_count<25 else
                        (2,'start') if ready_count<60 else
                        (3,'lock') if ready_count<75 else (4,'custom_home'))
                if INTERRUPT_HOME and ready_count>=(140 if CUSTOM_HOME else 80):
                    identifier,action=5,'lock'
                packet['pilot'] = dict(id=identifier, action=action, connected=True)
                packet['axes'] = [0.,0.,.4,0.,0.,0.] if ready_count >= (30 if CUSTOM_HOME else 12) else [0.]*6
                gate = message.get('pilot', {})
                modes_seen.add(gate.get('mode'))
                locked_interval=(64 <= ready_count < 75) if CUSTOM_HOME else (36 <= ready_count < 45)
                if locked_interval:
                    assert gate['mode'] == 'locked', message
            process.stdin.write(json.dumps(packet) + '\n')
            process.stdin.flush()
        code = process.wait(timeout=15)
        stderr = process.stderr.read()
        assert result, stderr
        assert result.get('published_targets', 0) >= 3, result
        assert code == (1 if disconnect else 0), (code, result, stderr)
        assert 'cleanup_error' not in result, result
        if PILOT:
            assert {'locked','manual','homing'} <= modes_seen, modes_seen
            assert result['pilot_gate'] and result['pilot']['home_set'], result
            assert result['pilot']['mode'] == 'locked' and not result['pilot']['error'], result
            rows=[json.loads(line) for line in (Path(result['run_directory'])/'samples.jsonl').read_text().splitlines()]
            locked=[r['target'] for r in rows if r['pilot']['command_id']==3]
            assert len(locked)>2 and all(t==locked[0] for t in locked), locked
            home=result['pilot']['home']['xyz']
            goal=result['pilot']['home']['q']
            if CUSTOM_HOME:
                from official_home import HOME_Q
                from fr3_kinematics import pose_and_jacobian
                custom=result['pilot']['custom_home']
                assert custom['available'] and custom['persistent'] and not custom['error'],custom
                assert goal==HOME_Q and max(abs(a-b) for a,b in zip(custom['q'],HOME_Q))>.1
                assert max(abs(a-b) for a,b in zip(custom['q'],Q))<.001
                assert json.loads(Path('/hil-serl-state/custom_home.json').read_text())['q']==custom['q']
                goal=custom['q'];home=pose_and_jacobian(model()[0],goal)[0][:3,3].tolist()
            if not INTERRUPT_HOME:
                assert max(abs(a-b) for a,b in zip(rows[-1]['state']['xyz'],home)) < .0015, (home,rows[-1])
                if OFFICIAL_HOME:
                    assert max(abs(a-b) for a,b in zip(rows[-1]['state']['q'],goal)) < .005
            elif OFFICIAL_HOME:
                assert result['pilot']['active_home_joint_error_rad'] > (.001 if CUSTOM_HOME else .02)
        if MANUAL:
            assert result['mode'] == 'manual_six_axis', result
            assert result['session_seconds'] == (None if FREE else 600), result
            assert result['trial_bounds'] is not FREE, result
            if OFFICIAL:
                assert result['input_mode'] == 'upstream_axes_no_enable_button', result
                translation_scale = SPEED_SCALE if TRANSLATION_SCALE is None else TRANSLATION_SCALE
                rotation_scale = SPEED_SCALE if ROTATION_SCALE is None else ROTATION_SCALE
                assert result['response_profile'] == ('ram_measured_step' if SPEED_SCALE == translation_scale == rotation_scale == 1. else 'measured_step_scaled'), result
                assert result['speed_scale'] == SPEED_SCALE, result
                assert result['translation_scale'] == translation_scale, result
                assert result['position_action_scale_mm'] == 10 * translation_scale, result
                assert result['rotation_action_scale_rad'] == .06 * rotation_scale, result
                assert result['rotation_scale'] == rotation_scale, result
                assert result['rotation_joint_speed_rad_s'] == (.55 if rotation_scale > 2 else .4), result
                assert result['action_hz'] == 10, result
                assert result['rotation_response'] == ROTATION_RESPONSE, result
                compliance = result['trial_compliance']
                assert compliance['rotational_stiffness'] == (300 if ROTATION_RESPONSE in ('fast','responsive') else 150), result
                assert compliance['rotational_damping'] == (10 if ROTATION_RESPONSE in ('fast','responsive') else 7), result
                assert result['rotation_filter_coefficient'] == (.02 if ROTATION_RESPONSE=='responsive' else .005), result
                assert compliance['translational_stiffness'] == 2000, result
                assert compliance['translational_damping'] == 89, result
                for axis in ('x', 'y', 'z'):
                    for sign in ('', 'neg_'):
                        assert compliance['rotational_clip_' + sign + axis] == .03, result
                        assert compliance['translational_clip_' + sign + axis] == .005, result
        if disconnect:
            assert 'input connection closed' in result['error'], result
        else:
            assert result['operator_stopped'], result
        print(json.dumps({'mode': 'manual_six_axis' if MANUAL else 'upward_only',
                          'pilot_gate': PILOT,
                          'official_home': OFFICIAL_HOME,
                          'interrupt_home': INTERRUPT_HOME,
                          'custom_home': CUSTOM_HOME,
                          'trial_bounds': not FREE,
                          'official_input': OFFICIAL,
                          'speed_scale': SPEED_SCALE,
                          'translation_scale': TRANSLATION_SCALE,
                          'rotation_response': ROTATION_RESPONSE,
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
