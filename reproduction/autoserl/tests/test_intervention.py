import pickle

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from reproduction.autoserl.intervention import AutoIntervention
from reproduction.autoserl.mock_env import Signals, make_demo, observation_stack


def build(tmp_path, **overrides):
    path = tmp_path / "one_demo.pkl"
    make_demo(path)
    args = dict(demo_path=path, demo_initial_tcp_pose=np.zeros(6),
                recover_point0=2, recover_point1=7, th1=.002, th2=.02,
                l_stag=3, l_term=2, expert=Signals())
    args.update(overrides)
    env = AutoIntervention(observation_stack(), np.zeros(6), **args)
    env.reset()
    return env


def test_reconstructs_euler_demo_in_world_frame(tmp_path):
    origin = np.array([.3, -.2, .4, .2, -.1, 1.2])
    initial = np.r_[origin[:3], Rotation.from_euler("xyz", origin[3:]).as_quat()]
    path = tmp_path / "rotated.pkl"
    make_demo(path, initial_pose=initial)
    env = build(tmp_path, demo_path=path, demo_initial_tcp_pose=origin)
    direction = Rotation.from_euler("xyz", origin[3:]).apply([1, 0, 0])
    expected = origin[:3] + np.arange(12)[:, None] * .005 * direction
    np.testing.assert_allclose(env.demo_tcp_list[:, :3], expected, atol=1e-7)
    for pose in env.demo_tcp_list:
        assert (Rotation.from_quat(pose[3:]).inv() * Rotation.from_quat(initial[3:])).magnitude() < 1e-6


def test_stagnation_retreat_replays_and_returns_control(tmp_path):
    env = build(tmp_path)
    env.unwrapped.currpos[:3] = [.025, 0, 0]
    env.slide_window = list(range(2, 8))
    for _ in range(3):
        env.step(np.zeros(6))
    saw_recovery = False
    for _ in range(35):
        _, _, _, _, info = env.step(np.zeros(6))
        saw_recovery |= info['auto_recovery_count'] > 0
        if saw_recovery and env.before_intervened_completed_traj_min_idx == -1:
            break
    assert saw_recovery
    assert env.total_recover_cnt == 1
    assert env.before_intervened_completed_traj_min_idx == -1
    assert not env.recover_action_list
    assert env.unwrapped.currpos[0] > .025


def test_sliding_intervention_records_executed_action(tmp_path):
    env = build(tmp_path)
    env.unwrapped.currpos[:3] = [-.04, 0, 0]
    before = env.unwrapped.currpos.copy()
    _, _, _, _, info = env.step(np.zeros(6))
    assert info['auto_intervention']
    np.testing.assert_allclose(info['intervene_action'][:3] * .01, env.unwrapped.currpos[:3] - before[:3])


def test_success_disables_intervention_across_resets(tmp_path):
    env = build(tmp_path)
    env.expert.success = True
    _, reward, done, _, info = env.step(np.zeros(6))
    assert reward and done and info['succeed']
    assert env.forever_no_window_intervention
    env.expert.success = False
    env.reset()
    env.unwrapped.currpos[:3] = [-.05, 0, 0]
    _, _, _, _, info = env.step(np.zeros(6))
    assert not info['auto_intervention']


def test_abort_does_not_mark_success_or_disable_guidance(tmp_path):
    env = build(tmp_path)
    env.expert.abort = True
    _, reward, done, _, info = env.step(np.zeros(6))
    assert done and not reward and not info['succeed']
    assert not env.forever_no_window_intervention


def test_duplicate_points_have_defined_direction_check(tmp_path):
    env = build(tmp_path)
    env.current_demo_tcp_list[1] = env.current_demo_tcp_list[0]
    with np.errstate(all='raise'):
        assert not env.judge_whether_true_intervention(env.current_demo_tcp_list[2], 0)


@pytest.mark.parametrize('indices', [(-1, 4), (4, 4), (4, 2), (2, 12)])
def test_invalid_recovery_points_rejected(tmp_path, indices):
    with pytest.raises(ValueError, match='Recovery points'):
        build(tmp_path, recover_point0=indices[0], recover_point1=indices[1])


def test_pose_format_is_explicit(tmp_path):
    with pytest.raises(ValueError, match='Expected finite state'):
        build(tmp_path, demo_pose_format='quaternion')


def test_learned_gripper_actions_rejected(tmp_path):
    path = tmp_path / 'seven.pkl'
    demo = make_demo(path)
    demo[0]['actions'] = np.zeros(7)
    with path.open('wb') as stream:
        pickle.dump(demo, stream)
    with pytest.raises(ValueError, match='6D fixed-gripper'):
        build(tmp_path, demo_path=path)


def test_two_demos_rejected(tmp_path):
    path = tmp_path / 'two.pkl'
    demo = make_demo(path)
    with path.open('wb') as stream:
        pickle.dump(demo + demo, stream)
    with pytest.raises(ValueError, match='exactly one'):
        build(tmp_path, demo_path=path)


def test_evaluation_never_uses_demo_intervention(tmp_path):
    env = build(tmp_path, enable_interventions=False)
    env.unwrapped.currpos[:3] = [-.1, 0, 0]
    _, _, _, _, info = env.step(np.zeros(6))
    assert not info['auto_intervention']
    assert env.total_recover_cnt == 0
