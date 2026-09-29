import copy
import gzip
import json
import pickle
import time
from types import SimpleNamespace

import pytest

from reproduction.portal.pilot_training import atomic_json,session_status,session_command,read_commands,ready_at_home,training_settings,save_training_settings
from reproduction.autoserl.continuous import ContinuousRunner


def publish(tmp_path,**extra):
    value=dict(session_id='run',episode=3,phase='awaiting_reset',can_label=True,heartbeat_unix=time.time())
    value.update(extra);atomic_json(tmp_path/'training-status.json',value)
    return value


def test_duration_setting_defaults_to_unlimited_and_persists(tmp_path):
    assert training_settings(tmp_path)=={'time_limit_seconds':0}
    save_training_settings({'time_limit_seconds':90},tmp_path)
    assert training_settings(tmp_path)=={'time_limit_seconds':90}
    for value in (-1,False,1.5,None,'60'):
        with pytest.raises(ValueError):save_training_settings({'time_limit_seconds':value},tmp_path)
    assert training_settings(tmp_path)=={'time_limit_seconds':90}
    save_training_settings({'time_limit_seconds':0},tmp_path)
    assert training_settings(tmp_path)=={'time_limit_seconds':0}


def test_late_label_and_pause_are_both_preserved(tmp_path):
    publish(tmp_path)
    for action in ('success','pause'):
        session_command(dict(action=action,session_id='run',episode=3),tmp_path)
    sequence,rows=read_commands('run',0,tmp_path)
    assert sequence==2 and [r['action'] for r in rows]==['success','pause']
    assert read_commands('another-run',0,tmp_path)[1]==[]


def test_pause_is_not_lost_at_episode_boundary(tmp_path):
    publish(tmp_path,episode=4,phase='starting')
    session_command(dict(action='pause',session_id='run',episode=3),tmp_path)
    assert read_commands('run',0,tmp_path)[1][0]['action']=='pause'


@pytest.mark.parametrize('change',[dict(heartbeat_unix=0),dict(episode=4),dict(session_id='other'),dict(phase='starting')])
def test_no_stale_or_cross_episode_label(tmp_path,change):
    publish(tmp_path,**change)
    with pytest.raises(ValueError):session_command(dict(action='success',session_id='run',episode=3),tmp_path)


def home_data():
    plan=dict(custom_home_q=[0.]*7,initial_pose=[0.,0.,.3,0.,0.,0.,1.])
    data=dict(pilot=dict(mode='locked',joint_phase='idle',command='custom_home',command_id=8,active_home='custom',
        custom_home=dict(available=True,joint_error_rad=.001)),state=dict(q=[0.]*7,dq=[0.]*7,xyz=[0.,0.,.3],success=1.))
    return data,dict(ready=True,recording=False),plan


def test_next_episode_requires_explicit_completed_home_not_just_stop():
    data,status,plan=home_data()
    assert ready_at_home(data,status,plan,after_command=5)
    data['pilot']['command']='lock'
    assert not ready_at_home(data,status,plan,after_command=5)
    data['pilot']['command']='custom_home';data['pilot']['mode']='homing'
    assert not ready_at_home(data,status,plan,after_command=5)
    data['pilot']['mode']='locked';data['state']['xyz'][2]=.27
    assert not ready_at_home(data,status,plan,after_command=5)


class Training:
    gradient_updates=42
    def __init__(self):self.inserted=[];self.events=[];self.saves=0
    def poll(self):pass
    def insert(self,t,flag):self.inserted.append((copy.deepcopy(t),flag))
    def event(self,kind,**data):self.events.append((kind,data))
    def save(self):self.saves+=1


def test_delayed_success_reaches_final_transition_once_and_pause_blocks_resume(tmp_path):
    trainer=Training();env=SimpleNamespace(total_recover_cnt=1,intervention_cnt=25,intervention_termination_step_threshold=10,forever_no_window_intervention=False)
    runner=ContinuousRunner(None,env,trainer,tmp_path,tmp_path/'runtime')
    try:
        runner.have_episode=True
        runner.pending=({'rewards':0.,'masks':0.,'dones':True},True)
        runner.reason='force_limit';runner.set_status('awaiting_reset')
        runner.save_pending()
        for action in ('success','pause'):
            session_command(dict(action=action,session_id=runner.session_id,episode=0),runner.runtime)
        runner.poll_commands()
        assert runner.label=='success' and not runner.enabled and not trainer.inserted
        with gzip.open(tmp_path/'episode_0000.pkl.gz','rb') as stream:
            assert pickle.load(stream)[-1]['rewards']==1.
        runner.commit();runner.commit()
        assert trainer.inserted==[({'rewards':1.,'masks':0.,'dones':True},True)]
        assert len([e for e in trainer.events if e[0]=='episode_end'])==1
    finally:
        runner.heartbeat_stop.set();runner.heartbeat.join(timeout=2.)


@pytest.mark.parametrize('phase',['awaiting_label','waiting_connection'])
def test_label_after_protective_stop_does_not_require_live_robot_connection(tmp_path,phase):
    publish(tmp_path,phase=phase)
    session_command(dict(action='success',session_id='run',episode=3),tmp_path)
    assert read_commands('run',0,tmp_path)[1][0]['action']=='success'


def test_unlabelled_stop_waits_even_at_home_and_shutdown_does_not_insert_failure(tmp_path,monkeypatch):
    trainer=Training()
    runner=ContinuousRunner(None,None,trainer,tmp_path,tmp_path/'runtime')
    try:
        runner.have_episode=True;runner.reason='force_limit'
        runner.pending=({'rewards':0.,'masks':0.,'dones':True},False)
        runner.save_pending();runner.set_status('awaiting_label')
        metadata=json.loads((tmp_path/'episode_0000.json').read_text())
        assert metadata['outcome']=='pending' and metadata['pending_final_replay_insert']
        assert session_status(runner.runtime)['episode_return'] is None
        assert runner.commit() is False
        assert not trainer.inserted and not trainer.events and runner.episode==0
        monkeypatch.setattr(runner,'status',lambda:dict(pilot=dict(mode='locked')))
        def exit_after_wait(_):raise KeyboardInterrupt
        monkeypatch.setattr('reproduction.autoserl.continuous.time.sleep',exit_after_wait)
        # The runner has no transport: it must wait for a label before even
        # considering a Home countdown, and shutdown must preserve that pending label.
        runner.run()
        assert not trainer.inserted and runner.episode==0
        assert json.loads((tmp_path/'episode_0000.json').read_text())['outcome']=='pending'
    finally:
        runner.heartbeat_stop.set();runner.heartbeat.join(timeout=2.)
