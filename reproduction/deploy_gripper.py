#!/usr/bin/env python3
"""Deploy only the dedicated gripper sources; does not start containers or hardware."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--deploy', action='store_true', required=True)
parser.parse_args()
repository = Path(__file__).resolve().parents[1]

files = {
    'run_gripper.py': 'reproduction/nuc/run_gripper.py',
    'attended_gripper.py': 'reproduction/nuc/attended_gripper.py',
    'gripper_input.py': 'reproduction/nuc/gripper_input.py',
    'robot_servers/robotiq_rs485_gripper_server.py': 'serl_robot_infra/robot_servers/robotiq_rs485_gripper_server.py',
    'robot_servers/gripper_server.py': 'serl_robot_infra/robot_servers/gripper_server.py',
}
data = {name: (repository / path).read_bytes() for name, path in files.items()}
data['robot_servers/__init__.py'] = b''
expected = {name: hashlib.sha256(content).hexdigest() for name, content in data.items()}
payload = {name: base64.b64encode(content).decode() for name, content in data.items()}
script = 'PAYLOAD = ' + repr(payload) + '\nEXPECTED = ' + repr(expected) + '\n' + r'''
import base64, hashlib, json, os, subprocess
from pathlib import Path
root = Path('/home/tasl/hil_serl_runtime_20260918')
assert root.is_dir() and not root.is_symlink()
assert subprocess.run(['docker', 'inspect', 'hil-serl-robotiq-attended-20260921'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
source = root / 'gripper_source'
source.mkdir(mode=0o750, exist_ok=True)
assert source.resolve() == source
for name, encoded in PAYLOAD.items():
    path = source / name
    assert path.resolve().is_relative_to(source)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    content = base64.b64decode(encoded)
    assert hashlib.sha256(content).hexdigest() == EXPECTED[name]
    temp = path.with_suffix(path.suffix + '.new')
    temp.write_bytes(content)
    temp.chmod(0o640)
    temp.replace(path)
manifest = root / 'gripper-source-sha256.json'
temp = manifest.with_suffix('.json.new')
temp.write_text(json.dumps(EXPECTED, indent=2) + '\n')
temp.chmod(0o640)
temp.replace(manifest)
print(json.dumps(dict(deployed_files=len(EXPECTED), root=str(source), source_sha256=EXPECTED)))
'''
result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                         '-o', 'StrictHostKeyChecking=yes', 'FrankaNUC', 'python3', '-'],
                        input=script, text=True, capture_output=True, timeout=20)
log = repository / 'reproduction/logs/gripper-deployment.json'
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(result.stdout)
print(result.stdout, end='')
if result.returncode:
    print(result.stderr)
    raise SystemExit(result.returncode)
