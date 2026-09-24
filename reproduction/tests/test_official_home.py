import threading
import time
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).parents[1]/'nuc'))
from official_home import JointHome, OfficialHomeMotion, ROSSwitcher, JOINT, CARTESIAN, HOME_Q, checked_switch_sample
from manual_guard import ManualGuard
from home_profile import PROFILE, plan_duration, cpp_header


class Switcher:
    def __init__(self):
        self.calls=[]
        self.allow=threading.Event();self.allow.set()
    def switch(self,start,stop):
        self.calls.append((start,stop))
        if not self.allow.wait(1): raise ValueError('test switch blocked')


class OfficialHomeTests(unittest.TestCase):
    def test_short_home_is_faster_and_long_paths_keep_the_existing_speed_cap(self):
        import math
        for delta in (0., .001, .2, .6, .8, 1.2, 1.28):
            seconds=plan_duration(delta)
            self.assertGreaterEqual(seconds,6.)
            self.assertLessEqual(seconds,12.)
            self.assertLessEqual(1.875*delta/seconds,.2+1e-12)
            self.assertLessEqual((10/math.sqrt(3))*delta/seconds**2,.1+1e-12)
            self.assertLessEqual(60*delta/seconds**3,.2+1e-12)
        self.assertEqual(plan_duration(.6),6.)
        self.assertAlmostEqual(plan_duration(1.28),12.)
        for invalid in (1.281,-.1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):plan_duration(invalid)

    def test_cpp_profile_and_preflight_use_the_same_timing(self):
        import shutil, subprocess, tempfile
        if not shutil.which('g++'):self.skipTest('g++ is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'home_profile_limits.h').write_text(cpp_header())
            (root/'probe.cpp').write_text('#include "home_profile.h"\n#include <iostream>\n#include <iomanip>\nint main(){for(double d:{0.,.001,.2,.6,.8,1.2,1.28})std::cout<<std::setprecision(17)<<hil_serl_home::duration(d)<<"\\n";}')
            subprocess.run(['g++','-std=c++17','-I'+str(root),'-I'+str(Path(__file__).parents[1]/'nuc'),str(root/'probe.cpp'),'-o',str(root/'probe')],check=True,capture_output=True)
            values=subprocess.check_output([str(root/'probe')],text=True).splitlines()
            for delta,value in zip((0.,.001,.2,.6,.8,1.2,1.28),values):
                self.assertAlmostEqual(float(value),plan_duration(delta),places=12)

    def test_connect_and_start_hold_measured_pose_without_using_home_goal(self):
        sample=dict(q=[-.2,-.8,.1,-2.6,.1,1.8,.5],dq=[0.]*7,
                    xyz=[.31,-.04,.47],rotation=np.eye(3).flatten(order='F').tolist(),
                    mode=2,current_errors=[],last_motion_errors=[],success=1.)
        lower=[-2.3093,-1.5133,-2.4937,-2.7478,-2.48,.8521,-2.6895]
        upper=[2.3093,1.5133,2.4937,-.4461,2.48,4.2094,2.6895]
        guard=ManualGuard(sample,lower,upper,trial_bounds=False,official_input=True)
        switcher=Switcher();joint=JointHome(switcher)
        home_transform=np.eye(4);home_transform[:3,3]=[.5,.1,.6]
        with patch('official_home.pose_and_jacobian',return_value=(home_transform,None)):
            motion=OfficialHomeMotion(guard,[],joint)
        original=guard.pose()
        self.assertEqual(motion.mode,'locked')
        self.assertEqual(motion.status()['home']['q'],HOME_Q)
        self.assertNotEqual(sample['q'],HOME_Q)
        for sequence,action,axes in [(0,'lock',[1.]*6),(1,'start',[1.]*6),(2,'start',[0.]*6)]:
            now=1.+.02*sequence
            sample['received_at']=now
            guard.check_state(sample,now,np.c_[np.eye(6),np.zeros(6)])
            packet=dict(seq=sequence,server_time=now,axes=axes,enable=False,stop=False,
                        pilot=dict(id=min(sequence,1),action=action,connected=True))
            self.assertEqual(motion.step(packet,now,.02,sample),original)
            self.assertEqual(joint.phase,'idle')
            self.assertEqual(switcher.calls,[])
        self.assertEqual(motion.mode,'manual')

    def sample(self):
        return dict(q=list(HOME_Q),q_d=list(HOME_Q),dq=[0.]*7,mode=2,received_at=time.monotonic()+.11)
    def test_switch_initialization_never_hides_faults_or_persistent_bad_rate(self):
        state=dict(mode=2,success=0.,current_errors=['collision'],last_motion_errors=[])
        checked=checked_switch_sample(state,'activating',10.,10.05)
        self.assertEqual(checked['success'],1.)
        self.assertEqual(checked['current_errors'],['collision'])
        self.assertEqual(state['success'],0.)
        for phase,mode,rate,now in [('active',2,0.,10.05),('activating',2,0.,10.16),
                                  ('activating',2,.5,10.05),('activating',0,0.,10.05),
                                  ('resuming',5,0.,10.05)]:
            raw=dict(state,mode=mode,success=rate)
            checked=checked_switch_sample(raw,phase,10.,now)
            self.assertEqual((checked['mode'],checked['success']),(mode,rate))
    def test_complete_home_returns_to_cartesian_hold(self):
        switcher=Switcher();home=JointHome(switcher)
        home.start();home.thread.join(1)
        self.assertEqual(home.phase,'activating')
        time.sleep(.11)
        home.poll(self.sample(),time.monotonic())
        self.assertEqual(home.phase,'active')
        now=home.changed_at+6.6;home.poll(self.sample(),now);home.poll(self.sample(),now+.25)
        home.thread.join(1);home.poll(self.sample(),time.monotonic())
        self.assertEqual(home.phase,'idle')
        self.assertEqual(switcher.calls,[(JOINT,CARTESIAN),(CARTESIAN,JOINT)])
    def test_diagnostic_goal_is_current_joints_without_changing_official_home(self):
        goal=[-.5,-.16,.35,-2.04,.03,1.88,-.18]
        home=JointHome(Switcher(),goal,minimum_duration=0.)
        goal[0]=99
        self.assertEqual(home.goal[0],-.5)
        self.assertEqual(HOME_Q[0],0.)
        home.phase='active';home.changed_at=time.monotonic()
        sample=dict(self.sample(),q=list(home.goal),q_d=list(home.goal))
        now=time.monotonic();home.poll(sample,now);home.poll(sample,now+.25)
        home.thread.join(1)
        self.assertEqual(home.phase,'resuming')
    def test_finish_during_pending_start_cannot_leave_joint_controller_active(self):
        switcher=Switcher();switcher.allow.clear();home=JointHome(switcher)
        home.start();home.request_stop();switcher.allow.set();home.thread.join(1)
        home.poll(self.sample(),time.monotonic())
        self.assertEqual(home.phase,'idle')
        self.assertEqual(switcher.calls,[(JOINT,CARTESIAN),(CARTESIAN,JOINT)])
    def test_finish_during_motion_and_repeat_finish(self):
        switcher=Switcher();home=JointHome(switcher)
        home.start();home.thread.join(1)
        home.request_stop();home.request_stop();home.thread.join(1)
        home.poll(self.sample(),time.monotonic())
        self.assertEqual(home.phase,'idle')
        self.assertEqual(len(switcher.calls),2)
    def test_other_controller_or_failed_switch_is_not_ignored(self):
        with self.assertRaisesRegex(ValueError,'unexpected active'):
            ROSSwitcher.check_running({'franka_state_controller':'running',CARTESIAN:'running','other':'running'},CARTESIAN)
        def fail(*args): raise ValueError('switch rejected')
        home=JointHome(SimpleNamespace(switch=fail));home.start();home.thread.join(1)
        with self.assertRaisesRegex(ValueError,'switch rejected'):home.poll(self.sample(),time.monotonic())
    def test_transition_and_motion_have_bounded_deadlines(self):
        home=JointHome(Switcher());home.phase='starting';home.changed_at=time.monotonic()-3
        with self.assertRaisesRegex(ValueError,'switch timed out'):home.poll(self.sample(),time.monotonic())
        home.phase='active';home.changed_at=time.monotonic()-15
        with self.assertRaisesRegex(ValueError,'joint Home timed out'):home.poll(self.sample(),time.monotonic())

    def test_droid_target_and_no_early_switch_before_profile_finishes(self):
        import math
        self.assertEqual(HOME_Q,[0.,-math.pi/5,0.,-4*math.pi/5,0.,3*math.pi/5,0.])
        home=JointHome(Switcher());home.phase='active';home.changed_at=100.
        near=self.sample()
        for elapsed in (1.,4.,6.,6.4):
            home.poll(near,100.+elapsed)
            self.assertEqual(home.phase,'active')
        near['q_d'][0]+=.0001
        for elapsed in (6.6,8.,11.,12.):home.poll(near,100.+elapsed)
        self.assertEqual(home.phase,'active')
        near['q_d']=list(HOME_Q)
        home.poll(near,112.3);home.poll(near,112.6);home.thread.join(1)
        self.assertEqual(home.phase,'resuming')

    def test_new_home_resets_previous_settle_window(self):
        home=JointHome(Switcher());home.settled_at=1.
        home.start();home.thread.join(1)
        self.assertIsNone(home.settled_at)
