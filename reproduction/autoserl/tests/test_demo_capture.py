import copy
import json
from pathlib import Path
import pickle
import sys
from unittest.mock import patch

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'nuc'))
from autoserl_capture import ActionJournal, DemoGuard, PROTOCOL
from pilot_motion import PilotMotion
from reproduction.autoserl.export_demo import export_demo, checked_trace, state_observation
from reproduction.autoserl.collect_demo_trace import collect_trace
from reproduction.pilot_dataset import Episode
from reproduction.tests.test_pilot_dataset import snapshot


def measured(now=100., angle=.7):
    return dict(q=[0.] * 7, dq=[.001] * 7, xyz=[.3, .1, .5],
        rotation=Rotation.from_euler('z', angle).as_matrix().flatten(order='F').tolist(),
        mode=2, current_errors=[], last_motion_errors=[], success=1., received_at=now,
        K_F_ext_hat_K=[1., 2., 3., .1, .2, .3], tcp_wrench_K=[1., 2., 3., .1, .2, .3],
        tcp_vel_base=[.001] * 6)


def test_guard_journal_neutral_decisions_rotation_limits_and_finish(tmp_path):
    state = measured()
    jacobian = np.c_[np.eye(6) * .1, np.zeros(6)]
    guard = DemoGuard(state, [-1.] * 7, [1.] * 7, trial_bounds=False, official_input=True)
    pilot = PilotMotion(guard)
    journal = ActionJournal(tmp_path / 'journal.jsonl')
    for index in range(22):
        now = 100. + index * .02
        state['received_at'] = now
        guard.check_state(state, now, jacobian)
        action = 'lock' if index in (0, 21) else 'start'
        packet = dict(seq=index, server_time=now, axes=[0.] * 6 if index < 8 else [1.] * 6,
            enable=False, stop=False, pilot=dict(id=1 if index == 0 else (3 if index == 21 else 2),
                                              action=action, connected=True))
        pilot.step(packet, now, .02, state)
        journal.observe(guard, pilot, packet, now)
    journal.close()
    rows = [json.loads(line) for line in (tmp_path / 'journal.jsonl').read_text().splitlines()]
    assert [row['kind'] for row in rows] == ['action'] * 4 + ['terminal']
    assert [row['index'] for row in rows] == list(range(5))
    assert {row['episode_command_id'] for row in rows} == {2}
    np.testing.assert_allclose(np.diff([row['nuc_time'] for row in rows[:-1]]), .1)
    np.testing.assert_allclose(rows[0]['actions'], 0., atol=1e-9)
    np.testing.assert_allclose(rows[1]['actions'], 0., atol=1e-9)
    row = rows[2]
    rotation = Rotation.from_matrix(np.array(state['rotation']).reshape(3, 3, order='F'))
    actions = np.asarray(row['actions'])
    assert 0 < np.max(np.abs(actions)) < 1  # reduced by Jacobian joint budget
    delta = rotation.apply(actions[:3]) * .01
    turn = rotation.apply(actions[3:]) * .06
    assert np.max(np.abs(np.linalg.pinv(jacobian) @ np.r_[delta, turn])) <= .04 + 1e-9
    np.testing.assert_allclose(np.array(state['xyz']) + delta, row['target_xyz'], atol=1e-9)
    assert pilot.mode == 'locked'


def test_wrench_required_and_operator_stop_never_records_fabricated_state():
    state = measured()
    guard = DemoGuard(state, [-1.] * 7, [1.] * 7, trial_bounds=False, official_input=True)
    jacobian = np.c_[np.eye(6), np.zeros(6)]
    for value in (None, [0.] * 7, [float('nan')] * 6):
        bad = dict(state, K_F_ext_hat_K=value)
        with pytest.raises(ValueError, match='measured K_F'):
            guard.check_state(bad, 100., jacobian)
    guard.check_state(state, 100., jacobian)
    with pytest.raises(ValueError, match='operator stop'):
        guard.step(dict(seq=0, server_time=100., axes=[0.] * 6, enable=False, stop=True), 100., .02)
    assert guard.decision is None


