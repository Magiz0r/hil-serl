"""Local experiment catalog, read-only metrics, and explicitly prepared workers."""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from reproduction.autoserl.provenance import ALGORITHMS, algorithm_id
from reproduction.portal.pilot_training import atomic_json, session_status


ROOT = Path(__file__).resolve().parents[2]


def read_json(path):
    return json.loads(Path(path).read_text())


def json_lines(path, tail_bytes=None):
    if not path.exists():return []
    with path.open('rb') as stream:
        if tail_bytes and path.stat().st_size > tail_bytes:
            stream.seek(-tail_bytes, 2);stream.readline()
        lines=stream.read().splitlines()
    rows=[]
    for line in lines:
        try:rows.append(json.loads(line))
        except (ValueError,UnicodeError):continue  # Last append can be incomplete.
    return rows


def identifier(path, root):
    return hashlib.sha256(str(path.resolve().relative_to(root.resolve())).encode()).hexdigest()[:20]


class ExperimentStore:
    def __init__(self, root=ROOT):
        self.root=Path(root).resolve();self.logs=self.root/'reproduction/logs'
        self.runtime=self.root/'reproduction/runtime'
        self.lock=threading.RLock();self.cached=None;self.cached_at=0
        self.runs={};self.models={};self.metric_cache={}

    def catalog(self, force=False):
        with self.lock:
            if not force and self.cached is not None and time.monotonic()-self.cached_at<2:
                return self.cached
            selection=read_json(self.root/'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json')
            from reproduction.hilserl.reward_classifier import active_metadata
            try:reward=active_metadata(self.root)
            except (ValueError,OSError,KeyError):reward=None
            runs=[];self.runs={};self.models={}
            for path in self.logs.rglob('manifest.json'):
                try:
                    folder=path.parent.resolve()
                    if not folder.is_relative_to(self.logs.resolve()):continue
                    manifest=read_json(path)
                    schema=manifest.get('schema')
                    if manifest.get('synthetic') is not False or schema not in tuple(f'{a}_{kind}_v1' for a in ALGORITHMS for kind in ('online_run','frozen_evaluation')):continue
                    rid=identifier(folder,self.root)
                    algorithm=algorithm_id(manifest)
                    is_training=schema==f'{algorithm}_online_run_v1' and manifest.get('eligible_for_training',True)
                    compatible=manifest.get('demo_sha256')==selection['demo_pickle_sha256']
                    if algorithm=='hilserl':compatible=compatible and reward is not None and manifest.get('reward_identity')==reward['identity']
                    checkpoints=[]
                    if is_training and compatible:
                        candidates=list((folder/'learner').glob('checkpoint_*.msgpack')) or list(folder.glob('checkpoint_*.msgpack'))
                        for checkpoint in sorted(candidates,reverse=True):
                            if not checkpoint.resolve().is_relative_to(folder) or checkpoint.stat().st_size==0:continue
                            updates=int(checkpoint.stem.split('_')[-1]);mid=identifier(checkpoint,self.root)
                            model=dict(id=mid,run_id=rid,algorithm_id=algorithm,name=checkpoint.name,updates=updates,
                                saved_unix=checkpoint.stat().st_mtime)
                            checkpoints.append(model);self.models[mid]=(folder,checkpoint.resolve(),model)
                    row=dict(id=rid,name=folder.name,algorithm_id=algorithm,kind='training' if is_training else 'evaluation',
                        compatible=compatible,created_unix=path.stat().st_mtime,checkpoints=checkpoints)
                    self.runs[rid]=folder;runs.append(row)
                except (OSError,ValueError,KeyError,TypeError):continue
            runs.sort(key=lambda r:r['created_unix'],reverse=True)
            self.cached=dict(reward_ready=reward is not None,runs=runs,initial_demo_episodes=1,demo_name=Path(selection['demo_path']).name)
            self.cached_at=time.monotonic()
            return self.cached

    def model(self, mid, algorithm=None):
        self.catalog(force=True)
        if not isinstance(mid,str) or mid not in self.models:raise ValueError('模型不存在或不属于当前示范，请刷新列表')
        if algorithm is not None and self.models[mid][2]['algorithm_id']!=algorithm:
            raise ValueError('所选模型不属于当前算法，不能混用 baseline')
        return self.models[mid]

    def metrics(self, rid):
        self.catalog()
        if rid not in self.runs:raise ValueError('训练记录不存在，请刷新列表')
        folder=self.runs[rid]
        algorithm=algorithm_id(read_json(folder/'manifest.json'))
        with self.lock:
            previous=self.metric_cache.get(rid)
            if previous and time.monotonic()-previous[0]<1:return previous[1]
            episodes=[];previous_recoveries=0
            for path in sorted(folder.glob('episode_*.json')):
                try:
                    row=read_json(path);steps=int(row['transitions'])
                    recoveries=row.get('recoveries')
                    if recoveries is not None and not row.get('evaluation'):
                        total=recoveries;recoveries=max(0,total-previous_recoveries);previous_recoveries=total
                    pending_label=row['outcome'] not in ('success','failure','interrupted')
                    valid=row.get('evaluation_valid',True) and not pending_label and row['outcome']!='interrupted'
                    episodes.append(dict(episode=int(row['episode'])+1,outcome=row['outcome'],
                        return_=None if pending_label else row.get('return_',float(row['outcome']=='success')),steps=steps,
                        reward_source=row.get('reward_source','operator'),operator_review=row.get('operator_review'),
                        automatic_interventions=row.get('automatic_interventions'),human_interventions=row.get('human_interventions'),recoveries=recoveries,
                        reason=row.get('reason'),condition=row.get('condition'),valid=valid,pending_label=pending_label,
                        exclusion_reason=row.get('exclusion_reason'),provisional=bool(row.get('pending_final_replay_insert'))))
                except (OSError,ValueError,KeyError,TypeError):continue
            valid=[r for r in episodes if r['valid']]
            steps=sum(r['steps'] for r in valid)
            auto_steps=sum(r['automatic_interventions'] or 0 for r in valid)
            known_steps=sum(r['steps'] for r in valid if r['automatic_interventions'] is not None)
            success=sum(r['outcome']=='success' for r in valid)
            for i,row in enumerate(valid):
                window=valid[max(0,i-9):i+1]
                row['success_last10']=sum(r['outcome']=='success' for r in window)/len(window)
            logfile=folder/'learner/events.jsonl'
            if not logfile.exists():logfile=folder/'events.jsonl'
            learner=[]
            for row in json_lines(logfile,4_000_000):
                if row.get('kind')!='learning':continue
                metric=row.get('metrics',{})
                point=dict(updates=row['gradient_updates'],online_steps=row.get('online_steps'),time_unix=row.get('time_unix'))
                for name,key in [('q',"['critic']['predicted_qs']"),('critic_loss',"['critic']['critic_loss']"),
                                 ('actor_loss',"['actor']['actor_loss']"),('entropy',"['actor']['entropy']")]:
                    value=metric.get(key)
                    point[name]=value if isinstance(value,(int,float)) and math.isfinite(value) else None
                learner.append(point)
            last10=valid[-10:]
            result=dict(run_id=rid,name=folder.name,algorithm_id=algorithm,episodes=episodes[-1000:],learner=learner[-1000:],
                summary=dict(episodes=len(valid),excluded_episodes=sum(not r['valid'] and not r['pending_label'] for r in episodes),
                    pending_episodes=sum(r['pending_label'] for r in episodes),successes=success,
                    success_rate=success/len(valid) if valid else None,
                    mean_return=sum(r['return_'] for r in valid)/len(valid) if valid else None,
                    last10_successes=sum(r['outcome']=='success' for r in last10),last10_count=len(last10),
                    transitions=steps,automatic_fraction=auto_steps/known_steps if known_steps else None,
                    human_fraction=(sum(r['human_interventions'] or 0 for r in valid)/
                        sum(r['steps'] for r in valid if r['human_interventions'] is not None))
                        if any(r['steps'] and r['human_interventions'] is not None for r in valid) else None,
                    stop_reasons=dict(Counter(r['reason'] or '人工停止' for r in valid))),
                generated_unix=time.time())
            self.metric_cache[rid]=(time.monotonic(),result)
            return result


