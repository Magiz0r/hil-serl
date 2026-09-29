"""Preserve transient feedback faults and the timing immediately before them."""
from collections import deque
import json
import time


class HealthJournal:
    def __init__(self, path):
        self.path = path
        self.history = deque(maxlen=150)
        self.ever_ready = False
        self.last_problem = None
        self.last_fault = None
        self.recovered_unix = None
        self.write_error = None

    def observe(self, snapshot, now, problem, *, loop_gap, snapshot_seconds):
        arm = snapshot.get('arm') or {}
        data = arm.get('data') or {}
        sample = data.get('sample') or {}
        state = sample.get('state') or {}
        pilot = sample.get('pilot') or {}
        ages = {name: now-value['pc_received_at'] if value else None
                for name in ('arm', 'control', 'gripper') for value in [snapshot.get(name)]}
        cameras = {name: now-frame['pc_captured_at'] if frame else None
                   for name, frame in snapshot.get('cameras', {}).items()}
        source_age = (data['source_read_at']-state['received_at']
                      if 'source_read_at' in data and 'received_at' in state else None)
        timing = dict(pc_monotonic=now, ages=ages, camera_ages=cameras, source_age=source_age,
                      arm_timing=arm.get('timing'), loop_gap=loop_gap,
                      snapshot_seconds=snapshot_seconds, pilot_mode=pilot.get('mode'),
                      command=pilot.get('command'), command_id=pilot.get('command_id'))
        self.history.append(timing)
        event = None
        if problem and self.ever_ready and problem != self.last_problem:
            self.last_fault = dict(time_unix=time.time(), reason=problem, **timing)
            self.recovered_unix = None
            event = dict(kind='feedback_fault', **self.last_fault, preceding=list(self.history))
        elif not problem:
            if self.ever_ready and self.last_problem:
                self.recovered_unix = time.time()
                event = dict(kind='feedback_recovered', time_unix=self.recovered_unix, **timing)
            self.ever_ready = True
        self.last_problem = problem
        if event:
            try:
                with self.path.open('a') as stream:
                    stream.write(json.dumps(event, allow_nan=False) + '\n')
            except (OSError, ValueError) as error:
                # Stop was already requested. Diagnostics must not hide it.
                self.write_error = str(error)

    def status(self):
        return dict(last_fault=self.last_fault, recovered_unix=self.recovered_unix,
                    journal_error=self.write_error)
