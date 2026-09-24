#!/usr/bin/env python3
"""Dedicated RS485 process; no ROS, Franka connection, reset/recovery of the arm."""

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
import time

from gripper_input import GripperInput, MAX_AGE
from robot_servers.robotiq_rs485_gripper_server import RobotiqRS485GripperServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--probe-only', action='store_true')
    modes.add_argument('--execute-attended-gripper', action='store_true')
    parser.add_argument('--activate-if-needed', action='store_true')
    parser.add_argument('--speed', type=int, choices=(64, 128, 192, 255), default=64)
    args = parser.parse_args()
    if args.probe_only and args.activate_if_needed:
        parser.error('probe mode cannot activate')
    backend = None
    claimed = activating = False
    result, code = {}, 1
    state, lock = {}, threading.Lock()
    guard = GripperInput()
    started = time.monotonic()
    last_received_processed = None

    def interrupt(signum, frame):
        raise KeyboardInterrupt('signal %s' % signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)

    def receive():
        try:
            while True:
                line = sys.stdin.readline(2049)
                if not line:
                    raise ValueError('gripper input connection closed')
                if len(line) > 2048 or not line.endswith('\n'):
                    raise ValueError('invalid gripper input framing')
                packet = json.loads(line)
                with lock:
                    state['packet'] = packet
                    state['received_at'] = time.monotonic()
                    if packet.get('stop') is True:
                        state['error'] = 'operator stop'
        except Exception as error:
            with lock:
                state['error'] = str(error)

    def input_action():
        nonlocal last_received_processed
        now = time.monotonic()
        with lock:
            data = dict(state)
        if data.get('error'):
            raise ValueError(data['error'])
        if 'packet' not in data:
            if now - started > 2:
                raise ValueError('no gripper input heartbeat')
            return None
        if now - data['received_at'] > MAX_AGE:
            raise ValueError('gripper input stream stale')
        packet = data['packet']
        if data['received_at'] != last_received_processed:
            last_received_processed = data['received_at']
            return guard.accept(packet, now)
        return None

    with Path('/hil-serl-gripper-state/events.jsonl').open('x') as log:
        def emit(phase, **data):
            message = dict(phase=phase, server_time=time.monotonic(), **data)
            line = json.dumps(message, allow_nan=False)
            log.write(line + '\n')
            log.flush()
            print(line, flush=True)

        try:
            if not args.probe_only:
                threading.Thread(target=receive, daemon=True).start()
            emit('precheck')
            backend = RobotiqRS485GripperServer(device='/dev/robotiq', timeout=0.1)
            status = backend.last_status
            if args.probe_only:
                emit('read_only_status', status=status, control_writes=False)
                code = 0
            else:
                # A communication-timeout fault can clear with read traffic.
                deadline = time.monotonic() + 0.6
                while status['gFLT'] == 9 and time.monotonic() < deadline:
                    input_action()
                    emit('checking_communication', status=status)
                    time.sleep(0.05)
                    status = backend.read_status()
                if status['gFLT'] not in (0, 7):
                    raise ValueError('gripper fault: 0x%02x' % status['gFLT'])
                ready = status['gACT'] == 1 and status['gSTA'] == 3 and status['gFLT'] == 0
                if not ready:
                    if not args.activate_if_needed:
                        raise ValueError('gripper needs explicit activation permission')
                    if status['gACT'] != 0 or status['gSTA'] != 0:
                        raise ValueError('gripper is not reset; no automatic fault reset')
                    input_action()
                    emit('activating', status=status)
                    claimed = activating = True
                    status = backend.activate_gripper(speed=args.speed, force=30, go_to=False)
                    deadline = time.monotonic() + 12
                    while not (status['gACT'] == 1 and status['gSTA'] == 3 and status['gFLT'] == 0):
                        if input_action() is not None:
                            raise ValueError('gripper command before ready')
                        if time.monotonic() > deadline or status['gFLT'] not in (0, 5, 7):
                            raise ValueError('activation did not complete: %r' % status)
                        emit('activating', status=status)
                        time.sleep(0.05)
                        status = backend.read_status()
                    activating = False
                claimed = True
                while True:
                    action = input_action()
                    if action:
                        status = (backend.stop() if action == 'hold' else
                                  backend.move(0 if action == 'open' else 255, speed=args.speed, force=30))
                        emit('command', action=action, command_id=guard.command_id, status=status)
                    else:
                        status = backend.read_status()
                    if status['gFLT'] or status['gACT'] != 1 or status['gSTA'] != 3:
                        raise ValueError('gripper state changed: %r' % status)
                    emit('ready', status=status, command_id=guard.command_id, speed_raw=args.speed, force_raw=30)
                    time.sleep(0.1)
        except (Exception, KeyboardInterrupt) as error:
            result['error'] = str(error)
            if str(error) in ('operator stop', 'gripper input connection closed'):
                code = 0
        finally:
            if backend is not None:
                try:
                    if claimed:
                        # Calibration ignores rGTO; cancel only our own unfinished activation.
                        status = backend.reset_gripper() if activating else backend.stop()
                        result['stop_status'] = status
                        result['stop_confirmed'] = status['gGTO'] == 0 and (not activating or status['gACT'] == 0)
                        if not result['stop_confirmed']:
                            code = 1
                    backend.shutdown()
                except Exception as error:
                    result.update(stop_confirmed=False, cleanup_error=str(error))
                    code = 1
            result['exit_code'] = code
            Path('/hil-serl-gripper-state/result.json').write_text(json.dumps(result, indent=2) + '\n')
            try:
                emit('stopped', **result)
            except BrokenPipeError:
                pass
    return code


if __name__ == '__main__':
    sys.exit(main())