class ExperimentManager:
    """Own only workers launched here; preparation never enables robot actions."""
    def __init__(self, store, popen=subprocess.Popen):
        self.store=store;self.root=store.root;self.runtime=store.runtime
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.path=self.runtime/'experiment-job.json';self.lock=threading.RLock()
        self.popen=popen;self.process=None
        try:self.job=read_json(self.path)
        except (OSError,ValueError):self.job=None

    def owned(self):
        if not self.job:return False
        try:
            proc=Path('/proc')/str(self.job['pid'])
            stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
            return (proc.stat().st_uid==os.getuid() and stat[0]!='Z' and
                stat[19]==self.job['start_ticks'] and
                (proc/'cmdline').read_bytes().split(b'\0')[:-1]==[x.encode() for x in self.job['argv']])
        except (OSError,KeyError):return False

    def status(self):
        with self.lock:
            if self.process:self.process.poll()
            active=self.owned()
            live=session_status(self.runtime)
            external=bool(live.get('available') and (not self.job or live.get('output')!=self.job['output']))
            if not self.job:return dict(phase='external' if external else 'idle',active=external,can_prepare=not external)
            ready=bool(active and live.get('available') and live.get('output')==self.job['output'])
            phase='stopping' if active and self.job.get('stopping') else 'ready' if ready else 'loading' if active else 'ended'
            error=None
            if not active and self.process and self.process.returncode not in (None,0,-signal.SIGTERM):
                try:
                    with Path(self.job['log']).open('rb') as stream:
                        stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-1500))
                        error=stream.read().decode(errors='replace')[-800:]
                except OSError:error='任务进程退出，日志暂不可读'
                phase='error'
            return dict(phase=phase,active=active or external,can_prepare=not(active or external),
                managed=active,mode=self.job['mode'],algorithm_id=self.job.get('algorithm_id','autoserl'),model_label=self.job['model_label'],
                run_id=identifier(Path(self.job['output']),self.root),output=self.job['output'],error=error)

    def prepare(self, data):
        if set(data) not in ({'action','mode','model_id','episodes'}, {'action','mode','model_id','episodes','algorithm'}) or data['action']!='prepare':raise ValueError('准备参数不完整')
        mode=data['mode'];episodes=data['episodes']
        algorithm=data.get('algorithm','autoserl')
        if algorithm not in ALGORITHMS:raise ValueError('未知算法')
        if algorithm=='hilserl':
            from reproduction.hilserl.reward_classifier import active_metadata
            active_metadata(self.root)
        if algorithm=='hilserl' and mode=='evaluate':raise ValueError('HIL-SERL 请使用无干预评估')
        if mode not in ('fresh','resume','evaluate','evaluate_unassisted'):raise ValueError('未知运行方式')
        if type(episodes) is not int or not 1<=episodes<=100:raise ValueError('评估轮数应为 1–100')
        with self.lock:
            if not self.status()['can_prepare']:raise ValueError('请先结束当前任务，再准备另一个模型')
            with (self.runtime/'autoserl-actor.lock').open('a') as claim:
                try:fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError('已有训练或评估程序占用机械臂入口')
            source=checkpoint=None;label='从头训练 · 当前 1 条示范'
            if mode=='fresh':
                if data['model_id'] is not None:raise ValueError('从头训练不加载旧模型')
                self.store.catalog(force=True)
            else:
                source,checkpoint,model=self.store.model(data['model_id'],algorithm)
                label=f"{source.name} · 更新 {model['updates']}"
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            output=self.root/f'reproduction/logs/{algorithm}-web'/f'{stamp}-{mode}-{uuid.uuid4().hex[:6]}'
            output.parent.mkdir(parents=True,exist_ok=True)
            if mode in ('evaluate','evaluate_unassisted'):
                argv=[sys.executable,'-m',f'reproduction.{algorithm}.evaluate_frozen','--execute-attended-evaluation',
                    '--source-run',str(source),'--checkpoint',str(checkpoint),'--episodes',str(episodes),'--output',str(output)]
                if mode=='evaluate_unassisted':argv.append('--disable-automatic-assistance')
            else:
                argv=[sys.executable,'-m',f'reproduction.{algorithm}.train_online','--execute-attended-online',
                    '--continuous','--start-paused','--output',str(output)]
                if mode=='resume':argv+=['--resume-from',str(source),'--resume-checkpoint',str(checkpoint)]
            log=output.with_suffix('.stdout.log')
            with log.open('xb') as stream:
                process=self.popen(argv,cwd=self.root,stdin=subprocess.DEVNULL,stdout=stream,
                    stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            self.process=process
            try:tick=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            except OSError:tick=None
            self.job=dict(pid=process.pid,start_ticks=tick,argv=argv,output=str(output),log=str(log),
                mode=mode,algorithm_id=algorithm,model_label=label,prepared_unix=time.time(),stopping=False)
            atomic_json(self.path,self.job)
            return self.status()

    def stop(self):
        with self.lock:
            if not self.owned():raise ValueError('当前没有由网页启动的任务')
            self.job['stopping']=True;atomic_json(self.path,self.job)
            live=session_status(self.runtime)
            if live.get('output')==self.job['output']:
                if live.get('phase') in ('completed','stopped'):return dict(accepted=True)
                os.kill(self.job['pid'],signal.SIGTERM)  # Active runner stops and saves.
            else:
                # During preparation no motion or learning has started. Include
                # the unready learner child, which cannot yet handle shutdown IPC.
                if os.getpgid(self.job['pid'])!=self.job['pid']:raise ValueError('任务进程组不匹配')
                os.killpg(self.job['pid'],signal.SIGTERM)
            return dict(accepted=True)

    def close(self):
        if not self.owned():return True
        self.stop()
        deadline=time.monotonic()+30.
        while self.owned() and time.monotonic()<deadline:
            if self.process:self.process.poll()
            time.sleep(.1)
        return not self.owned()
