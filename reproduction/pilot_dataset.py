"""Append-only pilot episodes. Raw observations, not an HIL-SERL replay export."""

import hashlib
import json
import math
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone


SCHEMA = 'hilserl_manual_pilot_v1'
CAMERAS = ('external', 'wrist')


def new_episode_name():
    return 'episode_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:8]


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
    temporary.replace(path)


def health_error(snapshot, now, allow_home_transition=False):
    """Compare clocks only within the same machine; transport delay is separate."""
    if not math.isfinite(now):
        return 'invalid local clock'
    for name, age_limit in (('arm', .5), ('gripper', .7), ('control', .5)):
        stream = snapshot.get(name)
        if not stream:
            return name + ': waiting for data'
        if not 0 <= now - stream['pc_received_at'] <= age_limit:
            return name + ': stale stream'
    arm = snapshot['arm']['data']
    state = arm['sample']['state']
    if not 0 <= arm['source_read_at'] - state['received_at'] <= .35:
        return 'arm: stale source state'
    pilot = arm['sample'].get('pilot', {})
    switching_home = (allow_home_transition and pilot.get('home_kind') == 'official_joint'
                      and pilot.get('mode') == 'homing'
                      and pilot.get('joint_phase') in ('starting','activating','stopping','resuming'))
    if state['mode'] not in ((1,2) if switching_home else (2,)) or state['current_errors'] or state['last_motion_errors']:
        return 'arm: mode or error changed'
    if (not math.isfinite(state['success']) or not 0 <= state['success'] <= 1
            or (state['success'] < .95 and not (switching_home and state['success'] == 0))):
        return 'arm: command success rate degraded'
    if not arm['sample']['armed'] or arm['sample']['input']['stop']:
        return 'arm: input is not armed'
    if snapshot['control']['data'].get('phase') != 'ready':
        return 'manual control stopped'
    gripper = snapshot['gripper']['data']
    if gripper.get('phase') != 'ready' or gripper['status']['gFLT']:
        return 'gripper is not ready'
    for name in CAMERAS:
        camera = snapshot.get('cameras', {}).get(name)
        if not camera or not 0 <= now - camera['pc_captured_at'] <= .35:
            return name + ': camera missing or stale'
        if not camera.get('jpeg'):
            return name + ': empty camera frame'
    return None


class Episode:
    def __init__(self, directory, metadata, now):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        for camera in CAMERAS:
            (self.directory / camera).mkdir()
        self.metadata = dict(metadata, schema=SCHEMA, status='recording',
                             started_pc_monotonic=now, created_unix=time.time())
        atomic_json(self.directory / 'episode.json', self.metadata)
        self.stream = (self.directory / 'samples.jsonl').open('x')
        self.count = 0
        self.last_time = None
        self.frames = {}
        self.closed = False

    def append(self, snapshot, now):
        if self.closed:
            raise ValueError('episode already closed')
        problem = health_error(snapshot, now)
        if problem:
            raise ValueError(problem)
        if self.last_time is not None and not 0 < now - self.last_time <= .35:
            raise ValueError('recording sample timing discontinuity')
        images = {}
        for camera in CAMERAS:
            frame = snapshot['cameras'][camera]
            key = (camera, frame['sequence'])
            if key not in self.frames:
                relative = camera + '/%08d.jpg' % frame['sequence']
                with (self.directory / relative).open('xb') as stream:
                    stream.write(frame['jpeg'])
                self.frames[key] = dict(path=relative,
                    sha256=hashlib.sha256(frame['jpeg']).hexdigest(),
                    sequence=frame['sequence'], pc_captured_at=frame['pc_captured_at'],
                    shape=frame['shape'], encoding='jpeg', decoded_color_order='BGR')
            images[camera] = self.frames[key]
        sample = dict(index=self.count, pc_sampled_at=now,
                      arm=snapshot['arm'], gripper=snapshot['gripper'],
                      control=snapshot['control'], cameras=images)
        self.stream.write(json.dumps(sample, allow_nan=False) + '\n')
        self.stream.flush()
        self.last_time = now
        self.count += 1

    def finish(self, outcome, now, reason=None):
        if self.closed:
            return self.metadata
        if outcome not in ('success', 'failure', 'discard', 'incomplete', 'unlabeled'):
            raise ValueError('unknown outcome')
        if outcome in ('success', 'failure', 'unlabeled') and self.count < 2:
            outcome, reason = 'incomplete', 'fewer than two samples'
        self.stream.close()
        self.closed = True
        self.metadata.update(status='complete' if outcome in ('success', 'failure') else outcome,
                             outcome=outcome, outcome_source='operator' if outcome in ('success', 'failure', 'discard') else 'recorder',
                             ended_pc_monotonic=now, sample_count=self.count,
                             unique_image_count=len(self.frames), reason=reason,
                             samples_sha256=hashlib.sha256((self.directory / 'samples.jsonl').read_bytes()).hexdigest())
        atomic_json(self.directory / 'episode.json', self.metadata)
        return self.metadata


def validate_episode(directory):
    """Validate labels, frame hashes and chronology without trusting pickle files."""
    directory = Path(directory).resolve()
    metadata = json.loads((directory / 'episode.json').read_text())
    assert metadata['schema'] == SCHEMA
    raw = (directory / 'samples.jsonl').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata['samples_sha256']
    samples = [json.loads(line) for line in raw.splitlines()]
    assert len(samples) == metadata['sample_count']
    previous = None
    checked = set()
    for index, sample in enumerate(samples):
        assert sample['index'] == index
        now = sample['pc_sampled_at']
        assert math.isfinite(now) and (previous is None or 0 < now - previous <= .35)
        previous = now
        assert set(sample['cameras']) == set(CAMERAS)
        for camera, frame in sample['cameras'].items():
            path = (directory / frame['path']).resolve()
            assert path.parent == directory / camera
            assert 0 <= now - frame['pc_captured_at'] <= .35
            if path not in checked:
                assert hashlib.sha256(path.read_bytes()).hexdigest() == frame['sha256']
                checked.add(path)
    if metadata['status'] == 'complete':
        assert metadata['outcome'] in ('success', 'failure') and len(samples) >= 2
    return dict(directory=str(directory), status=metadata['status'],
                outcome=metadata['outcome'], samples=len(samples), images=len(checked))
