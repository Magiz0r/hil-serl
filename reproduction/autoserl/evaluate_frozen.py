"""Bounded, attended A/B evaluation with frozen weights and manual Home resets.

Uses the same action/label path as training, but owns no learner or replay buffer.
The ten trials alternate early/latest checkpoints. Automatic assistance can be
disabled explicitly, while retaining the portal's force limits and stop controls.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import pickle
import signal
import threading
import time

from .bootstrap import ROOT, configure
from .continuous import ContinuousRunner
from reproduction.portal.pilot_training import atomic_json


def schedule():
    return [dict(condition=condition, pair=pair+1, seed=9040+pair)
            for pair in range(5) for condition in ('early', 'latest')]


def select_checkpoints(source, demo_sha, *, algorithm='autoserl'):
    source = Path(source).resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    from .provenance import require_algorithm
    require_algorithm(manifest,algorithm)
    if not manifest.get('eligible_for_training',True):raise ValueError('Expected a training checkpoint')
    if manifest['synthetic'] or manifest['demo_sha256'] != demo_sha:
        raise ValueError('Evaluation requires the same real single demonstration')
    actor = [json.loads(x) for x in (source/'actor-events.jsonl').read_text().splitlines()]
    learner = [json.loads(x) for x in (source/'learner/events.jsonl').read_text().splitlines()]
    end = next(x['time_unix'] for x in actor if x['kind'] == 'episode_end' and x['episode'] == 9)
    early = next(x for x in learner if x['kind'] == 'checkpoint' and x['time_unix'] >= end)
    latest = next(x for x in reversed(learner) if x['kind'] == 'checkpoint')
    selected = {}
    for name, row in [('early', early), ('latest', latest)]:
        path = source/'learner'/row['path']
        selected[name] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              source_updates=row['gradient_updates'], online_steps=row['online_steps'])
    if selected['early']['source_updates'] >= selected['latest']['source_updates']:
        raise ValueError('Need distinct early and latest checkpoints')
    return selected


class FrozenPolicy:
    gradient_updates = 0

    def __init__(self, output, sample_obs, checkpoints):
        import jax
        import numpy as np
        from flax import serialization
        from serl_launcher.utils.launcher import make_sac_pixel_agent
        from .online_env import IMAGE_KEYS
        self.jax, self.np, self.serialization = jax, np, serialization
        self.output = Path(output)
        self.events = (self.output/'actor-events.jsonl').open('x', buffering=1)
        self.journal_lock = threading.Lock()
        self.checkpoints = checkpoints
        self.agents = {}
        self.initial_hashes = {}
        self.recorded_steps = 0
        self.active = None
        template = make_sac_pixel_agent(42, sample_obs, np.zeros(6, np.float32),
            image_keys=IMAGE_KEYS, encoder_type='resnet-pretrained', discount=.97)
        for name, checkpoint in checkpoints.items():
            raw = Path(checkpoint['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != checkpoint['sha256']:
                raise ValueError('Checkpoint changed during loading')
            restored = serialization.from_bytes(template.state, raw)
            # Only parameters are used. No update method, optimizer or learner is run.
            self.agents[name] = template.replace(state=template.state.replace(params=jax.device_put(restored.params)))
            self.initial_hashes[name] = self.parameter_hash(name)
            self.select(name, 9040)
            self.sample(sample_obs)  # Compile both actors before physical control.
        self.event('ready', frozen=True, gradient_updates=0, robot_io=False,
                   checkpoints=checkpoints, parameter_hashes=self.initial_hashes)
        self.save()

    def parameter_hash(self, name):
        return hashlib.sha256(self.serialization.to_bytes(
            self.jax.device_get(self.agents[name].state.params))).hexdigest()

    def event(self, kind, **data):
        with self.journal_lock:
            self.events.write(json.dumps(dict(kind=kind, time_unix=time.time(), **data), allow_nan=False)+'\n')

    def select(self, condition, seed):
        self.active = condition
        self.rng = self.jax.random.PRNGKey(seed)

    def sample(self, obs):
        self.rng, key = self.jax.random.split(self.rng)
        return self.np.asarray(self.jax.device_get(
            self.agents[self.active].sample_actions(obs, seed=key, argmax=False)))

    def poll(self):
        pass

    def insert(self, transition, intervention):
        # ContinuousRunner saves trajectories; evaluation never inserts into replay.
        self.recorded_steps += 1

    def set_learning(self, enabled):
        # The shared runner toggles training at episode boundaries. Here it is inert.
        self.event('learning_disabled', requested=bool(enabled), enabled=False, gradient_updates=0)

    def save(self):
        atomic_json(self.output/'frozen-state.json', dict(gradient_updates=0,
            recorded_steps=self.recorded_steps, replay_insertions=0, condition=self.active))

    def close(self):
        hashes = {name: self.parameter_hash(name) for name in self.agents}
        verified = hashes == self.initial_hashes
        atomic_json(self.output/'weights-verification.json', dict(unchanged=verified,
            before=self.initial_hashes, after=hashes, gradient_updates=0))
        self.save()
        self.events.close()
        if not verified:
            raise RuntimeError('Frozen evaluation weights changed')


class EvaluationRunner(ContinuousRunner):
    def __init__(self, base, env, training, output, *args, start_paused=False, trials=None, **kwargs):
        self.schedule = schedule() if trials is None else trials
        if not self.schedule:raise ValueError('Evaluation requires at least one trial')
        self.finished = False
        self.automatic_assistance = bool(env.enable_interventions)
        super().__init__(base, env, training, output, *args, start_paused=start_paused, **kwargs)

    def trial(self):
        return self.schedule[min(self.episode, len(self.schedule)-1)]

    def set_status(self, phase, **extra):
        trial = self.trial()
        name = {'early':'A 较早模型','latest':'B 最新模型','selected':'所选模型'}[trial['condition']]
        per_model=sum(t['condition']==trial['condition'] for t in self.schedule)
        message = extra.pop('message', '')
        extra.update(mode='evaluation', total_episodes=len(self.schedule),
                     condition=trial['condition'], pair=trial['pair'], frozen=True,
                     automatic_assistance=self.automatic_assistance)
        assistance = '有自动干预' if self.automatic_assistance else '无自动干预'
        if phase == 'running' and not self.automatic_assistance:
            message = ('策略独立运行；分类器自动判断成功，人工按钮只复核 / 停止'
                if self.reward_source=='classifier' else '策略独立运行；左键成功，右键失败')
        if phase != 'completed':
            message = f"{assistance} · 冻结评估 {min(self.episode+1, len(self.schedule))}/{len(self.schedule)} · {name} {trial['pair']}/{per_model} · "+message
        super().set_status(phase, message=message, **extra)

    def annotate(self):
        path = self.output/f'episode_{self.episode:04d}.json'
        if path.exists():
            row = json.loads(path.read_text())
            row.update(self.trial(), evaluation=True, gradient_updates=0,
                automatic_assistance=self.automatic_assistance,
                checkpoint=self.training.checkpoints[self.trial()['condition']])
            atomic_json(path, row)

    def save_pending(self):
        super().save_pending()
        if self.have_episode:
            self.annotate()

    def commit(self):
        if not self.have_episode:
            return True
        episode = self.episode
        committed = super().commit()
        path = self.output/f'episode_{episode:04d}.json'
        row = json.loads(path.read_text())
        trial = self.schedule[episode]
        row.update(trial, evaluation=True, gradient_updates=0,
                   automatic_assistance=self.automatic_assistance,
                   checkpoint=self.training.checkpoints[trial['condition']])
        row['evaluation_valid'] = committed and row['outcome'] in ('success','failure')
        if not row['evaluation_valid']:
            row['exclusion_reason'] = 'interrupted' if row['outcome']=='interrupted' else 'awaiting_operator_label'
        atomic_json(path, row)
        self.report()
        return committed

    def report(self):
        rows = [json.loads(p.read_text()) for p in sorted(self.output.glob('episode_*.json'))]
        rows = [r for r in rows if r.get('evaluation')]
        attempted = len(rows)
        rows = [r for r in rows if r.get('evaluation_valid', True) and r['outcome'] in ('success','failure')]
        groups = {}
        for condition in dict.fromkeys(t['condition'] for t in self.schedule):
            group = [r for r in rows if r['condition'] == condition]
            steps = sum(r['transitions'] for r in group)
            groups[condition] = dict(episodes=len(group), successes=sum(r['outcome']=='success' for r in group),
                transitions=steps, automatic_fraction=sum(r['automatic_interventions'] for r in group)/steps if steps else None)
        result = dict(evaluation=True, complete=len(rows)==len(self.schedule), frozen=True,
            gradient_updates=0, automatic_assistance=self.automatic_assistance, episodes=len(rows), groups=groups)
        result.update(attempted_episodes=attempted, excluded_episodes=attempted-len(rows))
        atomic_json(self.output/'summary.json', result)
        return result

    def run_episode(self):
        if bool(self.env.enable_interventions) != self.automatic_assistance:
            raise RuntimeError('Evaluation assistance mode changed')
        trial = self.trial()
        self.training.select(trial['condition'], trial['seed'])
        # Each trial starts with the same intervention state. An earlier success
        # must not permanently disable assistance for a later A/B trial.
        if self.algorithm_id=='autoserl':
            self.env.forever_no_window_intervention = not self.automatic_assistance
            self.env.total_recover_cnt = 0
        elif self.base.human_intervention_enabled:
            raise RuntimeError('Human intervention must be disabled during evaluation')
        self.training.event('evaluation_trial', episode=self.episode, **trial,
            checkpoint=self.training.checkpoints[trial['condition']], gradient_updates=0,
            automatic_assistance=self.automatic_assistance)
        super().run_episode()

    def wait_for_final_label(self):
        self.enabled = False
        while self.label is None:
            self.poll_commands()
            self.enabled = False  # Resume cannot create an eleventh episode.
            try:
                status = self.status()
                if status.get('pilot', {}).get('mode') == 'locked' and not status.get('recording'):
                    for edge in self.buttons.poll(status.get('directory')):
                        if not all(edge['buttons']):
                            self.label = 'failure' if edge['buttons'][1] else 'success'
                            self.training.event('post_stop_button', episode=self.episode, **edge, label=self.label)
            except (RuntimeError, ValueError, OSError):
                pass
            self.save_pending()
            self.set_status('awaiting_reset', message=f'已跑满 {len(self.schedule)} 轮；请确认最后一轮成功或失败，不会再启动')
            if self.label is None:
                time.sleep(.15)

    def run(self):
        def stop(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, stop)
        try:
            while self.episode < len(self.schedule):
                self.wait_for_home()
                if not self.commit():continue
                if self.episode >= len(self.schedule):
                    break
                try:
                    self.run_episode()
                except (RuntimeError, ValueError, OSError) as error:
                    self.stop_robot()
                    self.enabled = False
                    self.reason = str(error)
                    self.set_status('paused', message='启动检查未通过；处理后点击继续', detail=str(error)[:200])
                    continue
                if self.episode == len(self.schedule)-1:
                    self.wait_for_final_label()
                    self.commit()
                    self.finished = self.report()['complete']
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.enabled = False
            self.stop_robot()
            try:
                self.poll_commands()
                self.commit()
                result = self.report()
                description='，'.join(f"{name} {g['successes']}/{g['episodes']}" for name,g in result['groups'].items())
                self.enabled = False
                self.set_status('completed' if self.finished else 'stopped', can_label=False,
                    message=f"冻结评估{'完成' if self.finished else '中止'}：{description}；已停止")
            finally:
                self.heartbeat_stop.set()
                self.heartbeat.join(timeout=2.)
                signal.signal(signal.SIGTERM, previous)


def main(*, algorithm='autoserl'):
    parser = argparse.ArgumentParser(description=('Frozen HIL-SERL evaluation; no human or automatic assistance.' if algorithm=='hilserl' else __doc__))
    parser.add_argument('--execute-attended-evaluation', action='store_true', required=True)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checkpoint',type=Path,help='Evaluate one selected checkpoint instead of the A/B protocol')
    parser.add_argument('--episodes',type=int,default=5)
    parser.add_argument('--disable-automatic-assistance', action='store_true',
        help='Evaluate the policy alone: disable demo guidance and recovery; keep safety stops')
    parser.add_argument('--start-at-home', action='store_true',
        help='Explicitly enable starting after Home checks; default is prepared and paused')
    args = parser.parse_args()
    if algorithm=='hilserl':args.disable_automatic_assistance=True
    if not 1<=args.episodes<=100:parser.error('Evaluation episodes must be 1 to 100')
    configure()
    import numpy as np
    from .online_env import PortalEnv, PortalSignals
    from .intervention import AutoIntervention
    runtime = ROOT/'reproduction/runtime'
    runtime.mkdir(exist_ok=True)
    with (runtime/'autoserl-actor.lock').open('a') as claim:
        fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if algorithm=='hilserl':
            from reproduction.hilserl.online_env import HILPortalEnv
            base=HILPortalEnv(ROOT,evaluation=True)
        else:base=PortalEnv(ROOT)
        classifier=None
        if algorithm=='hilserl':
            from reproduction.hilserl.reward_classifier import load_active
            from reproduction.hilserl.online_env import ClassifierReward
            classifier=load_active(ROOT)
            source_manifest=json.loads((args.source_run/'manifest.json').read_text())
            if source_manifest.get('reward_identity')!=classifier.metadata['identity']:
                raise ValueError('评估必须使用训练时的奖励分类器；不能混入旧人工奖励模型')
        selection = base.selection
        trials=schedule()
        if args.checkpoint:
            from .train_online import checkpoint_metadata
            path,updates,count=checkpoint_metadata(args.source_run,args.checkpoint,selection['demo_pickle_sha256'],algorithm=algorithm)
            checkpoints={'selected':dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                source_updates=updates,online_steps=count)}
            trials=[dict(condition='selected',pair=i+1,seed=9040+i) for i in range(args.episodes)]
        else:
            checkpoints = select_checkpoints(args.source_run, selection['demo_pickle_sha256'],algorithm=algorithm)
        args.output.mkdir(parents=True, exist_ok=False)
        atomic_json(args.output/'manifest.json', dict(schema=f'{algorithm}_frozen_evaluation_v1',algorithm_id=algorithm,human_intervention=False,
            synthetic=False, eligible_for_training=False, initial_demo_episodes=1,
            reward_source='classifier' if classifier else 'operator',
            reward_identity=classifier.metadata['identity'] if classifier else None,
            demo_sha256=selection['demo_pickle_sha256'], source_run=str(args.source_run.resolve()),
            checkpoints=checkpoints, schedule=trials, gradient_updates=0,
            automatic_assistance=not args.disable_automatic_assistance, reset_intervention_state_each_trial=True,
            start_paused=not args.start_at_home,
            action_sampling='stochastic as in training; same seed within each A/B pair',
            success_criterion='trained binary classifier' if classifier else 'operator-confirmed fully inserted; post-stop labels accepted',
            online_plan=base.plan, online_plan_sha256=base.plan_sha, image_preprocessing=base.preprocessing))
        with (ROOT/selection['demo_path']).open('rb') as stream:
            demo = pickle.load(stream)
        policy = FrozenPolicy(args.output, demo[0]['observations'], checkpoints)
        env=ClassifierReward(base,classifier) if classifier else base
        if algorithm=='autoserl':
            env = AutoIntervention(base, np.zeros(6), expert=PortalSignals(), demo_path=ROOT/selection['demo_path'],
                demo_initial_tcp_pose=selection['demo_initial_tcp_pose'], recover_point0=selection['recover_point0'],
                recover_point1=selection['recover_point1'], enable_interventions=not args.disable_automatic_assistance,
                **selection['control_parameters'])
        try:
            print(f'Frozen actors ready. {len(trials)} attended trials; automatic_assistance={env.enable_interventions}; Home resets remain manual.', flush=True)
            EvaluationRunner(base, env, policy, args.output, start_paused=not args.start_at_home,trials=trials).run()
        finally:
            policy.close()


if __name__ == '__main__':
    main()
