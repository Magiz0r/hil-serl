import contextlib
import io
import json
import queue
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'nuc'))
import attended_gripper


def status(activated=True):
    return dict(gACT=int(activated), gGTO=0, gSTA=3 if activated else 0,
                gFLT=0, gPR=0, gPO=3, gCU=0, gOBJ=3)


class GripperLifecycleTests(unittest.TestCase):
    def run_helper(self, flags, backend, stdin=None):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(attended_gripper, 'Path', side_effect=lambda p: Path(directory) / Path(p).name), \
             patch.object(attended_gripper, 'RobotiqRS485GripperServer', return_value=backend), \
             (patch.object(attended_gripper.threading, 'Thread') if stdin is None else contextlib.nullcontext()), \
             (patch.object(sys, 'stdin', stdin) if stdin is not None else contextlib.nullcontext()), \
             patch.object(attended_gripper.signal, 'signal'), \
             patch.object(sys, 'argv', ['attended_gripper.py'] + flags), \
             contextlib.redirect_stdout(io.StringIO()):
            return attended_gripper.main()

    def test_read_only_mode_never_activates_moves_or_stops(self):
        backend = Mock(last_status=status(False))
        self.assertEqual(self.run_helper(['--probe-only'], backend), 0)
        for method in (backend.activate_gripper, backend.move, backend.stop, backend.reset_gripper):
            method.assert_not_called()
        backend.shutdown.assert_called_once()

    def test_unactivated_without_permission_is_read_only(self):
        backend = Mock(last_status=status(False))
        self.assertEqual(self.run_helper(['--execute-attended-gripper'], backend), 1)
        backend.activate_gripper.assert_not_called()
        backend.stop.assert_not_called()
        backend.reset_gripper.assert_not_called()

    def test_interrupted_own_activation_is_cancelled(self):
        backend = Mock(last_status=status(False))
        backend.activate_gripper.side_effect = KeyboardInterrupt('test interruption')
        backend.reset_gripper.return_value = status(False)
        self.assertEqual(self.run_helper(['--execute-attended-gripper', '--activate-if-needed'], backend), 1)
        backend.reset_gripper.assert_called_once()
        backend.move.assert_not_called()

    def test_regular_exit_stops_without_reset_or_release(self):
        backend = Mock(last_status=status())
        backend.read_status.side_effect = KeyboardInterrupt('test interruption')
        backend.stop.return_value = status()
        self.assertEqual(self.run_helper(['--execute-attended-gripper'], backend), 1)
        backend.stop.assert_called_once()
        backend.reset_gripper.assert_not_called()
        backend.move.assert_not_called()

    def test_hold_stops_motion_but_allows_later_open_without_reset(self):
        class InputStream:
            def __init__(self):
                self.items = queue.Queue()
                self.items.put(dict(seq=1, command_id=1, action='hold', stop=False))
            def readline(self,size):
                packet=self.items.get(timeout=2)
                if packet is None: return ''
                return json.dumps(dict(packet,server_time=time.monotonic()))+'\n'
        stream=InputStream()
        backend=Mock(last_status=status())
        backend.read_status.return_value=status()
        calls=[]
        def stop():
            calls.append('hold')
            if len(calls)==1:
                stream.items.put(dict(seq=2,command_id=2,action='open',stop=False))
            return status()
        def move(position,**kwargs):
            calls.append(position)
            stream.items.put(dict(seq=3,command_id=2,action='open',stop=True))
            stream.items.put(None)
            return status()
        backend.stop.side_effect=stop; backend.move.side_effect=move
        self.assertEqual(self.run_helper(['--execute-attended-gripper'],backend,stdin=stream),0)
        self.assertEqual(calls,['hold',0,'hold'])
        backend.reset_gripper.assert_not_called()
        backend.activate_gripper.assert_not_called()

    def test_selected_speed_reaches_moves_without_changing_force_or_stop(self):
        class InputStream:
            def __init__(self):
                self.items = queue.Queue()
                self.items.put(dict(seq=1, command_id=1, action='close', stop=False))

            def readline(self, size):
                packet = self.items.get(timeout=2)
                if packet is None:
                    return ''
                packet['server_time'] = time.monotonic()
                return json.dumps(packet) + '\n'

        for speed in (64, 192, 255):
            stream = InputStream()
            backend = Mock(last_status=status())
            backend.read_status.return_value = status()
            backend.stop.return_value = status()

            def move(position, **kwargs):
                if position == 255:
                    stream.items.put(dict(seq=2, command_id=2, action='open', stop=False))
                else:
                    stream.items.put(dict(seq=3, command_id=2, action='open', stop=True))
                    stream.items.put(None)
                return status()

            backend.move.side_effect = move
            self.assertEqual(self.run_helper(
                ['--execute-attended-gripper', '--speed', str(speed)], backend, stdin=stream), 0)
            self.assertEqual([call.args[0] for call in backend.move.call_args_list], [255, 0])
            self.assertTrue(all(call.kwargs == dict(speed=speed, force=30)
                                for call in backend.move.call_args_list))
            backend.stop.assert_called_once()
            backend.activate_gripper.assert_not_called()
            backend.reset_gripper.assert_not_called()


if __name__ == '__main__':
    unittest.main()
