"""One attended recovery probe, using the existing checked impedance command path.

No ROS publishing, controller switches, sockets or gripper commands here. A
verified immutable plan supplies poses and actions from one local demonstration.
"""
import copy
import hashlib
import json

import numpy as np
from scipy.spatial.transform import Rotation

from official_home import OfficialHomeMotion


class RecoveryPlan:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        if value.get('schema') != 'autoserl_recovery_probe_v1':
            raise ValueError('Unknown recovery probe plan')
        self.point0, self.point1 = value['point0'], value['point1']
        self.poses = np.asarray(value['poses_xyz_quaternion'], dtype=float)
        self.actions = np.asarray(value['actions_body'], dtype=float)
        self.periods = np.asarray(value['periods_seconds'], dtype=float)
        if (type(self.point0) is not int or type(self.point1) is not int
                or not 0 < self.point0 < self.point1 < len(self.poses) - 1
                or self.poses.shape != (self.point1 + 2, 7)
                or self.actions.shape != (self.point1 + 1, 6)
                or self.periods.shape != (len(self.actions),)
                or not np.isfinite(self.poses).all() or not np.isfinite(self.actions).all()
                or not np.isfinite(self.periods).all() or np.max(np.abs(self.actions)) > 1. + 1e-7
                or np.min(self.periods) < .075 or np.max(self.periods) > .20
                or not np.allclose(np.linalg.norm(self.poses[:, 3:], axis=1), 1., atol=1e-6)):
            raise ValueError('Invalid recovery probe trajectory')
        self.custom_q = np.asarray(value['custom_home_q'], dtype=float)
        if self.custom_q.shape != (7,) or not np.isfinite(self.custom_q).all():
            raise ValueError('Missing demonstration custom Home')
        self.gripper_position = value['gripper_position']
        if type(self.gripper_position) is not int or not 0 < self.gripper_position < 250:
            raise ValueError('Missing demonstrated grasp')
        self.lower = self.poses[:, :3].min(axis=0) - .005
        self.upper = self.poses[:, :3].max(axis=0) + .005
        self.sha256 = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def pose_error(self, guard, index):
        pose = self.poses[index]
        return (float(np.linalg.norm(pose[:3] - guard.measured)),
                float((Rotation.from_quat(pose[3:]) * guard.measured_rotation.inv()).magnitude()))


