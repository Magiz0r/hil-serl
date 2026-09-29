"""Loopback online API using the existing camera and robot-state owners."""
import base64
import copy
import threading
import time

import numpy as np


class OnlineAPI:
    def __init__(self, recorder):
        self.recorder = recorder
        self.request_lock = threading.Lock()
        self.index = -1
        self.command_id = None

    def snapshot(self):
        r = self.recorder
        with r.lock:
            if not r.status.get('ready') or r.done.is_set():
                raise ValueError(r.status.get('reason') or 'Control is not ready')
            snap = getattr(r, 'latest_snapshot', None)
            if snap is None: raise ValueError('Waiting for checked observations')
            snap = copy.deepcopy(snap)
        now = time.monotonic()
        if now-snap['arm']['pc_received_at'] > .15:
            raise ValueError('Robot observation is stale')
        sample = snap['arm']['data']['sample']
        state = sample.get('autoserl_state')
        if state is None: raise ValueError('Controller does not provide online observations')
        pilot = sample['pilot']; online = pilot.get('online', {})
        if not online.get('available'): raise ValueError('Controller does not support online actions')
        grip = snap['gripper']['data']['status']
        if now-snap['gripper']['pc_received_at'] > .25 or grip['gFLT'] or abs(grip['gPO']-online['gripper_position'])>3:
            raise ValueError('Demonstrated grasp changed or feedback is stale')
        images = {}
        for name, frame in snap['cameras'].items():
            if frame is None or not 0 <= now-frame['pc_captured_at'] <= .35:
                raise ValueError('Camera observation is stale')
            images[name] = dict(sequence=frame['sequence'], pc_captured_at=frame['pc_captured_at'],
                jpeg_base64=base64.b64encode(frame['jpeg']).decode())
        return dict(state=state, images=images, gripper_position=grip['gPO'],
                    pilot=pilot, pc_observed_at=now)

    def request(self, data):
        if not self.request_lock.acquire(blocking=False):
            raise ValueError('Another online request is in progress')
        try:
            op = data.get('operation')
            if op == 'stop' and set(data)=={'operation'}:
                self.recorder.command('finish',source='actor')
                return dict(accepted=True)
            if op == 'observe' and set(data)=={'operation'}:
                return self.snapshot()
            if op == 'start' and set(data) in ({'operation','plan_sha256'}, {'operation','plan_sha256','human_intervention'}):
                obs = self.snapshot(); p=obs['pilot']
                human = data.get('human_intervention', False)
                if type(human) is not bool:raise ValueError('Invalid intervention mode')
                if human and not p['online'].get('human_intervention_v1'):
                    raise ValueError('NUC 尚未部署 HIL-SERL 人工接管协议')
                if p['online']['plan_sha256']!=data['plan_sha256']:
                    raise ValueError('Online plan mismatch or arm not stopped')
                if p['mode']=='policy' and p['online']['index']==-1 and p['command_id']!=self.command_id:
                    if bool(p['online'].get('human_intervention_enabled')) != human:
                        raise ValueError('Online intervention mode mismatch')
                    self.command_id=p['command_id'];self.index=-1
                    return obs
                if p['mode']!='locked':raise ValueError('Online episode is already active')
                if self.recorder.episode or self.recorder.pending:
                    raise ValueError('Recorder is busy')
                previous_id=p['command_id']
                self.recorder.command('hil_policy_start' if human else 'policy_start')
                deadline=time.monotonic()+3.
                while time.monotonic()<deadline:
                    obs=self.snapshot();p=obs['pilot']
                    if p['mode']=='policy' and p['command_id']>previous_id:
                        if bool(p['online'].get('human_intervention_enabled')) != human:
                            raise ValueError('Online intervention mode mismatch')
                        self.command_id=p['command_id'];self.index=-1
                        return obs
                    error=self.recorder.get_status().get('last_command_error')
                    if error:raise ValueError(error)
                    time.sleep(.01)
                raise ValueError('Online start was not acknowledged')
            if op == 'step' and set(data)=={'operation','index','action'}:
                action=np.asarray(data['action'],float)
                if (type(data['index']) is not int or data['index']!=self.index+1
                        or action.shape!=(6,) or not np.isfinite(action).all()
                        or np.max(np.abs(action))>1.+1e-7):
                    raise ValueError('Invalid or unordered online action')
                obs=self.snapshot();p=obs['pilot']
                if p['mode']!='policy' or p['command_id']!=self.command_id:
                    raise ValueError('Online episode is stopped')
                self.recorder.gate.set_policy(data['index'],action.tolist())
                deadline=time.monotonic()+1.
                while time.monotonic()<deadline:
                    obs=self.snapshot();p=obs['pilot'];online=p['online']
                    result=online.get('result')
                    if result and result['command_id']==self.command_id and result['index']==data['index']:
                        self.index=data['index']
                        return obs
                    if p['mode']!='policy' or p['command_id']!=self.command_id:
                        raise ValueError('Online episode stopped before action completion: '+str(online.get('reason')))
                    time.sleep(.005)
                raise ValueError('Online action was not acknowledged')
            raise ValueError('Unknown online operation')
        except Exception:
            if data.get('operation') in ('start','step'):
                self.recorder.command('finish',source='actor')
            raise
        finally:
            self.request_lock.release()
