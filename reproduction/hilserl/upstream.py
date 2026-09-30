"""Run the repository's original scripts with our task-specific environment config."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import runpy
import sys
import types

from reproduction.autoserl.bootstrap import ROOT, configure


def flag_value(arguments,name,default=None):
    value=default
    for i,item in enumerate(arguments):
        if item.startswith(name+'='):value=item.split('=',1)[1]
        elif item==name and i+1<len(arguments):value=arguments[i+1]
    return value


def check_policy_checkpoint(folder,*,actor,evaluation_step):
    if evaluation_step<0:raise ValueError('Evaluation checkpoint step must be nonnegative')
    if evaluation_step:
        if not actor:raise ValueError('Evaluation requires --actor')
        if not (folder/f'checkpoint_{evaluation_step}').exists():
            raise ValueError('Requested evaluation checkpoint does not exist; refusing random-weight fallback')
    if folder.exists():
        if not any(p.name.removeprefix('checkpoint_').isdigit() for p in folder.glob('checkpoint_*')):
            raise ValueError('Existing run has no saved policy checkpoint; use a new output path')
        if actor and not evaluation_step and not any((folder/'buffer').glob('*.pkl')):
            raise ValueError('Original actor resume requires saved replay buffers')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('script',choices=('train_reward_classifier','train_rlpd','record_demos','record_success_fail'))
    parser.add_argument('--execute-attended-robot',action='store_true')
    args,remaining=parser.parse_known_args()
    actor=any(x=='--actor' or x in ('--actor=true','--actor=True','--actor=1') for x in remaining)
    learner=any(x=='--learner' or x in ('--learner=true','--learner=True','--learner=1') for x in remaining)
    help_requested=any(x.startswith('--help') for x in remaining)
    if args.script=='train_rlpd' and not help_requested and actor==learner:
        parser.error('Choose exactly one of --actor and --learner')
    robot=args.script in ('record_demos','record_success_fail') or (args.script=='train_rlpd' and actor)
    if robot and not help_requested and not args.execute_attended_robot:parser.error('Robot entry requires --execute-attended-robot')
    # Fixed experiment identity: never run another task's poses/controllers by accident.
    if any(x.startswith('--exp_name') for x in remaining):parser.error('This entry fixes exp_name=fmb_portal')
    if args.script=='train_rlpd' and not help_requested:
        from reproduction.hilserl.reward_classifier import active_metadata
        reward=active_metadata(ROOT)
        checkpoint=flag_value(remaining,'--checkpoint_path')
        if not checkpoint:parser.error('Specify --checkpoint_path for original actor/learner runs')
        folder=Path(checkpoint).resolve();sidecar=folder.with_name(folder.name+'.hilserl.json')
        check_policy_checkpoint(folder,actor=actor,evaluation_step=int(flag_value(remaining,'--eval_checkpoint_step',0)))
        identity=dict(schema='hilserl_original_entry_v1',reward_identity=reward['identity'],
            image_context=reward['manifest']['context'],
            actor_source_sha256=hashlib.sha256((ROOT/'examples/train_rlpd.py').read_bytes()).hexdigest())
        if sidecar.exists():
            if json.loads(sidecar.read_text())!=identity:raise ValueError('Original run classifier/config/source changed; use a new checkpoint path')
        else:
            if folder.exists():raise ValueError('Existing checkpoints have no classifier provenance; use a new output path')
            sidecar.parent.mkdir(parents=True,exist_ok=True)
            with sidecar.open('x') as stream:json.dump(identity,stream,indent=2)
    configure()
    sys.path.insert(0,str(ROOT/'examples'))
    from reproduction.hilserl.config import TrainConfig
    # Import only this task: the upstream all-task registry initializes other
    # tasks' X11 keyboard dependencies even for an offline classifier learner.
    registry=types.ModuleType('experiments.mappings')
    registry.CONFIG_MAPPING={'fmb_portal':TrainConfig}
    sys.modules['experiments.mappings']=registry
    sys.argv=[args.script,'--exp_name=fmb_portal',*remaining]
    claim=None
    try:
        if robot and not help_requested:
            runtime=ROOT/'reproduction/runtime';runtime.mkdir(exist_ok=True)
            claim=(runtime/'autoserl-actor.lock').open('a');fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runpy.run_path(str(ROOT/'examples'/f'{args.script}.py'),run_name='__main__')
    finally:
        from reproduction.hilserl.config import OPEN_ENVS
        for env in OPEN_ENVS:env.close()
        if claim:claim.close()


if __name__=='__main__':main()
