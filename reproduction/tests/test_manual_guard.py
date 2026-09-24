import copy
import math
from pathlib import Path
import sys
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).parents[1] / 'nuc'))
from manual_guard import (ManualGuard, LINEAR_SPEED, ANGULAR_SPEED,
                          WORKSPACE_RADIUS, ORIENTATION_RADIUS)


class ManualGuardTests(unittest.TestCase):
    def setUp(self):
        self.sample = dict(q=[0.] * 7, dq=[0.] * 7, xyz=[.3, 0., .5],
            rotation=np.eye(3).flatten(order='F').tolist(), mode=2,
            current_errors=[], last_motion_errors=[], success=1., received_at=1.)
        self.jacobian = np.c_[np.eye(6), np.zeros(6)]
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.seq = 0

    def packet(self, axes=(0.,) * 6, enable=False, stop=False):
        self.seq += 1
        return dict(seq=self.seq, server_time=1., axes=list(axes), enable=enable, stop=stop)

    def arm(self):
        self.guard.step(self.packet(), 1.01, .02)
        self.guard.step(self.packet(enable=True), 1.01, .02)

    def follow(self):
        sample = copy.deepcopy(self.sample)
        sample['xyz'] = self.guard.target.tolist()
        sample['rotation'] = self.guard.target_rotation.as_matrix().flatten(order='F').tolist()
        self.guard.check_state(sample, 1.01, self.jacobian)

    def test_release_and_center_are_required_and_release_freezes_pose(self):
        original = self.guard.pose()
        self.guard.step(self.packet((0, 0, 1, 0, 0, 0), True), 1.01, .02)
        self.assertEqual(self.guard.pose(), original)
        self.arm()
        moved = self.guard.step(self.packet((0, 0, 1, 0, 0, 0), True), 1.01, .02)
        self.assertGreater(moved[0][2], original[0][2])
        self.assertEqual(self.guard.step(self.packet((1,) * 6), 1.01, .02), moved)
        self.assertFalse(self.guard.armed)

    def test_all_six_signed_axes_move_in_base_frame(self):
        for axis in range(6):
            for sign in (-1, 1):
                self.setUp()
                self.arm()
                axes = [0] * 6
                axes[axis] = sign
                xyz, quat = self.guard.step(self.packet(axes, True), 1.01, .02)
                if axis < 3:
                    self.assertGreater(sign * (xyz[axis] - self.sample['xyz'][axis]), 0)
                else:
                    self.assertGreater(sign * Rotation.from_quat(quat).as_rotvec()[axis - 3], 0)

    def test_combined_axes_respect_vector_speeds_and_workspace(self):
        self.arm()
        previous = np.array(self.sample['xyz'])
        old_rotation = Rotation.identity()
        for _ in range(550):
            self.follow()
            xyz, quat = self.guard.step(self.packet((1,) * 6, True), 1.01, .02)
            rotation = Rotation.from_quat(quat)
            self.assertLessEqual(np.linalg.norm(np.array(xyz) - previous), LINEAR_SPEED * .02 + 1e-10)
            self.assertLessEqual((rotation * old_rotation.inv()).magnitude(), ANGULAR_SPEED * .02 + 1e-10)
            self.assertLessEqual(np.linalg.norm(np.array(xyz) - self.sample['xyz']), WORKSPACE_RADIUS + 1e-10)
            self.assertLessEqual(rotation.magnitude(), ORIENTATION_RADIUS + 1e-10)
            previous, old_rotation = np.array(xyz), rotation

    def test_lag_caps_target_without_integrating_farther(self):
        self.arm()
        for _ in range(200):
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0), True), 1.01, .02)
        self.assertLessEqual(self.guard.target[0] - self.sample['xyz'][0], .005000001)
        self.assertEqual(self.guard.limit_reason, 'waiting_for_translation_tracking')
        before = self.guard.target.copy()
        self.guard.step(self.packet((-1, 0, 0, 0, 0, 0), True), 1.01, .02)
        self.assertLess(self.guard.target[0], before[0])

    def test_rotation_lead_is_bounded(self):
        self.arm()
        for _ in range(200):
            self.guard.step(self.packet((0, 0, 0, 0, 0, 1), True), 1.01, .02)
        self.assertLessEqual(self.guard.target_rotation.magnitude(), math.radians(3) + 1e-9)
        self.assertEqual(self.guard.limit_reason, 'waiting_for_rotation_tracking')

    def test_joint_limit_direction_is_blocked_but_retreat_allowed(self):
        self.sample['q'][3] = -.997
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.arm()
        for _ in range(20):
            self.guard.step(self.packet((0, 0, 0, -1, 0, 0), True), 1.01, .02)
        self.assertGreaterEqual(self.sample['q'][3] + self.guard.target_rotation.as_rotvec()[0], -.998000001)
        self.assertEqual(self.guard.limit_reason, 'joint_limit')
        before = self.guard.target_rotation.as_rotvec()[0]
        self.guard.step(self.packet((0, 0, 0, 1, 0, 0), True), 1.01, .02)
        self.assertGreater(self.guard.target_rotation.as_rotvec()[0], before)

    def test_right_stop_bad_input_and_stale_streams_fail_closed(self):
        for changes in [dict(stop=True), dict(axes=[0] * 3), dict(axes=[float('nan')] * 6),
                        dict(axes=[2] * 6), dict(server_time=0.), dict(enable=1), dict(extra=1)]:
            packet = self.packet()
            packet.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.guard.step(packet, 1.01, .02)
        packet = self.packet()
        self.guard.step(packet, 1.01, .02)
        with self.assertRaises(ValueError):
            self.guard.step(packet, 1.01, .02)
        with self.assertRaises(ValueError):
            self.guard.step(self.packet(), 1.01, .2)

    def test_state_error_stale_tracking_and_singularity_stop(self):
        for changes in [dict(received_at=0.), dict(mode=5), dict(current_errors=['fault']),
                        dict(dq=[.16] * 7), dict(xyz=[.309, 0., .5]), dict(success=.8),
                        dict(rotation=Rotation.from_rotvec([.1, 0, 0]).as_matrix().flatten(order='F').tolist())]:
            sample = copy.deepcopy(self.sample)
            sample.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.guard.check_state(sample, 1.01, self.jacobian)
        with self.assertRaisesRegex(ValueError, 'singularity'):
            self.guard.check_state(self.sample, 1.01, np.zeros((6, 7)))

    def test_no_trial_bounds_allows_more_than_five_cm_and_ten_degrees(self):
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7, trial_bounds=False)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.arm()
        for _ in range(450):
            self.follow()
            self.guard.step(self.packet((1, 0, 0, 0, 0, 1), True), 1.01, .02)
        self.assertGreater(self.guard.target[0] - self.sample['xyz'][0], .08)
        self.assertGreater(self.guard.target_rotation.magnitude(), math.radians(30))

    def test_existing_limit_near_start_allows_retreat_without_expanding_limit(self):
        self.sample['q'][3] = -.999
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7, trial_bounds=False)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.arm()
        for _ in range(10):
            self.guard.step(self.packet((0, 0, 0, -1, 0, 0), True), 1.01, .02)
        self.assertAlmostEqual(self.guard.target_rotation.magnitude(), 0)
        self.guard.step(self.packet((0, 0, 0, 1, 0, 0), True), 1.01, .02)
        self.assertGreater(self.guard.target_rotation.as_rotvec()[0], 0)
        sample = copy.deepcopy(self.sample)
        sample['q'][3] = -1.00001
        with self.assertRaisesRegex(ValueError, 'joint 4'):
            self.guard.check_state(sample, 1.01, self.jacobian)

    def test_official_input_moves_without_button_after_initial_center(self):
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7,
                                 trial_bounds=False, official_input=True)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        original = self.guard.pose()
        self.assertEqual(self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02), original)
        self.guard.step(self.packet(), 1.01, .02)
        xyz, quat = self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertGreater(xyz[0], original[0][0])
        self.assertEqual(quat, original[1])
        self.assertEqual(self.guard.step(self.packet(), 1.01, .02), original)

    def test_official_input_tilt_stays_rotation_and_uses_upstream_threshold(self):
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7,
                                 trial_bounds=False, official_input=True)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.guard.step(self.packet(), 1.01, .02)
        original = self.guard.pose()
        self.assertEqual(self.guard.step(self.packet((0, 0, 0, 0, .0005, 0)), 1.01, .02), original)
        xyz, quat = self.guard.step(self.packet((0, 0, 0, 0, .1, 0)), 1.01, .02)
        self.assertEqual(xyz, original[0])
        self.assertGreater(Rotation.from_quat(quat).as_rotvec()[1], 0)
        with self.assertRaisesRegex(ValueError, 'operator stop'):
            self.guard.step(self.packet(stop=True), 1.01, .02)

    def official_guard(self, speed_scale=1., translation_scale=None, rotation_scale=None):
        self.guard = ManualGuard(self.sample, [-1.] * 7, [1.] * 7,
                                 trial_bounds=False, official_input=True,
                                 speed_scale=speed_scale, translation_scale=translation_scale, rotation_scale=rotation_scale)
        self.guard.check_state(self.sample, 1.01, self.jacobian)
        self.guard.step(self.packet(), 1.01, .02)

    def test_measured_actions_do_not_accumulate_when_robot_does_not_follow(self):
        self.official_guard()
        for _ in range(100):
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .31)
        self.assertIsNone(self.guard.limit_reason)

    def test_measured_action_reverses_from_actual_pose_and_release_captures_once(self):
        self.official_guard()
        self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        sample = copy.deepcopy(self.sample)
        sample['xyz'][0] = .304
        self.guard.check_state(sample, 1.01, self.jacobian)
        for _ in range(5):
            self.guard.step(self.packet((-1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .294)
        self.guard.step(self.packet(), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .304)
        sample['xyz'][0] = .305
        self.guard.check_state(sample, 1.01, self.jacobian)
        self.guard.step(self.packet(), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .304)

    def test_old_rotation_target_does_not_block_new_translation(self):
        self.official_guard()
        self.guard.step(self.packet((0, 0, 0, 1, 0, 0)), 1.01, .02)
        self.assertGreater(self.guard.target_rotation.magnitude(), 0)
        for _ in range(5):
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .31)
        self.assertAlmostEqual(self.guard.target_rotation.magnitude(), 0)

    def test_measured_action_rate_and_joint_prediction_still_apply(self):
        self.official_guard()
        self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.follow()
        for _ in range(4):
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .31)
        self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target[0], .32)
        self.sample['q'][3] = -.999
        self.official_guard()
        self.guard.step(self.packet((0, 0, 0, -1, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target_rotation.magnitude(), 0)
        for _ in range(5):
            self.guard.step(self.packet((0, 0, 0, 1, 0, 0)), 1.01, .02)
        self.assertGreater(self.guard.target_rotation.as_rotvec()[0], 0)

    def test_responsive_state_checks_reject_faults_and_excessive_motion(self):
        for speed_scale in (1., 1.5, 2.):
            self.official_guard(speed_scale)
            for changes in [dict(dq=[.61] * 7), dict(xyz=[.326, 0, .5]),
                            dict(current_errors=['fault']), dict(received_at=0.),
                            dict(rotation=Rotation.from_rotvec([.13, 0, 0]).as_matrix().flatten(order='F').tolist())]:
                sample = copy.deepcopy(self.sample)
                sample.update(changes)
                with self.subTest(scale=speed_scale, changes=changes), self.assertRaises(ValueError):
                    self.guard.check_state(sample, 1.01, self.jacobian)

    def test_scaled_input_changes_response_without_accumulating_or_changing_rate(self):
        for speed_scale in (1., 1.5, 2.):
            self.official_guard(speed_scale)
            for _ in range(100):
                self.guard.step(self.packet((.2, 0, 0, 0, 0, .2)), 1.01, .02)
            self.assertAlmostEqual(self.guard.target[0], .3 + .002 * speed_scale)
            self.assertAlmostEqual(self.guard.target_rotation.magnitude(), .012 * speed_scale)
            self.guard.step(self.packet(), 1.01, .02)
            self.assertAlmostEqual(self.guard.target[0], .3)
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
            self.follow()
            for _ in range(4):
                self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
            self.assertAlmostEqual(self.guard.target[0], .3 + .01 * speed_scale)
            self.guard.step(self.packet((1, 0, 0, 0, 0, 0)), 1.01, .02)
            self.assertAlmostEqual(self.guard.target[0], .3 + .02 * speed_scale)

    def test_scaled_combined_input_respects_tracking_and_joint_prediction(self):
        for jacobian_scale in (.1, 1., 10.):
            self.official_guard(2.)
            jacobian = self.jacobian * jacobian_scale
            self.guard.check_state(self.sample, 1.01, jacobian)
            self.guard.step(self.packet((1,) * 6), 1.01, .02)
            translation = self.guard.target - self.guard.measured
            rotation = self.guard.target_rotation.as_rotvec()
            self.assertLessEqual(np.linalg.norm(translation), .020 + 1e-10)
            self.assertLessEqual(np.linalg.norm(rotation), .10 + 1e-10)
            self.assertLessEqual(np.max(np.abs(np.linalg.pinv(jacobian) @ np.r_[translation, rotation])), .04 + 1e-10)
            self.guard.check_state(self.sample, 1.01, jacobian)
        self.sample['q'][3] = -.999
        self.official_guard(2.)
        self.guard.step(self.packet((0, 0, 0, -1, 0, 0)), 1.01, .02)
        self.assertAlmostEqual(self.guard.target_rotation.magnitude(), 0)
        for _ in range(5):
            self.guard.step(self.packet((0, 0, 0, 1, 0, 0)), 1.01, .02)
        self.assertGreater(self.guard.target_rotation.as_rotvec()[0], 0)

    def test_scaled_input_rejects_invalid_values_and_other_modes(self):
        for value in (0, 3, True, float('nan'), '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.official_guard(value)
        for options in ({}, {'trial_bounds': False}, {'official_input': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ManualGuard(self.sample, [-1.] * 7, [1.] * 7, speed_scale=2., **options)

    def test_translation_override_reduces_translation_and_preserves_rotation(self):
        for axes in ((.2, 0, 0, 0, 0, .2), (1, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 1)):
            self.official_guard(2.)
            self.guard.step(self.packet(axes), 1.01, .02)
            original_xyz = self.guard.target - self.guard.measured
            original_rotation = self.guard.target_rotation.as_rotvec()
            self.official_guard(2., translation_scale=1.6)
            for _ in range(100):
                self.guard.step(self.packet(axes), 1.01, .02)
            np.testing.assert_allclose(self.guard.target - self.guard.measured, .8 * original_xyz)
            np.testing.assert_allclose(self.guard.target_rotation.as_rotvec(), original_rotation)
            self.guard.check_state(self.sample, 1.01, self.jacobian)
            self.guard.step(self.packet(), 1.01, .02)
            np.testing.assert_allclose(self.guard.target, self.sample['xyz'])
            self.assertAlmostEqual(self.guard.target_rotation.magnitude(), 0)
        for value in (0, 3, True, float('nan'), '1.6'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.official_guard(2., translation_scale=value)
        for options in ({}, {'trial_bounds': False}, {'official_input': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ManualGuard(self.sample, [-1.] * 7, [1.] * 7, translation_scale=1.6, **options)

    def test_faster_rotation_preserves_translation_and_increases_small_and_full_input(self):
        for axes in ((1,0,0,0,0,0),(0,0,0,0,0,.2),(0,0,0,0,0,1)):
            self.official_guard(2.,translation_scale=1.6)
            self.guard.step(self.packet(axes),1.01,.02)
            old_position=self.guard.target.copy();old_angle=self.guard.target_rotation.magnitude()
            self.official_guard(2.,translation_scale=1.6,rotation_scale=3.)
            self.guard.step(self.packet(axes),1.01,.02)
            np.testing.assert_allclose(self.guard.target,old_position)
            if axes[-1]:
                self.assertAlmostEqual(self.guard.target_rotation.magnitude()/old_angle,1.5 if axes[-1]==.2 else 1.375)
            self.guard.step(self.packet(),1.01,.02)
            np.testing.assert_allclose(self.guard.target,self.sample['xyz'])
            self.assertAlmostEqual(self.guard.target_rotation.magnitude(),0)

    def test_faster_rotation_retains_tracking_state_and_stop_checks(self):
        for size in (.1,1.,10.):
            self.official_guard(2.,translation_scale=1.6,rotation_scale=3.)
            jacobian=self.jacobian*size
            self.guard.check_state(self.sample,1.01,jacobian)
            self.guard.step(self.packet((1,)*6),1.01,.02)
            translation=self.guard.target-self.guard.measured
            rotation=self.guard.target_rotation.as_rotvec()
            self.assertLessEqual(np.linalg.norm(translation),.020+1e-10)
            self.assertLessEqual(np.linalg.norm(rotation),.10+1e-10)
            self.assertLessEqual(np.max(np.abs(np.linalg.pinv(jacobian)@np.r_[translation,rotation])),.055+1e-10)
            self.assertLessEqual(np.max(np.abs(np.linalg.pinv(jacobian)@np.r_[translation,[0.]*3])),.04+1e-10)
            for changes in (dict(dq=[.61]*7),dict(mode=4),dict(current_errors=['fault']),dict(received_at=0.)):
                with self.assertRaises(ValueError):self.guard.check_state(dict(self.sample,**changes),1.01,jacobian)
            with self.assertRaisesRegex(ValueError,'operator stop'):self.guard.step(self.packet(stop=True),1.01,.02)
        for value in (0,4,True,float('nan'),'3'):
            with self.assertRaises(ValueError):self.official_guard(rotation_scale=value)


if __name__ == '__main__':
    unittest.main()
