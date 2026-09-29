import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'nuc'))
from autoserl_capture import DemoGuard
from official_home import JointHome
from recovery_check import RecoveryPlan, RecoveryProbe, RecoveryCheckMotion


def fixture_plan():
    return dict(schema='autoserl_recovery_probe_v1', point0=2, point1=12,
        custom_home_q=[0.] * 7, gripper_position=130,
        poses_xyz_quaternion=[[.3, .1, .5-.001*i, 0., 0., 0., 1.] for i in range(14)],
        actions_body=[[0., 0., -.1, 0., 0., 0.] for _ in range(13)],
        periods_seconds=[.1]*13)


def state(now, xyz=None):
    return dict(q=[0.] * 7, dq=[0.] * 7, xyz=xyz or [.3, .1, .5],
        rotation=np.eye(3).flatten(order='F').tolist(), mode=2,
        current_errors=[], last_motion_errors=[], success=1., received_at=now,
        K_F_ext_hat_K=[1., 0., 0., 0., 0., 0.])


@pytest.fixture
def system(tmp_path):
    sample = state(100.)
    guard = DemoGuard(sample, [-4.] * 7, [5.] * 7, trial_bounds=False, official_input=True)
    guard.check_state(sample, 100., np.c_[np.eye(6), np.zeros(6)])
    switcher = SimpleNamespace(switch=lambda *a: pytest.fail('probe switched controller'))
    with patch('official_home.pose_and_jacobian', return_value=(np.eye(4), None)):
        motion = RecoveryCheckMotion(guard, [], JointHome(switcher),
            recovery_plan=fixture_plan(), recovery_log=tmp_path/'probe.jsonl')
    motion.custom_saved = dict(q=[0.] * 7)
    return motion, guard, sample


def tick(system, seq, action='recovery_check', command_id=1, connected=True, axes=None, stop=False, bias=None, measured_rotation=None):
    motion, guard, sample = system
    now = 100.+seq*.02
    rotation = guard.target_rotation if measured_rotation is None else measured_rotation
    sample.update(received_at=now, xyz=(guard.target-(np.zeros(3) if bias is None else bias)).tolist(),
                  rotation=rotation.as_matrix().flatten(order='F').tolist())
    guard.check_state(sample, now, np.c_[np.eye(6), np.zeros(6)])
    return motion.step(dict(seq=seq, server_time=now, axes=axes or [0.]*6, enable=False,
        stop=stop, pilot=dict(id=command_id, action=action, connected=connected)), now, .02, sample)


def test_complete_probe_replays_inclusive_indices_and_locks(system):
    motion, guard, _ = system
    for seq in range(2000):
        tick(system, seq)
        if motion.probe and not motion.probe.active:
            break
    assert motion.probe.phase == 'passed', motion.status()
    assert motion.mode == 'locked'
    prefix=[row for row in motion.probe.trace if row['phase']=='approach' and row['index']<12]
    assert [row['index'] for row in prefix]==list(range(12))
    np.testing.assert_allclose([row['actions'] for row in prefix], fixture_plan()['actions_body'][:12])
    for phase in ('replay_reference',):
        replay = [row for row in motion.probe.trace if row['phase']==phase and row['index']<=12]
        assert [row['index'] for row in replay] == list(range(2,13))
        np.testing.assert_allclose([row['actions'] for row in replay], fixture_plan()['actions_body'][2:])
    assert len(motion.probe.checks)==3
    count = guard.decision_id
    for later in range(seq+1,seq+11): tick(system,later)
    assert guard.decision_id == count
    saved = json.loads(motion.recovery_log.read_text())
    assert saved['command_id']==1 and saved['status']['phase']=='passed'
    assert len(saved['trace']) == count


