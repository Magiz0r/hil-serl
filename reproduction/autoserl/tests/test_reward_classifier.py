import copy
import gzip
import json
import pickle
from pathlib import Path
from types import SimpleNamespace

import gymnasium as gym
import numpy as np
import pytest

from reproduction.hilserl.reward_data import RewardWorkspace,digest
from reproduction.hilserl.reward_classifier import active_metadata,activate,file_hashes,validation_metrics
from reproduction.hilserl.online_env import ClassifierReward,relabel_demo
from reproduction.autoserl.continuous import ContinuousRunner
from test_continuous import Training
from test_online_resume import make_run
from reproduction.autoserl.train_online import restore_run


def workspace(tmp_path):
    config=tmp_path/'reproduction/configs/autoserl';config.mkdir(parents=True)
    (config/'fmb_insertion_images_v1.json').write_text(json.dumps(dict(crops_xywh={k:[0,0,720,720] for k in ('external','wrist')})))
    (config/'fmb_insertion_recovery_v1.json').write_text(json.dumps(dict(demo_pickle_sha256='demo',demo_path='demo.pkl')))
    return RewardWorkspace(tmp_path)


def observation(value):
    return dict(state=np.zeros((1,19),np.float32),**{k:np.full((1,128,128,3),value,np.uint8) for k in ('wrist_1','wrist_2')})


def test_reward_frames_are_unlabelled_until_review_and_split_by_episode(tmp_path):
    ws=workspace(tmp_path)
    rows=[dict(next_observations=observation(i)) for i in (1,2,3)]
    with (tmp_path/'demo.pkl').open('wb') as stream:pickle.dump(rows,stream)
    sid=next(iter(ws.sources()));ws.import_source(sid,'train')
    assert all(r['label'] is None for r in ws.status()['frames'])
    assert not ws.status()['can_train']
    with pytest.raises(ValueError,match='同一回合'):ws.import_source(sid,'validation')
    first=ws.status()['frames'][0]['id'];ws.label(first,1)
    assert ws.status()['counts']['train']['1']==1
    np.testing.assert_array_equal(ws.observation(first)['wrist_1'],observation(1)['wrist_1'])
    assert ws.preview(first)['wrist_1'].startswith('data:image/png;base64,')
    with pytest.raises(ValueError):ws.observation('../../secret')
    with pytest.raises(ValueError):ws.add(observation(1),group='another',split='validation',source={},label=1)


def test_reward_export_has_independent_positive_and_negative_datasets(tmp_path,monkeypatch):
    ws=workspace(tmp_path)
    for i,(split,label) in enumerate((('train',0),('train',1),('validation',0),('validation',1))):
        ws.add(observation(i),group=split,split=split,source={},label=label)
    snapshot=ws.read();reads=[]
    def read_once():
        reads.append(True)
        assert len(reads)==1, 'Concurrent annotation must not change an export snapshot'
        return snapshot
    monkeypatch.setattr(ws,'read',read_once)
    output=tmp_path/'export';manifest=ws.export(output)
    assert manifest['counts']=={'train':{'0':1,'1':1},'validation':{'0':1,'1':1}}
    with (output/'classifier_data/success.pkl').open('rb') as stream:rows=pickle.load(stream)
    assert len(rows)==1 and rows[0]['observations']['wrist_1'][0,0,0,0]==1
    with (output/'validation_data/success.pkl').open('rb') as stream:rows=pickle.load(stream)
    assert rows[0]['observations']['wrist_1'][0,0,0,0]==3


def make_classifier(ws,*,synthetic=False):
    folder=ws.root/'reproduction/logs/hilserl-classifier/test'
    checkpoint=folder/'classifier_ckpt';checkpoint.mkdir(parents=True)
    (checkpoint/'checkpoint_1').write_bytes(b'weights')
    manifest=dict(schema='hilserl_reward_classifier_v1',synthetic=synthetic,context=ws.context(),
        threshold=.85,checkpoint_files=file_hashes(checkpoint),validation=dict(samples=4))
    (folder/'manifest.json').write_text(json.dumps(manifest));return folder


