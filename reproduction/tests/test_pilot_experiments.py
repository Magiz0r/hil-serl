import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reproduction.pilot_experiments import ExperimentStore, ExperimentManager


def write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data))


def fixture_store(tmp_path):
    write(tmp_path/'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json',
          dict(demo_pickle_sha256='demo',demo_path='one-demo.pkl'))
    root=tmp_path/'reproduction/logs/real'
    write(root/'manifest.json',dict(schema='autoserl_online_run_v1',synthetic=False,demo_sha256='demo'))
    (root/'learner').mkdir()
    (root/'learner/checkpoint_00000042.msgpack').write_bytes(b'weights')
    return ExperimentStore(tmp_path),root


def test_catalog_excludes_synthetic_foreign_demo_and_symlink_models(tmp_path):
    store,root=fixture_store(tmp_path)
    write(root.parent/'synthetic/manifest.json',dict(schema='autoserl_online_run_v1',synthetic=True,demo_sha256='demo'))
    write(root.parent/'foreign/manifest.json',dict(schema='autoserl_online_run_v1',synthetic=False,demo_sha256='different'))
    (root.parent/'foreign/checkpoint_00000099.msgpack').write_bytes(b'weights')
    (root/'learner/checkpoint_00000043.msgpack').symlink_to(root.parent/'foreign/checkpoint_00000099.msgpack')
    catalog=store.catalog()
    assert len(catalog['runs'])==2
    models=[m for r in catalog['runs'] for m in r['checkpoints']]
    assert len(models)==1 and models[0]['updates']==42
    with pytest.raises(ValueError):store.model('../../etc/passwd')


def test_final_labels_override_stop_reason_and_excluded_trials_do_not_count(tmp_path):
    store,root=fixture_store(tmp_path)
    write(root/'episode_0000.json',dict(episode=0,outcome='success',reason='force_limit',transitions=10,automatic_interventions=4,recoveries=2))
    write(root/'episode_0001.json',dict(episode=1,outcome='failure',reason='force_limit',transitions=20,automatic_interventions=5,recoveries=3))
    write(root/'episode_0002.json',dict(episode=2,outcome='failure',reason=None,transitions=7,automatic_interventions=0,evaluation_valid=False))
    (root/'learner/events.jsonl').write_text(json.dumps(dict(kind='learning',gradient_updates=42,online_steps=30,metrics={"['critic']['predicted_qs']":2.}))+'\n{"partial":')
    rid=store.catalog()['runs'][0]['id'];data=store.metrics(rid)
    assert data['summary']['episodes']==2 and data['summary']['excluded_episodes']==1
    assert data['summary']['mean_return']==.5 and data['summary']['successes']==1
    assert data['summary']['automatic_fraction']==.3
    assert data['episodes'][1]['recoveries']==1
    assert data['episodes'][0]['return_']==1
    assert data['learner'][0]['q']==2


@pytest.mark.parametrize('mode',['fresh','resume','evaluate','evaluate_unassisted'])
def test_prepare_never_starts_motion_and_selects_exact_model(tmp_path,mode):
    store,root=fixture_store(tmp_path)
    worker=SimpleNamespace(pid=os.getpid(),poll=lambda:None,returncode=None)
    spawn=Mock(return_value=worker);manager=ExperimentManager(store,popen=spawn)
    mid=store.catalog()['runs'][0]['checkpoints'][0]['id']
    manager.prepare(dict(action='prepare',mode=mode,model_id=None if mode=='fresh' else mid,episodes=5))
    argv=spawn.call_args.args[0]
    assert '--start-at-home' not in argv
    if mode in ('evaluate','evaluate_unassisted'):assert '--checkpoint' in argv and '--episodes' in argv
    else:assert '--start-paused' in argv and '--continuous' in argv
    if mode=='fresh':assert '--resume-from' not in argv and '--resume-checkpoint' not in argv
    if mode=='resume':assert argv[argv.index('--resume-checkpoint')+1]==str(root/'learner/checkpoint_00000042.msgpack')
    assert ('--disable-automatic-assistance' in argv)==(mode=='evaluate_unassisted')
    assert not root.joinpath('episode_0000.json').exists()


def test_manager_rejects_duplicate_prepare_and_never_stops_unowned_process(tmp_path,monkeypatch):
    store,_=fixture_store(tmp_path);spawn=Mock();manager=ExperimentManager(store,popen=spawn)
    monkeypatch.setattr(manager,'status',lambda:dict(can_prepare=False))
    with pytest.raises(ValueError,match='先结束'):manager.prepare(dict(action='prepare',mode='fresh',model_id=None,episodes=5))
    spawn.assert_not_called()
    kill=Mock();monkeypatch.setattr(os,'kill',kill)
    with pytest.raises(ValueError):manager.stop()
    kill.assert_not_called()


def test_unlabelled_protective_stop_is_pending_not_a_failed_return(tmp_path):
    store,root=fixture_store(tmp_path)
    write(root/'episode_0000.json',dict(episode=0,outcome='success',transitions=10))
    write(root/'episode_0001.json',dict(episode=1,outcome='pending',reason='force_limit',
        transitions=20,pending_final_replay_insert=True))
    data=store.metrics(store.catalog()['runs'][0]['id'])
    assert data['summary']['episodes']==1 and data['summary']['success_rate']==1.
    assert data['summary']['mean_return']==1. and data['summary']['pending_episodes']==1
    assert data['summary']['excluded_episodes']==0
    assert data['episodes'][1]['pending_label'] and not data['episodes'][1]['valid']
    assert data['episodes'][1]['return_'] is None
