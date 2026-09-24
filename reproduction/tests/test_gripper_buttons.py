import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1]))
from gripper_buttons import GripperButtons
from spacemouse_upward_trial import read_latest_input


class GripperButtonTests(unittest.TestCase):
    def test_single_click_fires_only_on_release(self):
        for pressed, expected in [((1, 0), 'close'), ((0, 1), 'open')]:
            buttons = GripperButtons()
            buttons.observe((0, 0))
            buttons.observe(pressed)
            buttons.observe(pressed)
            self.assertIsNone(buttons.take())
            buttons.observe((0, 0))
            self.assertEqual(buttons.take(), expected)
            self.assertIsNone(buttons.take())

    def test_held_button_at_start_does_not_execute(self):
        buttons = GripperButtons()
        buttons.observe((1, 0))
        buttons.observe((0, 0))
        self.assertIsNone(buttons.take())

    def test_chord_cancels_pending_click_and_latches_exit(self):
        for first in [(1, 0), (0, 1)]:
            buttons = GripperButtons()
            for state in [(0, 0), first, (0, 0), first, (1, 1), (0, 0)]:
                buttons.observe(state)
            self.assertTrue(buttons.stopped)
            self.assertIsNone(buttons.take())

    def test_queue_drain_preserves_brief_click(self):
        buttons = GripperButtons()
        released = SimpleNamespace(t=3, buttons=(0, 0))
        device = Mock(read=Mock(side_effect=[SimpleNamespace(t=1, buttons=(0, 0)),
                     SimpleNamespace(t=2, buttons=(1, 0)), released, released]))
        _, _, stop = read_latest_input(device, 0, True, on_buttons=buttons.observe)
        self.assertFalse(stop)
        self.assertEqual(buttons.take(), 'close')

    def test_queue_chord_does_not_execute_earlier_click(self):
        buttons = GripperButtons()
        states = [SimpleNamespace(t=i, buttons=b) for i, b in enumerate(
            [(0, 0), (1, 0), (0, 0), (1, 1), (0, 0)], 1)]
        device = Mock(read=Mock(side_effect=states + [states[-1]]))
        _, _, stop = read_latest_input(device, 0, True, on_buttons=buttons.observe)
        self.assertTrue(stop)
        self.assertIsNone(buttons.take())


if __name__ == '__main__':
    unittest.main()
