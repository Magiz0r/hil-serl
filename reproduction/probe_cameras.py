"""Camera-only probe. Run under timeout; never constructs a robot environment."""
import argparse
import json
import time
from pathlib import Path
import cv2
from franka_env.camera.zed_uvc_capture import ZEDUVCCapture

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).parent / 'configs' / 'cameras.json')
    parser.add_argument('--frames', type=int, default=30)
    parser.add_argument('--preview-dir', type=Path, help='Optionally save last single-eye frames locally')
    args = parser.parse_args()
    if not 1 <= args.frames <= 300:
        parser.error('frames must be between 1 and 300')
    captures = {}
    try:
        for name, settings in json.loads(args.config.read_text()).items():
            settings = dict(settings)
            if settings.pop('backend') != 'zed_uvc':
                raise ValueError('This probe accepts only zed_uvc cameras')
            captures[name] = ZEDUVCCapture(name=name, **settings)
        begin = time.monotonic()
        results = {}
        last_frames = {}
        for _ in range(args.frames):
            for name, capture in captures.items():
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f'{name}: capture failed')
                last_frames[name] = frame
                rgb = cv2.resize(frame, (128, 128))[..., ::-1]
                results[name] = dict(bgr_eye_shape=list(frame.shape),
                                     policy_rgb_shape=list(rgb.shape),
                                     mean=float(frame.mean()), std=float(frame.std()))
        if args.preview_dir:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            for name, frame in last_frames.items():
                if Path(name).name != name or name in ('.', '..'):
                    raise ValueError('Camera name must be a simple filename')
                if not cv2.imwrite(str(args.preview_dir / f'{name}.png'), frame):
                    raise RuntimeError(f'Failed to save preview for {name}')
        print(json.dumps(dict(frames_per_camera=args.frames,
                              elapsed_seconds=time.monotonic()-begin,
                              cameras=results), indent=2))
    finally:
        for capture in captures.values():
            capture.close()

if __name__ == '__main__':
    main()
