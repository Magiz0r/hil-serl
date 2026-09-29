"""Export an attended fixed-gripper demo; no robot I/O or inferred actions."""
import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import pickle

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from reproduction.portal.pilot_dataset import validate_episode

PROTOCOL = 'autoserl_fr3_demo_v1'
CAMERAS = {'wrist_1': 'external', 'wrist_2': 'wrist'}
MAX_IMAGE_AGE = .35
MAX_GRIPPER_AGE = .7


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def vector(value, size):
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError('missing or invalid measured vector of size %s' % size)
    return result


def measured_pose(state):
    xyz = vector(state.get('xyz'), 3)
    matrix = vector(state.get('rotation'), 9).reshape(3, 3, order='F')
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-5) or np.linalg.det(matrix) < .99999:
        raise ValueError('invalid measured rotation matrix')
    return xyz, Rotation.from_matrix(matrix)


def state_observation(state, gripper, origin):
    xyz, rotation = measured_pose(state)
    origin_xyz, origin_rotation = origin
    wrench = vector(state.get('tcp_wrench_K'), 6)
    if not np.allclose(wrench, vector(state.get('K_F_ext_hat_K'), 6)):
        raise ValueError('wrench provenance mismatch')
    velocity = vector(state.get('tcp_vel_base'), 6)
    relative_xyz = origin_rotation.inv().apply(xyz - origin_xyz)
    relative_euler = (origin_rotation.inv() * rotation).as_euler('xyz')
    body_velocity = np.r_[rotation.inv().apply(velocity[:3]), rotation.inv().apply(velocity[3:])]
    # Gym Dict flatten order: gripper_pose, tcp_force, tcp_pose, tcp_torque, tcp_vel.
    return np.r_[gripper, wrench[:3], relative_xyz, relative_euler,
                 wrench[3:], body_velocity].astype(np.float32)[None, :]


def checked_trace(rows, input_rows, command_id):
    rows = [row for row in rows if row.get('episode_command_id') == command_id]
    if len(rows) < 3 or rows[-1].get('kind') != 'terminal':
        raise ValueError('need at least two actions and one terminal observation')
    if [row.get('kind') for row in rows[:-1]] != ['action'] * (len(rows) - 1):
        raise ValueError('unexpected terminal or missing action in journal')
    if [row.get('index') for row in rows] != list(range(len(rows))):
        raise ValueError('dropped or duplicated journal row; cannot interpolate actions')
    decisions = [row['decision_id'] for row in rows[:-1]]
    if decisions != list(range(decisions[0], decisions[0] + len(decisions))):
        raise ValueError('dropped controller decision')
    inputs = {}
    for item in input_rows:
        if item['seq'] in inputs:
            raise ValueError('duplicate input sequence')
        inputs[item['seq']] = item
    pc_times, sequences = [], []
    for row in rows:
        if row.get('protocol') != PROTOCOL:
            raise ValueError('wrong action protocol')
        item = inputs.get(row['source_input_seq'])
        if item is None:
            raise ValueError('missing original PC input timestamp')
        if (not item['pilot']['connected'] or item['stop']
                or (row['kind'] == 'action' and item['pilot'] !=
                    dict(id=command_id, action='start', connected=True))
                or (row['kind'] == 'terminal' and not
                    (item['pilot']['action'] == 'lock' and item['pilot']['id'] > command_id))):
            raise ValueError('trace did not end with an attended Finish command')
        state = row['state']
        if (state['mode'] != 2 or state['current_errors'] or state['last_motion_errors']
                or not .95 <= state['success'] <= 1.
                or not 0 <= row['nuc_time'] - state['received_at'] <= .15):
            raise ValueError('invalid or stale controller observation')
        pc_times.append(float(item['pc_time']))
        sequences.append(row['source_input_seq'])
        if row['kind'] == 'action':
            actions = vector(row.get('actions'), 6)
            if np.max(np.abs(actions)) > 1. + 1e-7:
                raise ValueError('action outside policy space')
            xyz, rotation = measured_pose(state)
            delta = rotation.apply(actions[:3]) * .01
            turn = Rotation.from_rotvec(rotation.apply(actions[3:]) * .06) * rotation
            if (not np.allclose(xyz + delta, vector(row.get('target_xyz'), 3), atol=1e-8, rtol=0)
                    or (turn.inv() * Rotation.from_quat(vector(row.get('target_quaternion'), 4))).magnitude() > 1e-7):
                raise ValueError('recorded action does not reconstruct executed target')
    nuc_times = vector([row['nuc_time'] for row in rows], len(rows))
    pc_times = vector(pc_times, len(rows))
    periods = np.diff(nuc_times)
    if (np.any(np.diff(sequences) <= 0) or np.any(np.diff(pc_times) <= 0)
            or np.any(periods[:-1] < .075) or np.any(periods[:-1] > .20)
            or not 0 < periods[-1] <= .35):
        raise ValueError('action timing discontinuity; no resampling permitted')
    return rows, pc_times, periods


