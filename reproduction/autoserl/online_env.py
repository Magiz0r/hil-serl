"""Real portal environment with the exact exported observation/action convention."""
import base64
import hashlib
import json
from pathlib import Path
import time

import cv2
import gymnasium as gym
import numpy as np
import requests
from scipy.spatial.transform import Rotation

from .export_demo import measured_pose, state_observation
from reproduction.portal.pilot_training import training_settings


IMAGE_KEYS = ('wrist_1', 'wrist_2')


def load_config(root):
    root=Path(root)
    selection=json.loads((root/'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json').read_text())
    demo=root/selection['demo_path']; manifest=demo.with_suffix('.json')
    for path,key in ((demo,'demo_pickle_sha256'),(manifest,'demo_manifest_sha256')):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=selection[key]:
            raise ValueError('Selected single demo changed')
    return selection,json.loads(manifest.read_text())


def spaces():
    return gym.spaces.Dict(dict(state=gym.spaces.Box(-np.inf,np.inf,(1,19),np.float32),
        **{k:gym.spaces.Box(0,255,(1,128,128,3),np.uint8) for k in IMAGE_KEYS})), gym.spaces.Box(-1.,1.,(6,),np.float32)


class PortalTransport:
    def __init__(self, url='http://127.0.0.1:8765'):
        if url!='http://127.0.0.1:8765': raise ValueError('Use the local capture portal')
        self.url=url; self.session=requests.Session(); self.session.trust_env=False

    def request(self, data):
        response=self.session.post(self.url+'/autoserl',json=data,timeout=2.)
        if not response.ok:
            raise RuntimeError(response.json().get('error',response.text))
        return response.json()

    def stop(self):
        self.request(dict(operation='stop'))


