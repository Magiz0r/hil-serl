"""Synthetic pixel SAC update and reward classifier gradient; no robot I/O."""
import jax
import jax.numpy as jnp
import numpy as np
from flax.core import freeze
from serl_launcher.agents.continuous.sac import SACAgent
from serl_launcher.networks.reward_classifier import create_classifier
import optax
assert jax.default_backend() == 'gpu'
obs = {'image': jnp.zeros((2, 1, 128, 128, 3), dtype=jnp.uint8),
       'state': jnp.zeros((2, 1, 7))}
actions = jnp.zeros((2, 7))
agent = SACAgent.create_pixels(jax.random.PRNGKey(0), obs, actions,
    encoder_type='resnet-pretrained', use_proprio=True,
    critic_network_kwargs={'hidden_dims': [32, 32]},
    policy_network_kwargs={'hidden_dims': [32, 32]})
batch = freeze(dict(observations=obs, next_observations=obs, actions=actions,
                    rewards=jnp.zeros(2), masks=jnp.ones(2)))
agent, info = agent.update(batch)
for x in jax.tree_util.tree_leaves(info):
    assert np.isfinite(np.asarray(x)).all()
print('Synthetic pixel SAC gradient update PASS', flush=True)
classifier = create_classifier(jax.random.PRNGKey(1), obs, ['image'])
@jax.jit
def step(state):
    def loss(params):
        logits = state.apply_fn({'params': params}, obs, train=True,
            rngs={'dropout': jax.random.PRNGKey(2)})
        return optax.sigmoid_binary_cross_entropy(logits, jnp.array([[0.], [1.]])).mean()
    value, grads = jax.value_and_grad(loss)(state.params)
    return state.apply_gradients(grads=grads), value
classifier, loss = step(classifier)
assert np.isfinite(float(loss))
print('Synthetic reward classifier gradient PASS', float(loss), flush=True)
