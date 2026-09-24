"""Local recorder-to-input gate. Socket is owned by this manual session only."""
import fcntl
import json
import math
import os
from pathlib import Path
import socket
import time
from pilot_gripper import PREPARE_ACTIONS

ACTIONS = ('lock', 'start', 'set_home', 'home', 'set_custom_home', 'custom_home', *PREPARE_ACTIONS)


class PilotSocket:
    def __init__(self, directory):
        self.path = Path(directory) / 'pilot-control.sock'
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.socket.setblocking(False)
        self.latest = dict(id=0, action='lock', stamp=0.)
        self.received = False

    def poll(self):
        for _ in range(256):
            try:
                raw = self.socket.recv(1025)
            except BlockingIOError:
                break
            packet = json.loads(raw)
            if len(raw) > 1024 or set(packet) != {'id', 'action', 'stamp'}:
                raise ValueError('invalid pilot control packet')
            if type(packet['id']) is not int or packet['id'] < self.latest['id']:
                raise ValueError('unordered pilot control command')
            if packet['action'] not in ACTIONS or not math.isfinite(packet['stamp']):
                raise ValueError('invalid pilot control command')
            if self.received and packet['id'] == self.latest['id'] and packet['action'] != self.latest['action']:
                raise ValueError('pilot command changed without a new id')
            self.latest, self.received = packet, True
        else:
            raise ValueError('pilot control queue did not drain')
        connected = self.received and 0 <= time.monotonic() - self.latest['stamp'] <= .4
        return dict(id=self.latest['id'], action=self.latest['action'], connected=connected)

    def close(self):
        self.socket.close()
        self.path.unlink(missing_ok=True)


class PilotClient:
    def __init__(self, directory, initial_id=0):
        self.claim = (Path(directory) / 'pilot-recorder.lock').open('a')
        try:
            fcntl.flock(self.claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.claim.close()
            raise RuntimeError('another recorder already owns this manual session')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.socket.connect(str(Path(directory) / 'pilot-control.sock'))
            self.socket.setblocking(False)
        except Exception:
            self.socket.close()
            self.claim.close()
            raise
        self.id, self.action = max(0, initial_id) + 1, 'lock'

    def issue(self, action):
        if action not in ACTIONS:
            raise ValueError('invalid pilot command')
        self.id += 1
        self.action = action
        self.refresh()
        return self.id

    def refresh(self):
        self.socket.send(json.dumps(dict(id=self.id, action=self.action, stamp=time.monotonic())).encode())

    def close(self):
        try:
            self.issue('lock')
        except OSError:
            pass
        self.socket.close()
        self.claim.close()
