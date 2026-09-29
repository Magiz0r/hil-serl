"""Two synthetic episodes on one actor/learner pair; no physical robot I/O."""
import argparse
import json
from pathlib import Path
import pickle
import tempfile
import time

from .bootstrap import configure,ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();configure()
    import numpy as np
    from .online_smoke import SyntheticPortal
    from .online_env import PortalEnv,PortalSignals
    from .intervention import AutoIntervention
    from .train_online import ProcessTraining
    from .continuous import ContinuousRunner
    with tempfile.TemporaryDirectory(prefix='autoserl-continuous-test-') as temporary:
        temp=Path(temporary);transport=SyntheticPortal(ROOT,temp);base=PortalEnv(ROOT,transport)
        selection=base.selection
        with (ROOT/selection['demo_path']).open('rb') as stream:demo=pickle.load(stream)
        trainer=ProcessTraining(args.output,demo[0]['observations'],demo,batch_size=4,training_starts=16,capacity=1024,start_paused=True)
        (args.output/'manifest.json').write_text(json.dumps(dict(synthetic=True,robot_io=False,purpose='Continuous software test'))+'\n')
        env=AutoIntervention(base,np.zeros(6),expert=PortalSignals(),demo_path=ROOT/selection['demo_path'],
            demo_initial_tcp_pose=selection['demo_initial_tcp_pose'],recover_point0=selection['recover_point0'],
            recover_point1=selection['recover_point1'],**selection['control_parameters'])
        runner=ContinuousRunner(base,env,trainer,args.output,temp/'mailbox');runner.status=lambda:{'directory':None}
        pid=trainer.process.pid
        try:
            for episode in range(2):
                base.transport=SyntheticPortal(ROOT,temp)
                runner.run_episode()
                assert runner.label=='success' and len(runner.trajectory)==119
                runner.commit()
                assert trainer.process.pid==pid and trainer.process.is_alive()
                with (args.output/'learner/events.jsonl').open() as stream:
                    before=[json.loads(x) for x in stream]
                time.sleep(.5)
                trainer.save();time.sleep(.3)
                rows=[json.loads(x) for x in (args.output/'learner/events.jsonl').read_text().splitlines()]
                saved=[x for x in rows if x['kind']=='checkpoint']
                assert saved[-1]['online_steps']==(episode+1)*120
                # A barrier checkpoint after pause must not advance learner weights.
                settled=saved[-1]['gradient_updates'];trainer.save();time.sleep(.3)
                rows=[json.loads(x) for x in (args.output/'learner/events.jsonl').read_text().splitlines()]
                assert [x for x in rows if x['kind']=='checkpoint'][-1]['gradient_updates']==settled
        finally:
            runner.heartbeat_stop.set();runner.heartbeat.join(timeout=2.);base.close();trainer.close()
        report=dict(passed=True,synthetic=True,robot_io=False,episodes=2,transitions=240,
                    learner_pid_unchanged=True,updates=trainer.gradient_updates,pause_verified=True)
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
