"""Independent SSH gripper I/O; the arm input loop never waits on Modbus."""

import json
import subprocess
import threading
import time


class GripperBridge:
    def __init__(self, logdir, activate=False, speed=64):
        if type(speed) is not int or speed not in (64, 128, 192, 255):
            raise ValueError('invalid gripper speed')
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.closed = False
        self.message = {}
        self.received = None
        self.refreshed = time.monotonic()
        self.command_id = 0
        self.action = None
        self.preparation = None
        self.stop = False
        self.error = None
        self.events = (logdir / 'gripper-events.jsonl').open('x')
        self.stderr = (logdir / 'gripper-ssh.stderr.log').open('x')
        try:
            self.process = subprocess.Popen([
                'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'ServerAliveInterval=3',
                '-o', 'ServerAliveCountMax=2', 'FrankaNUC', 'python3',
                '/home/tasl/hil_serl_runtime_20260918/gripper_source/run_gripper.py',
                '--execute-attended-gripper', *(['--activate-if-needed'] if activate else []),
                '--speed', str(speed),
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
               text=True, bufsize=1)
        except Exception:
            self.events.close()
            self.stderr.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.reader.start()
        self.writer.start()

    def _fail(self, error):
        with self.lock:
            self.error = self.error or str(error)

    def _read(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                message['preparation_supported'] = True
                with self.lock:
                    if self.preparation and message.get('command_id') == self.preparation['command_id']:
                        message['preparation'] = dict(self.preparation)
                    self.message = message
                    self.received = time.monotonic()
                    if message['phase'] in ('stopped', 'container_stopped') and not (self.closed or self.stop):
                        self.error = message.get('error', 'gripper process stopped')
                self.events.write(json.dumps(message) + '\n')
                self.events.flush()
            if not self.closed:
                self._fail('gripper connection closed')
        except Exception as error:
            self._fail(error)

    def _write(self):
        seq = 0
        try:
            while True:
                with self.lock:
                    now = time.monotonic()
                    stale = now - self.refreshed > 0.5
                    telemetry_stale = self.received is not None and now - self.received > 1
                    stop = self.stop or self.done.is_set() or stale or telemetry_stale or bool(self.error)
                    server_time = self.message.get('server_time')
                    packet = dict(seq=seq, server_time=server_time, command_id=self.command_id,
                                  action=self.action, stop=stop)
                if server_time is not None:
                    seq += 1
                    packet['seq'] = seq
                    self.process.stdin.write(json.dumps(packet) + '\n')
                    self.process.stdin.flush()
                if stop:
                    if stale or telemetry_stale:
                        self._fail('gripper bridge heartbeat stale')
                    break
                self.done.wait(0.05)
        except Exception as error:
            self._fail(error)
        finally:
            try:
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def refresh(self, action=None, stop=False, preparation_id=None):
        with self.lock:
            self.refreshed = time.monotonic()
            if self.error:
                raise RuntimeError('gripper: ' + self.error)
            if action is not None:
                if action not in ('open', 'close', 'hold'):
                    raise ValueError('invalid gripper action')
                self.command_id += 1
                self.action = action
                self.preparation = (dict(id=preparation_id, command_id=self.command_id, action=action)
                                    if preparation_id is not None else None)
            self.stop |= stop
            return dict(self.message)

    def wait_ready(self):
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            message = self.refresh()
            if message.get('phase') == 'ready':
                return message
            time.sleep(0.02)
        raise TimeoutError('gripper did not become ready')

    def close(self):
        if self.closed:
            return
        with self.lock:
            self.closed = True
            self.stop = True
        self.done.set()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.writer.join(timeout=1)
        self.reader.join(timeout=1)
        if not self.reader.is_alive():
            self.process.stdout.close()
        self.events.close()
        self.stderr.close()
