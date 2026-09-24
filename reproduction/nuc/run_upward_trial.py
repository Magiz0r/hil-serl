#!/usr/bin/env python3
"""NUC host wrapper; always stop only the dedicated HIL-SERL container."""

import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys

CONTAINER = "8488aa9d251391c4366de4690fe2230313adca56e701621117ce0528ffff142b"
IMAGE = "sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151"
ROOT = Path("/home/tasl/hil_serl_runtime_20260918")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-attended-upward-trial", action="store_true")
    mode.add_argument("--execute-attended-manual", action="store_true")
    parser.add_argument("--no-trial-bounds", action="store_true")
    parser.add_argument("--official-input", action="store_true")
    parser.add_argument("--pilot-gate", action="store_true")
    parser.add_argument("--official-home", action="store_true")
    parser.add_argument("--home-switch-probe", action="store_true")
    parser.add_argument("--speed-scale", type=float, choices=(1., 1.5, 2.), default=1.)
    parser.add_argument("--translation-scale", type=float, choices=(1., 1.5, 1.6, 2.))
    parser.add_argument("--rotation-scale", type=float, choices=(1., 1.5, 2., 3.))
    parser.add_argument("--rotation-response", choices=("standard", "fast", "responsive"), default="standard")
    args = parser.parse_args()
    manual, free = args.execute_attended_manual, args.no_trial_bounds
    if free and not manual:
        parser.error("--no-trial-bounds requires manual mode")
    if args.official_input and not (manual and free):
        parser.error("--official-input requires manual mode without trial bounds")
    if args.official_home and not args.pilot_gate:
        parser.error("--official-home requires --pilot-gate")
    if args.home_switch_probe and not args.official_home:
        parser.error("--home-switch-probe requires --official-home")
    if args.pilot_gate and not args.official_input:
        parser.error("--pilot-gate requires --official-input")
    if args.speed_scale != 1. and not args.official_input:
        parser.error("--speed-scale requires --official-input")
    if args.translation_scale is not None and not args.official_input:
        parser.error("--translation-scale requires --official-input")
    if args.rotation_scale is not None and not args.official_input:
        parser.error("--rotation-scale requires --official-input")
    if args.rotation_response != "standard" and not args.official_input:
        parser.error("--rotation-response requires --official-input")
    action_flag = "--execute-attended-manual" if manual else "--execute-attended-upward-trial"
    if free:
        action_flag += " --no-trial-bounds"
    if args.official_input:
        action_flag += " --official-input"
    if args.official_home:
        action_flag += " --official-home"
    if args.home_switch_probe:
        action_flag += " --home-switch-probe"
    if args.pilot_gate:
        action_flag += " --pilot-gate"
    action_flag += " --speed-scale " + str(args.speed_scale)
    if args.translation_scale is not None:
        action_flag += " --translation-scale " + str(args.translation_scale)
    if args.rotation_scale is not None:
        action_flag += " --rotation-scale " + str(args.rotation_scale)
    action_flag += " --rotation-response " + args.rotation_response
    item = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER], text=True, timeout=5))[0]
    assert item["Name"] == "/hil-serl-fr3-hold-20260918" and item["Image"] == IMAGE
    assert not item["State"]["Running"] and item["HostConfig"]["ReadonlyRootfs"]
    expected = json.loads((ROOT / "source-sha256.json").read_text())
    for name, digest in expected.items():
        assert hashlib.sha256((ROOT / "source" / name).read_bytes()).hexdigest() == digest, name
    connections = subprocess.check_output(["ss", "-tnp", "state", "established"], text=True)
    assert "172.16.0.1:" not in connections, "existing robot connection"

    def interrupted(signum, frame):
        raise KeyboardInterrupt("signal %s" % signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    exit_code = 1
    try:
        subprocess.run(["docker", "start", CONTAINER], check=True, stdout=sys.stderr, timeout=10)
        result = subprocess.run([
            "docker", "exec", "-i", CONTAINER, "bash", "-c",
            "source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash && "
            "python3 /opt/hil-serl-preflight/upward_trial.py " + action_flag,
        ], timeout=None if free else (690 if manual else 150))
        exit_code = result.returncode
    finally:
        stopped = subprocess.run(["docker", "stop", "--timeout", "15", CONTAINER],
                                 capture_output=True, text=True, timeout=20)
        item = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER], text=True, timeout=5))[0]
        print(json.dumps({"phase": "container_stopped", "running": item["State"]["Running"],
                          "stop_exit_code": stopped.returncode}), flush=True)
        if stopped.returncode or item["State"]["Running"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