def fixture_episode(tmp_path):
    episode = tmp_path / 'session' / 'episode_1'
    item = Episode(episode, dict(task='synthetic_insertion', demo_protocol=PROTOCOL,
                                control_start_command_id=2), 1000.)
    rows, inputs = [], []
    for index in range(4):
        pc_now, nuc_now = 1000. + .1 * index, 100. + .1 * index
        raw = snapshot(pc_now, index)
        raw['gripper']['data']['status'].update(gPR=255, gPO=170)
        item.append(raw, pc_now)
        state = measured(nuc_now)
        state['xyz'][0] += .001 * index
        row = dict(protocol=PROTOCOL, episode_command_id=2, index=index,
                   source_input_seq=10+index, nuc_time=nuc_now+.01,
                   kind='terminal' if index == 3 else 'action', state=state)
        if index < 3:
            rotation = Rotation.from_matrix(np.array(state['rotation']).reshape(3, 3, order='F'))
            actions = [.1, .2, -.1, .01, 0., .1]
            row.update(decision_id=index+1, actions=actions,
                target_xyz=(np.array(state['xyz'])+rotation.apply(actions[:3])*.01).tolist(),
                target_quaternion=(Rotation.from_rotvec(rotation.apply(actions[3:])*.06)*rotation).as_quat().tolist())
        rows.append(row)
        inputs.append(dict(seq=10+index, pc_time=pc_now+.01, stop=False,
            pilot=dict(id=3 if index == 3 else 2, action='lock' if index == 3 else 'start', connected=True)))
    item.finish('success', 1000.4)
    configuration = dict(demo_protocol=PROTOCOL, action_hz=10., position_action_scale_mm=10.,
        rotation_action_scale_rad=.06, run_directory='/hil-serl-state/logs/manual-20260924T185810Z-8')
    (episode.parent / 'session.json').write_text(json.dumps(dict(control_configuration=configuration,
                                                               control_log_directory=str(tmp_path))))
    for name, value in [('demo-actions.jsonl', rows), ('demo-inputs.jsonl', inputs)]:
        (episode / name).write_text(''.join(json.dumps(row)+'\n' for row in value))
    (tmp_path / 'input.jsonl').write_text((episode / 'demo-inputs.jsonl').read_text())
    preprocessing = tmp_path / 'images.json'
    preprocessing.write_text(json.dumps(dict(schema='autoserl_image_preprocessing_v1',
        size=[128, 128], color='RGB', interpolation='INTER_AREA',
        crops_xywh={name: [0, 0, 32, 24] for name in ('external', 'wrist')})))
    return episode, preprocessing, rows, inputs


def test_export_shapes_real_image_color_alignment_and_terminal_reward(tmp_path):
    episode, preprocessing, rows, inputs = fixture_episode(tmp_path)
    output = tmp_path / 'demo.pkl'
    manifest = export_demo(episode, preprocessing, output)
    assert manifest['transitions'] == 3
    assert manifest['demo_initial_tcp_pose'] == pytest.approx([.3, .1, .5, 0, 0, .7])
    assert manifest['alignment'][0]['cameras']['external']['age_seconds'] == pytest.approx(.01)
    with output.open('rb') as stream:
        demo = pickle.load(stream)
    assert [t['rewards'] for t in demo] == [0., 0., 1.]
    assert [t['masks'] for t in demo] == [1., 1., 0.]
    assert [t['dones'] for t in demo] == [False, False, True]
    for transition in demo:
        assert transition['actions'].shape == (6,)
        assert transition['observations']['state'].shape == (1, 19)
        image = transition['observations']['wrist_1']
        assert image.shape == (1, 128, 128, 3) and image.dtype == np.uint8
        np.testing.assert_allclose(image[0, 0, 0], [200, 70, 10], atol=3)
    np.testing.assert_allclose(demo[0]['observations']['state'][0, 4:10], 0, atol=1e-7)
    for key in ('state', 'wrist_1', 'wrist_2'):
        np.testing.assert_array_equal(demo[0]['next_observations'][key], demo[1]['observations'][key])
    with pytest.raises(ValueError, match='new .pkl'):
        export_demo(episode, preprocessing, output)


