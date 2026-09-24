import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from gripper_bridge import GripperBridge

# A local process substitutes only SSH; actual pipe I/O and worker threads run.
FAKE_REMOTE = r'''
import json, sys, time
print(json.dumps(dict(phase='ready', server_time=time.monotonic())), flush=True)
for line in sys.stdin:
    packet = json.loads(line)
    if packet['stop']:
        print(json.dumps(dict(phase='stopped', stop_confirmed=True, packet=packet,
                              server_time=time.monotonic())), flush=True)
        break
    time.sleep(0.1)
    print(json.dumps(dict(phase='ready', packet=packet, command_id=packet['command_id'],
                          status=dict(gFLT=0,gGTO=0 if packet['action']=='hold' else 1,
                                      gPR=0 if packet['action']=='open' else 255,gPO=162,gOBJ=2),
                          server_time=time.monotonic())), flush=True)
'''


class GripperBridgeTests(unittest.TestCase):
    def test_preparation_ack_is_correlated_to_real_remote_command_id(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('gripper_bridge.subprocess.Popen', side_effect=self.fake_popen):
            bridge=GripperBridge(Path(directory))
            try:
                bridge.wait_ready()
                before=bridge.refresh('close',preparation_id=41)
                self.assertNotIn('preparation',before)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    message=bridge.refresh()
                    if message.get('preparation'):break
                    time.sleep(.01)
                self.assertEqual(message['preparation'],dict(id=41,action='close',command_id=1))
                self.assertEqual(message['packet']['action'],'close')
                bridge.refresh('hold')
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    message=bridge.refresh()
                    if message.get('command_id')==2:break
                    time.sleep(.01)
                self.assertEqual(message['status']['gGTO'],0)
                self.assertNotIn('preparation',message)
            finally:
                bridge.close()
            rows=[json.loads(row) for row in (Path(directory)/'gripper-events.jsonl').read_text().splitlines()]
            self.assertTrue(any(r.get('preparation',{}).get('id')==41 for r in rows))

    def fake_popen(self, *args, **kwargs):
        return self.popen([sys.executable, '-u', '-c', FAKE_REMOTE], **kwargs)

    def setUp(self):
        self.popen = subprocess.Popen

    def test_slow_remote_does_not_block_arm_loop_and_close_sends_stop(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('gripper_bridge.subprocess.Popen', side_effect=self.fake_popen):
            bridge = GripperBridge(Path(directory))
            try:
                bridge.wait_ready()
                bridge.refresh('close')
                times = []
                for _ in range(30):
                    start = time.monotonic()
                    bridge.refresh()
                    times.append(time.monotonic() - start)
                    time.sleep(0.01)
                self.assertLess(max(times), 0.05)
            finally:
                bridge.close()
            messages = [json.loads(line) for line in (Path(directory) / 'gripper-events.jsonl').read_text().splitlines()]
            self.assertTrue(messages[-1]['stop_confirmed'])
            self.assertTrue(messages[-1]['packet']['stop'])

    def test_missing_main_heartbeat_terminates_gripper_channel(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('gripper_bridge.subprocess.Popen', side_effect=self.fake_popen):
            bridge = GripperBridge(Path(directory))
            try:
                bridge.wait_ready()
                time.sleep(0.65)
                with self.assertRaisesRegex(RuntimeError, 'heartbeat stale'):
                    bridge.refresh()
            finally:
                bridge.close()


if __name__ == '__main__':
    unittest.main()
