import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
from pilot_dataset import Episode, health_error, validate_episode
from record_manual_pilot import Recorder


def snapshot(now, sequence=1):
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:] = [10, 70, 200]
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    frame = dict(sequence=sequence, pc_captured_at=now, shape=list(image.shape), jpeg=encoded.tobytes())
    state = dict(received_at=100., mode=2, current_errors=[], last_motion_errors=[], success=1.)
    return dict(arm=dict(pc_received_at=now, data=dict(source_read_at=100.01,
                    sample=dict(state=state, armed=True, input=dict(stop=False, axes=[0.] * 6)))),
                control=dict(pc_received_at=now, data=dict(phase='ready')),
                gripper=dict(pc_received_at=now, data=dict(phase='ready', preparation_supported=True, status=dict(gFLT=0, gGTO=0))),
                cameras={name:dict(frame) for name in ('external', 'wrist')})


class FakeGate:
    def __init__(self):
        self.id=1; self.action='lock'
    def issue(self,action):
        self.id+=1; self.action=action; return self.id
    def refresh(self):
        pass
    def state(self):
        action='lock' if self.action.startswith('gripper_') else self.action
        return dict(command_id=self.id, command=action, home_set=True,
            mode={'lock':'locked','set_home':'locked','start':'manual','home':'homing'}[action])


