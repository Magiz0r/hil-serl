"""Sync saved AutoSERL scalar metrics to W&B without importing robot or learner code.

Run from the repository root. --watch-web also follows new portal experiments.
Only finalized episode labels are logged; pending post-stop labels stay local.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import threading
import time

from reproduction.portal.pilot_training import atomic_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ('autoserl_online_run_v1', 'autoserl_frozen_evaluation_v1')
LEARNER_METRICS = {
    'actor_loss': "['actor']['actor_loss']",
    'entropy': "['actor']['entropy']",
    'temperature': "['actor']['temperature']",
    'critic_loss': "['critic']['critic_loss']",
    'q': "['critic']['predicted_qs']",
    'target_q': "['critic']['target_qs']",
    'reward_batch_mean': "['critic']['rewards']",
}


def read_json(path):
    return json.loads(path.read_text())


def read_lines(path):
    if not path.exists():
        return []
    rows = []
    with path.open() as stream:
        for line in stream:
            if not line.endswith('\n'):
                break  # A writer may still be appending the final line.
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def snapshot(folder):
    """Use final outcome, never the stop cause, for binary task reward."""
    manifest = read_json(folder / 'manifest.json')
    if manifest.get('schema') not in SCHEMAS or manifest.get('synthetic') is not False:
        raise ValueError('Only real AutoSERL training/evaluation runs can be synced')
    evaluation = manifest['schema'] == SCHEMAS[1]
    prefix = 'eval' if evaluation else 'train'
    history = []; successes = []; steps = auto = known_steps = 0
    previous_recoveries = 0; excluded = pending = 0
    for path in sorted(folder.glob('episode_*.json')):
        row = read_json(path)
        if row.get('pending_final_replay_insert') or (evaluation and 'evaluation_valid' not in row):
            pending += 1
            continue
        if row.get('evaluation_valid') is False or row.get('outcome') not in ('success', 'failure'):
            excluded += 1
            continue
        success = int(row['outcome'] == 'success')
        successes.append(success); steps += row['transitions']
        intervention = row.get('automatic_interventions')
        values = {
            'episode': row['episode'] + 1, 'return': float(success), 'success': success,
            'success_rate': sum(successes) / len(successes),
            'success_rate_last10': sum(successes[-10:]) / len(successes[-10:]),
            'steps': row['transitions'], 'environment_steps': steps,
            'stop_reason': row.get('reason') or 'operator_stop',
        }
        if intervention is not None:
            auto += intervention; known_steps += row['transitions']
            values['automatic_interventions'] = intervention
            values['automatic_fraction'] = intervention / row['transitions'] if row['transitions'] else 0.
        if row.get('recoveries') is not None:
            cumulative = row['recoveries']
            values['recoveries'] = cumulative if evaluation else max(0, cumulative - previous_recoveries)
            previous_recoveries = cumulative
        for name in ('condition', 'pair', 'seed'):
            if name in row:
                values[name] = row[name]
        history.append({prefix + '/' + k: v for k, v in values.items()})
    log = folder / 'learner/events.jsonl'
    if not log.exists():
        log = folder / 'events.jsonl'
    learner = []
    for row in read_lines(log):
        if row.get('kind') != 'learning' or evaluation:
            continue
        point = {'learner/updates': row['gradient_updates']}
        for name in ('online_steps', 'intervention_steps', 'time_unix'):
            if name in row:
                point['learner/' + name] = row[name]
        for name, key in LEARNER_METRICS.items():
            value = row.get('metrics', {}).get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                point['learner/' + name] = value
        learner.append(point)
    summary = {'episodes': len(successes), 'successes': sum(successes),
               'excluded_episodes': excluded, 'pending_episodes': pending,
               'environment_steps': steps}
    if successes:
        summary.update(success_rate=sum(successes) / len(successes),
                       mean_return=sum(successes) / len(successes))
    if known_steps:
        summary['automatic_fraction'] = auto / known_steps
    verification = folder / 'weights-verification.json'
    if verification.exists():
        summary['weights_unchanged'] = read_json(verification)['unchanged']
    if evaluation:
        summary['gradient_updates'] = 0
    config = {k: manifest[k] for k in ('schema', 'demo_sha256', 'initial_demo_episodes',
              'algorithm', 'automatic_assistance', 'reset_intervention_state_each_trial',
              'action_sampling', 'success_criterion') if k in manifest}
    config['automatic_assistance'] = manifest.get('automatic_assistance', True)
    config['reward_definition'] = 'operator-confirmed success=1, failure=0; final labels only'
    config['metric_source'] = 'saved local logs; no images, videos, replay or weights uploaded'
    if manifest.get('resume_from'):
        config['resume_from'] = Path(manifest['resume_from']).name
    if manifest.get('resume_checkpoint'):
        config['resume_checkpoint'] = Path(manifest['resume_checkpoint']).name
    if manifest.get('checkpoints'):
        config['checkpoints'] = {name: {k: value for k, value in checkpoint.items() if k != 'path'}
                                 for name, checkpoint in manifest['checkpoints'].items()}
    return dict(prefix=prefix, config=config, episodes=history, learner=learner,
                summary={prefix + '/' + k: v for k, v in summary.items()})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class MetricSync:
    def __init__(self, run):
        self.run = run
        self.episode_cursor = int(run.summary.get('sync/episode_cursor', 0))
        self.learner_cursor = int(run.summary.get('sync/learner_cursor', 0))
        self.episode_digest = run.summary.get('sync/episode_digest')
        self.learner_digest = run.summary.get('sync/learner_digest')
        for prefix in ('train', 'eval'):
            run.define_metric(prefix + '/episode')
            run.define_metric(prefix + '/*', step_metric=prefix + '/episode')
        run.define_metric('learner/updates')
        run.define_metric('learner/*', step_metric='learner/updates')

    def update(self, data):
        for kind in ('episode', 'learner'):
            rows = data['episodes' if kind == 'episode' else kind]
            cursor = getattr(self, kind + '_cursor')
            old_digest = getattr(self, kind + '_digest')
            if cursor > len(rows) or (old_digest and digest(rows[:cursor]) != old_digest):
                raise ValueError('Previously synced finalized metrics changed: ' + kind)
            for row in rows[cursor:]:
                self.run.log(row)
            setattr(self, kind + '_cursor', len(rows))
            setattr(self, kind + '_digest', digest(rows))
            self.run.summary['sync/' + kind + '_cursor'] = len(rows)
            self.run.summary['sync/' + kind + '_digest'] = digest(rows)
        self.run.summary.update(data['summary'])


def selected_folders(paths, watch_web, root=ROOT):
    folders = {path.resolve() for path in paths}
    if watch_web:
        selection = read_json(root / 'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json')
        # Only direct portal runs: nested audit/backup manifests are not experiments.
        for path in (root / 'reproduction/logs/autoserl-web').glob('*/manifest.json'):
            try:
                manifest = read_json(path)
                if (manifest.get('schema') in SCHEMAS and manifest.get('synthetic') is False
                        and manifest.get('demo_sha256') == selection['demo_pickle_sha256']):
                    folders.add(path.parent.resolve())
            except (OSError, ValueError):
                continue
    return sorted(folders)


def sync_id(folder, root=ROOT):
    relative = folder.resolve().relative_to(root / 'reproduction/logs')
    return 'as-' + hashlib.sha256(str(relative).encode()).hexdigest()[:20]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', default=[])
    parser.add_argument('--entity', required=True)
    parser.add_argument('--project', default='autoserl-fr3')
    parser.add_argument('--watch-web', action='store_true')
    parser.add_argument('--interval', type=float, default=5.)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.run and not args.watch_web:
        parser.error('Specify --run or --watch-web')
    if args.interval < 2:
        parser.error('Polling interval must be at least two seconds')
    runtime = ROOT / 'reproduction/runtime/wandb-sync'
    runtime.mkdir(parents=True, exist_ok=True)
    lock = (runtime / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    opened = {}; opened_paths = {}; seen = {}; failed = False
    try:
        while not stop.is_set():
            errors = {}
            for folder in selected_folders(args.run, args.watch_web):
                try:
                    data = snapshot(folder)
                    key = sync_id(folder)
                    signature = digest(data)
                    if not data['episodes'] and not data['learner']:
                        continue
                    if args.dry_run:
                        print(json.dumps(dict(run=folder.name, summary=data['summary'],
                                              learner_points=len(data['learner'])), ensure_ascii=False))
                        continue
                    if seen.get(key) == signature:
                        continue
                    if key not in opened:
                        import wandb
                        cache = runtime / key
                        cache.mkdir(exist_ok=True)
                        run = wandb.init(entity=args.entity, project=args.project, id=key,
                            name=folder.name, job_type='assisted-evaluation' if data['prefix'] == 'eval' else 'online-training',
                            config=data['config'], resume='allow', reinit='create_new', dir=str(cache),
                            settings=wandb.Settings(console='off', disable_code=True, disable_git=True,
                                save_code=False, x_disable_stats=True, x_disable_meta=True,
                                x_disable_machine_info=True, init_timeout=30))
                        opened[key] = MetricSync(run)
                        opened_paths[key] = folder
                        atomic_json(runtime / (key + '.json'), dict(run=str(folder), url=run.url,
                            entity=args.entity, project=args.project, id=key))
                        print(json.dumps(dict(run=folder.name, url=run.url)), flush=True)
                    opened[key].update(data)
                    seen[key] = signature
                except Exception as error:
                    failed = True
                    errors[str(folder)] = str(error)
                    print(json.dumps(dict(run=folder.name, error=str(error))), flush=True)
                    if not args.watch_web:
                        raise
            # End completed runs even when only the worker status (not metrics) changed.
            try:
                live = read_json(ROOT / 'reproduction/runtime/training-status.json')
            except (OSError, ValueError):
                live = {}
            for key in list(opened):
                active = (live.get('output') == str(opened_paths[key]) and
                          live.get('phase') not in ('completed', 'stopped') and
                          time.time() - live.get('heartbeat_unix', 0) < 15)
                if not active:
                    opened.pop(key).run.finish()
            if not args.dry_run:
                atomic_json(runtime / 'status.json', dict(pid=os.getpid(), checked_unix=time.time(),
                    watching=args.watch_web, synced_runs=len(seen), errors=errors))
            if args.dry_run or not args.watch_web:
                break
            stop.wait(args.interval)
    finally:
        for sync in opened.values():
            sync.run.finish(exit_code=1 if failed else 0)
        lock.close()


if __name__ == '__main__':
    main()
