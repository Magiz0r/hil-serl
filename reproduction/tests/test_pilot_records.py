import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]))
from pilot_dataset import SCHEMA
from pilot_records import EpisodeLibrary


class SavedRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.episode=self.root/'pilot_test'/'episode_0001';self.episode.mkdir(parents=True)
        self.metadata=dict(schema=SCHEMA,status='unlabeled',outcome='unlabeled',sample_count=2,
            task='new task',task_id='task_test',prompt='Pick up the tool',layout={'name':'scene A'},
            started_pc_monotonic=1.,ended_pc_monotonic=1.2,created_unix=100.)
        self.path=self.episode/'episode.json';self.path.write_text(json.dumps(self.metadata))
        cameras={}
        for name in ('external','wrist'):
            (self.episode/name).mkdir();(self.episode/name/'00000001.jpg').write_bytes(name.encode())
            cameras[name]={'path':name+'/00000001.jpg'}
        (self.episode/'samples.jsonl').write_text(json.dumps({'pc_sampled_at':1.,'cameras':cameras})+'\n')
        self.library=EpisodeLibrary([self.root]);self.identifier=self.library.list()[0]['id']

    def test_replay_review_and_soft_delete_preserve_samples(self):
        samples=(self.episode/'samples.jsonl').read_bytes()
        frames=self.library.frames(self.identifier)
        self.assertEqual(frames[0]['seconds'],0.)
        self.assertEqual(self.library.image(self.identifier,'wrist','00000001.jpg'),b'wrist')
        result=self.library.apply(dict(action='update',id=self.identifier,outcome='success',notes='Reviewed',prompt='Updated instruction'))
        self.assertEqual(result[0]['outcome'],'success')
        self.assertEqual(json.loads(self.path.read_text())['original_outcome'],'unlabeled')
        self.assertEqual(EpisodeLibrary([self.root]).list()[0]['notes'],'Reviewed')
        self.assertEqual((self.episode/'samples.jsonl').read_bytes(),samples)
        self.assertEqual(self.library.apply(dict(action='delete',id=self.identifier)),[])
        self.assertTrue((self.episode/'wrist'/'00000001.jpg').exists())
        with self.assertRaises(ValueError):self.library.frames(self.identifier)

    def test_active_and_aborted_records_do_not_become_model_failures(self):
        self.metadata.update(status='recording');self.path.write_text(json.dumps(self.metadata))
        self.assertEqual(EpisodeLibrary([self.root]).list(),[])
        self.metadata.update(status='incomplete',outcome='incomplete',reason='device disconnected')
        self.path.write_text(json.dumps(self.metadata))
        with self.assertRaises(ValueError):self.library.apply(dict(action='update',id=self.identifier,outcome='failure'))
        result=self.library.apply(dict(action='update',id=self.identifier,notes='Cable disconnected'))
        self.assertEqual(result[0]['outcome'],'incomplete')

    def test_no_arbitrary_paths_or_symlink_escape(self):
        with self.assertRaises(ValueError):self.library.image(self.identifier,'../','episode.json')
        image=self.episode/'external'/'00000001.jpg';image.unlink();image.symlink_to(self.path)
        with self.assertRaises(ValueError):self.library.image(self.identifier,'external','00000001.jpg')
        with self.assertRaises(ValueError):self.library.frames('../../etc/passwd')

    def test_same_legacy_basename_has_unique_display_and_keeps_frozen_task(self):
        other=self.root/'pilot_other'/'episode_0001';other.mkdir(parents=True)
        (other/'episode.json').write_text(json.dumps(dict(self.metadata,task='insert_tool',task_id='task_other',task_display_name='插入工具')))
        original=self.path.read_bytes()
        items=EpisodeLibrary([self.root]).list()
        self.assertEqual(len({e['name'] for e in items}),2)
        self.assertEqual(len({e['id'] for e in items}),2)
        self.assertEqual({e['storage_name'] for e in items},{'episode_0001'})
        self.assertEqual({e['task_display_name'] for e in items},{'new task','插入工具'})
        self.assertEqual(self.path.read_bytes(),original)
        self.library.apply(dict(action='update',id=self.identifier,outcome='success'))
        other_item=next(e for e in EpisodeLibrary([self.root]).list() if e['task_id']=='task_other')
        self.assertEqual(other_item['outcome'],'unlabeled')
