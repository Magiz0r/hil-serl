import gzip
import json
import pickle

import pytest

from reproduction.autoserl.train_online import restore_run


def make_run(path, *, parent=None, synthetic=False, separate=False, updates=2):
    path.mkdir()
    (path/'manifest.json').write_text(json.dumps(dict(synthetic=synthetic,demo_sha256='demo',
        resume_from=str(parent) if parent else None)))
    target=path/'learner' if separate else path
    target.mkdir(exist_ok=True)
    (target/f'checkpoint_{updates:08d}.msgpack').write_bytes(b'weights')
    events=path/('actor-events.jsonl' if separate else 'events.jsonl')
    events.write_text(json.dumps(dict(kind='step',episode=0,action_index=0,auto_intervention=separate))+'\n')
    with gzip.open(path/'episode_0000.pkl.gz','wb') as stream:pickle.dump([dict(id=path.name)],stream)
    return path


def test_resume_preserves_prior_data_and_intervention_flags(tmp_path):
    first=make_run(tmp_path/'first')
    second=make_run(tmp_path/'second',parent=first,separate=True,updates=55)
    checkpoint,updates,rows=restore_run(second,'demo')
    assert checkpoint==second/'learner/checkpoint_00000055.msgpack'
    assert updates==55
    assert rows==[({'id':'first'},False),({'id':'second'},True)]


@pytest.mark.parametrize('synthetic,sha',[(True,'demo'),(False,'wrong-demo')])
def test_rejects_synthetic_or_changed_demo(tmp_path,synthetic,sha):
    run=make_run(tmp_path/'run',synthetic=synthetic)
    with pytest.raises(ValueError,match='same real single demo'):restore_run(run,sha)


def test_cyclic_resume_rejected(tmp_path):
    run=tmp_path/'run'
    make_run(run,parent=run)
    with pytest.raises(ValueError,match='Cyclic'):restore_run(run,'demo')


def test_evaluation_cannot_be_loaded_into_training(tmp_path):
    run=make_run(tmp_path/'evaluation')
    path=run/'manifest.json'
    manifest=json.loads(path.read_text())
    manifest['eligible_for_training']=False
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Evaluation data'):restore_run(run,'demo')


def test_selected_checkpoint_excludes_future_data_and_separates_partial_image_history(tmp_path):
    run=make_run(tmp_path/'source',updates=90)
    old=run/'checkpoint_00000042.msgpack';old.write_bytes(b'old')
    events=[dict(kind='step',episode=0,action_index=i,auto_intervention=False) for i in range(3)]
    events += [dict(kind='checkpoint',path=old.name,online_steps=2,gradient_updates=42)]
    (run/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in events))
    ts=[dict(id=i,dones=i==2,masks=float(i!=2),rewards=float(i==2)) for i in range(3)]
    with gzip.open(run/'episode_0000.pkl.gz','wb') as f:pickle.dump(ts,f)
    checkpoint,updates,rows=restore_run(run,'demo',checkpoint_path=old)
    assert checkpoint==old and updates==42 and len(rows)==2
    assert rows[-1][0]['dones'] is True and rows[-1][0]['masks']==1
    assert sum(t['rewards'] for t,_ in rows)==0
    # A later resume must preserve the selected ancestor boundary, not recover
    # the third transition just because it exists in the ancestor directory.
    child=make_run(tmp_path/'child',parent=run,updates=100)
    manifest=json.loads((child/'manifest.json').read_text());manifest['resume_checkpoint']=str(old)
    (child/'manifest.json').write_text(json.dumps(manifest))
    _,_,again=restore_run(child,'demo')
    assert [t['id'] for t,_ in again]==[0,1,'child']


def test_resume_never_invents_failure_for_unlabelled_terminal(tmp_path):
    run=make_run(tmp_path/'pending')
    events=[dict(kind='step',episode=0,action_index=i,auto_intervention=False) for i in range(2)]
    (run/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in events))
    (run/'episode_0000.json').write_text(json.dumps(dict(outcome='pending',pending_final_replay_insert=True)))
    with gzip.open(run/'episode_0000.pkl.gz','wb') as f:
        pickle.dump([dict(rewards=0.,masks=1.,dones=False),dict(rewards=0.,masks=0.,dones=True)],f)
    _,_,rows=restore_run(run,'demo')
    assert rows==[({'rewards':0.,'masks':1.,'dones':True},False)]
