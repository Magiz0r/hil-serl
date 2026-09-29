"""Measure concurrent actor/learner latency using saved data, with no robot I/O."""
import argparse
import json
import pickle
import time
from pathlib import Path

from .bootstrap import ROOT, configure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume-from',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=200)
    args=parser.parse_args();configure()
    import numpy as np
    from .online_env import load_config
    from .train_online import ProcessTraining
    selection,_=load_config(ROOT)
    with (ROOT/selection['demo_path']).open('rb') as stream:demo=pickle.load(stream)
    trainer=ProcessTraining(args.output,demo[0]['observations'],demo,resume_from=args.resume_from,
                            batch_size=256,training_starts=100,capacity=200000)
    (args.output/'manifest.json').write_text(json.dumps(dict(synthetic=True,robot_io=False,
        purpose='Concurrent latency test only; never resume this as a real run'),indent=2)+'\n')
    durations=[];start_updates=trainer.gradient_updates
    try:
        for i in range(args.samples):
            start=time.monotonic()
            action=trainer.sample(demo[i%len(demo)]['observations'])
            elapsed=time.monotonic()-start;durations.append(elapsed)
            assert action.shape==(6,) and np.isfinite(action).all()
            time.sleep(max(0.,.1-elapsed))
    finally:trainer.close()
    rows=[json.loads(x) for x in (args.output/'learner/events.jsonl').read_text().splitlines()]
    learning=[x for x in rows if x['kind']=='learning']
    report=dict(robot_io=False,training_only_on_saved_real_data=True,samples=len(durations),
        batch_size=256,start_updates=start_updates,end_updates=trainer.gradient_updates,
        seconds=dict(median=float(np.median(durations)),p99=float(np.quantile(durations,.99)),
                     maximum=max(durations)),learning_events=len(learning),
        durations_seconds=durations)
    report['passed']=max(durations)<.15 and len(learning)>0 and trainer.gradient_updates>start_updates
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='durations_seconds'}),flush=True)
    if not report['passed']:raise RuntimeError('Concurrent inference latency check failed')


if __name__=='__main__':main()
