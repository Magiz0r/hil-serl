"""Classifier annotation and offline training; never sends robot commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from reproduction.hilserl.reward_data import RewardWorkspace
from reproduction.hilserl.reward_classifier import active_metadata,activate
from reproduction.portal.pilot_training import atomic_json


class RewardWorkbench:
    def __init__(self,root):
        self.root=Path(root);self.data=RewardWorkspace(root);self.lock=threading.RLock()
        self.directory=self.root/'reproduction/logs/hilserl-classifier'
        self.job_path=self.root/'reproduction/runtime/reward-job.json'
        self.process=None
        try:self.job=json.loads(self.job_path.read_text())
        except (OSError,ValueError):self.job=None

    def running(self):
        if self.process:self.process.poll()
        if not self.job:return False
        try:
            proc=Path('/proc')/str(self.job['pid'])
            stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
            return (proc.stat().st_uid==os.getuid() and stat[0]!='Z' and stat[19]==self.job['start_ticks'] and
                (proc/'cmdline').read_bytes().split(b'\0')[:-1]==[x.encode() for x in self.job['argv']])
        except (OSError,KeyError):return False

    def models(self):
        result=[]
        for path in sorted(self.directory.glob('*/manifest.json'),reverse=True):
            try:
                m=json.loads(path.read_text())
                if m.get('schema')=='hilserl_reward_classifier_v1' and m.get('synthetic') is False:
                    result.append(dict(id=path.parent.name,validation=m['validation'],threshold=m['threshold'],
                        compatible=m['context']==self.data.context()))
            except (OSError,ValueError,KeyError):continue
        return result

    def status(self):
        result=self.data.status();result.update(models=self.models(),running=self.running(),job=self.job['name'] if self.job else None)
        try:
            selected=active_metadata(self.root)
            result['active']=dict(name=Path(selected['path']).name,threshold=selected['threshold'],identity=selected['identity'])
        except (ValueError,OSError,KeyError) as error:result.update(active=None,requirement=str(error))
        if self.job:
            try:result['training']=json.loads((self.directory/self.job['name']/'status.json').read_text())
            except (OSError,ValueError):result['training']=dict(phase='starting' if result['running'] else 'ended')
        return result

    def request(self,data,cameras):
        with self.lock:
            op=data.get('action')
            if op=='label' and set(data)=={'action','id','label'}:return self.data.label(data['id'],data['label'])
            if op=='import' and set(data)=={'action','id','split'}:return self.data.import_source(data['id'],data['split'])
            if op=='capture' and set(data)=={'action','group','split','label'}:
                return self.data.capture(cameras,data['group'],data['split'],data['label'])
            if op=='activate' and set(data)=={'action','id'}:
                if not any(m['id']==data['id'] and m['compatible'] for m in self.models()):raise ValueError('Unknown task classifier')
                return activate(self.root,self.directory/data['id'])
            if op=='train' and set(data)=={'action','epochs','threshold'}:
                if self.running():raise ValueError('奖励分类器正在训练')
                if type(data['epochs']) is not int or not 1<=data['epochs']<=10000:raise ValueError('训练迭代次数应为 1–10000')
                if type(data['threshold']) not in (int,float) or not .0<data['threshold']<1.:raise ValueError('阈值应在 0 和 1 之间')
                if not self.data.status()['can_train']:raise ValueError('训练和验证都需要成功及未成功样本；请分回合采集')
                name=time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
                self.directory.mkdir(parents=True,exist_ok=True);output=self.directory/name
                argv=[sys.executable,'-m','reproduction.hilserl.reward_classifier','--output',str(output),
                    '--epochs',str(data['epochs']),'--threshold',str(data['threshold'])]
                with output.with_suffix('.stdout.log').open('xb') as stream:
                    self.process=subprocess.Popen(argv,cwd=self.root,stdin=subprocess.DEVNULL,stdout=stream,
                        stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
                stat=Path(f'/proc/{self.process.pid}/stat').read_text().rsplit(')',1)[1].split()
                self.job=dict(pid=self.process.pid,start_ticks=stat[19],argv=argv,name=name)
                atomic_json(self.job_path,self.job)
                return dict(accepted=True,job=name,robot_io=False)
            raise ValueError('Unknown classifier operation')
