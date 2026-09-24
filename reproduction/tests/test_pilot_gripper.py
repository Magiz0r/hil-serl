"""Exercise real desktop arbitration/protocol and completion without hardware."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from pilot_control import PilotClient, PilotSocket
from pilot_gripper import IdleGripper, arm_command, preparation_status


def command(action='gripper_close', identifier=2):
    return dict(id=identifier, action=action, connected=True)


def arm(identifier=2):
    return dict(phase='ready', armed=True, pilot=dict(mode='locked', command='lock', command_id=identifier))


def gripper(identifier=2, action='close', **status):
    return dict(phase='ready', command_id=7,
                preparation=dict(id=identifier, action=action, command_id=7),
                status=dict(gFLT=0, gGTO=1, gPR=255 if action=='close' else 0,
                            gPO=162, gOBJ=2 if action=='close' else 3, **status))


class PilotGripperTests(unittest.TestCase):
    def controller(self):
        control=IdleGripper()
        control.step(command('lock', 1), arm(1), False)
        control.step(command('lock', 1), arm(1), False)
        return control

    def test_socket_transports_intent_but_arm_only_receives_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            server=PilotSocket(directory);client=PilotClient(directory)
            try:
                for action in ('gripper_open','gripper_close'):
                    identifier=client.issue(action)
                    local=server.poll();mapped=arm_command(local)
                    self.assertEqual(local['action'], action)
                    self.assertEqual(mapped,dict(id=identifier,action='lock',connected=True))
            finally:
                client.close();server.close()

    def test_requires_matching_locked_arm_ack_and_issues_only_once(self):
        control=self.controller()
        for _ in range(5):
            self.assertIsNone(control.step(command(),arm(1),False))
        self.assertEqual(control.step(command(),arm(),False),'close')
        for _ in range(20):
            self.assertIsNone(control.step(command(),arm(),False))
        self.assertEqual(control.step(command('gripper_open',3),arm(3),False),'hold')
        self.assertEqual(control.step(command('gripper_open',3),arm(3),False),'open')

    def test_homing_stale_error_and_disconnected_never_prepare(self):
        variants=[]
        for key,value in [('mode','homing'),('error','controller error'),('custom_home',{'saving':True})]:
            value_arm=arm();value_arm['pilot'][key]=value;variants.append(value_arm)
        variants.append(dict(arm(),armed=False))
        for value_arm in variants:
            self.assertIsNone(self.controller().step(command(),value_arm,False))
        self.assertIsNone(self.controller().step(dict(command(),connected=False),arm(),False))
        self.assertIsNone(self.controller().step(command(),arm(),False,fresh=False))

    def test_stop_and_lost_heartbeat_hold_without_replaying_preparation(self):
        for next_command,kwargs in [(command('lock',3),{}),(dict(command(),connected=False),{}),
                                    (command(),dict(fresh=False))]:
            control=self.controller()
            self.assertEqual(control.step(command(),arm(),False),'close')
            self.assertEqual(control.step(next_command,arm(),False,**kwargs),'hold')
            self.assertIsNone(control.step(command(),arm(),False))

    def test_start_preserves_prepared_grasp_and_manual_exit_holds(self):
        control=self.controller()
        control.step(command(),arm(),False)
        start=command('start',3)
        self.assertIsNone(control.step(start,arm(),False,gripper=gripper()))
        self.assertIsNone(control.step(start,arm(),False,gripper=gripper()))
        self.assertIsNone(control.step(start,arm(),True))
        self.assertEqual(control.step(command('lock',4),arm(4),False),'hold')

    def test_acceptance_is_not_completion_and_contact_is_not_fully_closed(self):
        complete=gripper()
        result=preparation_status(complete)
        self.assertTrue(result['complete']);self.assertTrue(result['contact'])
        self.assertEqual(result['position'],162)
        for key,value in [('gOBJ',0),('gPR',0),('gFLT',9),('gGTO',0)]:
            data=copy.deepcopy(complete);data['status'][key]=value
            self.assertFalse(preparation_status(data)['complete'])
        for key,value in [('phase','command'),('command_id',6)]:
            data=dict(complete);data[key]=value
            self.assertFalse(preparation_status(data)['complete'])
        opened=preparation_status(gripper(action='open'))
        self.assertTrue(opened['complete']);self.assertFalse(opened['contact'])


if __name__ == '__main__':
    unittest.main()
