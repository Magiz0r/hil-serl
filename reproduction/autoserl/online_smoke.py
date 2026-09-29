"""Exercise the actual online stack and GPU learner with an in-process fake robot."""
import argparse
import base64
import copy
import json
from pathlib import Path
import pickle
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from .bootstrap import configure,ROOT


class SyntheticPortal:
    def __init__(self, root, temporary):
        import cv2
        import numpy as np
        from scipy.spatial.transform import Rotation
        sys.path.insert(0,str(root/'reproduction/nuc'))
        from autoserl_capture import DemoGuard
        from official_home import JointHome
        from online_motion import OnlineMotion
        self.np=np;self.time=100.;self.seq=0;self.cid=1;self.count=0
        p=json.loads((root/'reproduction/configs/autoserl/fmb_insertion_online_v1.json').read_text())
        self.state=dict(q=p['custom_home_q'],dq=[0.]*7,xyz=p['initial_pose'][:3],
            rotation=Rotation.from_quat(p['initial_pose'][3:]).as_matrix().flatten(order='F').tolist(),
            received_at=self.time,stamp=self.time,mode=2,success=1.,current_errors=[],last_motion_errors=[],K_F_ext_hat_K=[0.]*6)
        self.guard=DemoGuard(self.state,[-4.]*7,[5.]*7,trial_bounds=False,official_input=True)
        self.guard.check_state(self.state,self.time,np.c_[np.eye(6),np.zeros(6)])
        def forbidden(*args):raise AssertionError('Synthetic check attempted controller switching')
        with patch('official_home.pose_and_jacobian',return_value=(np.eye(4),None)):
            self.motion=OnlineMotion(self.guard,[],JointHome(SimpleNamespace(switch=forbidden)),
                recovery_plan=json.loads((root/'reproduction/configs/autoserl/fmb_insertion_probe_v3.json').read_text()),
                recovery_log=Path(temporary)/'recovery.jsonl',online_plan=p,online_log=Path(temporary)/'online.jsonl')
        self.motion.custom_saved=dict(q=p['custom_home_q'],saved_unix=0.)
        self.images={}
        for i,name in enumerate(('external','wrist')):
            ok,image=cv2.imencode('.jpg',np.full((720,1280,3),40+80*i,np.uint8));assert ok
            self.images[name]=dict(jpeg_base64=base64.b64encode(image).decode(),sequence=1,pc_captured_at=time.monotonic())

    def tick(self,policy=None,command='policy_start'):
        self.time+=.02;self.seq+=1
        s=self.state;g=self.guard;np=self.np
        s.update(received_at=self.time,stamp=self.time,xyz=g.target.tolist(),
                 rotation=g.target_rotation.as_matrix().flatten(order='F').tolist())
        g.check_state(s,self.time,np.c_[np.eye(6),np.zeros(6)])
        pilot=dict(id=self.cid,action=command,connected=True)
        if policy is not None:pilot['policy']=policy
        self.motion.step(dict(seq=self.seq,server_time=self.time,axes=[0.]*6,enable=False,stop=False,pilot=pilot),self.time,.02,s)

    def observation(self):
        return dict(state=copy.deepcopy(self.guard.capture_observation),images=self.images,
            gripper_position=130,pilot=self.motion.status(),synthetic=True,pc_observed_at=time.monotonic())

    def request(self,data):
        if data['operation']=='observe':return self.observation()
        if data['operation']=='start':
            assert data['plan_sha256']==self.motion.online_sha
            self.tick();assert self.motion.mode=='policy'
            return self.observation()
        if data['operation']=='step':
            self.count+=1
            for i in range(20):
                self.tick(dict(index=data['index'],action=data['action'],age=0.,buttons=[self.count==120 and i>=3,False]))
                if self.motion.online['completed_index']==data['index']:return self.observation()
                if self.motion.mode!='policy':raise RuntimeError('Synthetic episode stopped: '+str(self.motion.online['reason']))
            raise AssertionError('No action acknowledgement')
        raise ValueError('Unknown synthetic request')

    def stop(self):
        self.cid+=1;self.tick(command='lock')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();configure()
    import jax
    import numpy as np
    from .online_env import PortalEnv,PortalSignals
    from .intervention import AutoIntervention
    from .train_online import Training
    if jax.default_backend()!='gpu':raise RuntimeError('This check requires the local GPU')
    with tempfile.TemporaryDirectory(prefix='autoserl-online-synthetic-') as tmp:
        transport=SyntheticPortal(ROOT,tmp);base=PortalEnv(ROOT,transport)
        selection=base.selection
        with (ROOT/selection['demo_path']).open('rb') as f:demo=pickle.load(f)
        training=Training(args.output,base.preview(),demo,batch_size=4,training_starts=16,capacity=512)
        env=AutoIntervention(base,np.zeros(6),expert=PortalSignals(),demo_path=ROOT/selection['demo_path'],
            demo_initial_tcp_pose=selection['demo_initial_tcp_pose'],recover_point0=selection['recover_point0'],
            recover_point1=selection['recover_point1'],**selection['control_parameters'])
        obs,_=env.reset();count=interventions=0;reward=0.
        try:
            for i in range(120):
                action=training.sample(obs)
                nxt,reward,done,truncated,info=env.step(action)
                t=dict(observations=obs,actions=info['executed_action'],next_observations=nxt,
                       rewards=float(reward),masks=float(not done),dones=bool(done or truncated))
                training.insert(t,info['auto_intervention']);count+=1;interventions+=int(info['auto_intervention']);obs=nxt
                if done or truncated:break
            deadline=time.monotonic()+60
            while training.gradient_updates<2 and time.monotonic()<deadline:
                if training.error:raise RuntimeError(training.error)
                time.sleep(.1)
            assert count==120 and reward==1. and interventions>0
            assert training.gradient_updates>=2
        finally:
            base.close();training.close()
        report=dict(status='pass',synthetic=True,robot_io=False,transitions=count,automatic_interventions=interventions,
            recoveries=env.total_recover_cnt,gradient_updates=training.gradient_updates,
            initial_demo_episodes=1,terminal_success_label=bool(reward),
            scope='Actual online action gate, observation encoding, AutoIntervention, replay buffers and GPU learner; synthetic dynamics only')
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