@pytest.mark.parametrize('case', ['pose','joints','saved_home','force','nan_force'])
def test_invalid_start_does_not_command_motion(system, case):
    motion, guard, sample = system
    if case=='pose': guard.target[0]+=.01
    elif case=='joints': sample['q'][0]=.02
    elif case=='saved_home': motion.custom_saved['q'][0]=.02
    else: sample['K_F_ext_hat_K'][0]=11. if case=='force' else float('nan')
    if case=='nan_force':
        with pytest.raises(ValueError): tick(system,0)
    else:
        tick(system,0)
        assert motion.mode=='locked' and motion.error
    assert guard.decision_id==0


@pytest.mark.parametrize('interrupt', ['lock','disconnect','axes','new_home','force','stop_packet'])
def test_interrupt_cannot_resume_or_issue_home(system, interrupt):
    motion, guard, sample = system
    for seq in range(12): tick(system,seq)
    assert motion.probe.active
    options={}
    if interrupt=='lock': options=dict(action='lock',command_id=2)
    elif interrupt=='disconnect': options=dict(connected=False)
    elif interrupt=='axes': options=dict(axes=[.1,0,0,0,0,0])
    elif interrupt=='new_home': options=dict(action='custom_home',command_id=2)
    elif interrupt=='force': sample['K_F_ext_hat_K'][0]=11.
    elif interrupt=='stop_packet': options=dict(stop=True)
    count=guard.decision_id
    if interrupt=='stop_packet':
        with pytest.raises(ValueError,match='operator stop'):tick(system,12,**options)
        motion.finish_probe('operator stop')
    else:
        tick(system,12,**options)
        assert motion.mode=='locked'
    assert not motion.probe.active
    assert guard.decision_id==count
    assert motion.joint.phase=='idle'
    assert json.loads(motion.recovery_log.read_text())['status']['phase'] in ('stopped','failed')


def test_bad_replay_endpoint_is_not_corrected_to_look_successful(system):
    motion, guard, _ = system
    tick(system,0)
    probe=motion.probe
    probe.phase='verify_reference';probe.phase_started=99.
    probe.next_action=0
    guard.target=np.array(probe.plan.poses[0,:3])
    count=guard.decision_id
    tick(system,1)
    assert probe.phase=='failed'
    assert guard.decision_id==count


@pytest.mark.parametrize('change', ['period','actions','quaternion','home','index'])
def test_reject_bad_plan(change):
    value=fixture_plan()
    if change=='period': value['periods_seconds'][0]=.4
    elif change=='actions': value['actions_body'][0][0]=float('nan')
    elif change=='quaternion': value['poses_xyz_quaternion'][0][-1]=.5
    elif change=='home': value['custom_home_q']=[]
    else: value['point1']=100
    with pytest.raises(ValueError):RecoveryPlan(value)


def test_body_replay_reconstructs_recorded_target_with_rotated_tool(system):
    _,guard,sample=system
    sample['rotation']=Rotation.from_euler('xyz',[.2,-.1,.7]).as_matrix().flatten(order='F').tolist()
    guard.target_rotation=Rotation.from_matrix(np.array(sample['rotation']).reshape(3,3,order='F'))
    guard.check_state(sample,100.,np.c_[np.eye(6),np.zeros(6)])
    body=np.array([.2,-.1,.05,.01,.02,-.01])
    target=guard.measured+guard.measured_rotation.apply(body[:3])*.01
    guard.execute_body_action(body)
    np.testing.assert_allclose(guard.target,target,atol=1e-10)
    np.testing.assert_allclose(guard.decision['actions'],body,atol=1e-10)


def test_measured_impedance_bias_does_not_deadlock_retreat(system):
    phase='retreat_reference'
    motion, guard, sample = system
    tick(system,0)
    probe=motion.probe
    probe.phase=phase
    probe.index=probe.plan.point0
    guard.target=np.array(probe.plan.poses[12,:3])
    bias=np.array([.0012,-.00093,0.])  # first real probe's persistent position residual
    for seq in range(1,1000):
        tick(system,seq,bias=bias)
        if probe.phase!=phase:break
    assert probe.phase == 'replay_reference'


