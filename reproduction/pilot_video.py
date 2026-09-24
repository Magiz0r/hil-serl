"""Derived MP4s for saved episodes; raw frames and sample timestamps stay intact."""
import argparse
import atexit
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

from pilot_dataset import CAMERAS, validate_episode


def video_status(directory):
    directory = Path(directory)
    try:
        value = json.loads((directory/'videos.json').read_text())
        if not isinstance(value,dict) or value.get('status') not in ('queued', 'encoding', 'ready', 'error'):
            raise ValueError('invalid video status')
        if value['status'] == 'ready':
            for camera in CAMERAS:
                path = directory/(camera+'.mp4')
                if path.resolve().parent != directory.resolve() or path.stat().st_size == 0:
                    raise ValueError('video file missing')
        return value
    except FileNotFoundError:
        return dict(status='missing')
    except (OSError, ValueError):
        return dict(status='error', error='视频状态或文件不可用，可以重新生成')


def save_status(directory, **value):
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.video-status-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(dict(value, updated_unix=time.time()), stream, ensure_ascii=False, allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
            temporary.replace(Path(directory)/'videos.json')
        finally:
            temporary.unlink(missing_ok=True)


def run_encoder(command, log, cancel, timeout):
    with log.open('wb') as output, subprocess.Popen(command, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=output) as process:
        deadline = time.monotonic()+timeout
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise InterruptedError('视频生成将在下次启动时继续')
                if time.monotonic()>deadline:
                    raise TimeoutError('视频编码超时')
            if process.returncode:
                raise RuntimeError('视频编码失败：'+log.read_text(errors='replace')[-1500:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)


def export_episode(directory, cancel=None):
    directory = Path(directory).resolve()
    cancel = cancel or threading.Event()
    # Multiple portal/CLI requests must never encode the same episode together.
    with (directory/'.video-export.lock').open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return video_status(directory)
        if video_status(directory)['status']=='ready':return video_status(directory)
        metadata = json.loads((directory/'episode.json').read_text())
        if metadata.get('status')=='recording':raise ValueError('请先 Finish 保存记录')
        try:
            if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
                raise RuntimeError('未安装 ffmpeg / ffprobe，原始数据已保存')
            validate_episode(directory)
            samples = [json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
            if not samples:raise ValueError('记录中没有图像，无法生成视频')
            start, end = metadata['started_pc_monotonic'], metadata['ended_pc_monotonic']
            duration = end-start
            if not math.isfinite(duration) or duration<=0:raise ValueError('记录时间无效')
            times = [sample['pc_sampled_at']-start for sample in samples]
            if times[0]<0 or times[-1]>duration or any(b<=a for a,b in zip(times,times[1:])):
                raise ValueError('记录时间戳顺序无效')
            # Preserve real elapsed time, including irregular capture intervals.
            # MP4 is a 10 FPS review copy; samples.jsonl remains authoritative.
            times[0] = 0.
            lengths = [b-a for a,b in zip(times,times[1:]+[duration])]
            save_status(directory, status='encoding', duration_seconds=duration)
            files = {}
            with tempfile.TemporaryDirectory(dir=directory, prefix='.video-export-') as temporary:
                work = Path(temporary)
                for camera in CAMERAS:
                    concat = ['ffconcat version 1.0']
                    for sample, seconds in zip(samples,lengths):
                        relative = sample['cameras'][camera]['path']
                        if not re.fullmatch(camera+r'/[0-9]{8}\.jpg', relative):raise ValueError('invalid frame path')
                        if (directory/relative).resolve().parent != directory/camera:raise ValueError('invalid frame location')
                        concat += ["file '../"+relative+"'", 'duration %.9f'%max(seconds,.001)]
                    concat += ["file '../"+samples[-1]['cameras'][camera]['path']+"'"]
                    listing = work/(camera+'.ffconcat');listing.write_text('\n'.join(concat)+'\n')
                    target = work/(camera+'.mp4')
                    command = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',
                        '-threads','1','-f','concat','-safe','0','-protocol_whitelist','file',
                        '-i',str(listing),'-an','-vf','fps=10,format=yuv420p','-filter_threads','1',
                        '-c:v','libx264','-preset','veryfast','-crf','18','-threads','2',
                        '-movflags','+faststart','-t',str(max(duration,.1)),str(target)]
                    run_encoder(command,work/(camera+'.log'),cancel,max(120,duration*3))
                    probe = json.loads(subprocess.check_output(['ffprobe','-v','error',
                        '-show_entries','stream=codec_name,width,height,nb_frames:format=duration',
                        '-of','json',str(target)],text=True,timeout=10))
                    actual_duration = float(probe['format']['duration'])
                    if not probe['streams'] or abs(actual_duration-duration)>.11:
                        raise ValueError('导出视频时长与记录不一致')
                    files[camera] = dict(file=camera+'.mp4', bytes=target.stat().st_size,
                        duration_seconds=actual_duration, **probe['streams'][0])
                if cancel.is_set():raise InterruptedError('视频生成将在下次启动时继续')
                for camera in CAMERAS:(work/(camera+'.mp4')).replace(directory/(camera+'.mp4'))
            save_status(directory,status='ready',files=files,fps=10,duration_seconds=duration,
                        samples_sha256=metadata['samples_sha256'])
        except InterruptedError as error:
            save_status(directory,status='queued',error=str(error))
        except Exception as error:
            save_status(directory,status='error',error=str(error) or type(error).__name__)
        return video_status(directory)


class VideoExporter:
    """One background encoder, independent of camera and control loops."""
    def __init__(self):
        self.queue = queue.Queue()
        self.pending = set()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.work,daemon=True,name='episode-video-export')
        self.thread.start()
        atexit.register(self.close)

    def submit(self, directory):
        path = Path(directory).resolve()
        with self.lock:
            if self.done.is_set() or path in self.pending:return
            self.pending.add(path);self.queue.put(path)

    def is_pending(self, directory):
        with self.lock:return Path(directory).resolve() in self.pending

    def work(self):
        while not self.done.is_set():
            try:path=self.queue.get(timeout=.2)
            except queue.Empty:continue
            try:export_episode(path,self.done)
            except Exception as error:
                # A missing/deleted episode cannot terminate the queue worker.
                print('Video export failed for %s: %s'%(path,error),flush=True)
            finally:
                with self.lock:self.pending.discard(path)
                self.queue.task_done()

    def close(self):
        self.done.set()
        self.thread.join(timeout=8)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('episodes',nargs='+',type=Path)
    args=parser.parse_args()
    failed=False
    for path in args.episodes:
        result=export_episode(path);print(json.dumps(dict(directory=str(path),**result),ensure_ascii=False))
        failed |= result['status']!='ready'
    raise SystemExit(1 if failed else 0)
