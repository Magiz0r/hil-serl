import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

import numpy as np

sys.path.insert(0,str(Path(__file__).parents[1]/'nuc'))
from custom_home import CustomHomeStore
from manual_guard import ManualGuard
from official_home import OfficialHomeMotion, JointHome, ROSSwitcher, HOME_Q, JOINT, CUSTOM_JOINT, CARTESIAN


class Switcher:
    def __init__(self):
        self.calls=[];self.configured=[];self.allow=threading.Event();self.allow.set()
    def switch(self,start,stop):self.calls.append((start,stop))
    def configure_custom(self,q):
        if not self.allow.wait(1):raise ValueError('custom configuration timed out')
        self.configured.append(list(q))


class CustomHomeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'custom_home.json'
        self.store=CustomHomeStore(self.path)
        self.lower=np.array([-2.3093,-1.5133,-2.4937,-2.7478,-2.48,.8521,-2.6895])
        self.upper=np.array([2.3093,1.5133,2.4937,-.4461,2.48,4.2094,2.6895])
        self.sample=dict(q=[-.2,-.8,.1,-2.6,.1,1.8,.5],dq=[0.]*7,q_d=list(HOME_Q),
            xyz=[.3,0.,.6],rotation=np.eye(3).flatten(order='F').tolist(),mode=2,
            current_errors=[],last_motion_errors=[],success=1.)
        self.guard=ManualGuard(self.sample,self.lower,self.upper,trial_bounds=False,official_input=True)
        pose=np.eye(4);pose[:3,3]=self.sample['xyz'];self.jacobian=np.c_[np.eye(6),np.zeros(6)]
        self.mock_pose=patch('official_home.pose_and_jacobian',return_value=(pose,self.jacobian))
        self.mock_pose.start();self.addCleanup(self.mock_pose.stop)
        self.switcher=Switcher()
        self.motion=OfficialHomeMotion(self.guard,[],JointHome(self.switcher),self.store)
        self.sequence=0;self.command_id=0;self.action='lock'
        self.tick()

    def tick(self,action=None,connected=True):
        if action:self.command_id+=1;self.action=action
        self.sequence+=1;now=time.monotonic();self.sample['received_at']=now
        self.guard.check_state(self.sample,now,self.jacobian)
        return self.motion.step(dict(seq=self.sequence,server_time=now,axes=[0.]*6,enable=False,
            stop=False,pilot=dict(id=self.command_id,action=self.action,connected=connected)),now,.02,self.sample)

    def teach(self):
        before=self.guard.pose();self.tick('set_custom_home')
        self.motion.custom_worker.join(2);self.tick()
        self.assertEqual(self.guard.pose(),before)
        self.assertFalse(self.motion.custom_saving)
        self.assertTrue(self.motion.status()['custom_home']['available'])

    def finish_home(self):
        self.motion.joint.thread.join(1)
        self.tick('lock');self.motion.joint.thread.join(1)
        time.sleep(.11);self.tick()
        self.assertEqual(self.motion.mode,'locked')

    def begin_home(self,action):
        self.tick(action)
        for _ in range(25):
            if self.motion.preflight is None:break
            self.tick()
        self.motion.joint.thread.join(1)

    def test_teaching_overwriting_and_restart_never_change_droid_or_command_motion(self):
        droid=copy.deepcopy(self.motion.status()['home'])
        self.assertFalse(self.motion.status()['custom_home']['available'])
        self.teach();first=self.store.load(self.lower,self.upper)
        self.assertEqual(first['q'],self.sample['q'])
        self.sample['q'][0]+=.1;self.teach()
        self.assertNotEqual(first['q'],self.store.load(self.lower,self.upper)['q'])
        self.assertEqual(self.motion.status()['home'],droid)
        self.assertEqual(self.motion.droid_joint.goal,HOME_Q)
        restored=OfficialHomeMotion(self.guard,[],JointHome(self.switcher),self.store)
        self.assertEqual(restored.status()['custom_home']['q'],self.sample['q'])
        self.assertEqual(restored.status()['home'],droid)
        self.assertEqual(self.switcher.calls,[])

    def test_custom_and_droid_use_distinct_controller_instances_and_finish_interrupts_both(self):
        self.teach();custom=list(self.sample['q'])
        self.begin_home('custom_home')
        self.assertEqual(self.motion.active_home,'custom')
        self.assertEqual(self.motion.joint.goal,custom)
        self.assertEqual(self.switcher.calls[-1],(CUSTOM_JOINT,CARTESIAN))
        self.finish_home()
        self.assertEqual(self.switcher.calls[-1],(CARTESIAN,CUSTOM_JOINT))
        self.begin_home('home')
        self.assertEqual(self.motion.active_home,'droid')
        self.assertEqual(self.motion.joint.goal,HOME_Q)
        self.assertEqual(self.switcher.calls[-1],(JOINT,CARTESIAN))
        self.finish_home()
        self.assertEqual(self.switcher.calls[-1],(CARTESIAN,JOINT))
        self.assertEqual(self.store.load(self.lower,self.upper)['q'],custom)

    def test_unset_moving_and_manual_states_reject_custom_operations(self):
        self.tick('custom_home');self.assertIn('先设置',self.motion.error)
        self.tick('start');self.tick('set_custom_home')
        self.assertIn('Finish',self.motion.error);self.assertFalse(self.path.exists())
        self.tick('lock');self.sample['dq'][0]=.04;self.tick('set_custom_home')
        self.assertIn('停止',self.motion.error);self.assertFalse(self.path.exists())
        self.assertEqual(self.switcher.configured,[]);self.assertEqual(self.switcher.calls,[])

    def test_saving_blocks_new_start_or_home_but_keeps_finish_available(self):
        self.switcher.allow.clear();self.tick('set_custom_home')
        for action in ('start','home','custom_home'):
            self.tick(action);self.assertIn('正在保存',self.motion.error)
            self.assertEqual(self.motion.mode,'locked')
        self.tick('lock');self.assertIsNone(self.motion.error)
        self.switcher.allow.set();self.motion.custom_worker.join(2);self.tick()
        self.assertTrue(self.motion.status()['custom_home']['available'])
        self.assertEqual(self.switcher.calls,[])

    def test_bad_saved_file_and_save_failure_leave_droid_available(self):
        self.path.write_text('{broken')
        restored=OfficialHomeMotion(self.guard,[],JointHome(self.switcher),self.store)
        self.assertIsNotNone(restored.status()['custom_home']['error'])
        self.assertEqual(restored.droid_joint.goal,HOME_Q)
        self.path.unlink()
        with patch.object(self.store,'save',side_effect=OSError('disk full')):
            self.tick('set_custom_home');self.motion.custom_worker.join(2);self.tick()
        self.assertIn('disk full',self.motion.error)
        self.assertFalse(self.motion.status()['custom_home']['available'])
        self.begin_home('home')
        self.assertEqual(self.switcher.calls[-1],(JOINT,CARTESIAN));self.finish_home()

    def test_path_check_is_incremental_and_stop_prevents_any_motion(self):
        self.teach()
        self.tick('custom_home')
        self.assertEqual(self.motion.status()['joint_phase'],'checking')
        self.assertEqual(self.switcher.calls,[])
        self.tick('lock')
        for _ in range(25):self.tick()
        self.assertEqual(self.motion.mode,'locked');self.assertEqual(self.switcher.calls,[])
        self.tick('home');self.sample['q'][0]+=.01;self.tick()
        self.assertIn('moved during',self.motion.error)
        self.assertEqual(self.motion.mode,'locked');self.assertEqual(self.switcher.calls,[])

    def test_invalid_joint_values_do_not_overwrite_saved_target(self):
        self.teach();saved=self.path.read_bytes()
        for q in ([0.]*6,[float('nan')]*7,[True]*7,[99.]*7):
            with self.assertRaises(ValueError):self.store.save(q,self.lower,self.upper)
            self.assertEqual(self.path.read_bytes(),saved)

    def test_ros_reteach_only_reloads_inactive_custom_instance(self):
        switcher=object.__new__(ROSSwitcher)
        states={CARTESIAN:'running','franka_state_controller':'running',JOINT:'stopped'}
        params={JOINT:dict(target_joint_positions=list(HOME_Q))}
        calls=[]
        def service(path,_kind):
            def request(name):
                calls.append((path,name))
                self.assertEqual(name,CUSTOM_JOINT)
                if path.endswith('/unload_controller'):
                    self.assertEqual(states.pop(name),'initialized')
                else:states[name]='initialized'
                return SimpleNamespace(ok=True)
            return request
        switcher.states=lambda:dict(states)
        switcher.rospy=SimpleNamespace(ServiceProxy=service,
            set_param=lambda name,value:params.update({name.lstrip('/'):value}))
        services=SimpleNamespace(LoadController=Mock(),UnloadController=Mock())
        with patch.dict(sys.modules,{'controller_manager_msgs.srv':services}):
            first=list(self.sample['q']);switcher.configure_custom(first)
            second=list(first);second[0]+=.1;switcher.configure_custom(second)
            self.assertEqual(params[CUSTOM_JOINT]['target_joint_positions'],second)
            self.assertEqual(params[JOINT]['target_joint_positions'],HOME_Q)
            self.assertEqual([p.rsplit('/',1)[-1] for p,n in calls],
                             ['load_controller','unload_controller','load_controller'])
            states[CARTESIAN]='stopped';states[CUSTOM_JOINT]='running'
            with self.assertRaises(ValueError):switcher.configure_custom(first)
            self.assertEqual(len(calls),3)
