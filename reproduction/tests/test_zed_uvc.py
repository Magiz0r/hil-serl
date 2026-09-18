import unittest
import numpy as np
from franka_env.camera.zed_uvc_capture import select_eye, ZEDUVCCapture

class StereoContract(unittest.TestCase):
    def test_selects_eye_and_preserves_bgr(self):
        stereo = np.zeros((4, 12, 3), dtype=np.uint8)
        stereo[:, :6] = [1, 2, 3]
        stereo[:, 6:] = [4, 5, 6]
        for side, expected in [('left', [1, 2, 3]), ('right', [4, 5, 6])]:
            frame = select_eye(stereo, side)
            self.assertEqual(frame.shape, (4, 6, 3))
            np.testing.assert_array_equal(frame[0, 0], expected)
            self.assertFalse(np.shares_memory(frame, stereo))
    def test_rejects_malformed_frames(self):
        for frame in [None, np.zeros((2, 3, 3), dtype=np.uint8),
                      np.zeros((2, 4)), np.zeros((2, 4, 3), dtype=float)]:
            with self.assertRaises(ValueError):
                select_eye(frame, 'left')
    def test_usb_port_path_is_used_without_index_fallback(self):
        from unittest.mock import patch
        device = '/dev/v4l/by-path/pci-test-video-index0'
        with patch('franka_env.camera.zed_uvc_capture.os.access', return_value=True), \
             patch('franka_env.camera.zed_uvc_capture.cv2.VideoCapture') as capture:
            camera = ZEDUVCCapture('external', device)
            self.assertEqual(capture.call_args.args[0], device)
            camera.close()
            capture.return_value.release.assert_called_once()
        with patch('franka_env.camera.zed_uvc_capture.os.access', return_value=False), \
             patch('franka_env.camera.zed_uvc_capture.cv2.VideoCapture') as capture:
            with self.assertRaises(PermissionError):
                ZEDUVCCapture('external', device)
            capture.assert_not_called()

    def test_rejects_unstable_device_index(self):
        with self.assertRaises(ValueError):
            ZEDUVCCapture('test', '/dev/video0')


class BackendSelection(unittest.TestCase):
    def test_default_and_zed_backends_keep_config(self):
        from unittest.mock import patch
        from franka_env.envs.franka_env import FrankaEnv
        env = object.__new__(FrankaEnv)
        env.cap = None
        config = {'old': {'serial_number': 'test'},
                  'new': {'backend': 'zed_uvc', 'device': '/dev/v4l/by-id/test'}}
        with patch('franka_env.envs.franka_env.RSCapture') as rs, \
             patch('franka_env.camera.zed_uvc_capture.ZEDUVCCapture') as zed, \
             patch('franka_env.envs.franka_env.VideoCapture', side_effect=lambda cap: cap):
            env.init_cameras(config)
            rs.assert_called_once_with(name='old', serial_number='test')
            zed.assert_called_once_with(name='new', device='/dev/v4l/by-id/test')
            self.assertEqual(config['new']['backend'], 'zed_uvc')
            self.assertEqual(list(env.cap), ['old', 'new'])

if __name__ == '__main__':
    unittest.main()
