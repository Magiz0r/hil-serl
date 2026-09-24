"""Validate an existing portal episode and report AutoSERL compatibility read-only."""
import argparse
import json
from pathlib import Path

import numpy as np

from reproduction.pilot_dataset import validate_episode


def inspect_episode(directory):
    directory = Path(directory).resolve()
    validation = validate_episode(directory)
    metadata = json.loads((directory / 'episode.json').read_text())
    samples = [json.loads(line) for line in (directory / 'samples.jsonl').read_text().splitlines()]
    if len(samples) < 2:
        raise ValueError('Need at least two samples for compatibility inspection')
    states = [sample['arm']['data']['sample']['state'] for sample in samples]
    positions = np.asarray([state['xyz'] for state in states])
    timestamps = np.asarray([sample['pc_sampled_at'] for sample in samples])
    gripper_targets = sorted({sample['gripper']['data']['status']['gPR'] for sample in samples})
    return dict(validation=validation, task=metadata.get('task'), outcome_source=metadata.get('outcome_source'),
        samples_sha256=metadata['samples_sha256'], duration_seconds=float(timestamps[-1] - timestamps[0]),
        median_sample_period_seconds=float(np.median(np.diff(timestamps))),
        tcp_xyz_min=positions.min(axis=0).tolist(), tcp_xyz_max=positions.max(axis=0).tolist(),
        gripper_command_positions=gripper_targets, fixed_gripper=len(gripper_targets) == 1,
        available_state_fields=sorted(set.intersection(*(set(state) for state in states))),
        cameras={name: dict(shape=frame['shape'], encoding=frame['encoding'],
                            decoded_color_order=frame['decoded_color_order'])
                 for name, frame in samples[0]['cameras'].items()},
        trainable_as_official_demo=False,
        blockers=[
            'Portal records raw streams, not executed normalized 6D actions with matched observation/next_observation pairs.',
            'The official policy state includes TCP velocity, force and torque; raw joint velocities/torques are different quantities.',
            'Need explicit camera preprocessing, action frame/scales, reward timing, and the measured demo origin.',
        ] + ([] if len(gripper_targets) == 1 else [
            'This episode changes the gripper; the original AutoSERL task uses fixed-gripper 6D actions.'
        ]) + ([] if validation['status'] == 'complete' and validation['outcome'] == 'success' else [
            'A complete successful episode is required for the initial demonstration.'
        ]),
        recommendation='Keep this raw recording. Extend the existing portal recorder after fixing the task/control interface; do not fabricate missing training fields.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episode', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect_episode(args.episode)
    content = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content, end='')


if __name__ == '__main__':
    main()
