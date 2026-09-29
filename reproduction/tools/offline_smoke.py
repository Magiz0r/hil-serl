"""Offline verification only: no robot environment instantiation or device access."""
import importlib
import jax
import jax.numpy as jnp
import numpy as np

print('JAX', jax.__version__, 'devices', jax.devices(), flush=True)
assert any(d.platform == 'gpu' for d in jax.devices()), 'GPU required; CPU fallback is not a pass'
a = jnp.ones((1024, 1024), dtype=jnp.float32)
b = jax.jit(lambda x: x @ x)(a).block_until_ready()
np.testing.assert_allclose(np.asarray(b), 1024)
print('GPU matmul PASS', flush=True)
for name in (
    'serl_launcher.agents.continuous.sac',
    'serl_launcher.agents.continuous.sac_hybrid_single',
    'serl_launcher.agents.continuous.bc',
    'serl_launcher.networks.reward_classifier',
    'serl_launcher.data.replay_buffer',
    'serl_launcher.utils.launcher',
    'agentlace.trainer',
    'franka_env.envs.franka_env',
):
    importlib.import_module(name)
    print('IMPORT PASS', name, flush=True)
import gymnasium as gym
from serl_launcher.data.replay_buffer import ReplayBuffer
space = gym.spaces.Box(-1, 1, (7,), dtype=np.float32)
buffer = ReplayBuffer(space, space, 16)
for _ in range(8):
    buffer.insert(dict(observations=np.zeros(7), next_observations=np.ones(7),
                       actions=np.zeros(7), rewards=1., masks=1., dones=False))
batch = buffer.sample(4)
assert batch['actions'].shape == (4, 7)
print('Replay insert/sample PASS', flush=True)
print('OFFLINE SMOKE PASS; no hardware validation', flush=True)
