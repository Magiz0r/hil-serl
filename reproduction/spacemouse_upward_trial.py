#!/usr/bin/env python3
"""Attended SpaceMouse input for the isolated FR3 diagnostic controller.

Select upward-only or manual six-axis control explicitly. --official-input
keeps upstream six-axis mapping and nonzero-axis intervention without a held
button, with RAM measured-pose action increments at 10 Hz. Both buttons together
exit and gripper control remains disabled. This is not the full training environment.
"""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def read_latest_input(device, previous_stamp, official, max_reports=256):
    """Drain a nonblocking HID queue and preserve brief stop-button presses.

    Wireless BT was measured at 60–63 reports/s, faster than this bridge's
    50 Hz send loop. Reading one report per send queues stale human commands.
    """
    reports, stop_seen = 0, False
    for _ in range(max_reports):
        state = device.read()
        if state is None:
            raise ValueError("SpaceMouse disconnected")
        left, right = bool(state.buttons[0]), bool(state.buttons[1])
        stop_seen |= right and (left or not official)
        if state.t == previous_stamp:
            return state, reports, stop_seen
        previous_stamp = state.t
        reports += 1
    raise ValueError("SpaceMouse report queue did not drain")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-attended-upward-trial", action="store_true")
    mode.add_argument("--execute-attended-manual", action="store_true")
    parser.add_argument("--no-trial-bounds", action="store_true")
    parser.add_argument("--official-input", action="store_true")
    args = parser.parse_args()
    manual, free = args.execute_attended_manual, args.no_trial_bounds
    official = args.official_input
    if free and not manual:
        parser.error("--no-trial-bounds requires manual mode")
    if official and not (manual and free):
        parser.error("--official-input requires manual mode without trial bounds")
    action_flag = "--execute-attended-manual" if manual else "--execute-attended-upward-trial"
    from easyhid import Enumeration
    from franka_env.spacemouse import pyspacemouse

    matching = {d.path: d for d in Enumeration().find()
                if (d.vendor_id, d.product_id) == (0x256F, 0xC63A)}
    if len(matching) != 1:
        raise ValueError("expected one Wireless BT SpaceMouse")
    path = os.fsdecode(next(iter(matching)))
    if not os.access(path, os.R_OK | os.W_OK):
        raise PermissionError(path)
    opened = pyspacemouse.open(device="SpaceMouse Wireless BT", set_nonblocking_loop=True)
    devices = opened if isinstance(opened, list) else ([opened] if opened else [])
    if len(devices) != 1:
        for device in devices:
            device.close()
        raise ValueError("SpaceMouse did not open exactly once")

    logdir = Path(__file__).parent / "logs" / (
        ("spacemouse-manual-" if manual else "spacemouse-upward-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    logdir.mkdir()
    process = None
    shared, lock = {}, threading.Lock()
    try:
        with (logdir / "ssh.stderr.log").open("x") as stderr, (logdir / "events.jsonl").open("x") as events, (logdir / "input.jsonl").open("x") as inputs:
            process = subprocess.Popen([
                "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-o", "StrictHostKeyChecking=yes", "-o", "ServerAliveInterval=3",
                "-o", "ServerAliveCountMax=2", "FrankaNUC", "python3",
                "/home/tasl/hil_serl_runtime_20260918/source/run_upward_trial.py",
                action_flag, *(["--no-trial-bounds"] if free else []),
                *(["--official-input"] if official else []),
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)

            def receive():
                try:
                    for line in process.stdout:
                        events.write(line)
                        events.flush()
                        message = json.loads(line)
                        with lock:
                            shared["message"] = message
                            shared["received_at"] = time.monotonic()
                    with lock:
                        shared["eof"] = True
                except Exception as error:
                    with lock:
                        shared["error"] = str(error)

            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            last_stamp, last_hid, seq = None, time.monotonic(), 0
            operator_stop_latched = False
            last_phase, next_report = None, 0.0
            deadline = time.monotonic() + (710 if manual else 170)
            print("Preparing " + ("six-axis manual control" if manual else "upward-only trial") +
                  "; leave the cap and both buttons released.", flush=True)
            while (free or time.monotonic() < deadline) and process.poll() is None:
                now = time.monotonic()
                with lock:
                    data = dict(shared)
                if data.get("error"):
                    raise ValueError(data["error"])
                message = data.get("message")
                if message:
                    phase = message.get("phase")
                    if phase != last_phase:
                        fields = ("phase", "error", "run_directory", "armed", "target_up_mm",
                                  "actual_up_mm", "target_limit_mm", "target_speed_mm_s",
                                  "compliance", "workspace_radius_mm", "orientation_radius_deg",
                                  "angular_speed_deg_s", "session_seconds", "idle_seconds", "trial_bounds", "input_mode",
                                  "response_profile", "action_hz", "position_action_scale_mm", "rotation_action_scale_rad")
                        print(json.dumps({k: message[k] for k in fields if k in message}), flush=True)
                        if phase == "ready":
                            if official:
                                print("READY: center first, then move the cap; no button needed. "
                                      "Translate the cap for translation, tilt/twist for rotation. "
                                      "Responsive measured-pose increments; release to hold. "
                                      "Both buttons together EXIT. Gripper disabled; no trial workspace or timer.", flush=True)
                            else:
                                print("READY: center/release first, hold LEFT, then " +
                                  ("move/tilt the cap. Six axes, no trial workspace or timer. "
                                   "10 mm/s / 5 deg/s. " if free else
                                   "move/tilt the cap. Six axes, 50 mm / 10 degrees, 10 mm/s / 5 deg/s. "
                                   "Session up to 10 minutes; 2 minutes without LEFT ends it. " if manual else
                                   "gently lift. Up to 60 s to begin; 30 s starts on first enable. ") +
                                  "RIGHT ends.", flush=True)
                        last_phase = phase
                    if phase in ("stopping", "stopped", "container_stopped"):
                        break
                    if phase == "ready" and now >= next_report:
                        print(json.dumps(message), flush=True)
                        next_report = now + (3 if manual else 1)
                    if now - data["received_at"] > 4:
                        raise ValueError("NUC telemetry stale")
                state, reports, stop_seen = read_latest_input(devices[0], last_stamp, official)
                operator_stop_latched |= stop_seen
                if not os.path.exists(path):
                    raise ValueError("SpaceMouse disconnected")
                if state.t != last_stamp:
                    last_stamp, last_hid = state.t, now
                axes = [-state.y, state.x, state.z]
                if manual:
                    axes += [-state.roll, -state.pitch, -state.yaw]
                if not all(math.isfinite(v) and abs(v) <= 1 for v in axes):
                    raise ValueError("invalid SpaceMouse input")
                enabled, stopped = bool(state.buttons[0]), operator_stop_latched
                moving = (math.sqrt(sum(v * v for v in axes)) > 0.001 if official
                          else enabled and max(abs(v) for v in axes) > 0.15)
                if moving and now - last_hid > 0.25:
                    raise ValueError("HID input stopped updating while moving")
                if message and "server_time" in message:
                    seq += 1
                    packet = dict(seq=seq, server_time=message["server_time"], axes=axes,
                                  enable=enabled, stop=stopped)
                    inputs.write(json.dumps(dict(pc_time=time.monotonic(), hid_stamp=state.t,
                        drained_reports=reports, buttons=list(state.buttons), **packet)) + "\n")
                    inputs.flush()
                    process.stdin.write(json.dumps(packet) + "\n")
                    process.stdin.flush()
                time.sleep(0.02)
            else:
                if process.poll() is None:
                    raise TimeoutError("trial deadline")
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            code = process.wait(timeout=25)
            thread.join(timeout=1)
            print("Trial ended; logs: " + str(logdir), flush=True)
            return code
    finally:
        devices[0].close()
        if process is not None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                # Host wrapper also has its own deadline and container cleanup.
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
