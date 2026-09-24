"""DROID joint target using the isolated HIL-SERL smooth Home controller.

Uses only our ROS master and switches back to measured-pose impedance holding.
No error recovery, Desk changes, gripper motion, or new controller binaries.
"""
import math
import threading
import time
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from fr3_kinematics import pose_and_jacobian, checked_jacobian
from pilot_motion import PilotMotion
from home_profile import PROFILE, plan_duration
from custom_home import checked_joints

FRANKA_EXAMPLE_Q = [0., -math.pi/4, 0., -3*math.pi/4, 0., math.pi/2, math.pi/4]
HOME_Q = [0., -math.pi/5, 0., -4*math.pi/5, 0., 3*math.pi/5, 0.]
HOME_SOURCE = 'droid_default'
# The plugin computes a 6–12 s quintic from the desired joints at activation.
# A small measured residual alone must never terminate an unfinished profile.
MINIMUM_HOME_SECONDS = PROFILE['hold_seconds'] + PROFILE['minimum_duration'] + .3
CARTESIAN = 'cartesian_impedance_controller'
JOINT = 'hil_serl_official_home_controller'
CUSTOM_JOINT = 'hil_serl_custom_home_controller'
SWITCH_PHASES = ('starting','activating','stopping','resuming')


def activate_home_controller():
    source = Path(__file__).parent
    manifest = json.loads((source/'home_controller_artifacts.json').read_text())
    prefix = Path(manifest['prefix'])
    prefix.relative_to('/hil-serl-state/home-controller')
    for name,digest in manifest['source_sha256'].items():
        if Path(name).name != name or hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Home controller source does not match its build')
    for relative,digest in manifest['files'].items():
        path = (Path('/hil-serl-state')/relative).resolve()
        path.relative_to(prefix)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Home controller artifact changed: '+relative)
    for key,path in [('ROS_PACKAGE_PATH',prefix/'share'),('CMAKE_PREFIX_PATH',prefix),('LD_LIBRARY_PATH',prefix/'lib')]:
        os.environ[key]=str(path)+':'+os.environ.get(key,'')
    os.environ['HIL_SERL_HOME_PREFIX']=str(prefix)
    return manifest


def checked_switch_sample(sample, phase, changed_at, now):
    """Handle only Idle and a brief zero-valued command-rate initialization.

    The FR3 can publish Move with success=0 immediately after a new control
    session starts. Errors, unexpected modes and nonzero degraded rates retain
    the normal guard checks. The original state is always logged unchanged.
    """
    if phase not in SWITCH_PHASES or sample['mode'] not in (1,2):
        return sample
    success = sample['success']
    if not math.isfinite(success) or not 0 <= success <= 1:
        raise ValueError('invalid command success rate during controller switch')
    initializing = success == 0 and 0 <= now-changed_at <= .15
    return dict(sample, mode=2, success=1. if initializing else success)


