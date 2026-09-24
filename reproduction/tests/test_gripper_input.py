import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'nuc'))
from gripper_input import GripperInput


def packet(seq=1, command_id=0, action=None, stamp=10, stop=False):
    return dict(seq=seq, server_time=stamp, command_id=command_id, action=action, stop=stop)


class GripperProtocolTests(unittest.TestCase):
    def test_command_is_executed_once_across_heartbeats(self):
        guard = GripperInput()
        self.assertEqual(guard.accept(packet(1, 1, 'close'), 10.1), 'close')
        self.assertIsNone(guard.accept(packet(2, 1, 'close'), 10.2))
        self.assertEqual(guard.accept(packet(3, 2, 'open'), 10.3), 'open')

    def test_stale_replayed_or_changed_commands_fail(self):
        for bad in [packet(stamp=9), packet(stamp=float('nan')), packet(stamp=11),
                    packet(command_id=-1), packet(command_id=0, action='open'),
                    packet(command_id=1, action='reset')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                GripperInput().accept(bad, 10)
        guard = GripperInput()
        guard.accept(packet(1, 1, 'open'), 10)
        with self.assertRaisesRegex(ValueError, 'unordered'):
            guard.accept(packet(1, 1, 'open'), 10)
        with self.assertRaisesRegex(ValueError, 'without new id'):
            guard.accept(packet(2, 1, 'close'), 10)

    def test_stop_has_priority_over_stale_motion(self):
        with self.assertRaisesRegex(ValueError, 'operator stop'):
            GripperInput().accept(packet(0, 1, 'close', stamp=0, stop=True), 10)


if __name__ == '__main__':
    unittest.main()
