"""Exercise AutoSERL + existing pixel SAC on synthetic data; never opens hardware."""
import argparse
import copy
import json
from pathlib import Path
import tempfile

from .bootstrap import configure, assert_local_modules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=('gpu', 'cpu'), default='gpu')
    args = parser.parse_args()
    configure()
    import jax
    import numpy as np
    from flax import serialization
    from .intervention import AutoIntervention
    from .mock_env import IMAGE_KEYS, Signals, make_demo, observation_stack
    from serl_launcher.data.replay_buffer import ReplayBuffer
    from serl_launcher.data.memory_efficient_replay_buffer import MemoryEfficientReplayBuffer
    from serl_launcher.utils.launcher import make_sac_pixel_agent
    from serl_launcher.utils.train_utils import concat_batches

    origins = assert_local_modules()
    if jax.default_backend() != args.backend:
        raise RuntimeError(f'Required {args.backend}; found {jax.devices()}')
    print(f'Backend: {jax.devices()}; synthetic data only', flush=True)
    with tempfile.TemporaryDirectory(prefix='autoserl-smoke-') as temporary:
        demo_path = Path(temporary) / 'synthetic_one_demo.pkl'
        demo = make_demo(demo_path)
        signals = Signals()
        env = AutoIntervention(observation_stack(), np.zeros(6), expert=signals,
                               demo_path=demo_path, demo_initial_tcp_pose=np.zeros(6),
                               recover_point0=2, recover_point1=7,
                               th1=.002, th2=.02, l_stag=3, l_term=2)
        # Expert interventions can be nonconsecutive. The ordinary replay keeps
        # each image pair intact without assuming adjacent expert transitions.
        expert = ReplayBuffer(env.observation_space, env.action_space, capacity=256)
        online = MemoryEfficientReplayBuffer(env.observation_space, env.action_space,
                                              capacity=256, pixel_keys=IMAGE_KEYS)
        expert.seed(0)
        online.seed(1)
        for transition in demo:
            expert.insert(transition)
        obs, _ = env.reset()
        intervention_count = 0
        for index in range(40):
            proposed = np.zeros(6, np.float32)
            if index < 5:
                proposed[0] = .5  # Reach the recovery interval, then stagnate.
            signals.success = index == 39  # Explicit synthetic label, no detector.
            next_obs, reward, done, truncated, info = env.step(proposed)
            action = np.asarray(info.get('intervene_action', proposed), dtype=np.float32)
            transition = copy.deepcopy(dict(observations=obs, actions=action,
                next_observations=next_obs, rewards=float(reward), masks=float(not done),
                dones=bool(done or truncated)))
            online.insert(transition)
            if info['auto_intervention']:
                expert.insert(transition)
                intervention_count += 1
            obs = next_obs
        assert intervention_count > 0, 'Fixture did not exercise automatic corrections'
        assert env.total_recover_cnt > 0, 'Fixture did not exercise recovery'
        print(f'One {len(demo)}-step demo; 40 rollout steps; '
              f'{intervention_count} corrected actions; {env.total_recover_cnt} recoveries', flush=True)

        sample_obs, _ = env.reset()
        agent = make_sac_pixel_agent(0, sample_obs, np.zeros(6, np.float32), image_keys=IMAGE_KEYS)
        original_params = agent.state.params
        metrics = []
        for networks in (frozenset({'critic'}), frozenset({'actor', 'critic', 'temperature'})):
            batch = concat_batches(expert.sample(2), online.sample(2), axis=0)
            assert batch['actions'].shape == (4, 6)
            assert batch['observations']['state'].shape == (4, 1, 19)
            agent, info = agent.update(batch, networks_to_update=networks)
            row = {jax.tree_util.keystr(key): float(np.asarray(value).mean())
                   for key, value in jax.tree_util.tree_flatten_with_path(info)[0]}
            assert all(np.isfinite(np.asarray(value)).all() for value in jax.tree_util.tree_leaves(info))
            metrics.append(row)
            print(f'Gradient update passed: {sorted(networks)}', flush=True)
        assert any(not np.array_equal(np.asarray(before), np.asarray(after))
                   for before, after in zip(jax.tree_util.tree_leaves(original_params),
                                             jax.tree_util.tree_leaves(agent.state.params)))

        checkpoint = Path(temporary) / 'synthetic_state.msgpack'
        checkpoint.write_bytes(serialization.to_bytes(agent.state))
        restored = agent.replace(state=serialization.from_bytes(agent.state, checkpoint.read_bytes()))
        for before, after in zip(jax.tree_util.tree_leaves(agent.state), jax.tree_util.tree_leaves(restored.state)):
            np.testing.assert_array_equal(np.asarray(before), np.asarray(after))
        predicted = np.asarray(agent.sample_actions(sample_obs, argmax=True))
        np.testing.assert_allclose(predicted, restored.sample_actions(sample_obs, argmax=True), atol=1e-6)
        assert predicted.shape == (6,) and np.isfinite(predicted).all()
        report = dict(status='pass', scope='synthetic software integration; no physical learning or success-rate claim',
                      modules=origins, devices=[str(device) for device in jax.devices()],
                      initial_demo_episodes=1, synthetic=True, demo_transitions=len(demo),
                      rollout_transitions=40, intervention_transitions=intervention_count,
                      recoveries=env.total_recover_cnt, batch_size=4, expert_fraction=.5,
                      gradient_updates=2, parameters_changed=True, checkpoint_roundtrip=True,
                      metrics=metrics, jax_version=jax.__version__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(f'PASS: {args.output}; no robot I/O', flush=True)


if __name__ == '__main__':
    main()
