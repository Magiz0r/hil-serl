import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).parents[1]))
from record_manual_pilot import Recorder, handler_for
from pilot_web import ASSETS, asset, stream_status
from preview_pilot import PreviewHandler


class PilotHTTPTests(unittest.TestCase):
    def test_only_local_same_origin_commands_reach_recorder(self):
        recorder=Mock()
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(recorder))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01})
        thread.start()
        def post(host,origin):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            connection.request('POST','/command',json.dumps(dict(command='finish')),
                {'Host':host,'Origin':origin,'Content-Type':'application/json'})
            response=connection.getresponse(); code=response.status
            response.read(); connection.close(); return code
        try:
            host='127.0.0.1:%d'%server.server_port
            self.assertEqual(post('external.example:%d'%server.server_port,'http://external.example'),400)
            self.assertEqual(post(host,'http://external.example'),400)
            recorder.command.assert_not_called()
            self.assertEqual(post(host,'http://'+host),202)
            recorder.command.assert_called_once_with('finish')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_page_images_states_and_commands_work_without_access_link_or_cookie(self):
        recorder=Mock()
        recorder.get_status.return_value=dict(ready=True)
        recorder.catalog.snapshot.return_value={}
        recorder.catalog.image.return_value=b'layout-image'
        recorder.catalog_action.return_value={}
        recorder.library.list.return_value=[]
        recorder.library.apply.return_value=[]
        camera=Mock(error=None)
        camera.snapshot.side_effect=lambda:dict(pc_captured_at=time.monotonic(),preview=b'camera-image')
        recorder.cameras={'external':camera,'wrist':camera}
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(recorder))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01})
        thread.start()
        host='127.0.0.1:%d'%server.server_port
        def request(method,path,data=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            headers={'Host':host,'Origin':'http://'+host,'Content-Type':'application/json'}
            connection.request(method,path,json.dumps(data) if data is not None else None,headers)
            response=connection.getresponse(); result=(response.status,response.getheaders(),response.read())
            connection.close(); return result
        try:
            for path in (*ASSETS,'/status','/tasks','/episodes','/layouts/layout_abcdef123456/external.jpg','/camera/external.jpg','/camera/wrist.jpg'):
                code,headers,_=request('GET',path)
                self.assertEqual(code,200,path)
                self.assertNotIn('Set-Cookie',dict(headers))
            for path in ('/ui/../record_manual_pilot.py', '/data/episode.json', '/ui/missing.js'):
                self.assertEqual(request('GET',path)[0],404)
            self.assertEqual(request('POST','/command',dict(command='finish'))[0],202)
            recorder.command.assert_called_once_with('finish')
            for path,code in (('/catalog',200),('/runtime',202),('/episodes',200)):
                self.assertEqual(request('POST',path,dict(action='test'))[0],code)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_finish_cancels_queued_start_home_and_is_prioritized(self):
        recorder=object.__new__(Recorder)
        recorder.commands=queue.Queue(maxsize=8); recorder.done=threading.Event()
        recorder.lock=threading.Lock(); recorder.status={}; recorder.finish_request=None
        recorder.cameras={}; recorder.pending=None
        for command in ('start','home','set_custom_home','custom_home','finish'):
            recorder.command(command)
        self.assertTrue(recorder.commands.empty())
        self.assertEqual(recorder.finish_request,'finish')

    def test_stale_camera_is_not_served_as_a_live_frame(self):
        camera=Mock(error=None)
        camera.snapshot.return_value=dict(pc_captured_at=time.monotonic()-2,preview=b'old-frame')
        recorder=Mock(cameras={'external':camera})
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(recorder))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
        def get():
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            connection.request('GET','/camera/external.jpg')
            response=connection.getresponse();result=(response.status,response.read());connection.close();return result
        try:
            self.assertEqual(get()[0],503)
            camera.snapshot.return_value=dict(pc_captured_at=time.monotonic(),preview=b'fresh-frame')
            self.assertEqual(get(),(200,b'fresh-frame'))
            camera.error='camera ended'
            self.assertEqual(get()[0],503)
            recorder.command.assert_not_called()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_preview_has_no_control_api_and_only_serves_allowlisted_assets(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),PreviewHandler)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
        def request(method,path):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            connection.request(method,path)
            response=connection.getresponse();result=(response.status,response.getheader('Location'));response.read();connection.close();return result
        try:
            self.assertEqual(request('GET','/'),(302,'/?demo=1'))
            self.assertEqual(request('GET','/?demo=1')[0],200)
            self.assertEqual(request('GET','/status')[0],404)
            self.assertEqual(request('GET','/data/secret')[0],404)
            self.assertEqual(request('POST','/command')[0],405)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_stream_badges_do_not_infer_health_from_an_open_port(self):
        self.assertIsNone(asset('/ui/../../etc/passwd'))
        status=stream_status({},100.)
        self.assertFalse(status['arm']['ok'])
        status=stream_status(dict(gripper=dict(pc_received_at=99.9,data=dict(phase='ready',status=dict(gFLT=9)))),100.)
        self.assertFalse(status['gripper']['ok'])