class RecoveryProbe:
    FORCE_LIMIT_N = 10.
    TORQUE_LIMIT_NM = 1.
    # Diagnostic variant only; AutoIntervention's training parameters are separate.
    VARIANT = 'retreat_settle_v2'
    POSITION_TOLERANCE_M = .002
    ANGLE_TOLERANCE_RAD = .02
    LINEAR_SPEED_M_S = .003
    ANGULAR_SPEED_RAD_S = .03
    SETTLE_SECONDS = .3

    @classmethod
    def retreat_requirements(cls):
        return dict(variant=cls.VARIANT, position_tolerance_m=cls.POSITION_TOLERANCE_M,
                    angle_tolerance_rad=cls.ANGLE_TOLERANCE_RAD,
                    linear_speed_m_s=cls.LINEAR_SPEED_M_S,
                    angular_speed_rad_s=cls.ANGULAR_SPEED_RAD_S,
                    settle_seconds=cls.SETTLE_SECONDS)

    def __init__(self, plan, guard, now):
        self.plan, self.guard = plan, guard
        self.phase = 'approach'
        self.index = 0
        self.started = self.phase_started = now
        self.next_action = now
        self.last_tick = None
        self.retreat_settled_since = None
        self.retreat_progress = None
        self.error = None
        self.trace = []
        self.checks = []
        self.peak_force = 0.
        position, angle = plan.pose_error(guard, 0)
        if position > .003 or angle > .03 or np.max(np.abs(guard.q-plan.custom_q)) > .01:
            raise ValueError('请先退出接触并返回示范使用的自定义 Home；当前起点不匹配')
        self.check_state()

    @property
    def active(self):
        return self.phase not in ('passed', 'stopped', 'failed')

    def status(self):
        return dict(available=True, phase=self.phase, index=self.index, error=self.error,
                    point0=self.plan.point0, point1=self.plan.point1,
                    checks=copy.deepcopy(self.checks), peak_force_N=self.peak_force,
                    force_limit_N=self.FORCE_LIMIT_N, torque_limit_Nm=self.TORQUE_LIMIT_NM,
                    retreat_requirements=self.retreat_requirements(),
                    retreat_progress=copy.deepcopy(self.retreat_progress),
                    plan_sha256=self.plan.sha256)

    def check_state(self):
        guard = self.guard
        wrench = np.asarray(guard.capture_observation['tcp_wrench_K'], dtype=float)
        if wrench.shape != (6,) or not np.isfinite(wrench).all():
            raise ValueError('验证缺少有效力/力矩反馈')
        self.peak_force = max(self.peak_force, float(np.linalg.norm(wrench[:3])))
        if self.peak_force > self.FORCE_LIMIT_N or np.linalg.norm(wrench[3:]) > self.TORQUE_LIMIT_NM:
            raise ValueError('验证力/力矩超过限定值，已保持当前位置')
        if np.any(guard.measured < self.plan.lower) or np.any(guard.measured > self.plan.upper):
            raise ValueError('验证位姿超出示范范围，已保持当前位置')

    def change_phase(self, phase, now, index):
        self.phase, self.phase_started, self.index = phase, now, index
        self.retreat_settled_since = None

    def retreat_ready(self, now):
        errors = self.plan.pose_error(self.guard, self.plan.point0)
        velocity = np.asarray(self.guard.capture_observation.get('tcp_vel_base'), dtype=float)
        if velocity.shape != (6,) or not np.isfinite(velocity).all():
            raise ValueError('回退校验缺少有效 TCP 速度反馈，已保持当前位置')
        linear, angular = float(np.linalg.norm(velocity[:3])), float(np.linalg.norm(velocity[3:]))
        near_and_slow = (errors[0] < self.POSITION_TOLERANCE_M
                         and errors[1] < self.ANGLE_TOLERANCE_RAD
                         and linear < self.LINEAR_SPEED_M_S
                         and angular < self.ANGULAR_SPEED_RAD_S)
        if not near_and_slow:
            self.retreat_settled_since = None
        elif self.retreat_settled_since is None:
            self.retreat_settled_since = now
        stable = 0. if self.retreat_settled_since is None else now-self.retreat_settled_since
        self.retreat_progress = dict(errors=list(errors), linear_speed_m_s=linear,
                                     angular_speed_rad_s=angular, stable_seconds=stable)
        return near_and_slow and stable + 1e-9 >= self.SETTLE_SECONDS

    def goal_action(self, index):
        guard = self.guard
        pose = self.plan.poses[index]
        delta = pose[:3] - guard.measured
        turn = (Rotation.from_quat(pose[3:]) * guard.measured_rotation.inv()).as_rotvec()
        # Same base-frame component clipping as AutoIntervention.compute_delta_pose.
        return np.clip(np.r_[delta / .01, turn / .06], -1., 1.)

    def step(self, now):
        previous = self.guard.decision_id
        phase = self.phase
        result = self.advance(now)
        if self.guard.decision_id != previous:
            self.trace.append(dict(nuc_time=now, phase=phase, index=self.executed_index,
                                   **copy.deepcopy(self.guard.decision)))
        return result

    def advance(self, now):
        if not self.active:
            return self.guard.pose()
        self.check_state()
        if now-self.started > 120. or now-self.phase_started > 60.:
            raise ValueError('验证动作超时，已保持当前位置')
        if self.last_tick is not None and not 0 < now-self.last_tick <= .10:
            raise ValueError('验证控制循环中断，已保持当前位置')
        self.last_tick = now
        # Check every control sample, including samples between action decisions.
        # A brief departure from the limits restarts the full settling interval.
        retreat_ready = self.retreat_ready(now) if self.phase == 'retreat_reference' else False
        if now + 1e-9 < self.next_action:
            return self.guard.pose()
        # Keep an absolute replay schedule: rounding each individual period up
        # to a packet tick would cumulatively slow the entire demonstration.
        # Still enforce 75 ms minimum spacing; never burst commands after delay.
        due = self.next_action
        self.next_action = now + .1
        phase = self.phase
        self.executed_index = self.index
        if phase == 'approach':
            # The prefix uses the same recorded body commands as the recovery
            # replay. A separate tiny measured-offset pose servo can stall on
            # real impedance bias even far from contact.
            if self.index >= self.plan.point1:
                self.change_phase('verify_approach', now, self.plan.point1)
                return self.guard.execute_world_action(np.zeros(6))
            errors = self.plan.pose_error(self.guard, self.index)
            if errors[0] > .01 or errors[1] > .1:
                raise ValueError('接近重放偏离示范轨迹，已保持当前位置')
            self.guard.execute_body_action(self.plan.actions[self.index])
            self.next_action = max(due + float(self.plan.periods[self.index]), now + .075)
            self.index += 1
        elif phase == 'retreat_reference':
            if retreat_ready:
                self.checks.append(dict(phase=phase, target_index=self.plan.point0,
                    **copy.deepcopy(self.retreat_progress), requirements=self.retreat_requirements(),
                    measured_pose=np.r_[self.guard.measured, self.guard.measured_rotation.as_quat()].tolist(),
                    target_pose=self.plan.poses[self.plan.point0].tolist()))
                self.change_phase('replay_reference', now, self.plan.point0)
                return self.guard.execute_world_action(np.zeros(6))
            # Continue holding the original point0 target while settling; do not
            # capture the current (still approaching) position as a new target.
            action = self.goal_action(self.plan.point0)
            self.guard.execute_world_action(action)
        elif phase == 'replay_reference':
            if self.index > self.plan.point1:
                self.change_phase('verify_reference', now, self.plan.point1+1)
                return self.guard.execute_world_action(np.zeros(6))
            action = self.plan.actions[self.index]
            self.guard.execute_body_action(action)
            self.next_action = max(due + float(self.plan.periods[self.index]), now + .075)
            self.index += 1
        elif phase in ('verify_approach', 'verify_reference'):
            # Observe the replay endpoint without adding a position correction
            # that could conceal an unsuccessful replay.
            if now-self.phase_started < .3:
                return self.guard.pose()
            endpoint = self.plan.point1 if phase == 'verify_approach' else self.plan.point1+1
            errors = self.plan.pose_error(self.guard, endpoint)
            self.checks.append(dict(phase=phase, target_index=endpoint, errors=list(errors)))
            if errors[0] > .005 or errors[1] > .05:
                raise ValueError('动作重放未到达示范入孔位姿，已保持当前位置；需要检查轨迹和接触状态')
            following = dict(verify_approach='retreat_reference', verify_reference='passed')
            self.change_phase(following[phase], now, self.plan.point0)
            return self.guard.execute_world_action(np.zeros(6))
        else:
            raise ValueError('Unknown recovery probe phase')
        return self.guard.pose()


