"""Attended AutoSERL actor and asynchronous pixel-SAC learner on the local portal."""
import argparse
import copy
import fcntl
import multiprocessing
import os
import gzip
import json
from pathlib import Path
import pickle
import queue
import threading
import time

from .bootstrap import configure, ROOT


class Training:
    def __init__(self, output, sample_obs, demo, *, batch_size=256, training_starts=100,
                 capacity=200000, seed=42, initial_checkpoint=None, initial_updates=0,start_paused=False):
        import jax
        import numpy as np
        from .online_env import IMAGE_KEYS, spaces
        from serl_launcher.data.replay_buffer import ReplayBuffer
        from serl_launcher.data.memory_efficient_replay_buffer import MemoryEfficientReplayBuffer
        from serl_launcher.utils.launcher import make_sac_pixel_agent
        from serl_launcher.utils.train_utils import concat_batches
        self.jax,self.np,self.concat=jax,np,concat_batches
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.queue=queue.Queue(maxsize=2048);self.stop=threading.Event();self.error=None
        self.batch_size=batch_size;self.training_starts=training_starts
        observation_space,action_space=spaces()
        self.expert=ReplayBuffer(observation_space,action_space,capacity=capacity)
        self.online=MemoryEfficientReplayBuffer(observation_space,action_space,capacity=capacity,pixel_keys=IMAGE_KEYS)
        self.expert.seed(seed);self.online.seed(seed+1)
        for transition in demo:self.expert.insert(transition)
        self.agent=make_sac_pixel_agent(seed,sample_obs,np.zeros(6,np.float32),image_keys=IMAGE_KEYS,
                                       encoder_type='resnet-pretrained',discount=.97)
        if initial_checkpoint:
            from flax import serialization
            self.agent=self.agent.replace(state=serialization.from_bytes(self.agent.state,Path(initial_checkpoint).read_bytes()))
        self.actor_agent=self.agent;self.rng=jax.random.PRNGKey(seed+2)
        self.gradient_updates=initial_updates;self.online_steps=0;self.intervention_steps=0
        self.learning_enabled=not start_paused
        self.critic=frozenset({'critic'});self.full=frozenset({'critic','actor','temperature'})
        self.journal_lock=threading.Lock()
        self.events=(self.output/'events.jsonl').open('x',buffering=1)
        self.event('initializing',initial_demo_episodes=1,demo_transitions=len(demo),
            batch_size=batch_size,training_starts=training_starts,capacity=capacity,
            expert_fraction=.5,critic_to_actor_ratio=2,discount=.97,
            devices=[str(x) for x in jax.devices()], xla_flags=os.environ.get('XLA_FLAGS',''))
        print('Compiling actor and learner before permitting robot actions...',flush=True)
        self.sample(sample_obs)
        batch=self.concat(self.expert.sample(batch_size//2),self.expert.sample(batch_size//2),axis=0)
        # Compile using a disposable update. Initial online weights remain untouched.
        warmed=self.agent
        for networks in (self.critic,self.full,self.critic,self.full):
            warmed,metrics=warmed.update(batch,networks_to_update=networks)
            jax.block_until_ready((warmed.state.params,metrics))
            if not all(np.isfinite(np.asarray(x)).all() for x in jax.tree_util.tree_leaves(metrics)):
                raise ValueError('Non-finite warmup metrics')
        self.event('ready',weights_updated=False)
        self.thread=threading.Thread(target=self.learn,daemon=True)
        self.thread.start()

    def event(self,kind,**data):
        with self.journal_lock:
            self.events.write(json.dumps(dict(kind=kind,time_unix=time.time(),**data),allow_nan=False)+'\n')

    def sample(self,obs):
        if self.error:raise RuntimeError(self.error)
        self.rng,key=self.jax.random.split(self.rng)
        return self.np.asarray(self.jax.device_get(self.actor_agent.sample_actions(obs,seed=key,argmax=False)))

    def insert(self,transition,intervention):
        if self.error:raise RuntimeError(self.error)
        self.queue.put((copy.deepcopy(transition),bool(intervention)),timeout=1.)

    def checkpoint(self):
        from flax import serialization
        path=self.output/f'checkpoint_{self.gradient_updates:08d}.msgpack'
        tmp=path.with_suffix('.tmp');tmp.write_bytes(serialization.to_bytes(self.agent.state));tmp.replace(path)
        self.event('checkpoint',path=path.name,gradient_updates=self.gradient_updates,online_steps=self.online_steps)

    def learn(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                while True:
                    try:item=self.queue.get_nowait()
                    except queue.Empty:break
                    if isinstance(item,dict):
                        if item['control']=='learning':self.learning_enabled=item['enabled']
                        elif item['control']=='checkpoint':self.checkpoint()
                        continue
                    t,intervened=item
                    self.online.insert(t);self.online_steps+=1
                    if intervened:self.expert.insert(t);self.intervention_steps+=1
                if self.online_steps<self.training_starts or not self.learning_enabled:
                    if self.stop.is_set():break
                    self.stop.wait(.02);continue
                for networks in (self.critic,self.full):
                    batch=self.concat(self.expert.sample(self.batch_size//2),self.online.sample(self.batch_size//2),axis=0)
                    self.agent,metrics=self.agent.update(batch,networks_to_update=networks)
                self.jax.block_until_ready(metrics)
                self.gradient_updates+=1
                if self.gradient_updates%50==0:self.actor_agent=self.agent
                if self.gradient_updates==1 or self.gradient_updates%10==0:
                    values={self.jax.tree_util.keystr(k):float(self.np.asarray(v).mean())
                            for k,v in self.jax.tree_util.tree_flatten_with_path(metrics)[0]}
                    if not all(self.np.isfinite(x) for x in values.values()):raise ValueError('Non-finite learner metrics')
                    self.event('learning',gradient_updates=self.gradient_updates,online_steps=self.online_steps,
                               intervention_steps=self.intervention_steps,metrics=values)
                if self.gradient_updates%500==0:self.checkpoint()
            self.checkpoint()
        except BaseException as error:
            self.error=repr(error);self.event('learner_error',error=self.error);self.stop.set()

    def close(self):
        self.stop.set();self.thread.join(timeout=60.)
        if self.thread.is_alive():raise RuntimeError('Learner did not finish saving')
        if self.error:raise RuntimeError(self.error)
        self.events.close()


def learner_process(output, sample_obs, demo, config, incoming, outgoing, restored):
    """A separate CUDA context prevents training dispatch from blocking the actor."""
    configure()
    training=None
    try:
        training=Training(Path(output)/'learner',sample_obs,demo,**config)
        for transition,intervened in restored:training.insert(transition,intervened)
        outgoing.put(dict(kind='ready',params=training.jax.device_get(training.actor_agent.state.params)))
        published=training.gradient_updates;stopping=False
        while not stopping:
            try:
                item=incoming.get(timeout=.02)
                if item is None:stopping=True
                elif isinstance(item,dict):training.queue.put(item,timeout=1.)
                else:training.insert(*item)
            except queue.Empty:pass
            if training.error:raise RuntimeError(training.error)
            if training.gradient_updates>=published+50:
                outgoing.put(dict(kind='params',params=training.jax.device_get(training.actor_agent.state.params),
                    gradient_updates=training.gradient_updates))
                published=training.gradient_updates
        training.close()
        outgoing.put(dict(kind='closed',gradient_updates=training.gradient_updates,online_steps=training.online_steps))
        training=None
    except BaseException as error:
        outgoing.put(dict(kind='error',error=repr(error)))
    finally:
        if training is not None:
            try:training.close()
            except Exception:pass


def checkpoint_metadata(previous, checkpoint_path, demo_sha256):
    """Validate a selected real checkpoint without loading replay images."""
    previous=Path(previous).resolve();checkpoint=Path(checkpoint_path).resolve()
    manifest=json.loads((previous/'manifest.json').read_text())
    if manifest['synthetic'] or manifest['demo_sha256']!=demo_sha256 or not manifest.get('eligible_for_training',True):
        raise ValueError('Checkpoint must belong to training with the same real single demo')
    candidates=list((previous/'learner').glob('checkpoint_*.msgpack')) or list(previous.glob('checkpoint_*.msgpack'))
    if checkpoint not in [p.resolve() for p in candidates] or not checkpoint.is_relative_to(previous):
        raise ValueError('Checkpoint does not belong to the selected training run')
    log=previous/'learner/events.jsonl'
    if not log.exists():log=previous/'events.jsonl'
    matches=[]
    for line in log.read_text().splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if row['kind']=='checkpoint' and row['path']==checkpoint.name:matches.append(row)
    if not matches:raise ValueError('Checkpoint has no replay boundary record')
    return checkpoint,int(checkpoint.stem.split('_')[-1]),matches[-1]['online_steps']


def restore_run(previous, demo_sha256, seen=None, *, checkpoint_path=None):
    """Restore real trajectories, including earlier runs in a resume chain."""
    previous=Path(previous).resolve();seen=set() if seen is None else seen
    if previous in seen:raise ValueError('Cyclic resume history')
    seen.add(previous)
    manifest=json.loads((previous/'manifest.json').read_text())
    if not manifest.get('eligible_for_training', True):
        raise ValueError('Evaluation data cannot be resumed as a training run')
    if manifest['synthetic'] or manifest['demo_sha256']!=demo_sha256:
        raise ValueError('Resume run does not use the same real single demo')
    restored=[]
    if manifest.get('resume_from'):
        ancestor=Path(manifest['resume_from'])
        if not ancestor.is_absolute():ancestor=ROOT/ancestor
        _,_,restored=restore_run(ancestor,demo_sha256,seen,checkpoint_path=manifest.get('resume_checkpoint'))
    checkpoints=sorted((previous/'learner').glob('checkpoint_*.msgpack')) or sorted(previous.glob('checkpoint_*.msgpack'))
    if not checkpoints:raise ValueError(f'No saved learner checkpoint in {previous}')
    checkpoint=Path(checkpoint_path).resolve() if checkpoint_path else checkpoints[-1]
    if checkpoint.resolve() not in [p.resolve() for p in checkpoints]:
        raise ValueError('Checkpoint does not belong to the selected training run')
    updates=int(checkpoint.stem.split('_')[-1])
    events=previous/'actor-events.jsonl'
    if not events.exists():events=previous/'events.jsonl'
    flags={}
    for line in events.read_text().splitlines():
        row=json.loads(line)
        if row['kind']=='step':flags[(row['episode'],row['action_index'])]=row['auto_intervention']
    for path in sorted(previous.glob('episode_*.pkl.gz')):
        episode=int(path.name.split('_')[1].split('.')[0])
        with gzip.open(path,'rb') as stream:transitions=pickle.load(stream)
        metadata=previous/f'episode_{episode:04d}.json'
        if metadata.exists() and json.loads(metadata.read_text()).get('pending_final_replay_insert'):
            # The final transition is still waiting for an operator label and
            # has never entered replay. Preserve only the already inserted prefix.
            transitions=transitions[:-1]
            if transitions:
                transitions[-1]=copy.deepcopy(transitions[-1])
                transitions[-1]['dones']=True
        restored.extend((t,flags[(episode,i)]) for i,t in enumerate(transitions))
    if checkpoint_path:
        _,_,count=checkpoint_metadata(previous,checkpoint,demo_sha256)
        if not 0<=count<=len(restored):raise ValueError('Checkpoint replay data is incomplete')
        restored=restored[:count]
        if restored and not restored[-1][0].get('dones',False):
            # A snapshot may end mid-episode. Preserve bootstrapping (mask), but
            # start fresh image history for the new physical reset.
            transition,flag=restored[-1]
            transition=copy.deepcopy(transition);transition['dones']=True
            restored[-1]=(transition,flag)
    return checkpoint,updates,restored


class ProcessTraining:
    def __init__(self, output, sample_obs, demo, *, resume_from=None, resume_checkpoint=None, **config):
        import jax
        import numpy as np
        from .online_env import IMAGE_KEYS
        from serl_launcher.utils.launcher import make_sac_pixel_agent
        self.jax,self.np=jax,np;self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.events=(self.output/'actor-events.jsonl').open('x',buffering=1)
        self.journal_lock=threading.Lock();self.error=None;self.gradient_updates=0
        restored=[];self.closed=False
        if resume_from:
            from .online_env import load_config
            selection,_=load_config(ROOT)
            checkpoint,updates,restored=restore_run(resume_from,selection['demo_pickle_sha256'],checkpoint_path=resume_checkpoint)
            config.update(initial_checkpoint=str(checkpoint),initial_updates=updates)
            self.gradient_updates=updates
        ctx=multiprocessing.get_context('spawn')
        self.incoming=ctx.Queue(maxsize=2048);self.outgoing=ctx.Queue(maxsize=4)
        self.process=ctx.Process(target=learner_process,args=(str(self.output),sample_obs,demo,config,self.incoming,self.outgoing,restored))
        self.process.start()
        self.actor_agent=make_sac_pixel_agent(42,sample_obs,np.zeros(6,np.float32),image_keys=IMAGE_KEYS,
            encoder_type='resnet-pretrained',discount=.97)
        self.rng=jax.random.PRNGKey(44)
        while True:
            try:message=self.outgoing.get(timeout=.5)
            except queue.Empty:
                if not self.process.is_alive():raise RuntimeError('Learner exited during initialization')
                continue
            if message['kind']=='error':raise RuntimeError(message['error'])
            if message['kind']=='ready':
                self.actor_agent=self.actor_agent.replace(state=self.actor_agent.state.replace(params=jax.device_put(message['params'])))
                break
        self.sample(sample_obs)
        self.event('ready',separate_processes=True,learner_pid=self.process.pid,
            restored_transitions=len(restored),restored_gradient_updates=self.gradient_updates)

    event=Training.event

    def sample(self, obs):
        self.poll()
        self.rng,key=self.jax.random.split(self.rng)
        return self.np.asarray(self.jax.device_get(self.actor_agent.sample_actions(obs,seed=key,argmax=False)))

    def poll(self):
        while True:
            try:message=self.outgoing.get_nowait()
            except queue.Empty:break
            if message['kind']=='error':self.error=message['error']
            if message['kind']=='params':
                self.actor_agent=self.actor_agent.replace(state=self.actor_agent.state.replace(params=self.jax.device_put(message['params'])))
                self.gradient_updates=message['gradient_updates']
            if message['kind']=='closed':self.gradient_updates=message['gradient_updates']
        if self.error:raise RuntimeError(self.error)
        if not self.process.is_alive() and self.process.exitcode is not None:raise RuntimeError('Learner exited unexpectedly')

    def insert(self, transition, intervention):
        self.poll();self.incoming.put((transition,bool(intervention)),timeout=1.)

    def set_learning(self, enabled):
        self.poll();self.incoming.put(dict(control='learning',enabled=bool(enabled)),timeout=1.)

    def save(self):
        self.poll();self.incoming.put(dict(control='checkpoint'),timeout=1.)

    def close(self):
        if self.closed:return
        self.incoming.put(None,timeout=1.)
        deadline=time.monotonic()+60
        while self.process.is_alive() and time.monotonic()<deadline:
            try:
                msg=self.outgoing.get(timeout=.2)
                if msg['kind']=='closed':self.gradient_updates=msg['gradient_updates']
                if msg['kind']=='error':self.error=msg['error']
            except queue.Empty:pass
        self.process.join(timeout=1.)
        if self.process.is_alive():raise RuntimeError('Learner did not finish saving')
        while True:
            try:msg=self.outgoing.get_nowait()
            except queue.Empty:break
            if msg['kind']=='closed':self.gradient_updates=msg['gradient_updates']
            if msg['kind']=='error':self.error=msg['error']
        if self.process.exitcode:self.error=self.error or f'Learner exit code {self.process.exitcode}'
        self.closed=True
        self.events.close()
        if self.error:raise RuntimeError(self.error)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-attended-online',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--episodes',type=int,default=1)
    parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--training-starts',type=int,default=100)
    parser.add_argument('--capacity',type=int,default=200000)
    parser.add_argument('--resume-from',type=Path)
    parser.add_argument('--resume-checkpoint',type=Path)
    parser.add_argument('--start-paused',action='store_true')
    parser.add_argument('--continuous',action='store_true',help='Keep models loaded; auto-start after attended custom Home reset')
    args=parser.parse_args()
    if args.resume_checkpoint and not args.resume_from:parser.error('Checkpoint requires its source run')
    if args.start_paused and not args.continuous:parser.error('Paused preparation requires continuous mode')
    if args.episodes<1 or args.batch_size<2 or args.batch_size%2 or args.training_starts<1:
        parser.error('Invalid training settings')
    configure()
    import numpy as np
    from .intervention import AutoIntervention
    from .online_env import PortalEnv,PortalSignals
    runtime=ROOT/'reproduction/runtime';runtime.mkdir(exist_ok=True)
    claim=(runtime/'autoserl-actor.lock').open('a')
    fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
    base=PortalEnv(ROOT);selection=base.selection
    with (ROOT/selection['demo_path']).open('rb') as f:demo=pickle.load(f)
    # Compile from the real demo's shapes before connecting physical control.
    sample_obs=demo[0]['observations']
    training=ProcessTraining(args.output,sample_obs,demo,batch_size=args.batch_size,
                      training_starts=args.training_starts,capacity=args.capacity,resume_from=args.resume_from,resume_checkpoint=args.resume_checkpoint,
                      start_paused=args.continuous)
    env=AutoIntervention(base,np.zeros(6),expert=PortalSignals(),demo_path=ROOT/selection['demo_path'],
        demo_initial_tcp_pose=selection['demo_initial_tcp_pose'],recover_point0=selection['recover_point0'],
        recover_point1=selection['recover_point1'],**selection['control_parameters'])
    if args.resume_from and not args.resume_checkpoint and (args.resume_from/'intervention-state.json').exists():
        env.forever_no_window_intervention=bool(json.loads((args.resume_from/'intervention-state.json').read_text())['forever_no_window_intervention'])
    manifest=dict(schema='autoserl_online_run_v1',synthetic=False,initial_demo_episodes=1,
        demo_path=selection['demo_path'],demo_sha256=selection['demo_pickle_sha256'],
        online_plan=base.plan,online_plan_sha256=base.plan_sha,image_preprocessing=base.preprocessing,
        setup='FR3 Robotiq ZED; attended manual reset and SpaceMouse success/abort labels',
        algorithm='adapted AutoIntervention plus asynchronous pixel SAC, 50/50 expert/online, CTA=2',
        deviations='existing joint guards; configured contact termination; bounded local task workspace; measured action timing',
        resume_from=str(args.resume_from.resolve()) if args.resume_from else None,
        xla_flags=os.environ.get('XLA_FLAGS',''))
    manifest['continuous']=args.continuous
    manifest['contact_stop_mode']=base.plan.get('contact_stop_mode','force_limit')
    manifest['unlabelled_stop_outcome']='pending' if args.continuous else 'legacy_noncontinuous'
    manifest['resume_checkpoint']=str(args.resume_checkpoint.resolve()) if args.resume_checkpoint else None
    manifest['start_paused']=args.start_paused
    manifest['intervention_reset_on_selected_checkpoint']=bool(args.resume_checkpoint)
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    try:
        if args.continuous:
            from .continuous import ContinuousRunner
            ContinuousRunner(base,env,training,args.output,start_paused=args.start_paused).run()
            return
        print('Actor and learner compiled. Waiting for the portal controller at custom Home...',flush=True)
        deadline=time.monotonic()+300.
        while True:
            training.poll()
            try:
                base.preview()
                break
            except Exception as error:
                if time.monotonic()>=deadline:raise RuntimeError('Controller not ready: '+str(error))
                time.sleep(.25)
        for episode in range(args.episodes):
            if episode:
                print('Waiting for manual withdrawal, custom Home, then the online episode button.',flush=True)
                while True:
                    data=base.transport.request(dict(operation='observe'))
                    if data['pilot']['mode']=='policy' and data['pilot']['online']['index']==-1:break
                    training.poll()
                    time.sleep(.1)
            obs,_=env.reset();trajectory=[];pending=None
            training.event('episode_start',episode=episode)
            print(f'ONLINE episode {episode}: policy + AutoIntervention active; left=success, right=abort, web Stop stops.',flush=True)
            try:
                while True:
                    action=training.sample(obs)
                    next_obs,reward,done,truncated,info=env.step(action)
                    if pending is not None:
                        transition,intervened=pending;training.insert(transition,intervened);trajectory.append(transition)
                    transition=dict(observations=obs,actions=np.asarray(info['executed_action'],np.float32),
                        next_observations=next_obs,rewards=float(reward),masks=float(not done),dones=bool(done or truncated))
                    pending=(transition,info['auto_intervention']);obs=next_obs
                    training.event('step',episode=episode,action_index=info['action_index'],
                        auto_intervention=info['auto_intervention'],recoveries=info['auto_recovery_count'],
                        reward=float(reward),done=bool(done),truncated=bool(truncated),
                        reason=info['terminal_reason'],duration_seconds=info['duration_seconds'])
                    if done or truncated:break
            except (RuntimeError,ValueError) as error:
                training.event('episode_interrupted',episode=episode,error=str(error))
                # A stop between acknowledged actions terminates the last valid
                # transition. Never invent a transition for an unacknowledged command.
                try:
                    data=base.transport.request(dict(operation='observe'))
                    online=data['pilot']['online']
                    if pending is not None:
                        pending[0].update(next_observations=base.observation(data),
                            rewards=float(online['operator_success']),masks=0.,dones=True)
                except Exception:
                    if pending is not None:pending[0].update(masks=0.,dones=True)
            finally:
                base.close()
                if pending is not None:
                    transition,intervened=pending;training.insert(transition,intervened);trajectory.append(transition)
                with gzip.open(args.output/f'episode_{episode:04d}.pkl.gz','wb') as stream:
                    pickle.dump(trajectory,stream)
                training.event('episode_end',episode=episode,transitions=len(trajectory),
                    return_=sum(t['rewards'] for t in trajectory),recoveries=env.total_recover_cnt)
                print(f'Episode {episode} ended: {len(trajectory)} transitions; arm holds. No automatic Home.',flush=True)
    finally:
        try:base.close()
        finally:training.close();claim.close()
    print(f'Saved online run: {args.output}; learner updates: {training.gradient_updates}',flush=True)


if __name__=='__main__':main()