def test_reward_loading_never_falls_back_to_random_or_foreign_weights(tmp_path):
    ws=workspace(tmp_path)
    with pytest.raises(ValueError,match='需要任务奖励分类器'):active_metadata(tmp_path)
    folder=make_classifier(ws,synthetic=True)
    with pytest.raises(ValueError,match='real classifier'):activate(tmp_path,folder)
    m=json.loads((folder/'manifest.json').read_text());m['synthetic']=False
    (folder/'manifest.json').write_text(json.dumps(m));activate(tmp_path,folder)
    assert active_metadata(tmp_path)['identity']==digest(m)
    (folder/'classifier_ckpt/checkpoint_1').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):active_metadata(tmp_path)


class FakeBase(gym.Env):
    human_intervention_enabled=True
    algorithm_id='hilserl'
    def __init__(self,manual_reward):self.manual_reward=manual_reward
    def step(self,action):return observation(1),self.manual_reward,bool(self.manual_reward),False,dict(terminal_reason=None)


@pytest.mark.parametrize('manual,classifier_success',[(1,False),(0,True)])
def test_original_reward_wrapper_overrides_manual_reward(manual,classifier_success):
    predictor=lambda obs:dict(probability=.95 if classifier_success else .1,success=classifier_success)
    predictor.metadata=dict(identity='classifier')
    env=ClassifierReward(FakeBase(manual),predictor)
    _,reward,done,_,info=env.step(np.zeros(6))
    assert reward==int(classifier_success) and done
    assert info['classifier_probability']==(.95 if classifier_success else .1)
    assert env.unwrapped.reward_source=='classifier'


def test_classifier_reward_is_not_rewritten_by_post_stop_review(tmp_path):
    trainer=Training();base=SimpleNamespace(algorithm_id='hilserl',reward_source='classifier')
    runner=ContinuousRunner(base,base,trainer,tmp_path,tmp_path/'runtime',start_paused=True)
    try:
        runner.have_episode=True;runner.pending=(dict(rewards=0.,masks=1.,dones=False),True)
        runner.label='interrupted';runner.operator_review='success'
        runner.save_pending();runner.commit()
        assert trainer.inserted[0][0]['rewards']==0
        row=json.loads((tmp_path/'episode_0000.json').read_text())
        assert row['return_']==0 and row['outcome']=='interrupted' and row['operator_review']=='success'
        assert row['label_source']=='classifier'
    finally:runner.heartbeat_stop.set();runner.heartbeat.join(timeout=2.)


def test_demo_relabelling_matches_classifier_termination_without_mutating_source():
    rows=[dict(next_observations=observation(i),rewards=float(i==3),masks=float(i!=3),dones=i==3) for i in range(4)]
    classifier=lambda obs:dict(success=int(obs['wrist_1'][0,0,0,0])>=2)
    labelled=relabel_demo(rows,classifier)
    assert len(labelled)==3 and labelled[-1]['rewards']==1 and labelled[-1]['masks']==0
    assert len(rows)==4 and rows[2]['rewards']==0
    with pytest.raises(ValueError,match='未识别'):relabel_demo(rows,lambda obs:dict(success=False))


def test_resume_rejects_manual_reward_or_other_classifier(tmp_path):
    run=make_run(tmp_path/'run')
    with pytest.raises(ValueError,match='奖励分类器'):restore_run(run,'demo',reward_identity='wanted')


def test_validation_reports_false_positive_and_negative_at_runtime_threshold():
    result=validation_metrics([0,0,1,1],[.1,.9,.85,.99],.85)
    assert result['false_positive']==1 and result['false_negative']==1
    assert result['precision']==result['recall']==.5


def test_runtime_classifier_input_tree_matches_pre_motion_warmup():
    from reproduction.hilserl.reward_classifier import Classifier
    predictor=Classifier.__new__(Classifier);predictor.threshold=.85
    seen=[]
    def logits(obs):seen.append(set(obs));return np.asarray([2.])
    predictor.logits=logits
    result=predictor(observation(1))
    assert seen==[{'wrist_1','wrist_2'}] and result['success']
