import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from test_recovery_check import fixture_plan, state
from autoserl_capture import DemoGuard
from official_home import JointHome
from online_motion import OnlineMotion


@pytest.fixture
def online_system(tmp_path):
    sample=state(100.)
    guard=DemoGuard(sample,[-4.]*7,[5.]*7,trial_bounds=False,official_input=True)
    guard.check_state(sample,100.,np.c_[np.eye(6),np.zeros(6)])
    plan=dict(schema='autoserl_online_plan_v1',workspace_low=[.28,.08,.45],workspace_high=[.32,.12,.52],
        initial_pose=[.3,.1,.5,0.,0.,0.,1.],custom_home_q=[0.]*7,gripper_position=130,
        force_limit_N=10.,torque_limit_Nm=1.,max_steps=300)
    switcher=SimpleNamespace(switch=lambda *a:pytest.fail('online action switched controllers'))
    with patch('official_home.pose_and_jacobian',return_value=(np.eye(4),None)):
        motion=OnlineMotion(guard,[],JointHome(switcher),recovery_plan=fixture_plan(),
            recovery_log=tmp_path/'probe.jsonl',online_plan=plan,online_log=tmp_path/'online.jsonl')
    motion.custom_saved=dict(q=[0.]*7)
    return motion,guard,sample


def tick(system, seq, index=None, action=None, *, age=0., command='policy_start', cid=1,
         axes=None, buttons=None, connected=True):
    motion,guard,sample=system;now=100.+seq*.02
    sample.update(received_at=now,xyz=guard.target.tolist(),rotation=guard.target_rotation.as_matrix().flatten(order='F').tolist())
    guard.check_state(sample,now,np.c_[np.eye(6),np.zeros(6)])
    pilot=dict(id=cid,action=command,connected=connected)
    if index is not None:pilot['policy']=dict(index=index,action=action or [.1,0.,0.,0.,0.,0.],age=age,buttons=buttons or [False,False])
    return motion.step(dict(seq=seq,server_time=now,axes=axes or [0.]*6,enable=False,stop=False,pilot=pilot),now,.02,sample)


def test_each_index_executes_once_and_has_measured_completion(online_system):
    motion,guard,_=online_system
    tick(online_system,0)
    assert motion.mode=='policy' and guard.decision_id==0
    for seq in range(1,8):tick(online_system,seq,index=0)
    assert guard.decision_id==1
    result=motion.online['result']
    assert result['index']==0 and result['duration_seconds']>=.1-1e-9
    np.testing.assert_allclose(result['actions'],[.1,0,0,0,0,0])
    np.testing.assert_allclose(np.asarray(result['next_state']['xyz'])-result['state']['xyz'],[.001,0,0])
    for seq in range(8,15):tick(online_system,seq,index=1)
    assert guard.decision_id==2 and motion.online['completed_index']==1
    assert len(motion.online_log.read_text().splitlines())==2


@pytest.mark.parametrize('reason',['force','stale','axes','disconnect','stop','new_home','unordered','changed','success','abort'])
def test_online_stops_hold_and_never_auto_home(online_system,reason):
    motion,guard,sample=online_system
    tick(online_system,0);tick(online_system,1,index=0)
    count=guard.decision_id;args=dict(index=0)
    if reason=='force':sample['K_F_ext_hat_K'][0]=11.
    elif reason=='stale':args['age']=.5
    elif reason=='axes':args['axes']=[.1,0,0,0,0,0]
    elif reason=='disconnect':args['connected']=False
    elif reason in ('stop','new_home'):args.update(command='lock' if reason=='stop' else 'custom_home',cid=2)
    elif reason=='unordered':args['index']=2
    elif reason=='changed':args['action']=[.2,0,0,0,0,0]
    elif reason=='success':args['buttons']=[True,False]
    elif reason=='abort':args['buttons']=[False,True]
    tick(online_system,2,**args)
    assert motion.mode=='locked' and motion.online['phase']=='ended'
    assert guard.decision_id==count and motion.joint.phase=='idle'
    assert motion.online['result']['terminal_reason']
    assert motion.online['operator_success']==(reason=='success')


def test_workspace_clips_before_execution_and_records_actual_action(online_system):
    motion,guard,sample=online_system
    tick(online_system,0)
    guard.target=np.array([.319,.1,.5])
    tick(online_system,1,index=0,action=[1.,0,0,0,0,0])
    assert guard.target[0]<=.32
    assert guard.decision['actions'][0]==pytest.approx(.1)


def test_wrong_home_cannot_start_online(online_system):
    motion,guard,_=online_system
    guard.target[0]+=.01
    tick(online_system,0)
    assert motion.mode=='locked' and motion.error and guard.decision_id==0


def test_robot_reflex_mode_allows_contact_to_continue_across_actions(online_system):
    motion,guard,sample=online_system
    motion.contact_stop_mode='robot_reflex'
    tick(online_system,0)
    # Successful new demo reaches 14.86 N. Neither that contact nor crossing
    # the old torque threshold should terminate the task in this mode.
    sample['K_F_ext_hat_K']=[0.,0.,14.864,0.,1.01,0.]
    for seq in range(1,8):tick(online_system,seq,index=0)
    for seq in range(8,15):tick(online_system,seq,index=1)
    assert motion.mode=='policy' and motion.online['reason'] is None
    assert guard.decision_id==2 and motion.online['completed_index']==1
    tick(online_system,15,index=2,buttons=[True,False])
    assert motion.mode=='locked' and motion.online['operator_success']
    assert motion.online['reason']=='success'


def test_robot_reflex_mode_still_rejects_loaded_home_and_robot_errors(online_system):
    motion,guard,sample=online_system
    motion.contact_stop_mode='robot_reflex'
    sample['K_F_ext_hat_K'][0]=11.
    tick(online_system,0)
    assert motion.mode=='locked' and motion.error=='force_limit'
    sample['K_F_ext_hat_K'][0]=0.
    tick(online_system,1,cid=2)
    assert motion.mode=='policy'
    sample['current_errors']=['cartesian_reflex']
    with pytest.raises(ValueError):guard.check_state(sample,100.02,np.c_[np.eye(6),np.zeros(6)])


def test_robot_reflex_mode_never_accepts_invalid_wrench(online_system):
    motion,guard,_=online_system;motion.contact_stop_mode='robot_reflex'
    guard.capture_observation['tcp_wrench_K']=[float('nan')]*6
    with pytest.raises(ValueError,match='invalid_wrench'):motion.check_online_state()


@pytest.mark.parametrize('limit',[None,3])
def test_no_hidden_300_step_cap_and_explicit_legacy_limit_still_works(online_system,limit):
    motion,guard,_=online_system;motion.online_plan['max_steps']=limit
    tick(online_system,0)
    steps=302 if limit is None else limit
    for index in range(steps):
        for offset in range(7):tick(online_system,index*7+offset+1,index=index,action=[0.]*6)
    assert guard.decision_id==steps and motion.online['completed_index']==steps-1
    assert motion.online['reason']==(None if limit is None else 'time_limit')
    assert motion.mode==('policy' if limit is None else 'locked')
