import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from pilot_catalog import CaptureCatalog
from record_manual_pilot import Recorder


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = CaptureCatalog(self.root, dict(name='initial', prompt='Initial prompt'))

    def test_tasks_prompts_layouts_persist_and_snapshots_are_independent(self):
        c = self.catalog
        first = c.snapshot()['task_id']
        c.apply(dict(action='task_create', name='连接器插入', prompt='Insert connector', success_definition='Fully inserted'))
        task = c.snapshot()['task_id']
        frames = {n: dict(jpeg=(n+'-image').encode(), pc_captured_at=time.monotonic()) for n in ('external','wrist')}
        c.apply(dict(action='layout_capture', name='偏移场景', ood=True), frames)
        layout = c.snapshot()['layout_id']
        recorded = c.selection()
        self.assertEqual(c.image(layout, 'wrist'), b'wrist-image')
        c.apply(dict(action='task_update', task_id=task, prompt='New instruction', success_definition='New criterion'))
        c.apply(dict(action='layout_rename', layout_id=layout, name='Renamed'))
        self.assertEqual(recorded['prompt'], 'Insert connector')
        self.assertEqual(recorded['layout']['name'], '偏移场景')
        restored = CaptureCatalog(self.root, dict(name='unused'))
        self.assertEqual(restored.task(task)['prompt'], 'New instruction')
        self.assertTrue(restored.layout(layout)['ood'])
        with self.assertRaises(ValueError): c.apply(dict(action='select', task_id=first, layout_id=layout))
        c.apply(dict(action='layout_delete', layout_id=layout))
        c.apply(dict(action='task_delete', task_id=task))
        self.assertTrue((self.root/layout/'external.jpg').exists())
        self.assertEqual(recorded['task']['name'], '连接器插入')
        self.assertIsNone(c.snapshot()['task_id'])
        with self.assertRaises(ValueError): c.selection()

    def test_invalid_frames_paths_and_failed_save_do_not_change_selection(self):
        before = self.catalog.snapshot()
        for frames in (None, {'external': dict(jpeg=b'x',pc_captured_at=time.monotonic()-1)}):
            with self.assertRaises(ValueError): self.catalog.apply(dict(action='layout_capture',name='stale'), frames)
        with self.assertRaises(ValueError): self.catalog.image('../catalog.json','external')
        with patch('pilot_catalog.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.catalog.apply(dict(action='task_create',name='lost',prompt='test'))
        self.assertEqual(self.catalog.snapshot(), before)
        self.assertEqual(len(json.loads(self.catalog.path.read_text())['tasks']),1)

    def test_capture_and_queued_home_lock_catalog_configuration(self):
        recorder = object.__new__(Recorder)
        recorder.lock = threading.Lock()
        recorder.catalog = self.catalog
        recorder.episode = recorder.pending = None
        recorder.commands = queue.Queue()
        recorder.status = {'pilot': {'mode': 'locked'}}
        action = dict(action='task_create',name='another',prompt='Another task')
        for mode in ('manual','waiting_for_center','homing'):
            recorder.status['pilot']['mode']=mode
            with self.assertRaises(ValueError): recorder.catalog_action(action)
        recorder.status['pilot']['mode']='locked'
        recorder.commands.put('home')
        with self.assertRaises(ValueError): recorder.catalog_action(action)
        recorder.commands.get()
        self.assertEqual(recorder.catalog_action(action)['tasks'][-1]['name'],'another')