def retreat_tick(system, seq, *, offset=(0., 0., 0.), angle=0., linear=0., angular=0.):
    motion, guard, sample = system
    measured = motion.probe.plan.poses[motion.probe.plan.point0, :3] + offset
    # The fixture Jacobian maps these six joint velocities directly to TCP twist.
    sample['dq'] = [linear, 0., 0., angular, 0., 0., 0.]
    tick(system, seq, bias=guard.target-measured,
         measured_rotation=Rotation.from_rotvec([angle, 0., 0.]))


@pytest.mark.parametrize('unsettled', [dict(offset=(0., 0., -.0036)),
    dict(angle=.03), dict(linear=.007), dict(angular=.04)])
def test_retreat_cannot_replay_until_close_and_stationary(system, unsettled):
    motion, guard, _ = system
    tick(system, 0)
    probe = motion.probe
    probe.change_phase('retreat_reference', 100., probe.plan.point0)
    probe.next_action = 0.
    for seq in range(1, 26):
        retreat_tick(system, seq, **unsettled)
        assert probe.phase == 'retreat_reference'
    # In particular, the observed 3.6 mm residual must keep the point0 target
    # instead of accepting the old 5 mm gate and issuing a zero/hold action.
    np.testing.assert_allclose(guard.target, probe.plan.poses[probe.plan.point0, :3])
    for seq in range(26, 41):
        retreat_tick(system, seq)
        assert probe.phase == 'retreat_reference'
    for seq in range(41, 47):
        retreat_tick(system, seq)
        if probe.phase == 'replay_reference':
            break
    assert probe.phase == 'replay_reference'
    check = probe.checks[-1]
    assert check['stable_seconds'] >= .3 - 1e-9
    assert check['requirements']['variant'] == 'retreat_settle_v2'
    np.testing.assert_allclose(check['measured_pose'], check['target_pose'])


def test_brief_motion_between_action_ticks_restarts_settling(system):
    motion, _, _ = system
    tick(system, 0)
    probe = motion.probe
    probe.change_phase('retreat_reference', 100., probe.plan.point0)
    probe.next_action = 0.
    for seq in range(1, 13):
        retreat_tick(system, seq)
    assert probe.next_action > 100.26
    retreat_tick(system, 13, linear=.01)  # No action is due on this sample.
    assert probe.retreat_settled_since is None
    for seq in range(14, 29):
        retreat_tick(system, seq)
        assert probe.phase == 'retreat_reference'
    for seq in range(29, 35):
        retreat_tick(system, seq)
        if probe.phase == 'replay_reference':
            break
    assert probe.phase == 'replay_reference'


def test_retreat_requires_valid_measured_velocity(system):
    motion, guard, _ = system
    tick(system, 0)
    probe = motion.probe
    probe.change_phase('retreat_reference', 100., probe.plan.point0)
    guard.capture_observation['tcp_vel_base'] = [float('nan')] * 6
    count = guard.decision_id
    with pytest.raises(ValueError, match='速度反馈'):
        probe.step(100.02)
    assert guard.decision_id == count


def test_prefix_divergence_stops_before_next_action(system):
    motion,guard,_=system
    tick(system,0)
    guard.target[2]-=.01
    count=guard.decision_id
    motion.probe.next_action=0.
    tick(system,1)
    assert motion.probe.phase=='failed' and guard.decision_id==count


def test_replay_period_rounding_does_not_accumulate_over_demo(system):
    motion,_,_=system
    value=fixture_plan();value['periods_seconds']=[.10968]*13
    motion.recovery_plan=RecoveryPlan(value)
    for seq in range(200):
        tick(system,seq)
        if motion.probe.phase!='approach':break
    rows=[r for r in motion.probe.trace if r['phase']=='approach' and r['index']<12]
    times=np.array([r['nuc_time'] for r in rows])
    assert len(rows)==12
    assert np.max(np.abs(times-times[0]-np.arange(12)*.10968))<.021
    assert np.min(np.diff(times))>=.075
