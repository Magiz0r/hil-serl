import json
from pathlib import Path

import pytest

from reproduction.autoserl.wandb_sync import MetricSync, selected_folders, snapshot, sync_id


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def make_run(tmp_path, evaluation=False):
    folder = tmp_path / 'reproduction/logs/autoserl-web/run'
    write(folder / 'manifest.json', dict(schema='autoserl_frozen_evaluation_v1' if evaluation else
          'autoserl_online_run_v1', synthetic=False, demo_sha256='demo', automatic_assistance=True))
    return folder


def episode(folder, index, **extra):
    write(folder / f'episode_{index:04d}.json', dict(episode=index, transitions=10,
          automatic_interventions=4, outcome='failure', reason='force_limit', **extra))


class FakeRun:
    def __init__(self, summary=None):
        self.summary = dict(summary or {})
        self.rows = []
        self.definitions = []

    def define_metric(self, name, **kwargs):
        self.definitions.append((name, kwargs))

    def log(self, row):
        self.rows.append(dict(row))


def test_final_labels_pending_and_exclusions(tmp_path):
    folder = make_run(tmp_path, evaluation=True)
    episode(folder, 0, evaluation_valid=True)
    episode(folder, 1, evaluation_valid=True, pending_final_replay_insert=True)
    episode(folder, 2, evaluation_valid=False)
    episode(folder, 3)  # Evaluation metadata has not yet been annotated by writer.
    data = snapshot(folder)
    assert data['summary']['eval/episodes'] == 1
    assert data['summary']['eval/excluded_episodes'] == 1
    assert data['summary']['eval/pending_episodes'] == 2
    assert not data['learner']
    assert data['summary']['eval/gradient_updates'] == 0
    row = json.loads((folder / 'episode_0001.json').read_text())
    row.update(outcome='success', pending_final_replay_insert=False)
    write(folder / 'episode_0001.json', row)
    data = snapshot(folder)
    assert data['episodes'][1]['eval/return'] == 1
    assert data['episodes'][1]['eval/stop_reason'] == 'force_limit'
    assert data['summary']['eval/success_rate'] == .5


def test_learner_partial_write_and_nonfinite_metric(tmp_path):
    folder = make_run(tmp_path)
    (folder / 'events.jsonl').write_text(json.dumps(dict(kind='learning', gradient_updates=10,
        online_steps=100, metrics={"['critic']['predicted_qs']": 2., "['actor']['entropy']": float('nan')}))
        + '\n{"kind":"learning"')
    data = snapshot(folder)
    assert data['learner'] == [{'learner/updates': 10, 'learner/online_steps': 100, 'learner/q': 2.}]


def test_incremental_sync_resume_and_changed_history_detection(tmp_path):
    folder = make_run(tmp_path)
    episode(folder, 0, recoveries=1)
    run = FakeRun(); sync = MetricSync(run)
    sync.update(snapshot(folder)); sync.update(snapshot(folder))
    assert len(run.rows) == 1
    assert ('train/*', {'step_metric': 'train/episode'}) in run.definitions
    resumed = FakeRun(run.summary); sync = MetricSync(resumed)
    episode(folder, 1, recoveries=3)
    sync.update(snapshot(folder))
    assert len(resumed.rows) == 1 and resumed.rows[0]['train/episode'] == 2
    assert resumed.rows[0]['train/recoveries'] == 2
    row = json.loads((folder / 'episode_0000.json').read_text()); row['outcome'] = 'success'
    write(folder / 'episode_0000.json', row)
    with pytest.raises(ValueError, match='Previously synced'):
        sync.update(snapshot(folder))


def test_watch_does_not_discover_nested_backups_or_foreign_demos(tmp_path):
    folder = make_run(tmp_path)
    write(tmp_path / 'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json',
          {'demo_pickle_sha256': 'demo'})
    manifest = json.loads((folder / 'manifest.json').read_text())
    write(folder / 'backup/manifest.json', manifest)
    write(folder.parent / 'foreign/manifest.json', dict(manifest, demo_sha256='other'))
    write(folder.parent / 'synthetic/manifest.json', dict(manifest, synthetic=True))
    assert selected_folders([], True, tmp_path) == [folder]
    assert sync_id(folder, tmp_path) == sync_id(folder, tmp_path)
    with pytest.raises(ValueError):
        sync_id(tmp_path / 'outside', tmp_path)


def test_real_saved_runs_match_known_results():
    root = Path(__file__).resolve().parents[3]
    folder = root / 'reproduction/logs/autoserl-web/20260925T203736-evaluate-f625fd'
    if not folder.exists():
        pytest.skip('Local real evaluation is not present')
    data = snapshot(folder)
    assert [r['eval/return'] for r in data['episodes']] == [1, 0, 1, 1, 0]
    assert data['summary']['eval/success_rate'] == .6
    assert data['summary']['eval/weights_unchanged'] is True
