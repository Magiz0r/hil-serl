"""Original HIL-SERL training defaults, adapted only to the local FMB interface."""
import gymnasium as gym
import time

from experiments.config import DefaultTrainingConfig
from reproduction.autoserl.bootstrap import ROOT
from reproduction.autoserl.online_env import spaces


OPEN_ENVS=[]


class FakeEnv(gym.Env):
    def __init__(self):self.observation_space,self.action_space=spaces()
    def reset(self,**kwargs):return self.observation_space.sample(),{}
    def step(self,action):raise RuntimeError('Fake learner/classifier environment cannot execute actions')


class AttendedEnv(gym.Wrapper):
    def __init__(self,env):
        super().__init__(env);self.last_command=None;self.last_directory=None

    def reset(self,**kwargs):
        from reproduction.portal.pilot_training import ready_at_home
        base=self.unwrapped;self.env.close();ready_since=None
        print('等待人工退出接触并回到自定义 Home；到位稳定 2 秒后开始。',flush=True)
        while True:
            try:
                data=base.transport.request(dict(operation='observe'))
                response=base.transport.session.get(base.transport.url+'/status',timeout=2.)
                response.raise_for_status();status=response.json()
                after=self.last_command
                if after is not None and status.get('directory')!=self.last_directory:after=-1
                ready=ready_at_home(data,status,base.plan,after)
                if ready:
                    if ready_since is None:ready_since=time.monotonic()
                    if time.monotonic()-ready_since>=2.:break
                else:ready_since=None
            except (ValueError,RuntimeError,OSError):ready_since=None
            time.sleep(.15)
        obs,info=self.env.reset(**kwargs)
        self.last_command=base.transport.request(dict(operation='observe'))['pilot']['online']['command_id']
        self.last_directory=status.get('directory')
        return obs,info

    def step(self,action):
        obs,reward,done,truncated,info=self.env.step(action)
        if info.get('human_intervention'):info['intervene_action']=info['executed_action']
        if done or truncated:self.env.close()
        return obs,reward,done,truncated,info


class TrainConfig(DefaultTrainingConfig):
    image_keys=['wrist_1','wrist_2']
    classifier_keys=image_keys
    proprio_keys=['tcp_pose','tcp_vel','tcp_force','tcp_torque','gripper_pose']
    buffer_period=1000
    checkpoint_period=5000
    steps_per_update=50
    encoder_type='resnet-pretrained'
    setup_mode='single-arm-fixed-gripper'

    def get_environment(self,fake_env=False,save_video=False,classifier=False):
        if fake_env:return FakeEnv()
        from absl import flags
        from reproduction.hilserl.online_env import HILPortalEnv,ClassifierReward
        from reproduction.hilserl.reward_classifier import load_active
        evaluation='eval_checkpoint_step' in flags.FLAGS and flags.FLAGS['eval_checkpoint_step'].value>0
        reward=load_active(ROOT) if classifier else None
        env=HILPortalEnv(ROOT,evaluation=evaluation)
        wrapped=AttendedEnv(ClassifierReward(env,reward) if reward is not None else env)
        OPEN_ENVS.append(wrapped)
        return wrapped
