import copy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


spec = importlib.util.spec_from_file_location(
    "hold_observer", Path(__file__).parents[1] / "nuc" / "observe_hold.py"
)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class Errors:
    __slots__ = ("joint_reflex",)

    def __init__(self, active=False):
        self.joint_reflex = active


def message(stamp=1.0):
    return SimpleNamespace(
        header=SimpleNamespace(stamp=SimpleNamespace(to_sec=lambda: stamp)),
        O_T_EE=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.4, 0.1, 0.3, 1],
        q=[0.0] * 7,
        dq=[0.0] * 7,
        tau_J=[0.0] * 7,
        control_command_success_rate=1.0,
        robot_mode=2,
        current_errors=Errors(),
        last_motion_errors=Errors(),
    )


class HoldObserver(unittest.TestCase):
    def test_known_pose_rotation_speed_and_torque_changes(self):
        first, last = message(), message(1.1)
        last.O_T_EE[12] += 0.001
        last.O_T_EE[:12] = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0]
        last.dq[2] = -0.02
        last.q[2] = 0.004
        last.tau_J[0] = 0.5
        summary = observer.summarize([
            observer.sample_from_message(first, 10.0),
            observer.sample_from_message(last, 10.1),
        ], 10.15)
        self.assertAlmostEqual(summary["tcp_net_drift_mm"], 1.0)
        self.assertAlmostEqual(summary["rotation_max_deviation_deg"], 90.0)
        self.assertAlmostEqual(summary["max_joint_speed_rad_s"], 0.02)
        self.assertAlmostEqual(summary["joint_span_rad"][2], 0.004)
        self.assertAlmostEqual(summary["max_torque_change_norm_nm"], 0.5)

    def test_missing_or_nonfinite_state_is_rejected(self):
        for field in ("O_T_EE", "q", "dq", "tau_J"):
            for invalid in ([], [math.nan] * len(getattr(message(), field))):
                with self.subTest(field=field, invalid=invalid):
                    msg = message()
                    setattr(msg, field, invalid)
                    with self.assertRaises(ValueError):
                        observer.sample_from_message(msg, 10.0)

    def test_stream_stall_and_frozen_robot_stamp_are_rejected(self):
        first = observer.sample_from_message(message(), 10.0)
        second = observer.sample_from_message(message(1.1), 10.1)
        with self.assertRaisesRegex(ValueError, "stream gap"):
            observer.summarize([first, second], 11.0)
        frozen = copy.deepcopy(second)
        frozen["stamp"] = first["stamp"]
        with self.assertRaisesRegex(ValueError, "timestamps did not advance"):
            observer.summarize([first, frozen], 10.1)

    def test_errors_and_modes_remain_visible_in_a_complete_observation(self):
        first, last = message(), message(1.1)
        last.current_errors = Errors(True)
        last.last_motion_errors = Errors(True)
        last.robot_mode = 4
        last.control_command_success_rate = 0.8
        result = observer.summarize([
            observer.sample_from_message(first, 10.0),
            observer.sample_from_message(last, 10.1),
        ], 10.1)
        self.assertEqual(result["current_errors"], ["joint_reflex"])
        self.assertEqual(result["robot_modes"], [2, 4])
        self.assertEqual(result["min_control_command_success_rate"], 0.8)
        self.assertIn("operator review required", result["verdict"])


if __name__ == "__main__":
    unittest.main()
