import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "translation_guard", Path(__file__).parents[1] / "nuc" / "translation_guard.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class TrialWindowTests(unittest.TestCase):
    def test_waiting_does_not_consume_active_window(self):
        window = guard.TrialWindow(100)
        window.observe(False, 150)
        self.assertIsNone(window.started_at)
        window.observe(True, 155)
        self.assertFalse(window.expired(184.9))
        self.assertTrue(window.expired(185))

    def test_release_and_reenable_do_not_extend_window(self):
        window = guard.TrialWindow(100)
        window.observe(True, 101)
        window.observe(False, 120)
        window.observe(True, 130)
        self.assertTrue(window.expired(131))

    def test_expired_wait_cannot_be_rearmed(self):
        window = guard.TrialWindow(100)
        self.assertTrue(window.expired(160))
        with self.assertRaisesRegex(ValueError, 'window expired'):
            window.observe(True, 160)
        self.assertIsNone(window.started_at)


class TranslationGuardTests(unittest.TestCase):
    def setUp(self):
        self.sample = {
            "q": [0] * 7, "dq": [0] * 7, "xyz": [0.3, 0, 0.5],
            "rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1], "mode": 2,
            "current_errors": [], "last_motion_errors": [], "success": 1.0,
            "received_at": 1.0,
        }
        self.guard = guard.TranslationGuard(self.sample, [-1] * 7, [1] * 7)
        self.seq = 0

    def packet(self, axes=(0, 0, 0), enable=False, stop=False, server_time=1.0):
        self.seq += 1
        return dict(seq=self.seq, axes=list(axes), enable=enable, stop=stop, server_time=server_time)

    def arm(self):
        self.guard.step(self.packet(), 1.01, 0.02)
        self.guard.step(self.packet(enable=True), 1.01, 0.02)

    def test_no_motion_without_release_center_and_enabled_button(self):
        origin = list(self.guard.origin)
        for packet in [self.packet((1, 0, 0), True), self.packet((1, 0, 0)),
                       self.packet(enable=True)]:
            self.assertEqual(self.guard.step(packet, 1.01, 0.02), origin)
        self.arm()
        moved = self.guard.step(self.packet((0, 0, 1), True), 1.01, 0.02)
        self.assertAlmostEqual(moved[2] - origin[2], 0.0001)
        self.assertEqual(self.guard.step(self.packet((1, 0, 0)), 1.01, 0.02), moved)
        self.assertFalse(self.guard.armed)

    def test_target_is_bounded_and_only_upward(self):
        self.arm()
        previous = list(self.guard.target)
        for _ in range(400):
            target = self.guard.step(self.packet((0.7, 1, 1), True), 1.01, 0.02)
            self.assertLessEqual(guard.distance(target, previous), 0.000100001)
            self.assertLessEqual(guard.distance(target, self.guard.origin), 0.01000001)
            self.assertEqual(target[0], self.guard.origin[0])
            self.assertEqual(target[1], self.guard.origin[1])
            previous = target
        self.assertEqual(self.guard.step(self.packet((1, 1, -1), True), 1.01, 0.02), previous)

    def test_stale_invalid_and_stop_packets_are_rejected(self):
        for change in [dict(server_time=0.5), dict(server_time=2),
                       dict(axes=[float('nan'), 0, 0]), dict(axes=[2, 0, 0]),
                       dict(stop=True), dict(enable=1), dict(extra=True)]:
            packet = self.packet()
            packet.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.guard.step(packet, 1.01, 0.02)
        packet = self.packet()
        self.guard.step(packet, 1.01, 0.02)
        with self.assertRaises(ValueError):
            self.guard.step(packet, 1.01, 0.02)
        with self.assertRaises(ValueError):
            self.guard.step(self.packet(), 1.01, 0.5)

    def test_target_waits_for_measured_motion_without_increasing_force(self):
        self.arm()
        for _ in range(200):
            self.guard.step(self.packet((0, 0, 1), True), 1.01, 0.02)
        self.assertAlmostEqual(self.guard.target[2] - self.guard.origin[2], 0.005)
        self.guard.check_state(self.sample, 1.01)
        sample = copy.deepcopy(self.sample)
        sample['xyz'][2] += 0.004
        self.guard.check_state(sample, 1.01)
        for _ in range(200):
            self.guard.step(self.packet((0, 0, 1), True), 1.01, 0.02)
        self.assertAlmostEqual(self.guard.target[2] - self.guard.origin[2], 0.009)

    def test_measured_joint_four_posture_only_allows_away_trial(self):
        q = [-0.286408, -0.959036, 0.12647, -2.74571, 0.145695, 1.83043, 0.615604]
        lower = [-2.3093, -1.5133, -2.4937, -2.7478, -2.48, 0.8521, -2.6895]
        upper = [2.3093, 1.5133, 2.4937, -0.4461, 2.48, 4.2094, 2.6895]
        with self.assertRaisesRegex(ValueError, 'joint 4'):
            guard.check_joints(q, lower, upper, guard.START_MARGIN)
        sample = copy.deepcopy(self.sample)
        sample['q'] = q
        trial = guard.TranslationGuard(sample, lower, upper)
        trial.check_state(sample, 1.01)
        sample['q'] = list(q)
        sample['q'][3] -= 0.0003
        with self.assertRaisesRegex(ValueError, 'toward its lower limit'):
            trial.check_state(sample, 1.01)

    def test_state_faults_stop_the_trial(self):
        self.guard.check_state(self.sample, 1.01)
        for change in [dict(received_at=0), dict(mode=5), dict(current_errors=['fault']),
                       dict(last_motion_errors=['fault']), dict(q=[0.99] + [0] * 6),
                       dict(dq=[0.06] + [0] * 6), dict(xyz=[0.306, 0, 0.5]),
                       dict(rotation=[-1, 0, 0, 0, -1, 0, 0, 0, 1]), dict(success=0.9)]:
            sample = copy.deepcopy(self.sample)
            sample.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.guard.check_state(sample, 1.01)


if __name__ == '__main__':
    unittest.main()