class ROSSwitcher:
    def __init__(self, goal=HOME_Q):
        import rospy
        from controller_manager_msgs.srv import ListControllers, LoadController, SwitchController
        self.rospy = rospy
        self.list = rospy.ServiceProxy('/controller_manager/list_controllers', ListControllers)
        self.switch_service = rospy.ServiceProxy('/controller_manager/switch_controller', SwitchController)
        states = self.states()
        self.check_running(states, CARTESIAN)
        if JOINT in states:
            raise ValueError('official Home controller already exists')
        rospy.set_param('/' + JOINT, dict(type='hil_serl_home/HomeController',
            target_joint_positions=list(goal), arm_id='fr3', joint_names=['fr3_joint%d'%i for i in range(1,8)]))
        if not rospy.ServiceProxy('/controller_manager/load_controller', LoadController)(JOINT).ok:
            raise ValueError('could not load stopped official Home controller')
        states = self.states()
        self.check_running(states, CARTESIAN)
        if states.get(JOINT) not in ('initialized','stopped'):
            raise ValueError('Home controller did not load in an inactive state')

    def states(self):
        return {controller.name: controller.state for controller in self.list().controller}

    def configure_custom(self, goal):
        """Configure only a stopped custom instance; DROID parameters stay fixed."""
        from controller_manager_msgs.srv import LoadController, UnloadController
        states=self.states();self.check_running(states,CARTESIAN)
        if CUSTOM_JOINT in states:
            if states[CUSTOM_JOINT] not in ('initialized','stopped'):
                raise ValueError('custom Home controller is not inactive')
            if not self.rospy.ServiceProxy('/controller_manager/unload_controller',UnloadController)(CUSTOM_JOINT).ok:
                raise ValueError('could not unload inactive custom Home controller')
        self.rospy.set_param('/'+CUSTOM_JOINT,dict(type='hil_serl_home/HomeController',
            target_joint_positions=list(goal),arm_id='fr3',joint_names=['fr3_joint%d'%i for i in range(1,8)]))
        if not self.rospy.ServiceProxy('/controller_manager/load_controller',LoadController)(CUSTOM_JOINT).ok:
            raise ValueError('could not load inactive custom Home controller')
        states=self.states();self.check_running(states,CARTESIAN)
        if states.get(CUSTOM_JOINT) not in ('initialized','stopped'):
            raise ValueError('custom Home controller did not load in an inactive state')

    @staticmethod
    def check_running(states, expected):
        running = {name for name,state in states.items() if state == 'running'}
        if running != {'franka_state_controller', expected}:
            raise ValueError('unexpected active controllers: %r' % running)

    def switch(self, start, stop):
        self.check_running(self.states(), stop)
        result = self.switch_service(start_controllers=[start], stop_controllers=[stop],
            strictness=2, start_asap=False, timeout=1.)
        if not result.ok:
            raise ValueError('controller switch failed')
        self.check_running(self.states(), start)


class JointHome:
    def __init__(self, switcher, goal=HOME_Q, minimum_duration=MINIMUM_HOME_SECONDS, controller=JOINT):
        self.switcher = switcher
        self.goal = list(goal)
        self.controller = controller
        self.minimum_duration = minimum_duration
        self.phase, self.error = 'idle', None
        self.cancel = threading.Event()
        self.changed_at = time.monotonic()
        self.settled_at = None
        self.thread = None

    def start(self):
        if self.phase != 'idle':
            raise ValueError('joint Home is already active')
        self.cancel.clear()
        self.settled_at = None
        self.phase, self.changed_at = 'starting', time.monotonic()
        self.thread = threading.Thread(target=self.enter, daemon=True)
        self.thread.start()

    def enter(self):
        try:
            self.switcher.switch(self.controller, CARTESIAN)
            self.changed_at = time.monotonic()
            if self.cancel.is_set():
                self.phase = 'stopping'
                self.leave()
            else:
                self.phase = 'activating'
        except Exception as error:
            self.error = str(error)

    def request_stop(self):
        self.cancel.set()
        if self.phase in ('active','activating'):
            self.phase, self.changed_at = 'stopping', time.monotonic()
            self.thread = threading.Thread(target=self.leave, daemon=True)
            self.thread.start()

    def leave(self):
        try:
            self.switcher.switch(CARTESIAN, self.controller)
            self.phase, self.changed_at = 'resuming', time.monotonic()
        except Exception as error:
            self.error = str(error)

    def poll(self, sample, now):
        if self.error:
            raise ValueError(self.error)
        healthy = sample['mode'] == 2 and sample.get('success',1.) >= .95
        if self.phase == 'activating' and healthy and sample['received_at'] > self.changed_at+.1:
            self.phase = 'active'
        if self.cancel.is_set() and self.phase in ('active','activating'):
            self.request_stop()
        if self.phase == 'resuming' and healthy and sample['received_at'] > self.changed_at+.1:
            self.phase = 'idle'
        if self.phase in ('starting','activating','stopping','resuming') and now-self.changed_at > 2:
            raise ValueError('Home controller switch timed out')
        if self.phase == 'active':
            if now-self.changed_at > 14:
                raise ValueError('official joint Home timed out')
            desired = sample.get('q_d')
            desired_finished = (desired is not None and len(desired) == 7 and
                                max(abs(a-b) for a,b in zip(desired,self.goal)) < 1e-6)
            reached = (now-self.changed_at >= self.minimum_duration and desired_finished and
                       max(abs(a-b) for a,b in zip(sample['q'],self.goal)) < .005)
            settled = max(abs(v) for v in sample['dq']) < .03
            if reached and settled:
                self.settled_at = self.settled_at or now
                if now-self.settled_at > .2:
                    self.request_stop()
            else:
                self.settled_at = None


