import base64
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pilot_online import OnlineAPI
from reproduction.autoserl.online_smoke import SyntheticPortal
from reproduction.autoserl.online_env import PortalEnv
from reproduction.autoserl.bootstrap import ROOT


class Recorder:
    def __init__(self,transport):
        self.transport=transport;self.lock=threading.RLock();self.done=threading.Event()
        self.status={'ready':True};self.episode=self.pending=None
        self.gripper=130;self.stale=False;self.camera_stale=False;self.commands=[]
        self.gate=self

    @property
    def latest_snapshot(self):
        obs=self.transport.observation();now=time.monotonic()
        return dict(arm=dict(pc_received_at=now-(1. if self.stale else 0.),data=dict(sample=dict(
            autoserl_state=obs['state'],pilot=obs['pilot']))),
            gripper=dict(pc_received_at=now,data=dict(status=dict(gFLT=0,gPO=self.gripper))),
            cameras={name:dict(pc_captured_at=now-(1. if self.camera_stale else 0.),sequence=1,
                jpeg=base64.b64decode(frame['jpeg_base64'])) for name,frame in obs['images'].items()})

    def command(self,action,**kwargs):
        self.commands.append(action)
        if action=='policy_start':self.transport.request(dict(operation='start',plan_sha256=self.transport.motion.online_sha))
        elif action=='finish':self.transport.stop()

    def set_policy(self,index,action):self.transport.request(dict(operation='step',index=index,action=action))
    def get_status(self):return self.status


def test_online_api_roundtrip_uses_actual_acknowledged_action(tmp_path):
    synthetic=SyntheticPortal(ROOT,tmp_path);r=Recorder(synthetic);api=OnlineAPI(r)
    first=api.request(dict(operation='start',plan_sha256=synthetic.motion.online_sha))
    assert first['pilot']['mode']=='policy' and r.commands==['policy_start']
    step=api.request(dict(operation='step',index=0,action=[0.,0.,.01,0.,0.,0.]))
    assert step['pilot']['online']['completed_index']==0
    env=PortalEnv(ROOT,synthetic)
    obs=env.observation(step)
    assert obs['state'].shape==(1,19) and obs['wrist_1'].shape==(1,128,128,3)
    np.testing.assert_allclose(step['pilot']['online']['result']['actions'],[0.,0.,.01,0.,0.,0.],atol=1e-10)


@pytest.mark.parametrize('fault',['stale','camera_stale','gripper'])
def test_online_observation_rejects_stale_or_changed_grasp(tmp_path,fault):
    r=Recorder(SyntheticPortal(ROOT,tmp_path));api=OnlineAPI(r)
    setattr(r,fault,150 if fault=='gripper' else True)
    with pytest.raises(ValueError):api.request(dict(operation='observe'))


def test_unordered_action_requests_stop_without_execution(tmp_path):
    synthetic=SyntheticPortal(ROOT,tmp_path);r=Recorder(synthetic);api=OnlineAPI(r)
    api.request(dict(operation='start',plan_sha256=synthetic.motion.online_sha))
    with pytest.raises(ValueError,match='unordered'):
        api.request(dict(operation='step',index=3,action=[0.]*6))
    assert synthetic.guard.decision_id==0 and r.commands[-1]=='finish'
