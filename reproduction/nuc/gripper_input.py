"""Validate gripper heartbeat packets without hardware or ROS dependencies."""

import math

MAX_AGE = 0.5


class GripperInput:
    def __init__(self):
        self.seq = -1
        self.command_id = 0
        self.action = None

    def accept(self, packet, now):
        if set(packet) != {'seq', 'server_time', 'command_id', 'action', 'stop'}:
            raise ValueError('unexpected gripper input fields')
        if type(packet['stop']) is not bool:
            raise ValueError('invalid gripper stop')
        if packet['stop']:
            raise ValueError('operator stop')
        seq, command_id = packet['seq'], packet['command_id']
        if type(seq) is not int or seq <= self.seq:
            raise ValueError('unordered gripper input')
        if type(command_id) is not int or command_id < self.command_id:
            raise ValueError('unordered gripper command')
        age = now - float(packet['server_time'])
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
            raise ValueError('gripper heartbeat stale')
        action = packet['action']
        if action not in (None, 'open', 'close', 'hold'):
            raise ValueError('invalid gripper action')
        if (command_id == 0) != (action is None):
            raise ValueError('gripper action/id mismatch')
        if command_id == self.command_id and action != self.action:
            raise ValueError('gripper command changed without new id')
        new_action = action if command_id > self.command_id else None
        self.seq, self.command_id, self.action = seq, command_id, action
        return new_action
