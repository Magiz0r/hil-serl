"""Original entry compatibility checks without a server, GPU or robot."""
import ast
import copy
import datetime
import os
import pickle
from types import SimpleNamespace

import gymnasium as gym
import numpy as np
import pytest

from reproduction.autoserl.bootstrap import ROOT
from reproduction.hilserl.config import AttendedEnv,TrainConfig
from reproduction.hilserl.upstream import check_policy_checkpoint
from test_continuous import home_data


def test_fake_original_environment_never_executes_actions():
    env=TrainConfig().get_environment(fake_env=True,classifier=True)
    obs,_=env.reset()
    assert env.observation_space.contains(obs)
    with pytest.raises(RuntimeError,match='cannot execute'):env.step(np.zeros(6))


def test_original_entry_rejects_missing_policy_instead_of_random_fallback(tmp_path):
    folder=tmp_path/'run'
    check_policy_checkpoint(folder,actor=False,evaluation_step=0)
    with pytest.raises(ValueError,match='does not exist'):
        check_policy_checkpoint(folder,actor=True,evaluation_step=5000)
    folder.mkdir()
    with pytest.raises(ValueError,match='no saved policy'):
        check_policy_checkpoint(folder,actor=True,evaluation_step=0)
    (folder/'checkpoint_5000').write_bytes(b'saved checkpoint')
    check_policy_checkpoint(folder,actor=True,evaluation_step=5000)
    with pytest.raises(ValueError,match='replay buffers'):
        check_policy_checkpoint(folder,actor=True,evaluation_step=0)
    (folder/'buffer').mkdir();(folder/'buffer/transitions_1000.pkl').write_bytes(b'buffer')
    check_policy_checkpoint(folder,actor=True,evaluation_step=0)


def test_attended_reset_accepts_completed_home_after_portal_reconnect(monkeypatch):
    data,status,plan=home_data();status['directory']='new-session'
    data['pilot']['online']={'command_id':9}
    clock=[0.];requests=[]
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:status)
    def observe(command):
        requests.append(command)
        return data
    class Base(gym.Env):
        def __init__(self):
            self.plan=plan;self.closed=0;self.resets=0
            self.transport=SimpleNamespace(request=observe,url='offline',session=SimpleNamespace(get=lambda *a,**kw:response))
        def close(self):self.closed+=1
        def reset(self,**kwargs):self.resets+=1;return 'observation',{}
    def sleep(seconds):
        clock[0]+=seconds
        assert clock[0]<3, 'Old-session command IDs must not block the new Home forever'
    monkeypatch.setattr('reproduction.hilserl.config.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('reproduction.hilserl.config.time.sleep',sleep)
    base=Base();env=AttendedEnv(base);env.last_command=100;env.last_directory='old-session'
    assert env.reset()==('observation',{})
    assert clock[0]>=2 and base.resets==1 and base.closed==1
    assert env.last_command==9 and env.last_directory=='new-session'
    assert all(r=={'operation':'observe'} for r in requests)


@pytest.mark.parametrize('human',[False,True])
def test_attended_step_reports_only_actual_human_actions(human):
    applied=np.arange(6,dtype=np.float32)/10
    class Base(gym.Env):
        def __init__(self):self.closed=False
        def step(self,action):return None,0.,False,True,dict(human_intervention=human,executed_action=applied)
        def close(self):self.closed=True
    base=Base();env=AttendedEnv(base)
    _,_,done,truncated,info=env.step(np.zeros(6))
    assert not done and truncated and base.closed
    assert ('intervene_action' in info)==human
    if human:np.testing.assert_array_equal(info['intervene_action'],applied)


def test_original_evaluation_warms_before_start_and_stops_on_truncation(capsys):
    # Execute the real actor function without importing its GPU/server globals.
    source=ROOT/'examples/train_rlpd.py'
    function=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='actor')
    calls=[]
    class Agent:
        state='weights'
        def replace(self,**kwargs):return self
        def sample_actions(self,**kwargs):calls.append('sample');return np.zeros(6)
    class Env:
        observation_space=SimpleNamespace(sample=lambda:np.zeros(1))
        def reset(self):calls.append('reset');return np.zeros(1),{}
        def step(self,action):
            calls.append('step')
            assert calls.count('step')==1, 'No action after the truncated episode'
            return np.zeros(1),0.,False,True,{}
    namespace=dict(FLAGS=SimpleNamespace(eval_checkpoint_step=5000,checkpoint_path='/unused',eval_n_trajs=1),
        jax=SimpleNamespace(random=SimpleNamespace(split=lambda key:(key,key)),device_put=lambda v:v,
                            device_get=lambda v:v,block_until_ready=lambda v:v),
        checkpoints=SimpleNamespace(restore_checkpoint=lambda *a,**kw:'weights'),
        os=SimpleNamespace(path=SimpleNamespace(abspath=lambda p:p)),time=SimpleNamespace(time=lambda:0.),
        np=SimpleNamespace(asarray=np.asarray,mean=lambda values:float('nan')))
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
    namespace['actor'](Agent(),None,None,Env(),0)
    assert calls==['sample','reset','sample','step']
    assert 'success rate: 0.0' in capsys.readouterr().out


def test_original_demo_collector_discards_timeout_and_saves_without_extra_reset(tmp_path,monkeypatch):
    source=ROOT/'examples/record_demos.py'
    function=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    class Env:
        action_space=SimpleNamespace(sample=lambda:np.zeros(6))
        def __init__(self):self.resets=0;self.steps=0
        def reset(self):self.resets+=1;return np.zeros(1),{}
        def step(self,action):
            self.steps+=1
            assert self.resets==self.steps, 'Truncation must trigger a reset before the next action'
            success=self.steps==2
            return np.zeros(1),float(success),success,not success,dict(succeed=success,intervene_action=np.ones(6))
    env=Env();progress=SimpleNamespace(set_description=lambda *a:None,update=lambda *a:None)
    namespace=dict(FLAGS=SimpleNamespace(exp_name='fmb_portal',successes_needed=1),
        CONFIG_MAPPING={'fmb_portal':lambda:SimpleNamespace(get_environment=lambda **kw:env)},
        np=np,copy=copy,os=os,pkl=pickle,datetime=datetime,tqdm=lambda **kw:progress)
    monkeypatch.chdir(tmp_path)
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
    namespace['main']([])
    assert env.resets==2 and env.steps==2
    with next((tmp_path/'demo_data').glob('*.pkl')).open('rb') as stream:rows=pickle.load(stream)
    assert len(rows)==1 and rows[0]['rewards']==1.
    np.testing.assert_array_equal(rows[0]['actions'],np.ones(6))
