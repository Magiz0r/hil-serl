"""Task-specific frame labels and episode-separated classifier datasets."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import re
import threading
import time

import cv2
import numpy as np

from reproduction.portal.pilot_training import atomic_json

KEYS=('wrist_1','wrist_2')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def images(obs):
    result={}
    for key in KEYS:
        array=np.asarray(obs[key])
        if array.shape!=(1,128,128,3) or array.dtype!=np.uint8:
            raise ValueError('Classifier images must match policy crops: uint8 [1,128,128,3]')
        result[key]=array.copy()
    return result


def label_counts(data):
    counts={split:{str(label):0 for label in (0,1)} for split in ('train','validation')}
    for row in data['frames']:
        if row['label'] in (0,1):counts[data['groups'][row['group']]][str(row['label'])]+=1
    return counts


class RewardWorkspace:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.directory=self.root/'reproduction/data/hilserl/reward'
        self.path=self.directory/'labels.json'
        self.lock=threading.RLock()

    def context(self):
        # Shared task configuration only: recovery actions are never used here.
        path=self.root/'reproduction/configs/autoserl/fmb_insertion_images_v1.json'
        selection=json.loads((self.root/'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json').read_text())
        return dict(preprocessing=json.loads(path.read_text()),demo_sha256=selection['demo_pickle_sha256'])

    def read(self):
        if not self.path.exists():return dict(schema='hilserl_reward_labels_v1',context=self.context(),frames=[],groups={})
        value=json.loads(self.path.read_text())
        if value['context']!=self.context():raise ValueError('Task/demo or camera crops changed; classifier labels need a separate workspace')
        return value

    def sources(self):
        selection=json.loads((self.root/'reproduction/configs/autoserl/fmb_insertion_recovery_v1.json').read_text())
        paths=[self.root/selection['demo_path']]
        for manifest in (self.root/'reproduction/logs').rglob('manifest.json'):
            try:
                m=json.loads(manifest.read_text())
                if m.get('synthetic') is False and m.get('demo_sha256')==selection['demo_pickle_sha256']:
                    for p in sorted(manifest.parent.glob('episode_*.pkl.gz')):
                        meta=p.with_name(p.name[:-7]+'.json')
                        if meta.exists() and json.loads(meta.read_text()).get('outcome') in ('success','failure'):
                            paths.append(p)
            except (OSError,ValueError):continue
        return {hashlib.sha256(str(p.relative_to(self.root)).encode()).hexdigest()[:20]:p
                for p in paths if p.is_file() and p.resolve().is_relative_to(self.root)}

    def status(self):
        with self.lock:
            data=self.read()
            counts=label_counts(data)
            sources=[dict(id=key,name=str(path.relative_to(self.root)))
                     for key,path in self.sources().items()]
            frames=[dict(row,index=i) for i,row in enumerate(data['frames'])]
            return dict(counts=counts,frames=frames,sources=sources,groups=data['groups'],
                        context=data['context'],can_train=all(v>0 for c in counts.values() for v in c.values()))

    def add(self,obs,*,group,split,source,label=None):
        if split not in ('train','validation') or (label is not None and type(label) is not int) or label not in (None,0,1):
            raise ValueError('Invalid classifier label or split')
        obs=images(obs)
        if not isinstance(group,str) or not 1<=len(group)<=160:raise ValueError('Expected an episode/group name')
        with self.lock:
            data=self.read()
            if group in data['groups'] and data['groups'][group]!=split:
                raise ValueError('同一回合不能分到训练和验证两边；验证请另采一轮')
            fingerprint=hashlib.sha256(b''.join(obs[k].tobytes() for k in KEYS)).hexdigest()
            existing=next((r for r in data['frames'] if r['id']==fingerprint),None)
            if existing:
                if data['groups'][existing['group']]!=split:raise ValueError('重复图像不能跨训练/验证集')
                if label is not None:
                    existing['label']=label;atomic_json(self.path,data)
                return existing['id']
            data['groups'][group]=split
            folder=self.directory/'frames';folder.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(folder/(fingerprint+'.npz'),**obs)
            data['frames'].append(dict(id=fingerprint,group=group,source=source,label=label))
            atomic_json(self.path,data)
            return fingerprint

    def import_source(self,sid,split):
        sources=self.sources()
        if sid not in sources:raise ValueError('Unknown saved episode')
        path=sources[sid]
        with (gzip.open(path,'rb') if path.name.endswith('.gz') else path.open('rb')) as stream:rows=pickle.load(stream)
        # A successful episode is NOT a collection of positive frames.
        group='episode:'+sid
        for i,row in enumerate(rows):
            self.add(row['next_observations'],group=group,split=split,source=dict(episode=sid,step=i))
        return dict(imported=len(rows),labels='unlabelled')

    def capture(self,cameras,group,split,label):
        crops=self.context()['preprocessing']['crops_xywh'];obs={}
        for key,name in zip(KEYS,('external','wrist')):
            camera=cameras[name];frame=camera.snapshot()
            if not frame or camera.error or not 0<=time.monotonic()-frame['pc_captured_at']<=.35:
                raise ValueError('相机画面过期，未采样')
            bgr=cv2.imdecode(np.frombuffer(frame['jpeg'],np.uint8),cv2.IMREAD_COLOR)
            if bgr is None or bgr.shape!=(720,1280,3):raise ValueError('Unexpected camera resolution')
            x,y,w,h=crops[name]
            obs[key]=cv2.resize(cv2.cvtColor(bgr[y:y+h,x:x+w],cv2.COLOR_BGR2RGB),(128,128),interpolation=cv2.INTER_AREA)[None]
        return dict(id=self.add(obs,group='live:'+group,split=split,source=dict(captured_unix=time.time()),label=label))

    def label(self,fid,value):
        if value is not None and (type(value) is not int or value not in (0,1)):raise ValueError('Invalid label')
        with self.lock:
            data=self.read();row=next((r for r in data['frames'] if r['id']==fid),None)
            if row is None:raise ValueError('Unknown frame')
            row['label']=value;atomic_json(self.path,data)
        return dict(accepted=True)

    def observation(self,fid):
        if not re.fullmatch('[a-f0-9]{64}',fid):raise ValueError('Invalid frame id')
        with np.load(self.directory/'frames'/(fid+'.npz'),allow_pickle=False) as value:
            obs=images(value)
        if hashlib.sha256(b''.join(obs[k].tobytes() for k in KEYS)).hexdigest()!=fid:raise ValueError('Stored image changed')
        return dict(obs,state=np.zeros((1,19),np.float32))

    def preview(self,fid):
        obs=self.observation(fid);result={}
        for key in KEYS:
            ok,data=cv2.imencode('.png',cv2.cvtColor(obs[key][0],cv2.COLOR_RGB2BGR))
            if not ok:raise ValueError('Cannot encode classifier preview')
            result[key]='data:image/png;base64,'+base64.b64encode(data).decode()
        return result

    def export(self,output):
        output=Path(output)
        with self.lock:
            # The UI may keep annotating in another process. Counts, labels and
            # exported rows must all describe this one immutable snapshot.
            data=self.read();counts=label_counts(data)
            if not all(v>0 for c in counts.values() for v in c.values()):
                raise ValueError('训练集和独立验证集都需要成功及未成功画面')
            output.mkdir(parents=True,exist_ok=False)
            for split,folder in [('train','classifier_data'),('validation','validation_data')]:
                target=output/folder;target.mkdir()
                for label,name in [(1,'success'),(0,'failure')]:
                    rows=[]
                    for row in data['frames']:
                        if row['label']!=label or data['groups'][row['group']]!=split:continue
                        obs=self.observation(row['id'])
                        rows.append(dict(observations=obs,next_observations=obs,actions=np.zeros(6,np.float32),
                            rewards=float(label),masks=0.,dones=True))
                    with (target/(name+'.pkl')).open('xb') as stream:pickle.dump(rows,stream)
            manifest=dict(schema='hilserl_reward_dataset_v1',synthetic=False,context=data['context'],
                counts=counts,labels_sha256=digest(data),groups=data['groups'],frames=data['frames'])
            atomic_json(output/'dataset.json',manifest)
            return manifest
