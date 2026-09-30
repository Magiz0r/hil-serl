"""Original classifier training, held-out validation and checked runtime loading."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time

import numpy as np

from reproduction.autoserl.bootstrap import ROOT, configure
from reproduction.portal.pilot_training import atomic_json
from .reward_data import RewardWorkspace, KEYS, digest


def file_hashes(directory):
    directory=Path(directory)
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def active_metadata(root):
    root=Path(root).resolve();path=root/'reproduction/data/hilserl/reward/active.json'
    if not path.exists():raise ValueError('HIL-SERL 需要任务奖励分类器；请先标注成功/未成功画面、训练并选用模型')
    active=json.loads(path.read_text());folder=(root/active['path']).resolve()
    if not folder.is_relative_to(root/'reproduction/logs/hilserl-classifier'):raise ValueError('Invalid classifier path')
    manifest=json.loads((folder/'manifest.json').read_text())
    if manifest.get('synthetic') is not False or manifest.get('schema')!='hilserl_reward_classifier_v1':
        raise ValueError('Only validated real-image classifiers can be selected')
    if manifest['context']!=RewardWorkspace(root).context():raise ValueError('Classifier task/demo or image crops changed')
    hashes=file_hashes(folder/'classifier_ckpt')
    if not hashes or hashes!=manifest['checkpoint_files'] or digest(manifest)!=active['manifest_sha256']:
        raise ValueError('Classifier checkpoint/manifest changed')
    if not 0<float(manifest['threshold'])<1:raise ValueError('Invalid classifier threshold')
    if manifest.get('validation',{}).get('samples',0)<2:raise ValueError('Classifier has no held-out validation')
    return dict(path=str(folder),identity=active['manifest_sha256'],threshold=manifest['threshold'],manifest=manifest)


class Classifier:
    def __init__(self,folder,threshold,metadata=None):
        configure()
        import jax
        from flax.training import checkpoints
        from serl_launcher.networks.reward_classifier import load_classifier_func
        self.threshold=float(threshold);self.metadata=metadata
        if not math.isfinite(self.threshold) or not 0<self.threshold<1:raise ValueError('Invalid threshold')
        checkpoint=Path(folder)/'classifier_ckpt'
        # Upstream restore silently returns random initial weights if no checkpoint exists.
        if checkpoints.latest_checkpoint(str(checkpoint)) is None:raise ValueError('Missing trained classifier checkpoint')
        sample={k:np.zeros((1,128,128,3),np.uint8) for k in KEYS}
        self.logits=load_classifier_func(jax.random.PRNGKey(0),sample,list(KEYS),str(checkpoint))
        self(sample)  # Compile before any physical episode starts.

    def __call__(self,obs):
        # Keep the JIT input tree identical to warmup; proprio is unused upstream.
        value=float(np.asarray(self.logits({key:obs[key] for key in KEYS})).reshape(-1)[0])
        if not math.isfinite(value):raise ValueError('Non-finite reward classifier output')
        probability=float(1/(1+np.exp(-np.clip(value,-80,80))))
        return dict(probability=probability,success=probability>self.threshold)


def load_active(root):
    metadata=active_metadata(root)
    return Classifier(metadata['path'],metadata['threshold'],metadata=metadata)


def validation_metrics(labels,probabilities,threshold):
    y=np.asarray(labels);p=np.asarray(probabilities,float)
    if y.shape!=p.shape or not len(y) or not np.isfinite(p).all() or set(y.tolist())!={0,1}:
        raise ValueError('Validation requires both labelled classes and finite probabilities')
    pred=p>threshold
    tp=int(np.sum(pred & (y==1)));fp=int(np.sum(pred & (y==0)))
    tn=int(np.sum(~pred & (y==0)));fn=int(np.sum(~pred & (y==1)))
    return dict(samples=len(y),true_positive=tp,false_positive=fp,true_negative=tn,false_negative=fn,
        precision=tp/(tp+fp) if tp+fp else 0.,recall=tp/(tp+fn),
        false_positive_rate=fp/(fp+tn),accuracy=(tp+tn)/len(y))


def train(root,output,*,epochs=150,batch_size=256,threshold=.85,synthetic=False):
    root=Path(root).resolve();output=Path(output).resolve()
    if not output.is_relative_to(root/'reproduction/logs/hilserl-classifier'):
        raise ValueError('Classifier output must be under reproduction/logs/hilserl-classifier')
    if epochs<1 or batch_size<2 or batch_size%2 or not 0<threshold<1:raise ValueError('Invalid training settings')
    dataset=RewardWorkspace(root).export(output)
    dataset['synthetic']=bool(synthetic);atomic_json(output/'dataset.json',dataset)
    atomic_json(output/'status.json',dict(phase='training',robot_io=False))
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH',''))
    command=[sys.executable,'-m','reproduction.hilserl.upstream','train_reward_classifier',
             f'--num_epochs={epochs}',f'--batch_size={batch_size}']
    try:
        with (output/'training.log').open('x') as stream:
            subprocess.run(command,cwd=output,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
        predictor=Classifier(output,threshold)
        labels=[];probabilities=[]
        for label,name in [(0,'failure'),(1,'success')]:
            with (output/'validation_data'/(name+'.pkl')).open('rb') as stream:rows=pickle.load(stream)
            for row in rows:
                labels.append(label);probabilities.append(predictor(row['observations'])['probability'])
        report=validation_metrics(labels,probabilities,threshold)
        manifest=dict(schema='hilserl_reward_classifier_v1',synthetic=bool(synthetic),context=dataset['context'],
            dataset_sha256=digest(dataset),checkpoint_files=file_hashes(output/'classifier_ckpt'),threshold=threshold,
            epochs=epochs,batch_size=batch_size,validation=report,created_unix=time.time(),
            upstream_sources={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ('examples/train_reward_classifier.py','serl_launcher/serl_launcher/networks/reward_classifier.py')})
        atomic_json(output/'manifest.json',manifest)
        atomic_json(output/'validation.json',dict(report,labels=labels,probabilities=probabilities,threshold=threshold))
        atomic_json(output/'status.json',dict(phase='validated',robot_io=False,validation=report))
        return manifest
    except Exception as error:
        atomic_json(output/'status.json',dict(phase='error',error=str(error),robot_io=False))
        raise


def activate(root,folder):
    root=Path(root).resolve();folder=Path(folder).resolve()
    if not folder.is_relative_to(root/'reproduction/logs/hilserl-classifier'):raise ValueError('Unknown classifier directory')
    manifest=json.loads((folder/'manifest.json').read_text())
    # Validation is displayed to the operator; selecting a model is explicit,
    # not an invented statistical guarantee based on training accuracy.
    if manifest.get('schema')!='hilserl_reward_classifier_v1' or manifest.get('synthetic') is not False:
        raise ValueError('Expected real classifier checkpoint')
    if manifest.get('context')!=RewardWorkspace(root).context() or not manifest.get('validation',{}).get('samples'):
        raise ValueError('Classifier task mismatch or missing validation')
    if not manifest.get('checkpoint_files') or file_hashes(folder/'classifier_ckpt')!=manifest['checkpoint_files']:
        raise ValueError('Classifier weights changed')
    active=dict(path=str(folder.relative_to(root)),manifest_sha256=digest(manifest))
    atomic_json(root/'reproduction/data/hilserl/reward/active.json',active)
    return active


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=150)
    parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--threshold',type=float,default=.85)
    args=parser.parse_args();configure()
    runtime=ROOT/'reproduction/runtime';runtime.mkdir(exist_ok=True)
    with (runtime/'autoserl-actor.lock').open('a') as claim:
        fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps(train(ROOT,args.output,epochs=args.epochs,batch_size=args.batch_size,threshold=args.threshold)))


if __name__=='__main__':main()
