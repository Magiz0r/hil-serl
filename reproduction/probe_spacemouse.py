#!/usr/bin/env python3
"""Read the single Wireless BT SpaceMouse only; no robot or gripper imports."""

import argparse
import json
import math
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 60:
        parser.error("--seconds must be in (0, 60]")

    from easyhid import Enumeration
    from franka_env.spacemouse import pyspacemouse

    matching = list({
        d.path: d for d in Enumeration().find()
        if (d.vendor_id, d.product_id) == (0x256F, 0xC63A)
    }.values())
    if len(matching) != 1:
        raise RuntimeError("expected exactly one 256f:c63a SpaceMouse, found %d" % len(matching))
    path = os.fsdecode(matching[0].path)
    if not os.access(path, os.R_OK | os.W_OK):
        raise PermissionError("SpaceMouse needs this user's read/write HID access: " + path)

    opened = pyspacemouse.open(device="SpaceMouse Wireless BT", set_nonblocking_loop=True)
    devices = opened if isinstance(opened, list) else ([opened] if opened else [])
    if not devices or len(devices) != 1:
        for device in devices or []:
            device.close()
        raise RuntimeError("expected exactly one opened SpaceMouse")
    print(json.dumps({"device": path, "seconds": args.seconds, "robot_commands": False}), flush=True)
    peak = [0.0] * 6
    buttons_seen = [False, False]
    changed_states = 0
    last_stamp = -1
    deadline = time.monotonic() + args.seconds
    next_output = 0.0
    try:
        while time.monotonic() < deadline:
            state = devices[0].read()
            action = [-state.y, state.x, state.z, -state.roll, -state.pitch, -state.yaw]
            if not all(math.isfinite(value) for value in action):
                raise ValueError("non-finite SpaceMouse state")
            peak = [max(a, abs(b)) for a, b in zip(peak, action)]
            buttons_seen = [a or bool(b) for a, b in zip(buttons_seen, state.buttons)]
            if state.t != last_stamp:
                changed_states += 1
                last_stamp = state.t
            if time.monotonic() >= next_output:
                print(json.dumps({"action": action, "buttons": list(state.buttons)}), flush=True)
                next_output = time.monotonic() + 0.5
            time.sleep(0.005)
    finally:
        devices[0].close()
    print(json.dumps({
        "changed_states": changed_states,
        "action_peak_abs": peak,
        "buttons_seen_pressed": buttons_seen,
        "robot_commands": False,
    }), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
