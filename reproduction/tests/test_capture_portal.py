import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).parents[1]))
import capture_portal as portal


class PortalLauncherTests(unittest.TestCase):
    def setUp(self):
        self.service={'pid':999999,'local_url':'http://127.0.0.1:8765/','tailscale_url':'http://100.64.1.2:8765/'}

    def test_stale_pid_file_does_not_authorize_process_control(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'service.json';path.write_text(json.dumps(self.service))
            with patch.object(portal,'SERVICE',path),patch.object(portal,'owns_process',return_value=False),patch.object(portal.os,'kill') as kill:
                self.assertIsNone(portal.existing_service());kill.assert_not_called()

    def test_repeated_start_reuses_service_without_opening_cameras_again(self):
        with patch.object(portal,'existing_service',return_value=self.service),patch.object(portal,'status',return_value={}),patch.object(portal.subprocess,'Popen') as spawn:
            self.assertEqual(portal.ensure_server(8765),self.service)
            spawn.assert_not_called()

    def test_web_only_start_never_requests_robot_connection(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(portal,'RUNTIME',Path(directory)),patch.object(portal,'ensure_server',return_value=self.service),patch.object(portal,'connect_control') as connect,patch.object(portal,'open_browser') as browser:
            self.assertEqual(portal.start(SimpleNamespace(port=8765,web_only=True,no_open=True)),0)
            connect.assert_not_called();browser.assert_not_called()

    def test_robot_connection_requires_actual_locked_ready_feedback(self):
        states=[{'runtime':{'phase':'disconnected','can_connect':True}},
                {'runtime':{'phase':'connecting'},'ready':False},
                {'runtime':{'phase':'connected'},'ready':True,'pilot':{'mode':'locked'}}]
        with patch.object(portal,'status',side_effect=states),patch.object(portal,'request') as request,patch.object(portal.time,'sleep'):
            self.assertEqual(portal.connect_control(self.service),0)
            request.assert_called_once_with(self.service,'/runtime',{'action':'connect'})

    def test_control_failure_leaves_web_server_running(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(portal,'RUNTIME',Path(directory)),patch.object(portal,'ensure_server',return_value=self.service),patch.object(portal,'connect_control',side_effect=RuntimeError('robot offline')),patch.object(portal.os,'kill') as kill:
            self.assertEqual(portal.start(SimpleNamespace(port=8765,web_only=False,no_open=True)),2)
            kill.assert_not_called()

    def test_stop_waits_for_saved_record_before_terminating_owned_server(self):
        states=[{'runtime':{'phase':'connected'},'recording':True,'pilot':{'mode':'manual'}},
                {'runtime':{'phase':'connected'},'recording':True,'pending':{'action':'lock'},'pilot':{'mode':'manual'}},
                {'runtime':{'phase':'connected'},'recording':False,'pending':None,'pilot':{'mode':'locked'}}]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'shutdown.json').write_text(json.dumps({'pid':999999,'control_stopped':True}))
            events=[]
            with patch.object(portal,'RUNTIME',root),patch.object(portal,'existing_service',return_value=self.service),patch.object(portal,'status',side_effect=states),patch.object(portal,'request',side_effect=lambda *args:events.append(('request',args))),patch.object(portal,'owns_process',side_effect=[True,False]),patch.object(portal.os,'kill',side_effect=lambda *args:events.append(('kill',args))),patch.object(portal.time,'sleep'):
                self.assertEqual(portal.stop(),0)
            self.assertEqual(events[0],('request',(self.service,'/command',{'command':'finish'})))
            self.assertEqual(events[1],('kill',(999999,portal.signal.SIGTERM)))

    def test_save_failure_does_not_claim_stopped_or_kill_server(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(portal,'RUNTIME',Path(directory)),patch.object(portal,'existing_service',return_value=self.service),patch.object(portal,'status',side_effect=[{'runtime':{'phase':'connected'},'recording':True},{'fatal':'disk full'}]),patch.object(portal,'request'),patch.object(portal.os,'kill') as kill:
            with self.assertRaisesRegex(RuntimeError,'未确认记录已保存'):portal.stop()
            kill.assert_not_called()

    def test_failed_control_cleanup_is_reported_as_partial_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'shutdown.json').write_text(json.dumps({'pid':999999,'control_stopped':False,'error':'timeout'}))
            with patch.object(portal,'RUNTIME',root),patch.object(portal,'existing_service',return_value=self.service),patch.object(portal,'status',return_value={'runtime':{'phase':'connected'}}),patch.object(portal,'owns_process',side_effect=[True,False]),patch.object(portal.os,'kill'):
                with self.assertRaisesRegex(RuntimeError,'控制进程未确认退出'):portal.stop()
