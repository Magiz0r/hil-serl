"""Sequenced online actions through the existing single impedance publisher."""
import copy
import hashlib
import json

import numpy as np
from scipy.spatial.transform import Rotation

from recovery_check import RecoveryCheckMotion


class OnlineMotion(RecoveryCheckMotion):
    ACTIONS = RecoveryCheckMotion.ACTIONS + ('policy_start',)

    def __init__(self, *args, online_plan, online_log, **kwargs):
        super().__init__(*args, **kwargs)
        self.online_plan = copy.deepcopy(online_plan)
        p = self.online_plan
        if p.get('schema') != 'autoserl_online_plan_v1':
            raise ValueError('Invalid online plan')
        self.contact_stop_mode = p.get('contact_stop_mode', 'force_limit')
        if self.contact_stop_mode not in ('force_limit', 'robot_reflex'):
            raise ValueError('Invalid contact stop mode')
        self.online_lower = np.asarray(p['workspace_low'], float)
        self.online_upper = np.asarray(p['workspace_high'], float)
        self.online_origin = np.asarray(p['initial_pose'], float)
        if (self.online_lower.shape != (3,) or self.online_upper.shape != (3,)
                or self.online_origin.shape != (7,)
                or not np.isfinite(np.r_[self.online_lower,self.online_upper,self.online_origin]).all()
                or np.any(self.online_lower >= self.online_upper)
                or not np.isclose(np.linalg.norm(self.online_origin[3:]), 1.)
                or p['force_limit_N'] != 10. or p['torque_limit_Nm'] != 1.
                or (p['max_steps'] is not None and (type(p['max_steps']) is not int or p['max_steps'] < 1))):
            raise ValueError('Invalid online bounds')
        self.online_sha = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        self.online_log = online_log
        self.online = dict(phase='idle', index=-1, completed_index=-1, result=None,
                           reason=None, operator_success=False, operator_abort=False)
        self.inflight = None
        self.online_started = self.online_last_action = 0.
        self.online_command_id = None

    def status(self):
        return dict(super().status(), online=dict(copy.deepcopy(self.online),
            available=True, plan_sha256=self.online_sha,
            gripper_position=self.online_plan['gripper_position'],
            command_id=self.online_command_id))

    def handle_command(self, command, now, sample):
        if self.mode == 'policy':
            self.end_online('operator_stop' if command == 'lock' else 'new_command', now)
            return
        if command != 'policy_start':
            return super().handle_command(command, now, sample)
        if self.mode != 'locked' or self.joint.phase != 'idle' or self.custom_saving:
            self.error = '请先停止当前操作'; return
        try:
            p = self.online_plan
            if (self.custom_saved is None or not np.allclose(self.custom_saved['q'], p['custom_home_q'], atol=1e-6, rtol=0)
                    or np.max(np.abs(self.guard.q-p['custom_home_q'])) > .01
                    or np.linalg.norm(self.guard.measured-self.online_origin[:3]) > .003
                    or (self.guard.measured_rotation.inv()*Rotation.from_quat(self.online_origin[3:])).magnitude() > .03
                    or max(abs(v) for v in sample['dq']) > .03):
                raise ValueError('请先退出接触并返回原自定义 Home')
            self.check_online_state(starting=True)
            self.online = dict(phase='active', index=-1, completed_index=-1, result=None,
                reason=None, operator_success=False, operator_abort=False)
            self.inflight = None
            self.online_started = self.online_last_action = now
            self.online_command_id = self.command_id
            self.mode = 'policy'
        except ValueError as error:
            self.error = str(error)

    def check_online_state(self, *, starting=False):
        s = self.guard.capture_observation
        w = np.asarray(s['tcp_wrench_K'], float)
        if w.shape != (6,) or not np.isfinite(w).all():
            raise ValueError('invalid_wrench')
        # Home must be clear of contact. During the task, robot_reflex leaves
        # contact termination to the existing Franka collision/error checks;
        # normal insertion contact must not preempt AutoIntervention recovery.
        if ((starting or self.contact_stop_mode == 'force_limit') and
                (np.linalg.norm(w[:3]) > self.online_plan['force_limit_N'] or
                 np.linalg.norm(w[3:]) > self.online_plan['torque_limit_Nm'])):
            raise ValueError('force_limit')
        if np.any(self.guard.measured < self.online_lower-.003) or np.any(self.guard.measured > self.online_upper+.003):
            raise ValueError('workspace_limit')
        if (Rotation.from_quat(self.online_origin[3:]).inv()*self.guard.measured_rotation).magnitude() > .25:
            raise ValueError('orientation_limit')

    def complete_action(self, now, reason=None):
        if self.inflight is None:
            return
        row = dict(self.inflight, next_state=copy.deepcopy(self.guard.capture_observation),
            completed_at=now, duration_seconds=now-self.inflight['started_at'],
            terminal_reason=reason, operator_success=self.online['operator_success'],
            operator_abort=self.online['operator_abort'])
        self.online['completed_index'] = row['index']
        self.online['result'] = row
        with self.online_log.open('a') as stream:
            stream.write(json.dumps(row, allow_nan=False)+'\n')
        self.inflight = None

    def end_online(self, reason, now):
        self.complete_action(now, reason)
        self.online.update(phase='ended', reason=reason)
        self.hold()
        with self.online_log.open('a') as stream:
            stream.write(json.dumps(dict(kind='terminal',command_id=self.online_command_id,
                completed_index=self.online['completed_index'],reason=reason,
                operator_success=self.online['operator_success'],operator_abort=self.online['operator_abort'],
                state=copy.deepcopy(self.guard.capture_observation)),allow_nan=False)+'\n')

    def step(self, packet, now, dt, sample):
        packet = copy.deepcopy(packet)
        payload = packet['pilot'].pop('policy', None)
        was_active = self.mode == 'policy'
        if was_active:
            self.guard.active_input = False
        result = super().step(packet, now, dt, sample)
        if was_active and self.mode != 'policy' and self.online['phase']=='active':
            self.end_online('control_disconnected', now)
        if self.mode != 'policy':
            return result
        try:
            self.check_online_state()
            if np.linalg.norm(packet['axes']) > .05:
                raise ValueError('spacemouse_takeover')
            if payload is None:
                if now-self.online_started > 3.:
                    raise ValueError('actor_missing')
                return self.guard.pose()
            if set(payload) != {'index','action','age','buttons'}:
                raise ValueError('invalid_policy_packet')
            index = payload['index']
            action = np.asarray(payload['action'], float)
            age = payload['age']
            buttons = payload['buttons']
            if (type(index) is not int or index < 0 or action.shape != (6,)
                    or not np.isfinite(action).all() or np.max(np.abs(action))>1.+1e-7
                    or type(age) not in (float,int) or not np.isfinite(age) or not 0 <= age <= .4
                    or not isinstance(buttons,list) or len(buttons)!=2
                    or any(type(b) is not bool for b in buttons)):
                raise ValueError('invalid_or_stale_policy_action')
            if buttons[0] or buttons[1]:
                self.online.update(operator_success=buttons[0] and not buttons[1], operator_abort=buttons[1])
                self.end_online('success' if self.online['operator_success'] else 'operator_abort', now)
                return self.guard.pose()
            if self.inflight and now-self.online_last_action >= .1-1e-9:
                self.complete_action(now)
                if self.online_plan['max_steps'] is not None and self.online['index']+1 >= self.online_plan['max_steps']:
                    self.end_online('time_limit', now)
                    return self.guard.pose()
            if index == self.online['index']:
                if self.inflight and not np.allclose(action, self.inflight['requested_action'], atol=0., rtol=0.):
                    raise ValueError('policy_action_changed_without_sequence')
                return self.guard.pose()
            if index != self.online['index']+1 or self.inflight is not None:
                raise ValueError('unordered_policy_action')
            rotation = self.guard.measured_rotation
            world = np.r_[rotation.apply(action[:3]), rotation.apply(action[3:])]
            # Project the target into the task workspace before publishing.
            target = np.clip(self.guard.measured+world[:3]*.01, self.online_lower, self.online_upper)
            world[:3] = (target-self.guard.measured)/.01
            target_rotation=Rotation.from_rotvec(world[3:]*.06)*rotation
            origin_rotation=Rotation.from_quat(self.online_origin[3:])
            relative=(origin_rotation.inv()*target_rotation).as_rotvec()
            relative*=min(1.,.20/max(np.linalg.norm(relative),1e-12))
            target_rotation=origin_rotation*Rotation.from_rotvec(relative)
            world[3:]=(target_rotation*rotation.inv()).as_rotvec()/.06
            self.guard.execute_world_action(np.clip(world,-1.,1.))
            decision = copy.deepcopy(self.guard.decision)
            self.inflight = dict(command_id=self.command_id,index=index,started_at=now,
                requested_action=action.tolist(), **decision)
            self.online['index'] = index
            self.online_last_action = now
        except ValueError as error:
            self.end_online(str(error),now)
        return self.guard.pose()

    def finish_probe(self, error):
        if self.mode == 'policy':
            self.end_online(str(error), self.guard.capture_observation['received_at'])
        super().finish_probe(error)