def latest_at(items, times, when, age_limit, label):
    index = bisect_right(times, when) - 1
    if index < 0 or not 0 <= when - times[index] <= age_limit:
        raise ValueError(label + ': no fresh causal observation before input send')
    return items[index], float(when - times[index])


def export_demo(episode, preprocessing, output):
    episode, output = Path(episode).resolve(), Path(output)
    manifest_path = output.with_suffix('.json')
    if output.suffix != '.pkl' or output.exists() or manifest_path.exists():
        raise ValueError('output must be a new .pkl file with a new .json manifest')
    validation = validate_episode(episode)
    metadata = json.loads((episode / 'episode.json').read_text())
    session = json.loads((episode.parent / 'session.json').read_text())
    configuration = session['control_configuration']
    if (validation['status'] != 'complete' or validation['outcome'] != 'success'
            or metadata.get('demo_protocol') != PROTOCOL or configuration.get('demo_protocol') != PROTOCOL):
        raise ValueError('requires a successful episode recorded in AutoSERL demo mode')
    if (configuration.get('action_hz') != 10. or configuration.get('position_action_scale_mm') != 10.
            or configuration.get('rotation_action_scale_rad') != .06):
        raise ValueError('unexpected demonstration action scales or period')
    command_id = metadata['control_start_command_id']
    rows, pc_times, periods = checked_trace(read_rows(episode / 'demo-actions.jsonl'),
        read_rows(episode / 'demo-inputs.jsonl'), command_id)
    if not metadata['started_pc_monotonic'] <= pc_times[0] < pc_times[-1] <= metadata['ended_pc_monotonic']:
        raise ValueError('action trace falls outside episode recording interval')
    samples = read_rows(episode / 'samples.jsonl')
    grippers = sorted((sample['gripper'] for sample in samples), key=lambda item: item['pc_received_at'])
    gripper_times = [item['pc_received_at'] for item in grippers]
    if len({item['data']['status']['gPR'] for item in grippers}) != 1:
        raise ValueError('gripper command changed inside fixed-gripper demonstration')
    config = json.loads(Path(preprocessing).read_text())
    if (config.get('schema') != 'autoserl_image_preprocessing_v1' or config.get('size') != [128, 128]
            or config.get('color') != 'RGB' or config.get('interpolation') != 'INTER_AREA'
            or set(config.get('crops_xywh', {})) != set(CAMERAS.values())):
        raise ValueError('explicit RGB 128x128 INTER_AREA preprocessing with both camera crops is required')
    frame_lists, frame_times, images = {}, {}, {}
    for name in CAMERAS.values():
        unique = {sample['cameras'][name]['path']: sample['cameras'][name] for sample in samples}
        frame_lists[name] = sorted(unique.values(), key=lambda frame: frame['pc_captured_at'])
        frame_times[name] = [frame['pc_captured_at'] for frame in frame_lists[name]]
    origin = measured_pose(rows[0]['state'])
    observations, alignment = [], []
    for row, when in zip(rows, pc_times):
        gripper, gripper_age = latest_at(grippers, gripper_times, when, MAX_GRIPPER_AGE, 'gripper')
        status = gripper['data']['status']
        if gripper['data']['phase'] != 'ready' or status['gFLT'] or not 0 <= status['gPO'] <= 255:
            raise ValueError('invalid measured gripper state')
        # Robotiq register proxy: 1=open, 0=closed. Not a calibrated jaw width.
        obs = {'state': state_observation(row['state'], 1. - status['gPO'] / 255., origin)}
        provenance = dict(source_input_seq=row['source_input_seq'], pc_input_time=float(when),
            nuc_state_time=row['state']['received_at'], gripper_age=gripper_age, cameras={})
        for key, name in CAMERAS.items():
            frame, age = latest_at(frame_lists[name], frame_times[name], when, MAX_IMAGE_AGE, name)
            cache_key = frame['path']
            if cache_key not in images:
                image = cv2.imread(str(episode / cache_key), cv2.IMREAD_COLOR)
                if image is None or list(image.shape) != frame['shape'] or frame['decoded_color_order'] != 'BGR':
                    raise ValueError('invalid camera image or color provenance')
                crop = config['crops_xywh'][name]
                if not isinstance(crop, list) or len(crop) != 4 or any(type(n) is not int for n in crop):
                    raise ValueError('crop must be explicit integer [x,y,width,height]')
                x, y, w, h = crop
                if not (x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= image.shape[1] and y + h <= image.shape[0]):
                    raise ValueError('crop outside image')
                rgb = cv2.cvtColor(image[y:y+h, x:x+w], cv2.COLOR_BGR2RGB)
                images[cache_key] = cv2.resize(rgb, (128, 128), interpolation=cv2.INTER_AREA)[None, ...]
            obs[key] = images[cache_key]
            provenance['cameras'][name] = dict(path=cache_key, sha256=frame['sha256'], age_seconds=age)
        observations.append(obs)
        alignment.append(provenance)
    transitions = []
    for index, row in enumerate(rows[:-1]):
        done = index == len(rows) - 2
        transitions.append(dict(observations=observations[index],
            actions=np.asarray(row['actions'], dtype=np.float32), next_observations=observations[index+1],
            rewards=float(done), masks=float(not done), dones=done))
    sources = ['episode.json', 'samples.jsonl', 'demo-actions.jsonl', 'demo-inputs.jsonl']
    manifest = dict(schema='autoserl_demo_export_v1', protocol=PROTOCOL,
        episode=str(episode), transitions=len(transitions), outcome_source=metadata.get('outcome_source'),
        source_sha256={name: digest(episode / name) for name in sources},
        session_sha256=digest(episode.parent / 'session.json'),
        exporter_sha256=digest(__file__), preprocessing=config, camera_mapping=CAMERAS,
        demo_initial_tcp_pose=np.r_[origin[0], origin[1].as_euler('xyz')].tolist(),
        action_frame='current measured TCP body axes', action_scale=[.01, .06],
        state_layout='Euler19: gripper1, forceK3, reset-relative pose6, torqueK3, body velocity6',
        tcp_velocity_source='checked FR3 URDF Jacobian at measured q times measured dq; not driver zeroJacobian',
        gripper_state='1-gPO/255, Robotiq register proxy; not calibrated width',
        reward='operator-labeled success: last transition only; no automatic success detector',
        period_seconds=periods.tolist(), alignment=alignment,
        image_alignment='latest PC read-completion timestamp at or before PC input send; no cross-host clock subtraction or hardware sync',
        control_configuration=configuration,
        limitations=['No real-robot learner/actor environment is connected yet.',
                     'Reference plug_insert gains are an adaptation to the FMB peg, FR3, ZED and Robotiq setup.',
                     'Local joint guards can reduce actions; the journal records the resulting command.'])
    # Complete validation before creating either artifact. Exclusive creation
    # never overwrites an earlier demonstration; manifest certifies pickle hash.
    data = pickle.dumps(transitions, protocol=pickle.HIGHEST_PROTOCOL)
    manifest['pickle_sha256'] = hashlib.sha256(data).hexdigest()
    report = json.dumps(manifest, indent=2, allow_nan=False) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(data)
    with manifest_path.open('x') as stream:
        stream.write(report)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episode', type=Path)
    parser.add_argument('--preprocessing', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = export_demo(args.episode, args.preprocessing, args.output)
    print(json.dumps(dict(output=str(args.output), transitions=result['transitions'],
                         demo_initial_tcp_pose=result['demo_initial_tcp_pose']), indent=2))


if __name__ == '__main__':
    main()