class OfficialHomeMotion(PilotMotion):
    ACTIONS = PilotMotion.ACTIONS + ('set_custom_home','custom_home')

    def __init__(self, guard, chain, joint, custom_store=None):
        super().__init__(guard)
        self.chain, self.joint = chain, joint
        self.droid_joint=joint
        self.custom_store=custom_store
        self.custom_joint=None
        self.custom_saved=None
        self.custom_error=None
        self.custom_saving=False
        self.custom_result=None
        self.custom_worker=None
        self.custom_request_id=None
        self.active_home='droid'
        self.goal = joint.goal
        transform, _ = pose_and_jacobian(chain, self.goal)
        self.home = (transform[:3,3].copy(), Rotation.from_matrix(transform[:3,:3]), list(self.goal))
        self.preflight = None
        self.preflight_q = None
        self.preflight_deadline = None
        self.had_joint_control = False
        if np.any(np.array(self.goal) <= guard.lower) or np.any(np.array(self.goal) >= guard.upper):
            raise ValueError('official joint Home is outside this robot model')
        if custom_store:
            try:
                self.custom_saved=custom_store.load(guard.lower,guard.upper)
                if self.custom_saved:
                    joint.switcher.configure_custom(self.custom_saved['q'])
                    self.custom_joint=JointHome(joint.switcher,self.custom_saved['q'],controller=CUSTOM_JOINT)
            except Exception as error:self.custom_error=str(error)

    def status(self):
        return dict(super().status(), home_kind='official_joint', joint_phase='checking' if self.preflight is not None else self.joint.phase,
                    home_source=HOME_SOURCE if np.allclose(self.goal, HOME_Q) else 'diagnostic_current_joints',
                    home_minimum_seconds=self.joint.minimum_duration,
                    home_profile_seconds=[PROFILE['minimum_duration'], PROFILE['maximum_duration']],
                    home_joint_error_rad=float(np.max(np.abs(self.guard.q-self.goal))),
                    active_home=self.active_home,
                    active_home_joint_error_rad=float(np.max(np.abs(self.guard.q-self.joint.goal))),
                    custom_home=dict(available=self.custom_joint is not None, saving=self.custom_saving,
                        persistent=self.custom_store is not None, error=self.custom_error,
                        q=self.custom_saved['q'] if self.custom_saved else None,
                        saved_unix=self.custom_saved['saved_unix'] if self.custom_saved else None,
                        joint_error_rad=float(np.max(np.abs(self.guard.q-self.custom_joint.goal))) if self.custom_joint else None))

    def hold(self):
        self.preflight=None
        super().hold()
        if self.joint.phase != 'idle':
            self.joint.request_stop()
            self.mode = 'homing'

    def prepare(self, sample, goal=None):
        # The plugin uses the same timing configuration and also validates q_d.
        # Check the full joint path before switching, using the actual FR3 model.
        q = np.asarray(sample['q'])
        goal=np.asarray(checked_joints(list(self.goal if goal is None else goal),self.guard.lower,self.guard.upper))
        target_transform,_=pose_and_jacobian(self.chain,goal)
        if max(abs(v) for v in sample['dq']) > .03:
            raise ValueError('wait for the arm to settle before Home')
        plan_duration(float(np.max(np.abs(goal-q))))
        for fraction in np.linspace(0,1,101):
            candidate = q + fraction*(goal-q)
            transform, jacobian = pose_and_jacobian(self.chain,candidate)
            if np.linalg.svd(jacobian,compute_uv=False)[-1] < .05:
                raise ValueError('joint Home path approaches a singularity')
            if transform[2,3] < min(sample['xyz'][2],target_transform[2,3])-.005:
                raise ValueError('joint Home path dips below the start height; lift clear before Home')
            yield

    def step(self, packet, now, dt, sample):
        if self.custom_result is not None:
            saved,error=self.custom_result;self.custom_result=None
            self.custom_saving=False;self.custom_error=error
            if error:
                self.custom_joint=None
                if self.command_id==self.custom_request_id:self.error=error
            else:
                self.custom_saved=saved
                self.custom_joint=JointHome(self.droid_joint.switcher,saved['q'],controller=CUSTOM_JOINT)
        command = packet['pilot']
        result = super().step(packet,now,dt,sample)
        if not command['connected'] and self.joint.phase != 'idle':
            self.joint.poll(sample,now)
        return result

    def handle_command(self, command, now, sample):
        if self.custom_saving and command!='lock':
            self.error='正在保存自定义 Home，请稍候';return
        if command=='set_home':
            self.error='DROID Home 是固定目标，请使用设置自定义 Home';return
        if command=='set_custom_home':
            if self.mode!='locked' or self.joint.phase!='idle' or max(abs(v) for v in sample['dq'])>.03:
                self.error='请先 Finish，等待机械臂停止后设置自定义 Home';return
            try:q=checked_joints(list(sample['q']),self.guard.lower,self.guard.upper)
            except ValueError as error:self.error=str(error);return
            self.hold();self.custom_saving=True;self.custom_error=None
            self.custom_request_id=self.command_id
            def save():
                try:
                    self.droid_joint.switcher.configure_custom(q)
                    saved=(self.custom_store.save(q,self.guard.lower,self.guard.upper) if self.custom_store
                           else dict(q=q,saved_unix=time.time()))
                    self.custom_result=(saved,None)
                except Exception as error:self.custom_result=(None,str(error))
            self.custom_worker=threading.Thread(target=save,daemon=True)
            self.custom_worker.start();return
        if command in ('home','custom_home'):
            if self.mode!='locked' or self.joint.phase!='idle':
                self.error='请先 Finish，等待当前操作完成后返回 Home';return
            selected=self.droid_joint if command=='home' else self.custom_joint
            if selected is None:
                self.error='请先设置自定义 Home';return
            self.hold();self.joint=selected
            self.active_home='droid' if command=='home' else 'custom'
            self.preflight=self.prepare(sample,selected.goal)
            self.preflight_q=np.asarray(sample['q']).copy()
            self.preflight_deadline=now+2.
            self.mode='homing';return
        super().handle_command(command,now,sample)

    def home_step(self, now, dt, sample):
        if self.preflight is not None:
            # Keep heartbeat/state checks and Finish responsive during FK/SVD.
            # No controller switch occurs before every path point is checked.
            try:
                if now>self.preflight_deadline:
                    raise ValueError('Home path check timed out')
                if np.max(np.abs(np.asarray(sample['q'])-self.preflight_q))>.005 or max(abs(v) for v in sample['dq'])>.03:
                    raise ValueError('arm moved during Home path check; wait for it to settle')
                for _ in range(5):next(self.preflight)
                return
            except StopIteration:
                self.preflight=None
                self.joint.start()
                self.had_joint_control=True
            except ValueError as error:
                self.error=str(error);self.hold();return
        self.joint.poll(sample,now)
        if self.joint.phase == 'idle':
            self.hold()
        else:
            self.guard.target = self.guard.measured.copy()
            self.guard.target_rotation = self.guard.measured_rotation

    def check_state(self, sample, now):
        if self.had_joint_control:
            # Desired joints are meaningful after the position interface has
            # acknowledged activation; old torque-mode samples can retain q_d.
            active_sample = self.joint.phase == 'active' and sample['received_at'] > self.joint.changed_at
            if active_sample or self.joint.phase in ('stopping','resuming'):
                q_d = np.asarray(sample['q_d'],dtype=float)
                if q_d.shape != (7,) or not np.isfinite(q_d).all() or np.max(np.abs(q_d-sample['q'])) > .05:
                    raise ValueError('joint Home tracking error')
                transform, _ = pose_and_jacobian(self.chain,q_d)
                self.guard.target = transform[:3,3].copy()
                self.guard.target_rotation = Rotation.from_matrix(transform[:3,:3])
            else:
                self.guard.target = np.asarray(sample['xyz'])
                self.guard.target_rotation = Rotation.from_matrix(np.asarray(sample['rotation']).reshape(3,3,order='F'))
            if self.joint.phase == 'idle':
                self.had_joint_control = False
        checked = checked_switch_sample(sample,self.joint.phase,self.joint.changed_at,now)
        self.guard.check_state(checked,now,checked_jacobian(self.chain,sample))
        self.joint.poll(sample,now)
