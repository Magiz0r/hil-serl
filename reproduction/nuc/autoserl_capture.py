"""Opt-in fixed-gripper demonstration decisions and a lossless action journal.

Pure computation and file output only. The existing attended controller owns all
publishing, state checks, stop handling and Home operations.
"""
import copy
import json

import numpy as np
from scipy.spatial.transform import Rotation

from manual_guard import ManualGuard, ACTION_PERIOD

PROTOCOL = 'autoserl_fr3_demo_v1'
ACTION_SCALE = (0.01, 0.06)
# Pinned AutoSERL plug_insert/config.py COMPLIANCE_PARAM, not PRECISION_PARAM.
UPSTREAM_COMPLIANCE = dict(translational_stiffness=3000., translational_damping=89.,
    rotational_stiffness=150., rotational_damping=7., translational_Ki=0., rotational_Ki=0.,
    translational_clip_x=.0075, translational_clip_y=.0016, translational_clip_z=.0055,
    translational_clip_neg_x=.002, translational_clip_neg_y=.0016, translational_clip_neg_z=.005,
    rotational_clip_x=.01, rotational_clip_y=.025, rotational_clip_z=.02,
    rotational_clip_neg_x=.01, rotational_clip_neg_y=.025, rotational_clip_neg_z=.02)
# FR3/FMB hardware adaptation, 2026-09-25: +base-X stalled with full input at
# the upstream 2 mm negative-error clip. Start with 3 mm (9 N spring term at
# K=3000), keeping every other gain, action scale and guard unchanged. This is
# a tracking-error clip, not a collision/contact-force threshold. Capture and
# online control share this same configuration; physical response needs checking.
COMPLIANCE = dict(UPSTREAM_COMPLIANCE, translational_clip_neg_x=.003)


def capture_state(sample, jacobian):
    """Same wrench frame as upstream; checked URDF Jacobian supplies base twist."""
    wrench = np.asarray(sample.get('K_F_ext_hat_K'), dtype=float)
    if wrench.shape != (6,) or not np.isfinite(wrench).all():
        raise ValueError('AutoSERL capture requires measured K_F_ext_hat_K; no zero fallback')
    result = copy.deepcopy(sample)
    result['tcp_vel_base'] = (np.asarray(jacobian) @ np.asarray(sample['dq'])).tolist()
    result['tcp_wrench_K'] = wrench.tolist()
    return result


class DemoGuard(ManualGuard):
    """10 Hz decisions, including neutral input; manual mode remains unchanged."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.official_input or self.translation_scale != 1. or self.rotation_scale != 1.:
            raise ValueError('AutoSERL capture requires official input with unit scales')
        self.capture_enabled = False
        self.capture_elapsed = ACTION_PERIOD
        self.decision_id = 0
        self.decision = None
        self.capture_observation = None

    def check_state(self, sample, now, jacobian):
        super().check_state(sample, now, jacobian)
        self.capture_observation = capture_state(sample, jacobian)

    def measured_step(self, axes, neutral, dt):
        if not self.capture_enabled:
            self.capture_elapsed = ACTION_PERIOD
            return super().measured_step(axes, neutral, dt)
        self.capture_elapsed += dt
        if self.capture_elapsed + 1e-9 < ACTION_PERIOD:
            return self.pose()
        self.capture_elapsed = 0.
        return self.execute_world_action(axes)

    def execute_body_action(self, action):
        action = np.asarray(action, dtype=float)
        if action.shape != (6,) or not np.isfinite(action).all() or np.max(np.abs(action)) > 1. + 1e-7:
            raise ValueError('Invalid fixed-gripper body action')
        rotation = self.measured_rotation
        world = np.r_[rotation.apply(action[:3]), rotation.apply(action[3:])]
        return self.execute_world_action(np.clip(world, -1., 1.))

    def execute_world_action(self, axes):
        axes = np.asarray(axes, dtype=float)
        if (axes.shape != (6,) or not np.isfinite(axes).all() or np.max(np.abs(axes)) > 1. + 1e-7
                or self.inverse_jacobian is None or self.capture_observation is None):
            raise ValueError('Action requires normalized axes and checked robot state')
        rotation = self.measured_rotation
        # SpaceMouse input is base-frame. Bound the corresponding body-frame
        # action before execution, so the recorded action fits the policy Box.
        body = np.r_[rotation.inv().apply(axes[:3]), rotation.inv().apply(axes[3:])]
        # Uniform scaling keeps both base and body components inside [-1, 1].
        # Component clipping in body coordinates can enlarge a base component,
        # which the upstream base environment would then clip a second time.
        world = np.asarray(axes) / max(1., np.max(np.abs(body)))
        # Force one decision, including zero input. Parent still applies its
        # Jacobian/joint-limit scaling and all tracking checks.
        self.active_input = False
        pose = super().measured_step(world, False, ACTION_PERIOD)
        delta = self.target - self.measured
        turn = (self.target_rotation * rotation.inv()).as_rotvec()
        applied = np.r_[rotation.inv().apply(delta) / ACTION_SCALE[0],
                        rotation.inv().apply(turn) / ACTION_SCALE[1]]
        if not np.isfinite(applied).all() or np.max(np.abs(applied)) > 1. + 1e-7:
            raise ValueError('Executed action does not fit AutoSERL action space')
        self.decision_id += 1
        self.decision = dict(decision_id=self.decision_id, state=copy.deepcopy(self.capture_observation),
            actions=applied.tolist(), target_xyz=list(pose[0]), target_quaternion=list(pose[1]))
        return pose


class ActionJournal:
    """Never infer actions from robot displacement or fill a dropped event."""
    def __init__(self, path):
        self.stream = path.open('x')
        self.episode_id = None
        self.index = 0
        self.last_decision = -1

    def observe(self, guard, pilot, packet, now):
        active = pilot.mode == 'manual' and packet['pilot']['connected']
        if self.episode_id is not None and (not active or pilot.command_id != self.episode_id):
            self.write(dict(kind='terminal', state=guard.capture_observation), packet, now)
            self.episode_id = None
        if active and self.episode_id is None:
            self.episode_id, self.index = pilot.command_id, 0
            self.last_decision = guard.decision_id
        if active and guard.decision_id != self.last_decision:
            self.write(dict(kind='action', **guard.decision), packet, now)
            self.last_decision = guard.decision_id

    def write(self, value, packet, now):
        row = dict(protocol=PROTOCOL, episode_command_id=self.episode_id,
                   index=self.index, source_input_seq=packet['seq'], nuc_time=now, **value)
        self.stream.write(json.dumps(row, allow_nan=False) + '\n')
        self.stream.flush()
        self.index += 1

    def close(self):
        # A disconnected/failed run has no terminal row and cannot be exported
        # as a complete demonstration.
        self.stream.close()
