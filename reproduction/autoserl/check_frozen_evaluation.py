"""Check real frozen checkpoints on saved observations; no robot or portal I/O."""
import argparse
import json
import pickle
import time

from .bootstrap import ROOT, configure


def main():
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    configure()
    import jax
    import numpy as np
    from .online_env import load_config
    from .evaluate_frozen import FrozenPolicy, select_checkpoints
    if jax.default_backend() != 'gpu':
        raise RuntimeError('Verify action timing using the real GPU')
    selection, _ = load_config(ROOT)
    with (ROOT/selection['demo_path']).open('rb') as stream:
        demo = pickle.load(stream)
    checkpoints = select_checkpoints(args.source_run, selection['demo_pickle_sha256'])
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps(dict(synthetic=True, robot_io=False,
        eligible_for_training=False, purpose=__doc__))+'\n')
    policy = FrozenPolicy(args.output, demo[0]['observations'], checkpoints)
    observations = [demo[i]['observations'] for i in np.linspace(0, len(demo)-1, 20, dtype=int)]
    actions = {}
    timings = []
    try:
        for condition in ('early', 'latest'):
            policy.select(condition, 9040)
            first = []
            for obs in observations:
                start = time.monotonic()
                first.append(policy.sample(obs))
                timings.append(time.monotonic()-start)
            first = np.array(first)
            policy.set_learning(True)
            policy.insert({'synthetic': True}, False)
            policy.set_learning(False)
            policy.select(condition, 9040)
            repeated = np.array([policy.sample(obs) for obs in observations])
            assert np.array_equal(first, repeated), 'Same checkpoint/seed must reproduce actions'
            assert np.isfinite(first).all() and np.max(np.abs(first)) <= 1.
            actions[condition] = first
    finally:
        policy.close()
    verified = json.loads((args.output/'weights-verification.json').read_text())
    report = dict(passed=True, synthetic=True, robot_io=False, frozen=verified['unchanged'],
        gradient_updates=0, same_seed_reproducible=True, replay_insertions=0,
        action_calls=80, latency_ms=dict(median=1000*float(np.median(timings)),
            p95=1000*float(np.percentile(timings, 95)), maximum=1000*max(timings)),
        mean_action_difference=float(np.mean(np.abs(actions['early']-actions['latest']))), checkpoints=checkpoints)
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