class PortalEnv(gym.Env):
    def __init__(self, root, transport=None, *, human_intervention=False):
        self.human_intervention_enabled = human_intervention
        self.root=Path(root)
        self.selection,self.manifest=load_config(root)
        self.plan=json.loads((self.root/'reproduction/configs/autoserl/fmb_insertion_online_v1.json').read_text())
        self.plan_sha=hashlib.sha256(json.dumps(self.plan,sort_keys=True).encode()).hexdigest()
        self.preprocessing=json.loads((self.root/'reproduction/configs/autoserl/fmb_insertion_images_v1.json').read_text())
        if self.preprocessing!=self.manifest['preprocessing']:
            raise ValueError('Online image preprocessing differs from the selected demo')
        p=np.asarray(self.plan['initial_pose'])
        self.origin=(p[:3],Rotation.from_quat(p[3:]))
        self.currpos=p.copy()
        self.action_scale=np.array([.01,.06,1.])
        self.observation_space,self.action_space=spaces()
        self.transport=transport or PortalTransport()
        self.index=-1;self.active=False
        self._stop_pending=False
        self.episode_time_limit_seconds=0
        self.episode_started=None

    def observation(self, data, state=None):
        state=state or data['state']
        xyz,rot=measured_pose(state);self.currpos=np.r_[xyz,rot.as_quat()]
        obs=dict(state=state_observation(state,1.-data['gripper_position']/255.,self.origin))
        for key,name in zip(IMAGE_KEYS,('external','wrist')):
            raw=base64.b64decode(data['images'][name]['jpeg_base64'],validate=True)
            bgr=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
            if bgr is None or bgr.shape!=(720,1280,3):raise ValueError('Unexpected camera frame')
            x,y,w,h=self.preprocessing['crops_xywh'][name]
            rgb=cv2.cvtColor(bgr[y:y+h,x:x+w],cv2.COLOR_BGR2RGB)
            obs[key]=cv2.resize(rgb,(128,128),interpolation=cv2.INTER_AREA)[None]
        return obs

    def preview(self):
        return self.observation(self.transport.request(dict(operation='observe')))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        # A lost start acknowledgement can still leave the robot running.
        self._stop_pending=True
        # Snapshot the user's setting once per episode; edits apply next round.
        self.episode_time_limit_seconds=training_settings(self.root/'reproduction/runtime')['time_limit_seconds']
        request=dict(operation='start',plan_sha256=self.plan_sha)
        if self.human_intervention_enabled:request['human_intervention']=True
        data=self.transport.request(request)
        if bool(data['pilot']['online'].get('human_intervention_enabled')) != self.human_intervention_enabled:
            raise ValueError('Online intervention mode mismatch')
        self.index=-1;self.active=True
        self.episode_started=time.monotonic()
        return self.observation(data),dict(synthetic=False,plan_sha256=self.plan_sha)

    def step(self, action):
        if not self.active:raise RuntimeError('Start an episode before stepping')
        action=np.asarray(action,float)
        if action.shape!=(6,) or not np.isfinite(action).all() or np.max(abs(action))>np.sqrt(3.)+1e-7:
            raise ValueError('Invalid policy action')
        # AutoIntervention constructs bounded BASE-frame corrections. Rotating
        # them into the body frame can exceed a component of the policy Box.
        # Apply the SAME base clipping and uniform body bound used by DemoGuard
        # during collection; replay buffers store the acknowledged actual action.
        rotation=Rotation.from_quat(self.currpos[3:])
        world=np.clip(np.r_[rotation.apply(action[:3]),rotation.apply(action[3:])],-1.,1.)
        body=np.r_[rotation.inv().apply(world[:3]),rotation.inv().apply(world[3:])]
        bounded=body/max(1.,float(np.max(np.abs(body))))
        data=self.transport.request(dict(operation='step',index=self.index+1,action=bounded.tolist()))
        online=data['pilot']['online'];result=online['result']
        if self.human_intervention_enabled and type(result.get('human_intervention')) is not bool:
            raise ValueError('Missing acknowledged human action provenance')
        if not self.human_intervention_enabled and result.get('human_intervention'):
            raise ValueError('Unexpected human intervention during policy-only control')
        if result['index']!=self.index+1:raise ValueError('Wrong action acknowledgement')
        self.index+=1
        reason=result['terminal_reason'] or online.get('reason')
        success=bool(result['operator_success'] or online['operator_success'])
        abort=bool(result['operator_abort'] or online['operator_abort'])
        if (not reason and not success and not abort and self.episode_time_limit_seconds and
                time.monotonic()-self.episode_started >= self.episode_time_limit_seconds):
            # Stop at an acknowledged action boundary, without inventing an
            # extra transition or turning expiration into an operator failure.
            self.close()
            reason='time_limit'
        terminated=bool(success or abort or (reason and reason!='time_limit'))
        truncated=reason=='time_limit'
        self.active=not(terminated or truncated)
        info=dict(synthetic=False,operator_success=success,operator_abort=abort,succeed=success,
            time_limit_seconds=self.episode_time_limit_seconds,
            terminal_reason=reason,executed_action=np.asarray(result['actions'],np.float32),
            requested_action=action.astype(np.float32),decision_id=result['decision_id'],
            human_intervention=bool(result.get('human_intervention',False)),
            intervention_source=result.get('intervention_source','policy'),
            action_index=self.index,command_id=result['command_id'],duration_seconds=result['duration_seconds'])
        return self.observation(data,result['next_state']),float(success),terminated,truncated,info

    def transform_action(self, action):
        action=np.asarray(action).copy();r=Rotation.from_quat(self.currpos[3:])
        return np.r_[r.apply(action[:3]),r.apply(action[3:])]

    def transform_action_inv(self, action):
        action=np.asarray(action).copy();r=Rotation.from_quat(self.currpos[3:]).inv()
        return np.r_[r.apply(action[:3]),r.apply(action[3:])]

    def close(self):
        # Episode cleanup and runner shutdown both call close. Once Stop was
        # accepted, a later cleanup must not interrupt the operator's Home.
        # Keep this separate from active: terminal actions still need cleanup,
        # and failed starts/stops must remain eligible for a Stop retry.
        if self._stop_pending:
            self.transport.stop()
            self._stop_pending=False
        self.active=False


class PortalSignals:
    # Labels arrive in the action acknowledgement from the existing HID owner.
    def get_action(self):return np.zeros(6),[False,False]
