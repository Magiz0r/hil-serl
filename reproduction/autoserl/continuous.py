"""Resident attended training: label, manually reset, then automatically continue."""
import gzip
import copy
import json
from pathlib import Path
import pickle
import signal
import threading
import time
import uuid

from reproduction.portal.pilot_training import RUNTIME,atomic_json,read_commands,ready_at_home,mailbox_lock


class ButtonTail:
    """Read the existing HID journal; never open another HID device."""
    def __init__(self):self.path=None;self.offset=0;self.buttons=[0,0]

    def poll(self,directory):
        if not directory:return []
        try:
            session=json.loads((Path(directory)/'session.json').read_text())
            path=Path(session['control_log_directory'])/'input.jsonl'
            if path!=self.path:
                self.path=path;self.offset=path.stat().st_size;return []
            edges=[]
            with path.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    start=stream.tell();line=stream.readline()
                    if not line or not line.endswith(b'\n'):self.offset=start;break
                    row=json.loads(line);buttons=row.get('buttons',[0,0])[:2]
                    if buttons!=self.buttons and any(buttons):
                        edges.append(dict(buttons=buttons,pc_time=row['pc_time']))
                    self.buttons=buttons
            return edges
        except (OSError,ValueError,KeyError):return []


class ContinuousRunner:
    def __init__(self,base,env,training,output,runtime=RUNTIME,*,start_paused=False):
        self.base,self.env,self.training=base,env,training
        self.algorithm_id=getattr(base,'algorithm_id','autoserl')
        self.intervention_source='policy'
        self.reward_source=getattr(base,'reward_source','operator')
        self.operator_review=None;self.classifier_probability=None
        self.output=Path(output);self.runtime=Path(runtime);self.session_id=uuid.uuid4().hex
        self.episode=0;self.sequence=0;self.enabled=not start_paused;self.label=None;self.pending=None
        self.ever_started=False
        self.trajectory=[];self.interventions=[];self.last_command=None;self.reason=None
        self.have_episode=False;self.buttons=ButtonTail();self.home_since=None;self.last_directory=None
        self.state_lock=threading.Lock();self.state={};self.heartbeat_stop=threading.Event()
        self.set_status('paused' if start_paused else 'waiting_home',message='模型已准备；点击开始运行' if start_paused else '先退出接触并回自定义 Home；到位后自动开始')
        self.heartbeat=threading.Thread(target=self.publish_loop,daemon=True);self.heartbeat.start()

    def set_status(self,phase,**extra):
        with self.state_lock:
            self.state=dict(algorithm_id=self.algorithm_id,intervention_source=self.intervention_source,session_id=self.session_id,episode=self.episode,phase=phase,
                enabled=self.enabled,can_label=self.have_episode,label=self.label,
                reward_source=self.reward_source,classifier_probability=self.classifier_probability,operator_review=self.operator_review,
                steps=len(self.trajectory)+(self.pending is not None),reason=self.reason,
                gradient_updates=self.training.gradient_updates)
            self.state['time_limit_seconds']=getattr(self.base,'episode_time_limit_seconds',0)
            awaiting_label=self.have_episode and self.label is None and phase!='running'
            self.state.update(output=str(self.output.resolve()),pending_label=awaiting_label,
                episode_return=None if awaiting_label else 1. if self.label=='success' else 0. if self.label=='failure' else
                    sum(t.get('rewards',0.) for t in self.trajectory)+(self.pending[0].get('rewards',0.) if self.pending else 0.),
                **self.intervention_counts())
            self.state.update(extra)
            atomic_json(self.runtime/'training-status.json',dict(self.state,heartbeat_unix=time.time()))

    def intervention_counts(self):
        count=sum(self.interventions)+(int(self.pending[1]) if self.pending else 0)
        return dict(automatic_interventions=count if self.algorithm_id=='autoserl' else 0,
                    human_interventions=count if self.algorithm_id=='hilserl' else 0)

    def publish_loop(self):
        while not self.heartbeat_stop.is_set():
            with self.state_lock:
                atomic_json(self.runtime/'training-status.json',dict(self.state,heartbeat_unix=time.time()))
            self.heartbeat_stop.wait(.2)

    def poll_commands(self):
        self.training.poll()
        self.sequence,commands=read_commands(self.session_id,self.sequence,self.runtime)
        for command in commands:
            action=command['action']
            if action in ('pause','resume'):self.enabled=action=='resume'
            elif command['episode']!=self.episode:continue
            elif self.have_episode:
                if self.reward_source=='classifier':
                    self.operator_review=action
                    self.training.event('operator_review',episode=self.episode,review=action)
                    if self.state.get('phase')!='running':self.save_pending()
                    continue
                self.label=action
                if self.state.get('phase')!='running':self.save_pending()
            self.training.event('session_control',episode=self.episode,command=command)

    def stop_robot(self):
        if not self.ever_started:return
        try:self.base.close()
        except (OSError,RuntimeError,ValueError) as error:
            self.enabled=False
            self.training.event('stop_not_acknowledged',episode=self.episode,error=str(error))

    def status(self):
        response=self.base.transport.session.get(self.base.transport.url+'/status',timeout=2.)
        response.raise_for_status();return response.json()

    def save_pending(self):
        if not self.have_episode:return
        target=self.output/'pending';target.mkdir(exist_ok=True)
        trajectory=self.trajectory+([self.pending[0]] if self.pending else [])
        original=target/f'episode_{self.episode:04d}.pkl.gz'
        if not original.exists():self.write_episode(original,trajectory)
        labelled=copy.deepcopy(trajectory)
        if labelled:
            if self.reward_source=='operator':labelled[-1]['rewards']=float(self.label=='success')
            labelled[-1].update(masks=0.,dones=True)
        self.write_episode(self.output/f'episode_{self.episode:04d}.pkl.gz',labelled)
        atomic_json(self.output/f'episode_{self.episode:04d}.json',dict(episode=self.episode,
            outcome=self.label or 'pending',pending_final_replay_insert=True,reason=self.reason,transitions=len(labelled),
            time_limit_seconds=getattr(self.base,'episode_time_limit_seconds',0),
            algorithm_id=self.algorithm_id,reward_source=self.reward_source,operator_review=self.operator_review,
            return_=sum(t['rewards'] for t in labelled),**self.intervention_counts()))

    @staticmethod
    def write_episode(path,trajectory):
        tmp=path.with_name(path.name+'.tmp')
        with gzip.open(tmp,'wb') as stream:pickle.dump(trajectory,stream)
        tmp.replace(path)

    def commit(self):
        if not self.have_episode:return True
        if self.label not in (('success','failure','interrupted') if self.reward_source=='classifier' else ('success','failure')):
            self.save_pending()
            return False
        if self.pending is not None:
            transition,intervened=self.pending
            if self.reward_source=='operator':transition['rewards']=float(self.label=='success')
            transition.update(masks=0.,dones=True)
            self.training.insert(transition,intervened);self.trajectory.append(transition);self.interventions.append(intervened)
            self.pending=None
        self.write_episode(self.output/f'episode_{self.episode:04d}.pkl.gz',self.trajectory)
        result=dict(episode=self.episode,outcome=self.label,
            time_limit_seconds=getattr(self.base,'episode_time_limit_seconds',0),
            label_source=self.reward_source,reward_source=self.reward_source,operator_review=self.operator_review,
            classifier_probability=self.classifier_probability,reason=self.reason,
            transitions=len(self.trajectory),algorithm_id=self.algorithm_id,**self.intervention_counts(),
            recoveries=getattr(self.env,'total_recover_cnt',0),return_=sum(t['rewards'] for t in self.trajectory))
        atomic_json(self.output/f'episode_{self.episode:04d}.json',result)
        self.training.event('episode_end',**result);self.training.save()
        if self.algorithm_id=='autoserl':
            # Human takeover must remain available after any number of successes.
            if self.label=='success' and self.env.intervention_cnt<self.env.intervention_termination_step_threshold:
                self.env.forever_no_window_intervention=True
            atomic_json(self.output/'intervention-state.json',dict(
                forever_no_window_intervention=self.env.forever_no_window_intervention))
        print('Saved continuous episode: '+json.dumps(result),flush=True)
        self.have_episode=False;self.episode+=1
        return True

    def wait_for_home(self):
        while True:
            self.poll_commands()
            try:
                status=self.status()
                edges=self.buttons.poll(status.get('directory'))
                if self.have_episode and not status.get('recording') and status.get('pilot',{}).get('mode')=='locked':
                    for edge in edges:
                        if all(edge['buttons']):self.enabled=False
                        elif self.reward_source=='operator':self.label='failure' if edge['buttons'][1] else 'success'
                        else:self.operator_review='failure' if edge['buttons'][1] else 'success'
                        self.training.event('post_stop_button',episode=self.episode,**edge,label=self.label)
                        self.save_pending()
                if not self.enabled:
                    message='已暂停；请先标记本轮成功或失败' if self.have_episode and self.label is None else '已暂停；准备好后点击开始运行'
                    self.home_since=None;self.set_status('paused',message=message)
                    time.sleep(.15);continue
                if self.have_episode and self.label is None:
                    self.home_since=None
                    self.set_status('awaiting_label',message='本轮已停止，等待标记成功或失败；标记并回 Home 后自动继续')
                    time.sleep(.15);continue
                data=self.base.transport.request(dict(operation='observe'))
                after=self.last_command
                if after is not None and status.get('directory')!=self.last_directory:after=-1
                ready=ready_at_home(data,status,self.base.plan,after)
                if not self.enabled:
                    self.home_since=None;self.set_status('paused',message='循环已暂停；点击继续后等待 Home 到位')
                elif ready and not any(self.buttons.buttons):
                    if self.home_since is None:self.home_since=time.monotonic()
                    remaining=max(0.,2.-(time.monotonic()-self.home_since))
                    self.set_status('countdown',message='Home 已到位，下一轮即将开始',countdown_seconds=remaining)
                    if remaining==0:
                        with mailbox_lock(self.runtime):
                            self.poll_commands()
                            if self.enabled and (not self.have_episode or self.label in ('success','failure','interrupted')):
                                self.set_status('starting',can_label=False,message='开始下一轮')
                                return
                else:
                    self.home_since=None
                    self.set_status('awaiting_reset' if self.have_episode else 'waiting_home',
                        message='先退出接触，再返回自定义 Home；到位后自动下一轮')
            except (RuntimeError,ValueError,OSError) as error:
                self.home_since=None;self.set_status('waiting_connection',message='等待控制连接恢复并回到 Home',detail=str(error)[:200])
            time.sleep(.15)

    def run_episode(self):
        import numpy as np
        self.label=None;self.reason=None;self.trajectory=[];self.interventions=[];self.pending=None
        self.operator_review=None;self.classifier_probability=None
        self.ever_started=True
        obs,_=self.env.reset();self.have_episode=True
        data=self.base.transport.request(dict(operation='observe'));self.last_command=data['pilot']['online']['command_id']
        # Discard button edges from the manual reset; active labels come from the action acknowledgement.
        self.last_directory=self.status().get('directory');self.buttons.poll(self.last_directory)
        self.training.set_learning(True)
        self.training.event('episode_start',episode=self.episode,command_id=self.last_command)
        try:
            while True:
                self.poll_commands();self.set_status('running',message=('分类器自动判断成功；移动 SpaceMouse 接管，回中交还；Stop 暂停' if self.reward_source=='classifier' else (
                    '策略运行中；移动 SpaceMouse 接管，回中交还；左键成功，右键失败'
                    if self.algorithm_id=='hilserl' else '策略与自动干预运行中；左键成功，右键失败')))
                if not self.enabled or self.label is not None:break
                action=self.training.sample(obs)
                nxt,reward,done,truncated,info=self.env.step(action)
                self.classifier_probability=info.get('classifier_probability')
                if info.get('operator_success'):self.operator_review='success'
                elif info.get('operator_abort'):self.operator_review='failure'
                if self.pending is not None:
                    transition,intervened=self.pending;self.training.insert(transition,intervened)
                    self.trajectory.append(transition);self.interventions.append(intervened)
                transition=dict(observations=obs,actions=np.asarray(info['executed_action'],np.float32),
                    next_observations=nxt,rewards=float(reward),masks=float(not done),dones=bool(done or truncated))
                human=bool(info.get('human_intervention',False));auto=bool(info.get('auto_intervention',False))
                self.intervention_source='human' if human else 'automatic' if auto else 'policy'
                self.pending=(transition,human if self.algorithm_id=='hilserl' else auto);obs=nxt
                self.training.event('step',episode=self.episode,action_index=info['action_index'],
                    auto_intervention=auto,human_intervention=human,intervention_source=self.intervention_source,
                    reward_source=self.reward_source,classifier_probability=self.classifier_probability,
                    recoveries=info.get('auto_recovery_count',0),reward=float(reward),
                    done=bool(done),truncated=bool(truncated),reason=info['terminal_reason'],duration_seconds=info['duration_seconds'])
                self.reason=info['terminal_reason']
                if reward:self.label='success'
                elif self.reward_source=='classifier' and (done or truncated):
                    self.label='failure' if self.reason in ('time_limit','operator_abort') else 'interrupted'
                elif info.get('operator_abort'):self.label='failure'
                if done or truncated:break
        except (RuntimeError,ValueError) as error:
            self.reason=str(error);self.training.event('episode_interrupted',episode=self.episode,error=str(error))
            try:
                data=self.base.transport.request(dict(operation='observe'));online=data['pilot']['online']
                self.reason=online.get('reason') or str(error)
                if self.reward_source=='classifier':self.label='interrupted'
                elif online.get('operator_success'):self.label='success'
                elif online.get('operator_abort'):self.label='failure'
            except Exception:pass
        finally:
            self.stop_robot();self.training.set_learning(False)
            if self.reward_source=='classifier' and self.label is None:self.label='interrupted'
            if self.pending:self.pending[0].update(masks=0.,dones=True)
            self.poll_commands()
            self.save_pending();self.training.save();self.home_since=None
            self.set_status('awaiting_label' if self.label is None else 'awaiting_reset',
                message='已停止；请标记成功或失败，退出接触后回 Home 自动继续')

    def run(self):
        def stop(signum,frame):raise KeyboardInterrupt
        previous=signal.signal(signal.SIGTERM,stop)
        try:
            while True:
                self.wait_for_home()
                if not self.commit():continue
                try:self.run_episode()
                except (RuntimeError,ValueError,OSError) as error:
                    self.stop_robot();self.training.set_learning(False);self.enabled=False
                    self.reason=str(error)
                    self.set_status('paused',message='启动检查未通过；处理后点击继续',detail=str(error)[:200])
        except KeyboardInterrupt:pass
        finally:
            self.stop_robot()
            try:self.poll_commands();self.commit()
            finally:
                self.set_status('stopped',message='连续训练已结束')
                self.heartbeat_stop.set();self.heartbeat.join(timeout=2.)
                atomic_json(self.runtime/'training-status.json',dict(self.state,heartbeat_unix=time.time()))
                signal.signal(signal.SIGTERM,previous)
