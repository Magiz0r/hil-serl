"""Bounded six-axis manual input; offline math, not a safety-rated system."""

import math
import numpy as np
from scipy.spatial.transform import Rotation

from translation_guard import MAX_AGE, DEADZONE, check_joints, vector

LINEAR_SPEED = 0.010  # m/s, vector magnitude
ANGULAR_SPEED = math.radians(5)  # rad/s, vector magnitude
WORKSPACE_RADIUS = 0.050
ORIENTATION_RADIUS = math.radians(10)
MAX_POSITION_LEAD = 0.005
MAX_ROTATION_LEAD = math.radians(3)
SESSION_SECONDS = 600
IDLE_SECONDS = 120
# Pinned upstream RAM action scale at the environment's default 10 Hz.
# These are measured-pose increments, not guaranteed Cartesian velocities.
ACTION_PERIOD = 0.1
POSITION_ACTION_SCALE = 0.010
ROTATION_ACTION_SCALE = 0.060
ACTION_JOINT_SPEED = 0.4


def normalized_input(values, deadzone=DEADZONE):
    values = np.asarray(values, dtype=float)
    scaled = np.sign(values) * np.maximum(0, (np.abs(values) - deadzone) / (1 - deadzone))
    return scaled / max(1, np.linalg.norm(scaled))