class RecoveryCheckMotion(OfficialHomeMotion):
    ACTIONS = OfficialHomeMotion.ACTIONS + ('recovery_check',)

    def __init__(self, *args, recovery_plan, recovery_log, **kwargs):
        super().__init__(*args, **kwargs)
        self.recovery_plan = RecoveryPlan(recovery_plan)
        self.probe = None
        self.recovery_log = recovery_log
        self.saved_probe = None

    def status(self):
        status = self.probe.status() if self.probe else \
            dict(available=True, phase='idle', point0=self.recovery_plan.point0,
                 point1=self.recovery_plan.point1, plan_sha256=self.recovery_plan.sha256,
                 retreat_requirements=RecoveryProbe.retreat_requirements())
        status['gripper_position'] = self.recovery_plan.gripper_position
        return dict(super().status(), recovery_check=status)

    def hold(self):
        probe = getattr(self, 'probe', None)
        if probe and probe.active and self.mode == 'recovery_check':
            probe.phase = 'stopped'
        super().hold()

    def handle_command(self, command, now, sample):
        if self.mode == 'recovery_check':
            self.hold()
            self.error = None if command == 'lock' else '验证被新指令中止；已保持当前位置'
            return
        if command != 'recovery_check':
            return super().handle_command(command, now, sample)
        if self.mode != 'locked' or self.joint.phase != 'idle' or self.custom_saving:
            self.error = '请先 Stop 并等待当前操作完成'; return
        if max(abs(v) for v in sample['dq']) > .03:
            self.error = '请等待机械臂停止后开始验证'; return
        try:
            if (self.custom_saved is None or not np.allclose(self.custom_saved['q'],
                    self.recovery_plan.custom_q, atol=1e-6, rtol=0)):
                raise ValueError('自定义 Home 与示范保存的目标不同，请检查配置')
            self.probe = RecoveryProbe(self.recovery_plan, self.guard, now)
            self.probe_command_id = self.command_id
            self.mode = 'recovery_check'
        except ValueError as error:
            self.error = str(error)

    def save_result(self):
        if self.probe and not self.probe.active and self.saved_probe is not self.probe:
            row = dict(command_id=self.probe_command_id, status=self.probe.status(), trace=self.probe.trace)
            with self.recovery_log.open('a') as stream:
                stream.write(json.dumps(row, allow_nan=False)+'\n')
            self.saved_probe = self.probe

    def finish_probe(self, error):
        if self.probe and self.probe.active:
            self.probe.error = str(error)
            self.probe.phase = 'stopped'
        self.save_result()

    def step(self, packet, now, dt, sample):
        if self.mode == 'recovery_check':
            # Keep the last checked target between decisions. Parent still
            # validates heartbeat, sequence, Stop and command transitions.
            self.guard.active_input = False
        result = super().step(packet, now, dt, sample)
        if self.mode == 'recovery_check':
            try:
                if np.linalg.norm(packet['axes']) > .05:
                    raise ValueError('检测到 SpaceMouse 输入，验证已停止')
                result = self.probe.step(now)
                if not self.probe.active:
                    self.hold()
            except ValueError as error:
                self.probe.error = self.error = str(error)
                self.probe.phase = 'failed'
                self.hold()
            result = self.guard.pose()
        self.save_result()
        return result
