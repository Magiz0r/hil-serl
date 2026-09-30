"""Human actions are selected by the existing NUC input owner, never here."""
from reproduction.autoserl.online_env import PortalEnv
from franka_env.envs.wrappers import MultiCameraBinaryRewardClassifierWrapper


class HILPortalEnv(PortalEnv):
    algorithm_id = 'hilserl'
    enable_interventions = False  # No AutoIntervention wrapper or recovery.

    def __init__(self, root, transport=None, *, evaluation=False):
        super().__init__(root, transport, human_intervention=not evaluation)


class ClassifierReward(MultiCameraBinaryRewardClassifierWrapper):
    """Use the upstream reward/done wrapper; retain diagnostics outside reward."""
    def __init__(self,env,classifier):
        self.classifier=classifier;self.judgement=None
        env.reward_source='classifier'
        env.classifier_metadata=classifier.metadata
        super().__init__(env,self.judge)

    def judge(self,obs):
        self.judgement=self.classifier(obs)
        return int(self.judgement['success'])

    def step(self,action):
        obs,reward,done,truncated,info=super().step(action)
        info=dict(info,reward_source='classifier',classifier_probability=self.judgement['probability'],
            classifier_success=bool(reward))
        if reward:info['terminal_reason']='classifier_success'
        # Buttons may stop or review a prediction; they never overwrite reward.
        return obs,reward,bool(done),truncated,info


def relabel_demo(demo,classifier):
    """Match classifier-labelled expert collection without changing the source demo."""
    import copy
    result=[];episode=[];finished=False
    for original in demo:
        if not finished:
            transition=copy.deepcopy(original)
            reward=int(classifier(transition['next_observations'])['success'])
            transition.update(rewards=float(reward),masks=float(not(reward or original['dones'])),
                              dones=bool(reward or original['dones']))
            episode.append(transition)
            if reward:result.extend(episode);finished=True
        if original['dones']:
            if not finished:raise ValueError('分类器未识别示范的成功状态；请核对标注和模型，不能静默使用人工 reward')
            episode=[];finished=False
    if episode or not result:raise ValueError('Expected complete classifier-recognized demonstrations')
    return result