class ManualGuard:
    def __init__(self, sample, lower, upper, *, trial_bounds=True, official_input=False,
                 speed_scale=1.0, translation_scale=None, rotation_scale=None):
        if type(speed_scale) not in (int, float) or speed_scale not in (1., 1.5, 2.):
            raise ValueError('speed_scale must be 1, 1.5 or 2')
        if speed_scale != 1. and (trial_bounds or not official_input):
            raise ValueError('speed scaling requires official manual input without trial bounds')
        if translation_scale is not None:
            if type(translation_scale) not in (int, float) or translation_scale not in (1., 1.5, 1.6, 2.):
                raise ValueError('translation_scale must be 1, 1.5, 1.6 or 2')
            if trial_bounds or not official_input:
                raise ValueError('translation scaling requires official manual input without trial bounds')
        self.speed_scale = speed_scale
        self.translation_scale = speed_scale if translation_scale is None else translation_scale
        if rotation_scale is not None:
            if type(rotation_scale) not in (int,float) or rotation_scale not in (1.,1.5,2.,3.):
                raise ValueError('rotation_scale must be 1, 1.5, 2 or 3')
            if trial_bounds or not official_input:
                raise ValueError('rotation scaling requires official manual input without trial bounds')
        self.rotation_scale = speed_scale if rotation_scale is None else rotation_scale
        self.rotation_joint_speed = .55 if self.rotation_scale > 2 else ACTION_JOINT_SPEED
        self.trial_bounds = trial_bounds
        self.official_input = official_input
        check_joints(sample['q'], lower, upper, 0.10 if trial_bounds else 0.,
                     0.003 if trial_bounds else 0.)
        self.lower, self.upper = np.array(vector(lower, 7)), np.array(vector(upper, 7))
        self.origin = np.array(vector(sample['xyz'], 3))
        self.origin_rotation = Rotation.from_matrix(np.array(vector(sample['rotation'], 9)).reshape(3, 3, order='F'))
        self.target = self.origin.copy()
        self.target_rotation = self.origin_rotation
        self.measured = self.origin.copy()
        self.measured_rotation = self.origin_rotation
        self.q = np.array(vector(sample['q'], 7))
        self.inverse_jacobian = None
        self.armed = self.neutral_seen = False
        self.last_sequence = -1
        self.limit_reason = None
        self.action_elapsed = ACTION_PERIOD
        self.active_input = False

    def pose(self):
        return self.target.tolist(), self.target_rotation.as_quat().tolist()

    def check_state(self, sample, now, jacobian):
        age = now - sample['received_at']
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
            raise ValueError('robot state stale')
        if sample['mode'] != 2 or sample['current_errors'] or sample['last_motion_errors']:
            raise ValueError('robot mode or error changed: mode=%r current_errors=%r last_motion_errors=%r' %
                             (sample['mode'], sample['current_errors'], sample['last_motion_errors']))
        check_joints(sample['q'], self.lower, self.upper, 0.025 if self.trial_bounds else 0.,
                     0.0008 if self.trial_bounds else 0.)
        if max(abs(v) for v in vector(sample['dq'], 7)) > (0.6 if self.official_input else 0.15):
            raise ValueError('joint speed limit')
        xyz = np.array(vector(sample['xyz'], 3))
        rotation = Rotation.from_matrix(np.array(vector(sample['rotation'], 9)).reshape(3, 3, order='F'))
        if self.trial_bounds and np.linalg.norm(xyz - self.origin) > WORKSPACE_RADIUS + 0.010:
            raise ValueError('actual TCP left manual workspace')
        if self.trial_bounds and (rotation * self.origin_rotation.inv()).magnitude() > ORIENTATION_RADIUS + math.radians(5):
            raise ValueError('actual rotation left manual workspace')
        if np.linalg.norm(xyz - self.target) > (0.025 if self.official_input else 0.008):
            raise ValueError('TCP tracking error')
        if (rotation * self.target_rotation.inv()).magnitude() > (0.12 if self.official_input else math.radians(5)):
            raise ValueError('rotation tracking error')
        if not math.isfinite(sample['success']) or sample['success'] < 0.95:
            raise ValueError('control command success rate degraded')
        jacobian = np.asarray(jacobian, dtype=float)
        if jacobian.shape != (6, 7) or not np.isfinite(jacobian).all():
            raise ValueError('invalid Jacobian')
        if np.linalg.svd(jacobian, compute_uv=False)[-1] < 0.05:
            raise ValueError('near singularity')
        self.measured, self.measured_rotation = xyz, rotation
        self.q = np.array(vector(sample['q'], 7))
        self.inverse_jacobian = np.linalg.pinv(jacobian)

    def step(self, packet, now, dt):
        if not math.isfinite(dt) or not 0 < dt <= 0.10:
            raise ValueError('manual loop stalled')
        if set(packet) != {'seq', 'server_time', 'axes', 'enable', 'stop'}:
            raise ValueError('unexpected input fields')
        if type(packet['seq']) is not int or packet['seq'] <= self.last_sequence:
            raise ValueError('repeated or unordered input')
        if type(packet['enable']) is not bool or type(packet['stop']) is not bool:
            raise ValueError('invalid buttons')
        age = now - float(packet['server_time'])
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
            raise ValueError('input heartbeat stale')
        axes = np.array(vector(packet['axes'], 6))
        if np.max(np.abs(axes)) > 1:
            raise ValueError('input outside normalized range')
        self.last_sequence = packet['seq']
        if packet['stop']:
            raise ValueError('operator stop')
        neutral = (np.linalg.norm(axes) <= 0.001 if self.official_input
                   else np.max(np.abs(axes)) <= DEADZONE)
        self.limit_reason = None
        if self.official_input:
            # Match upstream nonzero-axis intervention, without a held button.
            # Require one neutral input first to avoid acting on a held cap at startup.
            if not self.armed:
                self.armed = bool(neutral)
                return self.pose()
        elif not packet['enable']:
            self.armed = False
            self.neutral_seen = bool(neutral)
            return self.pose()
        if not self.armed:
            if self.neutral_seen and neutral:
                self.armed = True
                self.neutral_seen = False
            return self.pose()
        if self.inverse_jacobian is None:
            raise ValueError('no checked robot state')
        if self.official_input:
            return self.measured_step(axes, neutral, dt)
        if neutral:
            return self.pose()
        deadzone = 0.0 if self.official_input else DEADZONE
        translation = normalized_input(axes[:3], deadzone) * LINEAR_SPEED * dt
        rotation_step = normalized_input(axes[3:], deadzone) * ANGULAR_SPEED * dt
        joint_speed = self.inverse_jacobian @ np.r_[translation, rotation_step] / dt
        scale = min(1.0, 0.08 / max(1e-12, np.max(np.abs(joint_speed))))
        translation *= scale
        rotation_step *= scale
        previous_position_lead = np.linalg.norm(self.target - self.measured)
        previous_rotation_lead = (self.target_rotation * self.measured_rotation.inv()).magnitude()
        margins = np.array([0.05, 0.05, 0.05, 0.002, 0.05, 0.05, 0.05])
        safe_lower, safe_upper = self.lower + margins, self.upper - margins
        if not self.trial_bounds:
            # Starting near an existing limit must permit moving away from it.
            # These local prediction margins never expand the URDF limits.
            safe_lower = np.minimum(self.lower + 0.002, self.q)
            safe_upper = np.maximum(self.upper - 0.002, self.q)
        for fraction in (1, .5, .25, .125, .0625, .03125, .015625, .0078125):
            candidate = self.target + fraction * translation
            candidate_rotation = Rotation.from_rotvec(fraction * rotation_step) * self.target_rotation
            relative_rotation = (candidate_rotation * self.measured_rotation.inv()).as_rotvec()
            if self.trial_bounds and np.linalg.norm(candidate - self.origin) > WORKSPACE_RADIUS:
                self.limit_reason = 'workspace_limit'
                continue
            if self.trial_bounds and (candidate_rotation * self.origin_rotation.inv()).magnitude() > ORIENTATION_RADIUS:
                self.limit_reason = 'orientation_limit'
                continue
            if np.linalg.norm(candidate - self.measured) > max(MAX_POSITION_LEAD, previous_position_lead) + 1e-10:
                self.limit_reason = 'waiting_for_translation_tracking'
                continue
            if np.linalg.norm(relative_rotation) > max(MAX_ROTATION_LEAD, previous_rotation_lead) + 1e-10:
                self.limit_reason = 'waiting_for_rotation_tracking'
                continue
            predicted_q = self.q + self.inverse_jacobian @ np.r_[candidate - self.measured, relative_rotation]
            if np.any(predicted_q < safe_lower - 1e-10) or np.any(predicted_q > safe_upper + 1e-10):
                self.limit_reason = 'joint_limit'
                continue
            self.target, self.target_rotation = candidate, candidate_rotation
            return self.pose()
        return self.pose()

    def measured_step(self, axes, neutral, dt):
        """Follow fresh measured-pose increments without accumulating old commands.

        Uses the upstream RAM period and explicitly selectable action scale. Neutral captures the
        measured pose once for diagnostic holding (there is no policy fallback).
        The existing robot/URDF limits and local joint prediction still apply.
        """
        if neutral:
            if self.active_input:
                self.target = self.measured.copy()
                self.target_rotation = self.measured_rotation
            self.active_input = False
            self.action_elapsed = ACTION_PERIOD
            return self.pose()
        self.action_elapsed += dt
        if self.active_input and self.action_elapsed + 1e-9 < ACTION_PERIOD:
            return self.pose()
        self.active_input = True
        self.action_elapsed = 0.
        translation = axes[:3] * POSITION_ACTION_SCALE * self.translation_scale
        rotation_step = axes[3:] * ROTATION_ACTION_SCALE * self.rotation_scale
        joint_delta = self.inverse_jacobian @ np.r_[translation, rotation_step]
        joint_budget = self.rotation_joint_speed if np.any(rotation_step) else ACTION_JOINT_SPEED
        scale = min(1., joint_budget * ACTION_PERIOD /
                    max(1e-12, np.max(np.abs(joint_delta))))
        if self.rotation_joint_speed > ACTION_JOINT_SPEED:
            # Faster rotation must not enlarge the translation-only joint budget.
            translation_joints = self.inverse_jacobian @ np.r_[translation, np.zeros(3)]
            scale = min(scale, ACTION_JOINT_SPEED * ACTION_PERIOD /
                        max(1e-12, np.max(np.abs(translation_joints))))
        if max(self.rotation_scale, self.translation_scale) > 1.:
            # Keep requested offsets inside the unchanged tracking checks,
            # including simultaneous full-scale input on several axes.
            scale = min(scale, .020 / max(1e-12, np.linalg.norm(translation)),
                        .10 / max(1e-12, np.linalg.norm(rotation_step)))
        translation *= scale
        rotation_step *= scale
        safe_lower = np.minimum(self.lower + 0.002, self.q)
        safe_upper = np.maximum(self.upper - 0.002, self.q)
        for fraction in (1, .5, .25, .125, .0625, .03125, .015625, .0078125):
            delta = fraction * np.r_[translation, rotation_step]
            predicted_q = self.q + self.inverse_jacobian @ delta
            if np.any(predicted_q < safe_lower - 1e-10) or np.any(predicted_q > safe_upper + 1e-10):
                self.limit_reason = 'joint_limit'
                continue
            self.target = self.measured + fraction * translation
            self.target_rotation = Rotation.from_rotvec(fraction * rotation_step) * self.measured_rotation
            return self.pose()
        # Drop the previous command if no new increment can be accepted.
        self.target = self.measured.copy()
        self.target_rotation = self.measured_rotation
        return self.pose()
