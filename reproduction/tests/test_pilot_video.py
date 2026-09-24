import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).parents[1]))
from pilot_dataset import Episode
from pilot_records import EpisodeLibrary
from pilot_video import export_episode, video_status, VideoExporter
from record_manual_pilot import Recorder, handler_for
from reproduction.tests.test_pilot_dataset import snapshot


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg/ffprobe required')
class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.path=self.root/'pilot_test/episode_0001'
        episode=Episode(self.path,dict(task='test'),1000.)
        for sequence,seconds in enumerate((0.,.1,.35,.6,.9),1):
            episode.append(snapshot(1000.+seconds,sequence),1000.+seconds)
        episode.finish('success',1001.)

    def originals(self):
        return {str(p.relative_to(self.path)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.path.rglob('*') if p.suffix in ('.jpg','.jsonl') or p.name=='episode.json'}

    def test_exports_both_h264_views_with_real_duration_and_preserves_originals(self):
        original=self.originals()
        result=export_episode(self.path)
        self.assertEqual(result['status'],'ready',result)
        for camera in ('external','wrist'):
            video=result['files'][camera]
            self.assertEqual(video['codec_name'],'h264')
            self.assertEqual(int(video['nb_frames']),10)
            self.assertAlmostEqual(video['duration_seconds'],1.,places=2)
            self.assertEqual((video['width'],video['height']),(32,24))
        self.assertEqual(self.originals(),original)
        timestamp=(self.path/'external.mp4').stat().st_mtime_ns
        self.assertEqual(export_episode(self.path)['status'],'ready')
        self.assertEqual((self.path/'external.mp4').stat().st_mtime_ns,timestamp)

    def test_corruption_is_separate_from_episode_result_and_can_retry(self):
        path=next((self.path/'external').glob('*.jpg'));original=path.read_bytes()
        path.write_bytes(b'broken')
        result=export_episode(self.path)
        self.assertEqual(result['status'],'error')
        self.assertFalse((self.path/'external.mp4').exists())
        self.assertEqual(json.loads((self.path/'episode.json').read_text())['outcome'],'success')
        path.write_bytes(original)
        self.assertEqual(export_episode(self.path)['status'],'ready')

    def test_cancelled_export_keeps_raw_frames_and_is_resumable(self):
        cancel=threading.Event();cancel.set()
        before=self.originals()
        self.assertEqual(export_episode(self.path,cancel)['status'],'queued')
        self.assertEqual(self.originals(),before)
        self.assertFalse((self.path/'external.mp4').exists())
        self.assertEqual(export_episode(self.path)['status'],'ready')

    def test_background_export_and_video_ranges(self):
        worker=VideoExporter();self.addCleanup(worker.close)
        library=EpisodeLibrary([self.root],worker)
        library.queue_missing_videos();worker.queue.join()
        item=library.list()[0]
        self.assertEqual(item['videos']['status'],'ready')
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(SimpleNamespace(library=library)))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
        def request(method,path,range_value=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request(method,path,headers={'Range':range_value} if range_value else {})
            response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read())
            connection.close();return result
        try:
            path=item['videos']['urls']['external'];content=(self.path/'external.mp4').read_bytes()
            code,headers,body=request('GET',path)
            self.assertEqual((code,headers['Content-Type'],body),(200,'video/mp4',content))
            self.assertEqual(request('GET',path,'bytes=4-15')[::2],(206,content[4:16]))
            self.assertEqual(request('GET',path,'bytes=-12')[::2],(206,content[-12:]))
            self.assertEqual(request('GET',path,'bytes=999999-')[0],416)
            self.assertEqual(request('HEAD',path)[::2],(200,b''))
            library.apply(dict(action='delete',id=item['id']))
            self.assertEqual(request('GET',path)[0],404)
            self.assertTrue((self.path/'external.mp4').exists())
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_finish_automatically_queues_mp4_after_raw_episode_is_saved(self):
        worker=VideoExporter();self.addCleanup(worker.close)
        path=self.root/'pilot_test/episode_0002'
        now=time.monotonic()-.2
        episode=Episode(path,dict(task='test'),now)
        episode.append(snapshot(now,1),now)
        episode.append(snapshot(now+.1,2),now+.1)
        recorder=object.__new__(Recorder)
        recorder.episode=episode;recorder.video_exporter=worker
        recorder.lock=threading.Lock();recorder.status={'episodes':[]}
        recorder.end('unlabeled')
        self.assertIsNone(recorder.episode)
        self.assertEqual(json.loads((path/'episode.json').read_text())['outcome'],'unlabeled')
        worker.queue.join()
        self.assertEqual(video_status(path)['status'],'ready')