class PilotDatasetTests(unittest.TestCase):
    def test_preparation_records_nothing_waits_for_real_completion_and_stop_cancels(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=object.__new__(Recorder)
            recorder.directory=Path(directory);recorder.task={'name':'insertion'}
            recorder.done=threading.Event();recorder.lock=threading.Lock()
            recorder.commands=queue.Queue(maxsize=8);recorder.episode=None
            recorder.status=dict(recording=False,ready=False,episodes=[])
            recorder.reader=SimpleNamespace(error=None);recorder.cameras={}
            recorder.gate=FakeGate();recorder.pending=None;recorder.finish_request=None
            complete=threading.Event();position=0;supported=True
            def current_snapshot():
                nonlocal position
                data=snapshot(time.monotonic())
                data['gripper']['data']['preparation_supported']=supported
                data['arm']['data']['sample']['pilot']=recorder.gate.state()
                action=recorder.gate.action
                if action.startswith('gripper_'):
                    closing=action=='gripper_close'
                    if complete.is_set():position=162 if closing else 0
                    data['gripper']['data'].update(command_id=17,
                        preparation=dict(id=recorder.gate.id,command_id=17,action=action[8:]))
                    data['gripper']['data']['status'].update(gGTO=1,gPR=255 if closing else 0,
                        gOBJ=(2 if closing else 3) if complete.is_set() else 0)
                data['gripper']['data']['status']['gPO']=position
                return data
            recorder.snapshot=current_snapshot
            def wait_for(predicate):
                deadline=time.monotonic()+2
                while not predicate() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(predicate(),recorder.get_status())
            thread=threading.Thread(target=recorder.work);thread.start()
            try:
                for action in ('gripper_open','gripper_close'):
                    complete.clear();recorder.command(action)
                    wait_for(lambda:(recorder.get_status().get('pending') or {}).get('action')==action)
                    self.assertFalse(recorder.get_status()['recording'])
                    self.assertEqual(list(Path(directory).iterdir()),[])
                    recorder.command('start')
                    wait_for(lambda:bool(recorder.get_status().get('last_command_error')))
                    self.assertIsNone(recorder.episode)
                    complete.set();wait_for(lambda:recorder.get_status().get('pending') is None)
                    self.assertTrue(recorder.get_status()['gripper_preparation']['complete'])
                recorder.command('start');wait_for(lambda:recorder.get_status().get('sample_count',0)>0)
                self.assertEqual(position,162)
                recorder.command('gripper_open')
                wait_for(lambda:bool(recorder.get_status().get('last_command_error')))
                self.assertEqual(recorder.gate.action,'start')
                recorder.command('finish');wait_for(lambda:len(recorder.get_status()['episodes'])==1)
                episode=Path(directory)/recorder.get_status()['episodes'][0]['name']
                first=json.loads((episode/'samples.jsonl').read_text().splitlines()[0])
                self.assertEqual(first['gripper']['data']['status']['gPO'],162)
                complete.clear();recorder.command('gripper_open')
                wait_for(lambda:recorder.get_status().get('pending') is not None)
                recorder.command('finish')
                wait_for(lambda:recorder.gate.action=='lock' and not recorder.get_status().get('pending'))
                self.assertEqual(len(recorder.get_status()['episodes']),1)
                recorder.command('gripper_close')
                wait_for(lambda:recorder.get_status().get('pending') is not None)
                recorder.pending['at']-=11
                wait_for(lambda:recorder.gate.action=='lock' and not recorder.get_status().get('pending'))
                self.assertIn('10 秒',recorder.get_status()['last_command_error'])
                self.assertFalse(recorder.get_status().get('fatal'))
                supported=False;recorder.command('gripper_open')
                wait_for(lambda:'版本不支持' in recorder.get_status().get('last_command_error',''))
                self.assertEqual(recorder.gate.action,'lock')
                self.assertEqual(len(recorder.get_status()['episodes']),1)
            finally:
                recorder.done.set();thread.join(timeout=2)

    def test_home_switch_zero_rate_is_allowed_only_outside_recording(self):
        data=snapshot(1000.)
        sample=data['arm']['data']['sample']
        sample['pilot']=dict(home_kind='official_joint',mode='homing',joint_phase='activating')
        sample['state']['success']=0.
        self.assertIsNone(health_error(data,1000.,allow_home_transition=True))
        self.assertIsNotNone(health_error(data,1000.))
        for key,value in [('success',.5),('mode',5),('current_errors',['collision'])]:
            bad=copy.deepcopy(data);bad['arm']['data']['sample']['state'][key]=value
            self.assertIsNotNone(health_error(bad,1000.,allow_home_transition=True))
    def test_complete_episode_preserves_frames_states_and_operator_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'episode_0001'
            episode = Episode(path, dict(task='block_into_cup'), 1000.)
            data = snapshot(1000.)
            episode.append(data, 1000.)
            episode.append(data, 1000.1)
            result = episode.finish('success', 1000.2)
            self.assertEqual(result['unique_image_count'], 2)
            self.assertEqual(result['outcome_source'], 'operator')
            self.assertEqual(validate_episode(path)['samples'], 2)
            records = [json.loads(line) for line in (path / 'samples.jsonl').read_text().splitlines()]
            self.assertEqual(records[0]['arm']['data']['sample'], data['arm']['data']['sample'])
            frame = cv2.imread(str(path / records[0]['cameras']['external']['path']))
            np.testing.assert_allclose(frame.mean(axis=(0,1)), [10,70,200], atol=3)
            with self.assertRaises(FileExistsError):
                Episode(path, {}, 1001.)

    def test_stale_cameras_and_control_faults_reject_sample(self):
        original = snapshot(1000.)
        variants = []
        value = copy.deepcopy(original); value['cameras']['wrist']['pc_captured_at'] = 999.; variants.append(value)
        value = copy.deepcopy(original); value['arm']['data']['sample']['state']['received_at'] = 99.; variants.append(value)
        value = copy.deepcopy(original); value['control']['data']['phase'] = 'stopped'; variants.append(value)
        value = copy.deepcopy(original); value['arm']['data']['sample']['input']['stop'] = True; variants.append(value)
        value = copy.deepcopy(original); value['gripper']['data']['status']['gFLT'] = 9; variants.append(value)
        value = copy.deepcopy(original); value['arm']['data']['sample']['state']['success'] = float('nan'); variants.append(value)
        for value in variants:
            self.assertIsNotNone(health_error(value,1000.))
        with tempfile.TemporaryDirectory() as directory:
            episode = Episode(Path(directory) / 'ep', {}, 1000.)
            for value in variants:
                with self.assertRaises(ValueError):
                    episode.append(value,1000.)
            result = episode.finish('success',1000.1)
            self.assertEqual(result['outcome'],'incomplete')

    def test_timing_gaps_and_modified_files_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ep'
            episode = Episode(path, {},1000.)
            episode.append(snapshot(1000.),1000.)
            with self.assertRaisesRegex(ValueError,'timing'):
                episode.append(snapshot(1001.,2),1001.)
            episode.finish('incomplete',1001.,'gap')
            self.assertEqual(validate_episode(path)['outcome'],'incomplete')
            (path/'external/00000001.jpg').write_bytes(b'corrupted')
            with self.assertRaises(AssertionError):
                validate_episode(path)

    def test_raw_data_does_not_label_intermediate_frames_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ep'
            episode = Episode(path, {},1000.)
            episode.append(snapshot(1000.),1000.)
            episode.append(snapshot(1000.1,2),1000.1)
            episode.finish('success',1000.2)
            for line in (path/'samples.jsonl').read_text().splitlines():
                data=json.loads(line)
                self.assertNotIn('reward',data)
                self.assertNotIn('success',data)

    def test_finish_waits_for_locked_ack_saves_unlabeled_and_home_can_be_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=object.__new__(Recorder)
            recorder.directory=Path(directory)
            recorder.task=dict(name='block_into_cup',success_definition='block remains in cup')
            recorder.done=threading.Event(); recorder.lock=threading.Lock()
            recorder.commands=queue.Queue(maxsize=8); recorder.episode=None
            recorder.status=dict(recording=False,ready=False,episodes=[])
            recorder.reader=SimpleNamespace(error=None)
            recorder.cameras={}
            recorder.gate=FakeGate(); recorder.pending=None; recorder.finish_request=None
            def current_snapshot():
                data=snapshot(time.monotonic())
                data['arm']['data']['sample']['pilot']=recorder.gate.state()
                return data
            recorder.snapshot=current_snapshot
            thread=threading.Thread(target=recorder.work)
            thread.start()
            try:
                recorder.command('start')
                deadline=time.monotonic()+2
                while recorder.get_status().get('sample_count',0)<2 and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertTrue(recorder.get_status()['recording'])
                recorder.command('finish')
                deadline=time.monotonic()+2
                while not recorder.get_status()['episodes'] and time.monotonic()<deadline:
                    time.sleep(.02)
                status=recorder.get_status()
                self.assertEqual(status['episodes'][0]['outcome'],'unlabeled')
                self.assertFalse(status['recording'])
                self.assertEqual(recorder.gate.action,'lock')
                self.assertEqual(validate_episode(Path(directory)/status['episodes'][0]['name'])['outcome'],'unlabeled')
                records=[json.loads(line) for line in (Path(directory)/status['episodes'][0]['name']/'samples.jsonl').read_text().splitlines()]
                self.assertEqual(records[0]['arm']['data']['sample']['pilot']['mode'],'locked')
                self.assertEqual(records[-1]['arm']['data']['sample']['pilot']['mode'],'locked')
                recorder.command('home')
                deadline=time.monotonic()+1
                while recorder.gate.action!='home' and time.monotonic()<deadline: time.sleep(.02)
                self.assertEqual(recorder.gate.action,'home')
                recorder.command('finish')
                deadline=time.monotonic()+1
                while recorder.gate.action!='lock' and time.monotonic()<deadline: time.sleep(.02)
                self.assertEqual(recorder.gate.action,'lock')
            finally:
                recorder.done.set(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_live_recorder_duplicate_start_and_lost_stream_preserve_incomplete_data(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=object.__new__(Recorder)
            recorder.directory=Path(directory)
            recorder.task=dict(name='block_into_cup',success_definition='block remains in cup')
            recorder.done=threading.Event(); recorder.lock=threading.Lock()
            recorder.commands=queue.Queue(maxsize=8); recorder.episode=None
            recorder.status=dict(recording=False,ready=False,episodes=[])
            recorder.reader=SimpleNamespace(error=None)
            recorder.cameras={}
            recorder.gate=FakeGate(); recorder.pending=None; recorder.finish_request=None
            def current_snapshot():
                data=snapshot(time.monotonic())
                data['arm']['data']['sample']['pilot']=recorder.gate.state()
                return data
            recorder.snapshot=current_snapshot
            thread=threading.Thread(target=recorder.work)
            thread.start()
            try:
                recorder.command('start'); recorder.command('start')
                deadline=time.monotonic()+2
                while recorder.get_status().get('sample_count',0)<2 and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertTrue(recorder.get_status()['recording'])
                recorder.reader.error='simulated telemetry disconnect'
                deadline=time.monotonic()+2
                while not recorder.get_status()['episodes'] and time.monotonic()<deadline:
                    time.sleep(.02)
                status=recorder.get_status()
                self.assertEqual(status['episodes'][0]['outcome'],'incomplete')
                self.assertFalse(status['recording'])
                self.assertEqual(recorder.gate.action,'lock')
                self.assertEqual(validate_episode(Path(directory)/status['episodes'][0]['name'])['outcome'],'incomplete')
            finally:
                recorder.done.set(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_three_finish_actions_wait_for_ack_and_save_frozen_task_and_unique_names(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=object.__new__(Recorder)
            recorder.directory=Path(directory);recorder.task={'name':'first task','display_name':'任务一'}
            recorder.done=threading.Event();recorder.lock=threading.Lock()
            recorder.commands=queue.Queue(maxsize=8);recorder.episode=None
            recorder.status=dict(recording=False,ready=False,episodes=[])
            recorder.reader=SimpleNamespace(error=None);recorder.cameras={}
            recorder.gate=FakeGate();recorder.pending=None;recorder.finish_request=None
            allow_lock=threading.Event();allow_lock.set()
            exported=[]
            recorder.video_exporter=SimpleNamespace(submit=lambda p:exported.append(json.loads((p/'episode.json').read_text())['outcome']))
            def current_snapshot():
                data=snapshot(time.monotonic())
                pilot=recorder.gate.state()
                if recorder.gate.action=='lock' and not allow_lock.is_set():pilot['command_id']-=1
                data['arm']['data']['sample']['pilot']=pilot
                return data
            recorder.snapshot=current_snapshot
            def wait_until(condition):
                deadline=time.monotonic()+3
                while not condition() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(condition(),recorder.get_status())
            thread=threading.Thread(target=recorder.work);thread.start()
            try:
                for index,(command,outcome) in enumerate([('finish_success','success'),('finish_failure','failure'),('finish','unlabeled')]):
                    recorder.task=dict(name='task_'+str(index),display_name='任务 '+str(index))
                    recorder.command('start');wait_until(lambda:recorder.get_status().get('sample_count',0)>=2)
                    active=recorder.episode.directory
                    allow_lock.clear();recorder.command(command);wait_until(lambda:bool(recorder.pending and recorder.pending['action']=='lock'))
                    recorder.command('finish_failure' if outcome!='failure' else 'finish_success')
                    self.assertEqual(json.loads((active/'episode.json').read_text())['status'],'recording')
                    allow_lock.set();wait_until(lambda:len(recorder.get_status()['episodes'])==index+1)
                    meta=json.loads((active/'episode.json').read_text())
                    self.assertEqual((meta['task'],meta['task_display_name'],meta['outcome']),('task_'+str(index),'任务 '+str(index),outcome))
                    self.assertEqual(validate_episode(active)['outcome'],outcome)
                    self.assertEqual(recorder.get_status()['last_saved_episode']['outcome'],outcome)
                self.assertEqual(len({e['name'] for e in recorder.get_status()['episodes']}),3)
                self.assertEqual(exported,['success','failure','unlabeled'])
                with self.assertRaisesRegex(ValueError,'补充标注'):recorder.command('finish_success')
            finally:
                allow_lock.set();recorder.done.set();thread.join(2)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
