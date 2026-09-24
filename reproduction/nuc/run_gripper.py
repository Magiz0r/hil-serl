#!/usr/bin/env python3
"""NUC wrapper for one isolated, ephemeral RS485 container, with no network."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path('/home/tasl/hil_serl_runtime_20260918')
IMAGE = 'sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151'
NAME = 'hil-serl-robotiq-attended-20260921'
DEVICE = Path('/dev/serial/by-id/usb-FTDI_USB_TO_RS-485_DAAQM50H-if00-port0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--probe-only', action='store_true')
    modes.add_argument('--execute-attended-gripper', action='store_true')
    parser.add_argument('--activate-if-needed', action='store_true')
    parser.add_argument('--speed', type=int, choices=(64, 128, 192, 255), default=64)
    args = parser.parse_args()
    if args.probe_only and args.activate_if_needed:
        parser.error('probe cannot activate')
    source = ROOT / 'gripper_source'
    expected = json.loads((ROOT / 'gripper-source-sha256.json').read_text())
    for name, digest in expected.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
    existing = subprocess.run(['docker', 'inspect', NAME], capture_output=True, timeout=5)
    if existing.returncode == 0:
        raise ValueError('dedicated gripper container already exists; do not replace it')
    device = DEVICE.resolve(strict=True)
    if device.parent != Path('/dev') or not device.name.startswith('ttyUSB'):
        raise ValueError('unexpected RS485 device')
    holders = subprocess.run(['fuser', str(device)], capture_output=True, text=True, timeout=5)
    if holders.returncode == 0:
        raise ValueError('RS485 device is already in use: ' + holders.stdout.strip())
    os.umask(0o007)
    run = ROOT / 'gripper_state' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-%f')
    run.mkdir(parents=True)
    cidfile = run / 'container.cid'

    def interrupt(signum, frame):
        raise KeyboardInterrupt('signal %s' % signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupt)
    command = [
        'docker', 'run', '--rm', '--init', '-i', '--name', NAME, '--cidfile', str(cidfile),
        '--label', 'org.hil-serl.purpose=attended-rs485', '--network', 'none',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--user', '%s:%s' % (os.getuid(), os.getgid()), '--group-add', str(device.stat().st_gid),
        '--device', str(device) + ':/dev/robotiq:rw',
        '--mount', 'type=bind,src=%s,dst=/opt/hil-serl-gripper,readonly' % source,
        '--mount', 'type=bind,src=%s,dst=/hil-serl-gripper-state' % run,
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=16777216,mode=1777',
        '-e', 'PYTHONPATH=/opt/hil-serl-gripper:/opt/hil-serl-python',
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'HOME=/hil-serl-gripper-state',
        IMAGE, 'python3', '-u', '/opt/hil-serl-gripper/attended_gripper.py',
        '--probe-only' if args.probe_only else '--execute-attended-gripper',
        '--speed', str(args.speed),
        *(['--activate-if-needed'] if args.activate_if_needed else []),
    ]
    code = 1
    try:
        code = subprocess.run(command).returncode
    finally:
        if cidfile.exists():
            cid = cidfile.read_text().strip()
            inspected = subprocess.run(['docker', 'inspect', cid], capture_output=True, text=True, timeout=5)
            if inspected.returncode == 0:
                item = json.loads(inspected.stdout)[0]
                assert item['Name'] == '/' + NAME and item['Image'] == IMAGE
                subprocess.run(['docker', 'stop', '--timeout', '5', cid], check=True,
                               stdout=sys.stderr, timeout=10)
        print(json.dumps({'phase': 'container_stopped', 'name': NAME, 'run_directory': str(run)}), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
