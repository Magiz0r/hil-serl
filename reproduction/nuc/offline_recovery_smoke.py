#!/usr/bin/env python3
"""Run the real command loop against synthetic ROS in a network-none container."""
import json
from pathlib import Path
import subprocess
import sys

FLAGS = ['--manual', '--no-trial-bounds', '--official-input', '--autoserl-demo',
         '--pilot-gate', '--official-home']
sys.argv += [flag for flag in FLAGS if flag not in sys.argv]
import offline_upward_smoke as fake
fake.require_offline()


def plan():
    import numpy as np
    from scipy.spatial.transform import Rotation
    _, transform = fake.model()
    xyz = transform[:3, 3]
    rotation = Rotation.from_matrix(transform[:3, :3])
    body = np.r_[rotation.inv().apply([0., 0., .1]), [0., 0., 0.]].tolist()
    return dict(schema='autoserl_recovery_probe_v1', point0=2, point1=12,
        custom_home_q=fake.Q, gripper_position=130,
        poses_xyz_quaternion=[np.r_[xyz+[0., 0., .001*i], rotation.as_quat()].tolist()
                              for i in range(14)],
        actions_body=[body for _ in range(13)], periods_seconds=[.1]*13)


def scenario(kind):
    process = subprocess.Popen([sys.executable, __file__, '--trial'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    count, result, sent_interrupt = 0, None, False
    phases = set()
    try:
        for line in process.stdout:
            message = json.loads(line)
            if message['phase'] == 'stopped':
                result = message
                break
            if message['phase'] == 'stopping' or process.stdin.closed:
                continue
            if message['phase'] == 'ready': count += 1
            status = message.get('pilot', {}).get('recovery_check', {})
            phase = status.get('phase')
            phases.add(phase)
            interrupt = phase == 'replay_reference' and kind != 'complete'
            if interrupt and kind == 'disconnect':
                process.stdin.close()
                continue
            if interrupt: sent_interrupt = True
            command = 'lock' if count < 5 or sent_interrupt else 'recovery_check'
            identifier = 2 if sent_interrupt else 0 if count < 5 else 1
            packet = dict(seq=count+int(message['server_time']*1000000),
                server_time=message['server_time'], axes=[0.]*6, enable=False,
                stop=phase == 'passed' or (sent_interrupt and phase == 'stopped') or count > 1800,
                pilot=dict(id=identifier, action=command, connected=True))
            process.stdin.write(json.dumps(packet)+'\n'); process.stdin.flush()
        code = process.wait(timeout=10)
        assert result, process.stderr.read()
        assert code == (1 if kind=='disconnect' else 0), result
        assert result['pilot']['recovery_check']['phase'] == ('passed' if kind=='complete' else 'stopped'), result
        assert 'cleanup_error' not in result and 'recovery_log_error' not in result, result
        run = Path(result['run_directory'])
        saved = [json.loads(s) for s in (run/'recovery-checks.jsonl').read_text().splitlines()]
        assert len(saved)==1 and saved[0]['command_id']==1
        assert (run/'demo-actions.jsonl').read_text() == ''
        if kind=='complete':
            assert {'approach','retreat_reference','replay_reference','passed'} <= phases
            assert len(saved[0]['status']['checks'])==3
        print(json.dumps(dict(scenario=kind, passed=True, checks=saved[0]['status']['checks'],
                              phase=saved[0]['status']['phase'], run=str(run))), flush=True)
    finally:
        if not process.stdin.closed: process.stdin.close()
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)


if __name__ == '__main__':
    if '--robot' in sys.argv:
        fake.synthetic_robot()
    elif '--trial' in sys.argv:
        import upward_trial
        from custom_home import CustomHomeStore
        from fr3_kinematics import chain_from_urdf
        import roslaunch
        cfg = roslaunch.config.ROSLaunchConfig()
        roslaunch.xmlloader.XmlLoader().load(str(fake.ROOT/'fr3_hold.launch'), cfg,
            argv=['robot_ip:=172.16.0.1'], verbose=False)
        _, lower, upper = chain_from_urdf(cfg.params['/robot_description'].value)
        CustomHomeStore('/hil-serl-state/custom_home.json').save(fake.Q, lower, upper)
        upward_trial.recovery_plan = plan
        sys.exit(fake.synthetic_trial())
    else:
        scenario(sys.argv[sys.argv.index('--scenario')+1])
