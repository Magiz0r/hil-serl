from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).parents[1]))
from start_capture_console import CaptureRuntime, control_failure


class RuntimeTests(unittest.TestCase):
    def test_control_failure_exposes_joint_limit_reason_despite_cleanup_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'control.log'
            path.write_text('SpaceMouse found\n{"phase":"stopped","error":"ValueError: joint 4 margin -0.003130 rad is below 0.000000 rad"}\n{"phase":"container_stopped"}\n')
            message=control_failure(path)
            self.assertIn('J4',message);self.assertIn('0.003130 rad',message)
            self.assertIn('移回允许范围',message);self.assertIn(str(path),message)
            path.write_text('incomplete startup output\n{"error":')
            self.assertIn('控制进程退出',control_failure(path))

    def test_camera_permission_error_is_visible_and_retry_clears_it_without_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'runtime').mkdir()
            camera=Mock(error=None);camera.snapshot.return_value=None
            with patch('start_capture_console.ROOT',root),patch('start_capture_console.Camera',side_effect=[PermissionError('read/write denied'),PermissionError('read/write denied'),camera,camera]),patch('start_capture_console.subprocess.Popen') as launch:
                runtime=CaptureRuntime({'external':{},'wrist':{}},{'name':'test','prompt':'Test'},root/'runtime')
                try:
                    status=runtime.get_status()
                    self.assertEqual(status['camera_errors'],dict(external='read/write denied',wrist='read/write denied'))
                    self.assertEqual(status['camera_ages'],dict(external=None,wrist=None))
                    runtime.runtime_action({'action':'retry_cameras'})
                    self.assertEqual(runtime.get_status()['camera_errors'],dict(external=None,wrist=None))
                    self.assertEqual(runtime.phase,'disconnected');launch.assert_not_called()
                finally:runtime.close()

    def test_page_status_and_catalog_never_start_controller_and_preflight_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'reproduction/nuc').mkdir(parents=True)
            (root/'reproduction/nuc/readonly_preflight.py').write_text('# test only')
            (root/'runtime').mkdir()
            camera=Mock(error=None)
            camera.snapshot.return_value=None
            with patch('start_capture_console.ROOT',root),patch('start_capture_console.Camera',return_value=camera),patch('start_capture_console.subprocess.run') as run,patch('start_capture_console.subprocess.Popen') as launch:
                runtime=CaptureRuntime({'external':{},'wrist':{}},{'name':'initial','prompt':'Test'},root/'runtime')
                try:
                    for _ in range(3): self.assertFalse(runtime.get_status()['ready'])
                    runtime.catalog_action(dict(action='task_create',name='other',prompt='Other prompt'))
                    self.assertEqual(runtime.get_status()['task']['name'],'other')
                    with self.assertRaises(ValueError): runtime.command('start')
                    run.assert_not_called();launch.assert_not_called()
                    run.return_value=Mock(returncode=1,stdout='',stderr='robot unavailable')
                    runtime.runtime_action({'action':'connect'})
                    runtime.worker.join(2)
                    self.assertEqual(runtime.get_status()['runtime']['phase'],'error')
                    self.assertIn('预检未通过',runtime.get_status()['reason'])
                    launch.assert_not_called()
                finally: runtime.close()

    def test_disconnect_rejects_an_active_recording(self):
        import threading
        runtime=object.__new__(CaptureRuntime)
        runtime.lock=threading.RLock();runtime.worker=None;runtime.phase='connected'
        runtime.recorder=Mock();runtime.recorder.get_status.return_value={'recording':True}
        with self.assertRaisesRegex(ValueError,'Finish'):runtime.runtime_action({'action':'disconnect'})
        runtime.recorder.close.assert_not_called()

    def test_cancel_during_preflight_cannot_launch_control(self):
        import threading
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'reproduction/nuc').mkdir(parents=True)
            (root/'reproduction/nuc/readonly_preflight.py').write_text('# test');(root/'runtime').mkdir()
            camera=Mock(error=None);camera.snapshot.return_value=None
            entered,released=threading.Event(),threading.Event()
            def preflight(*args,**kwargs):
                entered.set();released.wait(2);return Mock(returncode=0,stdout='READ_ONLY_PREFLIGHT_OK',stderr='')
            with patch('start_capture_console.ROOT',root),patch('start_capture_console.Camera',return_value=camera),patch('start_capture_console.subprocess.run',side_effect=preflight),patch('start_capture_console.subprocess.Popen') as launch:
                runtime=CaptureRuntime({'external':{},'wrist':{}},{'name':'test','prompt':'Test'},root/'runtime')
                try:
                    runtime.runtime_action({'action':'connect'});self.assertTrue(entered.wait(2))
                    runtime.runtime_action({'action':'disconnect'});released.set();runtime.worker.join(2)
                    self.assertEqual(runtime.phase,'disconnected');launch.assert_not_called()
                finally:released.set();runtime.close()