def test_state_matches_existing_serl_wrapper_stack():
    from reproduction.autoserl.bootstrap import configure
    configure()
    from reproduction.autoserl.mock_env import MockFranka, observation_stack
    class KnownState(MockFranka):
        def observation(self):
            obs = super().observation()
            obs['state'].update(tcp_force=np.array([1., 2., 3.]), tcp_torque=np.array([.1, .2, .3]),
                                tcp_vel=np.array([.001] * 6), gripper_pose=np.array([.3]))
            return obs
    state = measured()
    rotation = Rotation.from_matrix(np.array(state['rotation']).reshape(3, 3, order='F'))
    env = observation_stack(KnownState(np.r_[state['xyz'], rotation.as_quat()]))
    obs, _ = env.reset()
    exported = state_observation(state, .3, (np.array(state['xyz']), rotation))
    np.testing.assert_allclose(obs['state'], exported, atol=1e-7)
    next_obs, _, _, _, _ = env.step(np.array([.2, -.1, .4, .01, .02, .03]))
    state['xyz'] = env.unwrapped.currpos[:3].tolist()
    state['rotation'] = Rotation.from_quat(env.unwrapped.currpos[3:]).as_matrix().flatten(order='F').tolist()
    np.testing.assert_allclose(next_obs['state'], state_observation(state, .3, (np.array([.3, .1, .5]), rotation)), atol=1e-7)


@pytest.mark.parametrize('fault', ['dropped', 'missing_input', 'wrong_target', 'wrong_frame', 'gap', 'legacy', 'future_image'])
def test_export_rejects_missing_or_inconsistent_evidence(tmp_path, fault):
    episode, preprocessing, rows, inputs = fixture_episode(tmp_path)
    if fault == 'dropped':
        rows.pop(1)
    elif fault == 'missing_input':
        inputs.pop(1)
    elif fault == 'wrong_target':
        rows[0]['target_xyz'][0] += .01
    elif fault == 'wrong_frame':
        rows[0]['state']['rotation'][0] = 2.
    elif fault == 'gap':
        rows[-1]['nuc_time'] += 1.
    elif fault == 'legacy':
        metadata = json.loads((episode / 'episode.json').read_text())
        metadata.pop('demo_protocol')
        (episode / 'episode.json').write_text(json.dumps(metadata))
    else:
        inputs[0]['pc_time'] = 999.9
    (episode / 'demo-actions.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (episode / 'demo-inputs.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in inputs))
    with pytest.raises(ValueError):
        export_demo(episode, preprocessing, tmp_path / 'demo.pkl')
    assert not (tmp_path / 'demo.pkl').exists()


def test_collect_reads_only_dedicated_trace_and_retries_identically(tmp_path):
    from types import SimpleNamespace
    episode, _, rows, _ = fixture_episode(tmp_path)
    result = SimpleNamespace(stdout=(episode / 'demo-actions.jsonl').read_text())
    with patch('reproduction.autoserl.collect_demo_trace.subprocess.run', return_value=result) as run:
        assert collect_trace(episode)['actions'] == 3
        arguments = run.call_args
        assert arguments.args[0][-3:] == ['FrankaNUC', 'python3', '-']
        assert '/state/logs/manual-20260924T185810Z-8/demo-actions.jsonl' in arguments.kwargs['input']
        assert 'docker' not in arguments.kwargs['input']
        collect_trace(episode)


def test_trace_rejects_disconnection_instead_of_finish(tmp_path):
    _, _, rows, inputs = fixture_episode(tmp_path)
    inputs[-1]['pilot']['connected'] = False
    with pytest.raises(ValueError, match='Finish'):
        checked_trace(rows, inputs, 2)
