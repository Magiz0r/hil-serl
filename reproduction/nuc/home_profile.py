"""Shared Home timing parameters; also generates constants for the C++ plugin."""
import json
import math
from pathlib import Path

PROFILE = json.loads(Path(__file__).with_suffix('.json').read_text())
CPP_NAMES = dict(minimum_duration='kMinimumDuration', maximum_duration='kMaximumDuration',
                 hold_seconds='kHold', max_joint_velocity='kMaxVelocity',
                 max_joint_acceleration='kMaxAcceleration', max_joint_jerk='kMaxJerk')
if (PROFILE.keys() != CPP_NAMES.keys() or
        any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in PROFILE.values()) or
        PROFILE['minimum_duration'] > PROFILE['maximum_duration']):
    raise ValueError('invalid Home trajectory configuration')


def plan_duration(delta):
    """Quintic bounds: peak slope 1.875, acceleration 10/sqrt(3), jerk 60."""
    if not math.isfinite(delta) or delta < 0:
        raise ValueError('invalid Home joint displacement')
    duration = max(PROFILE['minimum_duration'],
                   1.875 * delta / PROFILE['max_joint_velocity'],
                   math.sqrt((10 / math.sqrt(3)) * delta / PROFILE['max_joint_acceleration']),
                   (60 * delta / PROFILE['max_joint_jerk']) ** (1 / 3))
    if duration > PROFILE['maximum_duration'] + 1e-12:
        raise ValueError('joint Home path exceeds 0.2 rad/s within 12 s; move closer to the default pose first')
    return min(duration, PROFILE['maximum_duration'])


def cpp_header():
    return ('#pragma once\nnamespace hil_serl_home {\n' +
            ''.join('constexpr double %s = %.17g;\n' % (CPP_NAMES[key], value)
                    for key, value in PROFILE.items()) + '}\n')
