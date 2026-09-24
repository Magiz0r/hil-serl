import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[1] / 'nuc'))
from manual_guard import ManualGuard
from pilot_motion import PilotMotion
from pilot_control import PilotSocket, PilotClient


class PilotMotionTests(unittest.TestCase):
    def setUp(self):
        self.sample = dict(q=[0.] * 7, dq=[0.] * 7, xyz=[.3, 0., .5],
            rotation=np.eye(3).flatten(order='F').tolist(), mode=2,
            current_errors=[], last_motion_errors=[], success=1., received_at=1.)
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7,
            trial_bounds=False, official_input=True, speed_scale=2, translation_scale=1.6)
        self.motion = PilotMotion(self.guard)
        self.seq, self.identifier, self.now, self.action = 0, 0, 1., 'lock'

    def step(self, action=None, axes=(0.,)*6, connected=True, stop=False, follow=True):
        if action:
            self.identifier += 1
            self.action = action
        self.seq += 1; self.now += .02
        if follow:
            self.sample['xyz'] = self.guard.target.tolist()
            self.sample['rotation'] = self.guard.target_rotation.as_matrix().flatten(order='F').tolist()
        self.sample['received_at'] = self.now
        self.guard.check_state(self.sample, self.now, np.c_[np.eye(6), np.zeros(6)])
        packet = dict(seq=self.seq, server_time=self.now, axes=list(axes), enable=False, stop=stop,
            pilot=dict(id=self.identifier, action=self.action, connected=connected))
        return self.motion.step(packet, self.now, .02, self.sample)

    def test_locked_input_start_center_finish_and_grip_buttons_stop(self):
        original = self.guard.pose()
        self.step(axes=[1.]*6)
        self.assertEqual(self.guard.pose(), original)
        self.step('start', axes=[1.]*6)
        self.assertEqual(self.motion.mode, 'waiting_for_center')
        self.assertEqual(self.guard.pose(), original)
        self.step()
        self.assertEqual(self.motion.mode, 'manual')
        self.step(axes=[1.,0,0,0,0,0])
        self.assertGreater(self.guard.target[0], original[0][0])
        self.step('lock', axes=[1.]*6, follow=False)
        locked = self.guard.pose()
        self.step(axes=[1.]*6)
        self.assertEqual(self.guard.pose(), locked)
        with self.assertRaisesRegex(ValueError, 'operator stop'):
            self.step(stop=True)

    def test_recorder_loss_locks_and_old_start_cannot_reenable(self):
        self.step('start'); self.step(axes=[1.,0,0,0,0,0])
        self.step(connected=False, axes=[1.]*6, follow=False)
        self.assertEqual(self.motion.mode, 'locked')
        locked = self.guard.pose()
        self.step(axes=[1.]*6)
        self.assertEqual(self.motion.mode, 'locked')
        self.assertEqual(self.guard.pose(), locked)
        self.step('start')
        self.assertEqual(self.motion.mode, 'manual')

    def move_away(self):
        self.guard.target += [.03, -.02, -.03]
        self.guard.target_rotation = Rotation.from_euler('z', 15, degrees=True) * self.guard.target_rotation
        self.step()

    def test_home_teaching_and_staged_return_ignores_spacemouse(self):
        self.step('home')
        self.assertFalse(self.motion.home)
        self.assertIsNotNone(self.motion.error)
        self.step('set_home')
        home = copy.deepcopy(self.motion.status()['home'])
        self.move_away()
        self.step('home', axes=[1.]*6)
        self.assertEqual(self.motion.mode, 'homing')
        self.assertEqual(self.motion.waypoints[0][0][2], home['xyz'][2])
        seen = set()
        for _ in range(1000):
            before = self.guard.target.copy()
            before_rotation = self.guard.target_rotation
            self.step(axes=[-1.]*6)
            seen.add(self.motion.waypoint)
            self.assertLessEqual(np.linalg.norm(self.guard.target-before), .02*.02+1e-9)
            self.assertLessEqual((self.guard.target_rotation*before_rotation.inv()).magnitude(), np.deg2rad(10)*.02+1e-9)
            if self.motion.mode == 'locked':
                break
        self.assertEqual(self.motion.mode, 'locked')
        self.assertIn(2, seen)
        np.testing.assert_allclose(self.guard.target, home['xyz'], atol=.001)
        self.assertLess((self.guard.target_rotation*Rotation.from_quat(home['quaternion']).inv()).magnitude(), .01)

    def test_finish_and_heartbeat_loss_interrupt_home_at_measured_pose(self):
        for disconnect in (False, True):
            self.setUp(); self.step('set_home'); self.move_away(); self.step('home')
            self.step(None if disconnect else 'lock', connected=not disconnect, follow=False)
            self.assertEqual(self.motion.mode, 'locked')
            np.testing.assert_allclose(self.guard.target,self.sample['xyz'])

    def test_teaching_requires_locked_and_stationary_and_guards_remain_active(self):
        self.step('start'); self.step('set_home')
        self.assertIsNone(self.motion.home)
        self.step('lock'); self.sample['dq'][0]=.04; self.step('set_home')
        self.assertIsNone(self.motion.home)
        self.sample['dq'][0]=.7
        with self.assertRaisesRegex(ValueError,'joint speed'):
            self.step()

    def test_stalled_home_has_timeout_and_does_not_accumulate_large_lead(self):
        self.step('set_home'); self.move_away(); self.step('home')
        for _ in range(100):
            self.step(follow=False)
            self.assertLessEqual(np.linalg.norm(self.guard.target-self.sample['xyz']),.005+1e-9)
        self.motion.deadline=self.now
        self.step(follow=False)
        self.assertEqual(self.motion.mode,'locked')
        self.assertIn('timed out',self.motion.error)

    def test_local_gate_has_exclusive_owner_and_heartbeat_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            server=PilotSocket(directory)
            client=PilotClient(directory)
            try:
                self.assertFalse(server.poll()['connected'])
                client.issue('start')
                self.assertEqual(server.poll()['action'],'start')
                self.assertTrue(server.poll()['connected'])
                with self.assertRaisesRegex(RuntimeError,'already owns'):
                    PilotClient(directory)
                with patch('pilot_control.time.monotonic',return_value=0):
                    self.assertFalse(server.poll()['connected'])
                client.close()
                self.assertEqual(server.poll()['action'],'lock')
            finally:
                server.close()
