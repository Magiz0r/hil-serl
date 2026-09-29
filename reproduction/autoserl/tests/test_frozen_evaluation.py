import copy
import gzip
import json
import pickle
from types import SimpleNamespace

import numpy as np

from reproduction.autoserl.evaluate_frozen import EvaluationRunner, schedule
from reproduction.pilot_training import session_command, session_status


class Policy:
    gradient_updates = 0
    checkpoints = {k: {'path': k, 'sha256': k} for k in ('early', 'latest')}

    def __init__(self):
        self.selected = []
        self.recorded = []

    def select(self, condition, seed):
        self.selected.append((condition, seed))

    def sample(self, obs):
        return np.zeros(6)

    def poll(self):
        pass

    def event(self, *args, **kwargs):
        pass

    def set_learning(self, enabled):
        pass

    def save(self):
        pass

    def insert(self, transition, intervened):
        self.recorded.append(copy.deepcopy(transition))


class Environment:
    enable_interventions = True
    intervention_cnt = 0
    intervention_termination_step_threshold = 10
    forever_no_window_intervention = True
    total_recover_cnt = 17

    def __init__(self, base):
        self.base = base
        self.starts = 0

    def reset(self):
        assert self.forever_no_window_intervention == (not self.enable_interventions)
        assert self.total_recover_cnt == 0
        self.starts += 1
        self.base.cid += 1
        self.step_index = 0
        return {'state': np.zeros((1,19))}, {}

    def step(self, action):
        self.step_index += 1
        done = self.step_index == 2
        return {'state': np.full((1,19), self.step_index)}, 0., done, False, dict(
            executed_action=action, auto_intervention=False, action_index=self.step_index-1,
            auto_recovery_count=0, terminal_reason='force_limit' if done else None,
            duration_seconds=.1, operator_abort=False)


def make_runner(tmp_path, runner_class, *, automatic_assistance=True):
    base = SimpleNamespace(cid=0, close=lambda: None)
    base.transport = SimpleNamespace(request=lambda data: {'pilot': {'online': {'command_id': base.cid}}})
    policy = Policy()
    env = Environment(base)
    env.enable_interventions = automatic_assistance
    runner = runner_class(base, env, policy, tmp_path, runtime=tmp_path/'runtime')
    runner.status = lambda: {'pilot': {'mode': 'locked'}, 'directory': None}
    return runner, env, policy


class OperatorRunner(EvaluationRunner):
    def wait_for_home(self):
        if self.have_episode:
            session_command(dict(action='success', session_id=self.session_id, episode=self.episode), self.runtime)
            self.poll_commands()

    def wait_for_final_label(self):
        # A delayed success and queued Resume must not permit an eleventh trial.
        for action in ('pause', 'resume', 'success'):
            session_command(dict(action=action, session_id=self.session_id, episode=self.episode), self.runtime)
        super().wait_for_final_label()


def test_ten_trial_cap_equal_conditions_late_labels_and_no_cross_trial_termination(tmp_path):
    runner, env, policy = make_runner(tmp_path, OperatorRunner)
    runner.run()
    assert env.starts == 10
    assert policy.selected == [(x['condition'], x['seed']) for x in schedule()]
    assert len(policy.recorded) == 20
    assert sum(t['rewards'] for t in policy.recorded) == 10
    assert runner.finished and not runner.enabled
    assert not session_status(runner.runtime)['available']
    assert session_status(runner.runtime)['phase'] == 'completed'
    assert not runner.heartbeat.is_alive()
    for i, trial in enumerate(schedule()):
        row = json.loads((tmp_path/f'episode_{i:04d}.json').read_text())
        assert row['condition'] == trial['condition'] and row['seed'] == trial['seed']
        assert row['outcome'] == 'success' and row['gradient_updates'] == 0
        with gzip.open(tmp_path/f'episode_{i:04d}.pkl.gz','rb') as stream:
            ts = pickle.load(stream)
        assert ts[-1]['rewards'] == 1 and ts[-1]['dones'] and ts[-1]['masks'] == 0
    report = json.loads((tmp_path/'summary.json').read_text())
    assert report['complete'] and report['episodes'] == 10
    assert all(group['episodes'] == 5 for group in report['groups'].values())


def test_interrupted_evaluation_saves_pending_trial_and_never_marks_complete(tmp_path):
    class Interrupted(OperatorRunner):
        def wait_for_home(self):
            if self.env.starts == 3:
                raise KeyboardInterrupt
            super().wait_for_home()
    runner, env, policy = make_runner(tmp_path, Interrupted)
    runner.run()
    assert env.starts == 3 and len(policy.recorded) == 5
    report = json.loads((tmp_path/'summary.json').read_text())
    assert report['episodes'] == 2 and report['excluded_episodes'] == 1 and not report['complete']
    assert json.loads((tmp_path/'episode_0002.json').read_text())['outcome']=='pending'
    assert session_status(runner.runtime)['phase'] == 'stopped'
    assert not runner.heartbeat.is_alive()


def test_administrative_stop_without_label_is_excluded(tmp_path):
    class Rest(OperatorRunner):
        def wait_for_home(self):
            if self.have_episode:
                self.reason = None
                raise KeyboardInterrupt
    runner, env, policy = make_runner(tmp_path, Rest)
    runner.run()
    report = json.loads((tmp_path/'summary.json').read_text())
    assert report['attempted_episodes'] == 1 and report['excluded_episodes'] == 1
    assert report['episodes'] == 0 and not report['complete']
    assert len(policy.recorded) == 1  # Final transition stays on disk pending a label.
    with gzip.open(tmp_path/'episode_0000.pkl.gz','rb') as stream:
        assert len(pickle.load(stream))==2


def test_prepare_mode_starts_paused(tmp_path):
    class Prepared(OperatorRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, start_paused=True, **kwargs)
    runner, env, policy = make_runner(tmp_path, Prepared)
    try:
        assert not runner.enabled and env.starts == 0
        assert session_status(runner.runtime)['phase'] == 'paused'
    finally:
        runner.heartbeat_stop.set()
        runner.heartbeat.join(timeout=2.)


def test_unassisted_trials_keep_mode_and_record_zero_interventions(tmp_path):
    runner, env, policy = make_runner(tmp_path, OperatorRunner, automatic_assistance=False)
    runner.run()
    report = json.loads((tmp_path/'summary.json').read_text())
    assert report['complete'] and report['episodes'] == 10
    assert report['automatic_assistance'] is False
    assert all(g['automatic_fraction'] == 0 for g in report['groups'].values())
    for path in tmp_path.glob('episode_*.json'):
        row = json.loads(path.read_text())
        assert row['automatic_assistance'] is False
        assert row['automatic_interventions'] == row['recoveries'] == 0
    assert not env.enable_interventions
    assert session_status(runner.runtime)['automatic_assistance'] is False
