"""Limits for a short, attended translation trial; no hardware imports.

These are trial limits, not a safety-rated collision or emergency-stop system.
"""

import math

SPEED = 0.005  # m/s, maximum target speed
TARGET_RADIUS = 0.010  # m; trial only raises base-frame Z
ACTUAL_RADIUS = 0.015
TRACKING_ERROR = 0.0055
MAX_TARGET_LEAD = 0.005  # m; stop advancing until the measured TCP catches up
START_MARGIN = 0.10  # rad, inside the existing URDF limits
RUN_MARGIN = 0.05
ELBOW_START_MARGIN = 0.0015
ELBOW_RUN_MARGIN = 0.0008
ELBOW_BACKTRACK = 0.00025  # rad; stop if J4 moves toward its lower limit
MAX_JOINT_SPEED = 0.05  # rad/s
MAX_ROTATION = math.radians(2)
MAX_AGE = 0.20  # s, both state and input heartbeat
DEADZONE = 0.15
READY_WAIT_SECONDS = 60
ACTIVE_SECONDS = 30


class TrialWindow:
    """Wait finitely for the operator, then allow one bounded active window."""

    def __init__(self, now):
        self.deadline = now + READY_WAIT_SECONDS
        self.started_at = None

    def expired(self, now):
        return now >= self.deadline

    def observe(self, armed, now):
        if self.expired(now):
            raise ValueError("trial window expired")
        if armed and self.started_at is None:
            self.started_at = now
            self.deadline = now + ACTIVE_SECONDS


def vector(values, length):
    result = [float(v) for v in values]
    if len(result) != length or not all(math.isfinite(v) for v in result):
        raise ValueError("invalid finite vector")
    return result


def norm(values):
    return math.sqrt(sum(v * v for v in values))


def distance(a, b):
    return norm([x - y for x, y in zip(a, b)])


def check_joints(q, lower, upper, margin, elbow_margin=None):
    q, lower, upper = (vector(v, 7) for v in (q, lower, upper))
    if any(lo >= hi for lo, hi in zip(lower, upper)):
        raise ValueError("invalid joint limits")
    for i, (angle, lo, hi) in enumerate(zip(q, lower, upper), 1):
        remaining = min(angle - lo, hi - angle)
        required = elbow_margin if i == 4 and elbow_margin is not None else margin
        if remaining < required:
            raise ValueError("joint %d margin %.6f rad is below %.6f rad" % (i, remaining, required))


class TranslationGuard:
    def __init__(self, sample, lower, upper):
        check_joints(sample["q"], lower, upper, START_MARGIN, ELBOW_START_MARGIN)
        self.lower, self.upper = vector(lower, 7), vector(upper, 7)
        self.origin = vector(sample["xyz"], 3)
        self.rotation = vector(sample["rotation"], 9)
        self.target = list(self.origin)
        self.measured = list(self.origin)
        self.initial_elbow = sample["q"][3]
        self.armed = False
        self.neutral_seen = False
        self.last_sequence = -1

    def check_state(self, sample, now):
        age = now - sample["received_at"]
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
            raise ValueError("robot state stale")
        if sample["mode"] != 2 or sample["current_errors"] or sample["last_motion_errors"]:
            raise ValueError("robot mode or error changed")
        check_joints(sample["q"], self.lower, self.upper, RUN_MARGIN, ELBOW_RUN_MARGIN)
        if sample["q"][3] < self.initial_elbow - ELBOW_BACKTRACK:
            raise ValueError("joint 4 moved toward its lower limit")
        if max(abs(v) for v in vector(sample["dq"], 7)) > MAX_JOINT_SPEED:
            raise ValueError("joint speed limit")
        xyz = vector(sample["xyz"], 3)
        if distance(xyz, self.origin) > ACTUAL_RADIUS:
            raise ValueError("actual TCP left trial radius")
        if distance(xyz, self.target) > TRACKING_ERROR:
            raise ValueError("TCP tracking error")
        rotation = vector(sample["rotation"], 9)
        cosine = (sum(a * b for a, b in zip(self.rotation, rotation)) - 1) / 2
        if math.acos(max(-1.0, min(1.0, cosine))) > MAX_ROTATION:
            raise ValueError("unexpected tool rotation")
        if sample["success"] < 0.95:
            raise ValueError("control command success rate degraded")
        self.measured = xyz

    def step(self, packet, now, dt):
        if not math.isfinite(dt) or not 0 < dt <= 0.10:
            raise ValueError("trial loop stalled")
        if set(packet) != {"seq", "server_time", "axes", "enable", "stop"}:
            raise ValueError("unexpected input fields")
        if type(packet["seq"]) is not int or packet["seq"] <= self.last_sequence:
            raise ValueError("repeated or unordered input")
        if type(packet["enable"]) is not bool or type(packet["stop"]) is not bool:
            raise ValueError("invalid buttons")
        age = now - float(packet["server_time"])
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
            raise ValueError("input heartbeat stale")
        axes = vector(packet["axes"], 3)
        if any(abs(v) > 1 for v in axes):
            raise ValueError("input outside normalized range")
        self.last_sequence = packet["seq"]
        if packet["stop"]:
            raise ValueError("operator stop")
        neutral = max(abs(v) for v in axes) <= DEADZONE
        if not packet["enable"]:
            self.armed = False
            self.neutral_seen = neutral
            return list(self.target)
        if not self.armed:
            if self.neutral_seen and neutral:
                self.armed = True
                self.neutral_seen = False
            return list(self.target)
        # Upward base-frame Z only. Downward, X/Y and rotation input do nothing.
        amount = max(0.0, (axes[2] - DEADZONE) / (1.0 - DEADZONE))
        self.target[2] = min(
            self.origin[2] + TARGET_RADIUS,
            self.target[2] + SPEED * amount * dt,
            max(self.target[2], self.measured[2] + MAX_TARGET_LEAD),
        )
        return list(self.target)
