from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import ssl
import subprocess
import urllib.request

import base64
import secrets
import struct

ROOT = Path('/home/tasl/hil_serl_runtime_20260918')
CONTAINER = '8488aa9d251391c4366de4690fe2230313adca56e701621117ce0528ffff142b'
IMAGE = 'sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151'


def run(*args):
    return subprocess.check_output(args, text=True, timeout=8)


def inspect(name):
    return json.loads(run('docker', 'inspect', name))[0]


def read_desk_status():
    # Read one public status message, with no token acquisition or application writes.
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    context = ssl._create_unverified_context()  # The local robot serves a self-signed certificate.
    with socket.create_connection(('172.16.0.1', 443), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname='172.16.0.1') as connection:
            request = ('GET /admin/api/system-status HTTP/1.1\r\nHost: 172.16.0.1\r\n'
                       'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                       'Sec-WebSocket-Key: '+key+'\r\nOrigin: https://172.16.0.1\r\n\r\n')
            connection.sendall(request.encode())
            stream = connection.makefile('rb')
            status = stream.readline(4096)
            if b' 101 ' not in status:
                raise ValueError('Desk WebSocket handshake failed')
            headers = {}
            for _ in range(100):
                line = stream.readline(4096)
                if line == b'\r\n':
                    break
                name, value = line.decode().split(':', 1)
                headers[name.lower()] = value.strip()
            expected = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if headers.get('sec-websocket-accept') != expected:
                raise ValueError('Desk WebSocket handshake mismatch')
            payload = b''
            for _ in range(64):
                head = stream.read(2)
                if len(head) != 2:
                    raise ValueError('Desk connection closed')
                first, second = head
                size = second & 127
                if size == 126: size = struct.unpack('!H', stream.read(2))[0]
                elif size == 127: size = struct.unpack('!Q', stream.read(8))[0]
                if size > 2000000 or second & 128:
                    raise ValueError('invalid Desk status frame')
                body = stream.read(size)
                if len(body) != size: raise ValueError('truncated Desk status')
                opcode = first & 15
                if opcode == 8: raise ValueError('Desk closed before status')
                if opcode not in (0, 1): continue
                payload += body
                if len(payload) > 2000000: raise ValueError('Desk status too large')
                if first & 128: return json.loads(payload)
            raise ValueError('Desk did not send a complete status')


def main():
    others = []
    for name in ('remote-teleop-ros2', 'remote-teleop-serl-soft', 'rlinf-explore'):
        item = inspect(name)
        others.append(dict(name=name, running=item['State']['Running'],
                           pid=item['State']['Pid'], started=item['State']['StartedAt'],
                           restarts=item['RestartCount'],
                           processes=run('docker', 'top', name, '-eo', 'pid,comm').splitlines()))
    item = inspect(CONTAINER)
    host = item['HostConfig']
    own = dict(name=item['Name'], image=item['Image'], running=item['State']['Running'],
               pid=item['State']['Pid'], readonly=host['ReadonlyRootfs'],
               privileged=host['Privileged'], devices=host['Devices'],
               restart=host['RestartPolicy'], mounts=item['Mounts'])
    assert item['Name'] == '/hil-serl-fr3-hold-20260918' and item['Image'] == IMAGE
    assert not own['running'] and own['pid'] == 0 and own['readonly']
    assert not own['privileged'] and not own['devices'] and own['restart']['Name'] == 'no'
    for mount in item['Mounts']:
        if mount['Type'] == 'bind':
            assert Path(mount['Source']).is_relative_to(ROOT)
            assert (mount['Source'], mount['RW']) in (
                (str(ROOT / 'source'), False), (str(ROOT / 'state'), True))
    expected = json.loads((ROOT / 'source-sha256.json').read_text())
    actual = {name: hashlib.sha256((ROOT / 'source' / name).read_bytes()).hexdigest()
              for name in expected}
    assert expected == actual, 'NUC deployment hashes changed'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 11321))
    connections = [line for line in run('ss', '-tnp', 'state', 'established').splitlines()
                   if '172.16.0.1:' in line]
    assert not connections, 'Existing robot connection'
    def get(path):
        with urllib.request.urlopen('http://127.0.0.1:4243/' + path, timeout=3) as response:
            return json.load(response)
    sidecar = dict(ping=get('ping'), state=get('state'))
    assert sidecar['ping']['in_freedrive'] is False
    assert sidecar['state'].get('msg') == 'no robot object'
    state = read_desk_status()
    safety = state['safety']
    token = state['controlToken']
    desk = dict(time=datetime.now(timezone.utc).isoformat(),
                mode=state['derived']['operatingMode'],
                execution_running=state['execution']['running'],
                execution_error=state['execution']['error'],
                robot_errors=state['robot']['robotErrors'],
                safety_status=safety['safetyControllerStatus'],
                recovery=safety['activeRecovery'],
                brakes=safety['brakeState'], sto=safety['stoState'],
                robot_power=safety['powerState']['robot'],
                fci_enabled=token['fciActive'],
                owner=(token.get('activeToken') or {}).get('ownedBy'),
                request_pending=token.get('tokenRequest') is not None)
    print(json.dumps(dict(other_containers=others, own_container=own,
                          source_sha256=actual, port_11321='free',
                          robot_connections=connections, sidecar=sidecar, desk=desk)), flush=True)
    assert desk['mode'] == 'Execution' and not desk['execution_running']
    assert not desk['execution_error'] and not desk['robot_errors']
    assert desk['safety_status'] == 'Work' and desk['recovery'] is None
    assert desk['robot_power'] == 'On' and desk['sto'] == 'SafeTorqueOn'
    assert all(value == 'Unlocked' for value in desk['brakes'])
    assert desk['fci_enabled'] and desk['owner'] == 'tasl' and not desk['request_pending']
    print('READ_ONLY_PREFLIGHT_OK', flush=True)


if __name__ == "__main__":
    main()
