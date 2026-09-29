"""Build a pose/action probe from a hash-pinned local export; no robot I/O."""
import argparse
import json
from pathlib import Path

import numpy as np

from reproduction.autoserl.export_demo import checked_trace, digest, measured_pose, read_rows


def build_plan(selection_path, root):
    selection_path, root = Path(selection_path), Path(root)
    selection = json.loads(selection_path.read_text())
    if selection['schema'] != 'autoserl_recovery_selection_v1':
        raise ValueError('Unknown selection schema')
    demo = root / selection['demo_path']
    manifest_path = demo.with_suffix('.json')
    if (digest(demo) != selection['demo_pickle_sha256'] or
            digest(manifest_path) != selection['demo_manifest_sha256']):
        raise ValueError('Selected demo changed')
    manifest = json.loads(manifest_path.read_text())
    episode = Path(manifest['episode'])
    for name, expected in manifest['source_sha256'].items():
        if digest(episode / name) != expected:
            raise ValueError('Demonstration source changed: ' + name)
    metadata = json.loads((episode / 'episode.json').read_text())
    rows, _, periods = checked_trace(read_rows(episode / 'demo-actions.jsonl'),
        read_rows(episode / 'demo-inputs.jsonl'), metadata['control_start_command_id'])
    point0, point1 = selection['recover_point0'], selection['recover_point1']
    if not 0 < point0 < point1 < len(rows)-2:
        raise ValueError('Recovery range lacks a measured endpoint')
    poses = [np.r_[xyz, rotation.as_quat()].tolist()
             for xyz, rotation in (measured_pose(row['state']) for row in rows[:point1+2])]
    positions = {row['gripper']['data']['status']['gPO'] for row in read_rows(episode / 'samples.jsonl')}
    if len(positions) != 1:
        raise ValueError('Demonstrated grasp changed')
    return dict(schema='autoserl_recovery_probe_v1', point0=point0, point1=point1,
        custom_home_q=metadata['custom_home']['q'], gripper_position=positions.pop(),
        poses_xyz_quaternion=poses, actions_body=[r['actions'] for r in rows[:point1+1]],
        periods_seconds=periods[:point1+1].tolist(),
        source=dict(selection_sha256=digest(selection_path),
            demo_pickle_sha256=digest(demo), demo_manifest_sha256=digest(manifest_path)),
        scope='Attended recorded-action approach and reference-gain retreat/replay; not RL or automatic reset')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = build_plan(args.selection, args.root)
    with args.output.open('x') as stream:
        stream.write(json.dumps(plan, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(output=str(args.output), point0=plan['point0'], point1=plan['point1'])))


if __name__ == '__main__':
    main()
