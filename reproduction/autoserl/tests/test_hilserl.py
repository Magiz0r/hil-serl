"""Offline HIL protocol, executed-action replay, provenance and release checks."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from test_online_motion import online_system, tick as online_tick
from test_online_api import Recorder
from test_continuous import Training
from test_online_resume import make_run
from reproduction.autoserl.bootstrap import ROOT
from reproduction.autoserl.continuous import ContinuousRunner
from reproduction.autoserl.online_smoke import SyntheticPortal
from reproduction.autoserl.train_online import restore_run
from reproduction.hilserl.online_env import HILPortalEnv
from reproduction.portal.pilot_online import OnlineAPI


def tick(*args,command='hil_policy_start',**kwargs):
    return online_tick(*args,command=command,**kwargs)


def test_human_override_release_and_single_decision_per_index(online_system):
    motion,guard,_=online_system
    tick(online_system,0,command='hil_policy_start')
    tick(online_system,1,index=0,axes=[0,.2,0,0,0,0])
    np.testing.assert_allclose(guard.decision['actions'],[0,.2,0,0,0,0],atol=1e-10)
    # Input changes do not corrupt an already in-flight replay transition.
    for seq in range(2,8):tick(online_system,seq,index=0,axes=[0,-.3,0,0,0,0])
    assert guard.decision_id==1
    assert motion.online['result']['human_intervention'] is True
    np.testing.assert_allclose(motion.online['result']['requested_action'],[.1,0,0,0,0,0])
    # Neutral input hands control back at the next decision, with no latch.
    for seq in range(8,15):tick(online_system,seq,index=1)
    result=motion.online['result']
    assert not result['human_intervention'] and result['intervention_source']=='policy'
    np.testing.assert_allclose(result['actions'],[.1,0,0,0,0,0],atol=1e-10)


def test_human_world_action_is_clipped_then_saved_in_body_frame(online_system):
    motion,guard,sample=online_system
    rotation=Rotation.from_euler('z',90,degrees=True)
    guard.target_rotation=rotation
    motion.online_origin[3:]=rotation.as_quat()
    tick(online_system,0,command='hil_policy_start')
    guard.target=np.array([.319,.1,.5])
    tick(online_system,1,index=0,axes=[1,0,0,0,0,0])
    assert motion.mode=='policy' and guard.target[0]==pytest.approx(.32)
    np.testing.assert_allclose(guard.decision['actions'],[0,-.1,0,0,0,0],atol=1e-10)


@pytest.mark.parametrize('fault',['stale','disconnect','both_buttons','stop'])
def test_human_input_never_bypasses_stop_or_freshness(online_system,fault):
    motion,guard,_=online_system
    tick(online_system,0,command='hil_policy_start')
    tick(online_system,1,index=0,axes=[0,.2,0,0,0,0])
    args=dict(index=0,axes=[0,.2,0,0,0,0])
    if fault=='stale':args['age']=.5
    if fault=='disconnect':args['connected']=False
    if fault=='both_buttons':args['buttons']=[True,True]
    if fault=='stop':args.update(command='lock',cid=2)
    tick(online_system,2,**args)
    assert motion.mode=='locked' and guard.decision_id==1
    assert not motion.online['operator_success'] and not motion.online['operator_abort']


def test_api_hil_handshake_and_executed_human_action(tmp_path):
    synthetic=SyntheticPortal(ROOT,tmp_path);recorder=Recorder(synthetic)
    api=OnlineAPI(recorder)
    api.request(dict(operation='start',plan_sha256=synthetic.motion.online_sha,human_intervention=True))
    assert recorder.commands==['hil_policy_start']
    synthetic.axes=[.1,0,0,0,0,0]
    ack=api.request(dict(operation='step',index=0,action=[0]*6))
    result=ack['pilot']['online']['result']
    assert result['human_intervention'] and np.linalg.norm(result['actions'])>0


def test_old_controller_cannot_start_hil(tmp_path,monkeypatch):
    synthetic=SyntheticPortal(ROOT,tmp_path);recorder=Recorder(synthetic)
    original=synthetic.motion.status
    def legacy():
        value=original();value['online'].pop('human_intervention_v1');return value
    monkeypatch.setattr(synthetic.motion,'status',legacy)
    with pytest.raises(ValueError,match='尚未部署'):
        OnlineAPI(recorder).request(dict(operation='start',plan_sha256=synthetic.motion.online_sha,human_intervention=True))
    assert 'hil_policy_start' not in recorder.commands and synthetic.guard.decision_id==0


def test_hil_env_roundtrip_and_unassisted_evaluation(tmp_path):
    synthetic=SyntheticPortal(ROOT,tmp_path);env=HILPortalEnv(ROOT,synthetic)
    obs,_=env.reset();synthetic.axes=[.1,0,0,0,0,0]
    _,_,_,_,info=env.step(np.zeros(6))
    assert info['human_intervention'] and np.linalg.norm(info['executed_action'])>0
    synthetic.axes=[0]*6
    _,_,_,_,info=env.step(np.zeros(6))
    assert not info['human_intervention']
    env.close()
    other=tmp_path/'eval';other.mkdir()
    synthetic=SyntheticPortal(ROOT,other);env=HILPortalEnv(ROOT,synthetic,evaluation=True)
    env.reset();synthetic.axes=[.1,0,0,0,0,0]
    with pytest.raises(RuntimeError,match='spacemouse_takeover'):env.step(np.zeros(6))
    assert synthetic.guard.decision_id==0


def test_hil_commit_records_human_and_keeps_takeover_enabled(tmp_path):
    trainer=Training();base=SimpleNamespace(algorithm_id='hilserl',human_intervention_enabled=True)
    runner=ContinuousRunner(base,base,trainer,tmp_path,tmp_path/'runtime',start_paused=True)
    try:
        assert not runner.enabled
        runner.have_episode=True;runner.pending=({'rewards':0.,'masks':0.,'dones':True},True)
        assert runner.commit() is False and not trainer.inserted
        runner.label='success';assert runner.commit()
        row=json.loads((tmp_path/'episode_0000.json').read_text())
        assert row['human_interventions']==1 and row['automatic_interventions']==0
        assert trainer.inserted==[({'rewards':1.,'masks':0.,'dones':True},True)]
        assert base.human_intervention_enabled and not (tmp_path/'intervention-state.json').exists()
    finally:
        runner.heartbeat_stop.set();runner.heartbeat.join(timeout=2.)


def test_hil_resume_uses_human_flags_and_rejects_other_algorithm(tmp_path):
    run=make_run(tmp_path/'hil')
    manifest=dict(schema='hilserl_online_run_v1',algorithm_id='hilserl',synthetic=False,demo_sha256='demo')
    (run/'manifest.json').write_text(json.dumps(manifest))
    (run/'events.jsonl').write_text(json.dumps(dict(kind='step',episode=0,action_index=0,human_intervention=True,auto_intervention=False))+'\n')
    assert restore_run(run,'demo',algorithm='hilserl')[2][0][1] is True
    with pytest.raises(ValueError,match='algorithm'):restore_run(run,'demo')
    child=make_run(tmp_path/'auto',parent=run)
    with pytest.raises(ValueError,match='algorithm'):restore_run(child,'demo')
