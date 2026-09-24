"""Kinematic software fixture; no contact physics, cameras, sockets or robot I/O."""
import copy
import pickle

import gymnasium as gym
import numpy as np
from scipy.spatial.transform import Rotation

from franka_env.envs.relative_env import RelativeFrame
from franka_env.envs.wrappers import GripperCloseEnv, Quat2EulerWrapper
from serl_launcher.wrappers.serl_obs_wrappers import SERLObsWrapper
from serl_launcher.wrappers.chunking import ChunkingWrapper

IMAGE_KEYS = ("wrist_1", "wrist_2")
PROPRIO_KEYS = ("tcp_pose", "tcp_vel", "tcp_force", "tcp_torque", "gripper_pose")


class Signals:
    def __init__(self):
        self.success = False
        self.abort = False

    def get_action(self):
        return np.zeros(6), [self.success, self.abort]


class MockFranka(gym.Env):
    def __init__(self, initial_pose=None):
        self.initial_pose = np.asarray(initial_pose if initial_pose is not None else [0, 0, 0, 0, 0, 0, 1], dtype=float)
        self.currpos = self.initial_pose.copy()
        self.action_scale = np.array([0.01, 0.06, 1.0])
        self.action_space = gym.spaces.Box(-1., 1., (7,), dtype=np.float32)
        self.observation_space = gym.spaces.Dict({
            "state": gym.spaces.Dict({
                "tcp_pose": gym.spaces.Box(-np.inf, np.inf, (7,), dtype=np.float32),
                "tcp_vel": gym.spaces.Box(-np.inf, np.inf, (6,), dtype=np.float32),
                "tcp_force": gym.spaces.Box(-np.inf, np.inf, (3,), dtype=np.float32),
                "tcp_torque": gym.spaces.Box(-np.inf, np.inf, (3,), dtype=np.float32),
                "gripper_pose": gym.spaces.Box(-1., 1., (1,), dtype=np.float32),
            }),
            "images": gym.spaces.Dict({key: gym.spaces.Box(0, 255, (128, 128, 3), dtype=np.uint8) for key in IMAGE_KEYS}),
        })
        self.steps = 0
        self.stuck = False

    def observation(self):
        intensity = int(np.clip(self.currpos[0] * 1000 + 100, 0, 255))
        return {"state": {
            "tcp_pose": self.currpos.astype(np.float32).copy(),
            "tcp_vel": np.zeros(6, np.float32),
            "tcp_force": np.zeros(3, np.float32),
            "tcp_torque": np.zeros(3, np.float32),
            "gripper_pose": np.zeros(1, np.float32),
        }, "images": {key: np.full((128, 128, 3), intensity, np.uint8) for key in IMAGE_KEYS}}

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.currpos = self.initial_pose.copy()
        self.steps = 0
        return self.observation(), {"synthetic": True}

    def step(self, action):
        action = np.clip(np.asarray(action), -1, 1)
        if not self.stuck:
            self.currpos[:3] += action[:3] * self.action_scale[0]
            self.currpos[3:] = (Rotation.from_rotvec(action[3:6] * self.action_scale[1]) * Rotation.from_quat(self.currpos[3:])).as_quat()
        self.steps += 1
        return self.observation(), 0., self.steps >= 100, False, {"succeed": False, "synthetic": True}


def observation_stack(base=None):
    env = GripperCloseEnv(base if base is not None else MockFranka())
    env = RelativeFrame(env)
    env = Quat2EulerWrapper(env)
    env = SERLObsWrapper(env, proprio_keys=PROPRIO_KEYS)
    return ChunkingWrapper(env, obs_horizon=1, act_exec_horizon=None)


def make_demo(path, initial_pose=None, steps=12):
    env = observation_stack(MockFranka(initial_pose))
    obs, _ = env.reset()
    transitions = []
    for index in range(steps):
        action = np.array([0.5, 0, 0, 0, 0, 0], dtype=np.float32)
        next_obs, _, _, _, _ = env.step(action)
        done = index == steps - 1
        transitions.append(copy.deepcopy(dict(observations=obs, actions=action, next_observations=next_obs,
                                             rewards=float(done), masks=float(not done), dones=done)))
        obs = next_obs
    with open(path, "wb") as stream:
        pickle.dump(transitions, stream)
    return transitions
