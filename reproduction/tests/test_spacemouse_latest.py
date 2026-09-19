import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1]))
from spacemouse_upward_trial import read_latest_input


def state(stamp, x=0., buttons=(0, 0)):
    return SimpleNamespace(t=stamp, x=x, buttons=buttons)


class LatestSpaceMouseInput(unittest.TestCase):
    def test_old_movement_is_drained_and_release_is_returned(self):
        released = state(4)
        device = Mock(read=Mock(side_effect=[state(1, 1), state(2, -1),
                                            state(3, 1), released, released]))
        latest, count, stop = read_latest_input(device, 0, True)
        self.assertIs(latest, released)
        self.assertEqual(count, 4)
        self.assertFalse(stop)

    def test_brief_stop_survives_later_button_release_in_same_batch(self):
        released = state(3)
        for official, buttons in [(True, (1, 1)), (False, (0, 1))]:
            with self.subTest(official=official):
                device = Mock(read=Mock(side_effect=[state(1), state(2, buttons=buttons),
                                                    released, released]))
                latest, _, stop = read_latest_input(device, 0, official)
                self.assertIs(latest, released)
                self.assertTrue(stop)
        pressed = state(1, buttons=(0, 1))
        _, _, stop = read_latest_input(Mock(read=Mock(return_value=pressed)), 1, True)
        self.assertFalse(stop)

    def test_empty_queue_keeps_stamp_and_does_not_claim_new_input(self):
        previous = state(5)
        latest, count, stop = read_latest_input(Mock(read=Mock(return_value=previous)), 5, True)
        self.assertIs(latest, previous)
        self.assertEqual(count, 0)
        self.assertFalse(stop)

    def test_disconnect_and_unbounded_queue_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            read_latest_input(Mock(read=Mock(return_value=None)), 0, True)
        device = Mock(read=Mock(side_effect=[state(i) for i in range(1, 5)]))
        with self.assertRaisesRegex(ValueError, 'did not drain'):
            read_latest_input(device, 0, True, max_reports=4)


if __name__ == '__main__':
    unittest.main()
