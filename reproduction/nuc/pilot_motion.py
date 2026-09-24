"""Start/Finish gating and an operator-taught Cartesian Home for manual pilots.

No Desk reset, joint reset, gripper action, or collision recovery. Home's elevated
waypoint assumes the operator teaches Home above all task objects; it is not an
obstacle avoidance planner. All existing ManualGuard state checks remain active.
"""
import math
import numpy as np
from scipy.spatial.transform import Rotation


class PilotMotion:
    ACTIONS = ('lock', 'start', 'set_home', 'home')
    def __init__(self, guard):
        self.guard = guard
        self.mode = 'locked'
        self.home = None
        self.command_id = -1
        self.command = None
        self.error = None
        self.waypoints = []
        self.waypoint = 0
        self.deadline = None
        self.settled_since = None

    def hold(self):
        self.guard.target = self.guard.measured.copy()
        self.guard.target_rotation = self.guard.measured_rotation
        self.guard.active_input = False
        self.mode = 'locked'
        self.waypoints = []

    def status(self):
        return dict(mode=self.mode, home_set=self.home is not None,
                    home=None if self.home is None else dict(xyz=self.home[0].tolist(),
                        quaternion=self.home[1].as_quat().tolist(), q=self.home[2]),
                    command_id=self.command_id, command=self.command, error=self.error,
                    waypoint=self.waypoint if self.mode == 'homing' else None)

    def step(self, packet, now, dt, sample):
        base = dict(packet)
        command = base.pop('pilot')
        if (set(command) != {'id', 'action', 'connected'}
                or type(command['id']) is not int or command['id'] < self.command_id
                or command['action'] not in self.ACTIONS
                or type(command['connected']) is not bool):
            raise ValueError('invalid pilot command')
        new_command = command['id'] > self.command_id
        if not new_command and command['action'] != self.command:
            raise ValueError('pilot command changed without new id')
        axes = np.asarray(base['axes'], dtype=float)
        if axes.shape != (6,) or not np.isfinite(axes).all() or np.max(np.abs(axes)) > 1:
            raise ValueError('invalid pilot axes')
        neutral = np.linalg.norm(axes) <= .001
        if self.mode != 'manual' or not command['connected'] or (new_command and command['action'] != 'start'):
            base.update(axes=[0.] * 6, enable=False)
        # Validate heartbeat, stop packet, sequence and input even while locked.
        self.guard.step(base, now, dt)
        if not command['connected']:
            if self.mode != 'locked':
                self.hold()
                self.error = 'recorder heartbeat lost; press Start again after reconnecting'
            return self.guard.pose()
        if new_command:
            self.command_id, self.command = command['id'], command['action']
            self.error = None
            self.handle_command(self.command,now,sample)
        if self.mode == 'waiting_for_center' and neutral:
            self.mode = 'manual'
        if self.mode == 'homing':
            self.home_step(now, dt, sample)
        return self.guard.pose()

    def handle_command(self, command, now, sample):
        if command == 'lock':
            self.hold()
        elif command == 'start':
            if self.mode not in ('locked', 'waiting_for_center'):
                self.error = 'Finish the current operation before Start'
            else:
                self.hold()
                self.mode = 'waiting_for_center'
        elif command == 'set_home':
            if self.mode != 'locked' or max(abs(v) for v in sample['dq']) > .03:
                self.error = 'Finish and wait for the arm to settle before setting Home'
            else:
                self.home = (self.guard.measured.copy(), self.guard.measured_rotation, list(sample['q']))
                self.hold()
        elif command == 'home':
            if self.mode != 'locked' or self.home is None:
                self.error = 'Finish first and set a Home pose before returning'
            else:
                self.hold()
                xyz, rotation, _ = self.home
                high = max(xyz[2], self.guard.measured[2])
                lift = self.guard.measured.copy(); lift[2] = high
                above = xyz.copy(); above[2] = high
                self.waypoints = [(lift, self.guard.measured_rotation), (above, rotation), (xyz, rotation)]
                self.waypoint, self.mode, self.deadline = 0, 'homing', now + 90.
                self.settled_since = None

    def home_step(self, now, dt, sample):
        guard = self.guard
        if now > self.deadline:
            self.hold(); self.error = 'Home timed out; holding current pose'
            return
        xyz, rotation = self.waypoints[self.waypoint]
        distance = np.linalg.norm(xyz - guard.measured)
        angle = (rotation * guard.measured_rotation.inv()).magnitude()
        if distance < .001 and angle < math.radians(.5) and max(abs(v) for v in sample['dq']) < .03:
            if self.settled_since is None:
                self.settled_since = now
            if now - self.settled_since >= .2:
                self.waypoint += 1
                self.settled_since = None
                if self.waypoint == len(self.waypoints):
                    guard.target, guard.target_rotation = xyz.copy(), rotation
                    self.mode = 'locked'
                    self.waypoints = []
                    return
                xyz, rotation = self.waypoints[self.waypoint]
        else:
            self.settled_since = None
        delta = xyz - guard.target
        delta *= min(1., .02 * dt / max(1e-12, np.linalg.norm(delta)))
        turn = (rotation * guard.target_rotation.inv()).as_rotvec()
        turn *= min(1., math.radians(10) * dt / max(1e-12, np.linalg.norm(turn)))
        dq = guard.inverse_jacobian @ np.r_[delta, turn]
        fraction = min(1., .15 * dt / max(1e-12, np.max(np.abs(dq))))
        for shrink in (1., .5, .25, .125, .0625, .03125, .015625, .0078125):
            scale = fraction * shrink
            candidate = guard.target + scale * delta
            candidate_rotation = Rotation.from_rotvec(scale * turn) * guard.target_rotation
            lead = candidate - guard.measured
            angular_lead = (candidate_rotation * guard.measured_rotation.inv()).as_rotvec()
            if np.linalg.norm(lead) > .005 or np.linalg.norm(angular_lead) > .04:
                continue
            predicted_q = guard.q + guard.inverse_jacobian @ np.r_[lead, angular_lead]
            lower = np.minimum(guard.lower + .002, guard.q)
            upper = np.maximum(guard.upper - .002, guard.q)
            if np.any(predicted_q < lower - 1e-10) or np.any(predicted_q > upper + 1e-10):
                self.hold(); self.error = 'Home path approaches a joint limit; holding current pose'
                return
            guard.target, guard.target_rotation = candidate, candidate_rotation
            return
