"""ZED raw UVC color capture, compatible with SERL's RSCapture read contract.

Returns one unrectified BGR eye. FrankaEnv.get_im performs the RGB conversion.
No ZED SDK, depth, robot connection or gripper command is used here.
"""
import os
import cv2
import numpy as np


def select_eye(frame, eye):
    if eye not in ("left", "right"):
        raise ValueError("eye must be left or right")
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3
            or frame.shape[0] == 0 or frame.shape[1] < 2 or frame.shape[1] % 2):
        raise ValueError("Expected an even-width uint8 BGR side-by-side frame")
    half = frame.shape[1] // 2
    return (frame[:, :half] if eye == "left" else frame[:, half:]).copy()


class ZEDUVCCapture:
    def __init__(self, name, device, eye="left", dim=(2560, 720), fps=15):
        if eye not in ("left", "right"):
            raise ValueError("eye must be left or right")
        if len(dim) != 2 or any(v <= 0 for v in dim) or dim[0] % 2 or fps <= 0:
            raise ValueError("dim is full stereo width/height; positive sizes/fps required")
        if not device.startswith(('/dev/v4l/by-id/', '/dev/v4l/by-path/')):
            raise ValueError("Use /dev/v4l/by-id/ or /dev/v4l/by-path/, not a camera index")
        if not os.access(device, os.R_OK | os.W_OK):
            raise PermissionError(f"Camera missing or inaccessible: {device}")
        self.name, self.eye = name, eye
        self.cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"Cannot open camera: {device}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, dim[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, dim[1])
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return False, None
        return True, select_eye(frame, self.eye)

    def close(self):
        self.cap.release()
