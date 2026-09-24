#!/usr/bin/env python3
"""Camera/telemetry recorder with an explicit Start/Finish/Home control gate.

Attaches to an already running isolated manual session. Serves a loopback-only
preview, operator labels, and optional direct Tailscale access. Data is raw pilot data, not a trainer replay export.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import urlparse
import uuid

from pilot_dataset import CAMERAS, SCHEMA, Episode, atomic_json, health_error, new_episode_name
from pilot_control import PilotClient
from pilot_gripper import PREPARE_ACTIONS, preparation_status
from pilot_web import asset, stream_status
from pilot_catalog import CaptureCatalog
from pilot_records import EpisodeLibrary
from pilot_video import VideoExporter


ROOT = Path(__file__).resolve().parents[1]


class Camera:
    def __init__(self, name, settings):
        from franka_env.camera.zed_uvc_capture import ZEDUVCCapture
        settings = dict(settings)
        if settings.pop('backend') != 'zed_uvc':
            raise ValueError('pilot recorder requires an explicitly identified ZED UVC camera')
        busy = subprocess.run(['fuser', settings['device']], capture_output=True, text=True)
        if busy.returncode not in (0, 1) or busy.stdout.strip():
            raise RuntimeError(name + ': camera device is already in use or cannot be checked')
        self.capture = ZEDUVCCapture(name=name, **settings)
        self.lock, self.done = threading.Lock(), threading.Event()
        self.frame, self.error = None, None
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        import cv2
        sequence = 0
        try:
            while not self.done.is_set():
                ok, frame = self.capture.read()
                captured = time.monotonic()
                if not ok:
                    raise RuntimeError('camera capture failed')
                sequence += 1
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                preview_ok, preview = cv2.imencode('.jpg', cv2.resize(frame, (640, 360)),
                                                  [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok or not preview_ok:
                    raise RuntimeError('camera encoding failed')
                latest = dict(sequence=sequence, pc_captured_at=captured,
                              shape=list(frame.shape), jpeg=encoded.tobytes(), preview=preview.tobytes())
                with self.lock:
                    self.frame = latest
        except Exception as error:
            self.error = str(error)
        finally:
            self.capture.close()

    def snapshot(self):
        with self.lock:
            return self.frame

    def close(self):
        self.done.set()
        self.thread.join(timeout=2)


class LocalTail:
    def __init__(self, path):
        self.file = Path(path).open()
        self.file.seek(0, 2)
        self.value = None

    def poll(self):
        while True:
            position = self.file.tell()
            line = self.file.readline()
            if not line or not line.endswith('\n'):
                self.file.seek(position)
                return self.value
            self.value = dict(pc_received_at=time.monotonic(), data=json.loads(line))

    def close(self):
        self.file.close()


class RobotReader:
    def __init__(self, run_name, directory):
        if not re.fullmatch(r'manual-\d{8}T\d{6}Z-\d+', run_name):
            raise ValueError('unexpected control run name')
        self.lock = threading.Lock()
        self.value, self.error = None, None
        self.directory = directory
        self.stderr = (directory / 'state-reader.stderr.log').open('x')
        # The only NUC operation is reading our own existing state log and manifest.
        script = "RUN_NAME = " + repr(run_name) + '\n' + r'''
import json, sys, time
from pathlib import Path
root = Path('/home/tasl/hil_serl_runtime_20260918')
path = root / 'state/logs' / RUN_NAME / 'samples.jsonl'
assert path.resolve() == path and path.is_file()
print(json.dumps(dict(kind='manifest', source_sha256=json.loads((root/'source-sha256.json').read_text()))), flush=True)
with path.open() as stream:
    stream.seek(0, 2)
    updated = started = time.monotonic()
    while time.monotonic() - started < 28800:
        latest = None
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line or not line.endswith('\n'):
                stream.seek(position)
                break
            latest = json.loads(line)
        if latest is not None:
            updated = time.monotonic()
            print(json.dumps(dict(kind='state', source_read_at=updated, sample=latest)), flush=True)
        elif time.monotonic() - updated > 5:
            break
        time.sleep(.02)
'''
        self.process = subprocess.Popen([
            'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ServerAliveInterval=3',
            '-o', 'ServerAliveCountMax=2', 'FrankaNUC', 'python3', '-u', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.process.stdin.write(script)
        self.process.stdin.close()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            for line in self.process.stdout:
                data = json.loads(line)
                if data['kind'] == 'manifest':
                    atomic_json(self.directory / 'nuc-source-manifest.json', data)
                else:
                    with self.lock:
                        self.value = dict(pc_received_at=time.monotonic(), data=data)
            self.error = 'robot state reader ended; attach to the next manual session to resume'
        except Exception as error:
            self.error = str(error)

    def snapshot(self):
        with self.lock:
            return self.value

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.thread.join(timeout=1)
        if not self.thread.is_alive():
            self.process.stdout.close()
        self.stderr.close()


class Recorder:
    def __init__(self, directory, session, settings, task, catalog_root=None, cameras=None, catalog=None, video_exporter=None):
        self.directory, self.task = directory, task
        self.catalog = catalog or CaptureCatalog(catalog_root or ROOT / 'reproduction/data/capture_catalog', task)
        self.owns_cameras = cameras is None
        self.cameras = cameras or {}
        self.reader = self.control = self.gripper = self.gate = None
        self.pending = None
        self.finish_request = None
        self.commands = queue.Queue(maxsize=8)
        self.done, self.lock = threading.Event(), threading.Lock()
        self.episode = None
        self.status = dict(recording=False, ready=False, reason='starting cameras and state reader',
                           directory=str(directory), episodes=[], sample_count=0,
                           task=task, session_started_unix=time.time(), sample_hz=10)
        records = [json.loads(line) for line in (session / 'events.jsonl').read_text().splitlines()]
        configured = next(row for row in records if row['phase'] == 'configured')
        if records[-1]['phase'] != 'ready' or time.time() - (session / 'events.jsonl').stat().st_mtime > 2:
            raise ValueError('selected manual session is not currently ready')
        if not configured.get('pilot_gate'):
            raise ValueError('restart manual control with --pilot-gate for Start/Finish/Home')
        run_name = Path(configured['run_directory']).name
        atomic_json(directory / 'session.json', dict(schema=SCHEMA, purpose='raw_pilot_not_trainer_export',
            task=task, cameras=settings, control_log_directory=str(session), control_configuration=configured,
            sample_hz=10, camera_timestamp='PC monotonic immediately after UVC read; no hardware synchronization',
            state_timestamp='NUC monotonic plus PC receipt time; clocks are not directly subtracted',
            image_format='single-eye JPEG at camera resolution; decode BGR then convert RGB for learning',
            action_semantics='raw normalized SpaceMouse input plus applied pose target and observed gripper command ID; no policy-action conversion yet',
            label_semantics='episode outcome from operator; intermediate frames are not automatically labelled successful',
            created_utc=datetime.now(timezone.utc).isoformat(),
            recorder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        try:
            self.owns_video_exporter=video_exporter is None
            self.video_exporter=video_exporter or VideoExporter()
            self.library=EpisodeLibrary([directory.parent],self.video_exporter)
            self.gate = PilotClient(session, records[-1].get('pilot', {}).get('command_id', 0))
            if self.owns_cameras:
                for name in CAMERAS:
                    self.cameras[name] = Camera(name, settings[name])
            self.control = LocalTail(session / 'events.jsonl')
            self.gripper = LocalTail(session / 'gripper-events.jsonl')
            self.reader = RobotReader(run_name, directory)
            self.thread = threading.Thread(target=self.work, daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        return dict(arm=self.reader.snapshot(), control=self.control.poll(), gripper=self.gripper.poll(),
                    cameras={name: camera.snapshot() for name, camera in self.cameras.items()})

    def end(self, outcome, reason=None):
        if self.episode:
            result = self.episode.finish(outcome, time.monotonic(), reason)
            if getattr(self,'video_exporter',None):self.video_exporter.submit(self.episode.directory)
            with self.lock:
                saved=dict(name=self.episode.directory.name, outcome=result['outcome'],
                    samples=result['sample_count'], reason=result['reason'], task=result.get('task'),
                    task_display_name=result.get('task_display_name',result.get('task')),
                    task_id=result.get('task_id'), prompt=result.get('prompt'), layout=result.get('layout'),
                    notes=result.get('notes', ''),
                    created_unix=result['created_unix'],
                    duration_seconds=round(result['ended_pc_monotonic'] - result['started_pc_monotonic'], 3))
                self.status['episodes'].append(saved)
                self.status['last_saved_episode']=copy.deepcopy(saved)
            self.episode = None
            if hasattr(self,'library'):self.library.refresh(force=True)

    def issue(self, action, now, outcome=None):
        identifier = self.gate.issue(action)
        self.pending = dict(id=identifier, action=action, at=now, outcome=outcome)
        with self.lock:
            self.status.pop('last_command_error', None)

    def fail_locked(self, reason):
        # A recording failure must also stop manual control and automated Home.
        if self.gate.action != 'lock':
            self.gate.issue('lock')
        self.pending = None
        self.end('incomplete', reason)

    def work(self):
        next_sample = time.monotonic()
        try:
            while not self.done.is_set():
                self.gate.refresh()
                snapshot = self.snapshot()
                now = time.monotonic()
                problem = self.reader.error or next((c.error for c in self.cameras.values() if c.error), None)
                problem = problem or health_error(snapshot, now, allow_home_transition=self.episode is None)
                pilot = ((snapshot.get('arm') or {}).get('data', {}).get('sample', {}).get('pilot', {}))
                mode = pilot.get('mode')
                preparation = preparation_status((snapshot.get('gripper') or {}).get('data', {}))
                if problem:
                    self.fail_locked(problem)
                elif not pilot:
                    problem = 'waiting for pilot control gate'
                    self.fail_locked(problem)
                elif self.pending:
                    pending = self.pending
                    gripper_holding = snapshot['gripper']['data']['status'].get('gGTO') == 0
                    custom_saved=(pending['action']!='set_custom_home' or not pilot.get('custom_home',{}).get('saving'))
                    gripper_done = (pending['action'] not in PREPARE_ACTIONS or
                                    (mode == 'locked' and preparation and preparation['id'] == pending['id']
                                     and preparation['action'] == PREPARE_ACTIONS[pending['action']]
                                     and preparation['complete']))
                    if pilot.get('command_id') == pending['id'] and custom_saved and gripper_done and (pending['action'] != 'lock' or (gripper_holding and mode == 'locked')):
                        self.pending = None
                        if pilot.get('error'):
                            with self.lock:
                                self.status['last_command_error'] = pilot['error']
                            if self.episode:
                                self.fail_locked(pilot['error'])
                        elif pending['action'] == 'lock' and self.episode:
                            if mode != 'locked':
                                raise ValueError('Finish was acknowledged without locking')
                            if self.episode.last_time is None or now - self.episode.last_time > .02:
                                self.episode.append(snapshot, now)
                            self.end(pending['outcome'] or 'unlabeled')
                    elif now - pending['at'] > (10 if pending['action']=='set_custom_home' or pending['action'] in PREPARE_ACTIONS else 3):
                        if pending['action'] in PREPARE_ACTIONS:
                            self.issue('lock', now)
                            with self.lock:
                                self.status['last_command_error'] = '夹爪未在 10 秒内确认完成，已请求停止；请检查物体和夹爪反馈'
                        else:
                            raise ValueError('pilot command acknowledgement timed out')
                elif self.episode and mode not in ('manual', 'waiting_for_center'):
                    self.fail_locked(pilot.get('error') or 'manual gate closed unexpectedly')
                with self.lock:
                    command, self.finish_request = self.finish_request, None
                    if command is None:
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            pass
                    self.command_in_progress = command is not None
                try:
                    if command and command.startswith('finish'):
                        outcome = command.partition('_')[2] or 'unlabeled'
                        self.issue('lock', now, outcome)
                    elif command:
                        if problem:
                            raise ValueError(problem)
                        if self.episode or self.pending or mode != 'locked':
                            raise ValueError('请先 Stop，等待当前操作完成、机械臂保持不动')
                        if pilot.get('custom_home',{}).get('saving'):
                            raise ValueError('正在保存自定义 Home，请稍候')
                        if command == 'start':
                            if shutil.disk_usage(self.directory).free < 2 * 1024 ** 3:
                                raise ValueError('less than 2 GiB free disk space')
                            selection = self.catalog.selection() if hasattr(self, 'catalog') else dict(task=self.task)
                            task = selection['task']
                            metadata = dict(task=task['name'], task_id=task.get('id'),
                                task_display_name=task.get('display_name') or task['name'],
                                success_definition=task.get('success_definition', ''),
                                prompt=selection.get('prompt', task.get('prompt', '')),
                                layout=selection.get('layout'), notes='',
                                home_target=copy.deepcopy(pilot.get('home')),
                                home_source=pilot.get('home_source', pilot.get('home_kind')),
                                custom_home=copy.deepcopy(pilot.get('custom_home')))
                            self.episode = Episode(self.directory / new_episode_name(),
                                metadata, now)
                            # Begin data before the first permitted motion command.
                            self.episode.append(snapshot, now)
                            next_sample = now + .1
                            self.issue('start', now)
                        elif command in PREPARE_ACTIONS:
                            if snapshot['gripper']['data'].get('preparation_supported') is not True:
                                raise ValueError('当前控制会话版本不支持网页开合夹爪，请断开并重新连接控制')
                            self.issue(command, now)
                        elif command in ('set_home', 'home','set_custom_home','custom_home'):
                            if command == 'home' and not pilot.get('home_set'):
                                raise ValueError('请先设置 Home')
                            if command in ('set_custom_home','custom_home') and 'custom_home' not in pilot:
                                raise ValueError('当前控制器尚未提供独立自定义 Home，请更新并重连控制会话')
                            if command=='custom_home' and not pilot['custom_home'].get('available'):
                                raise ValueError('请先设置自定义 Home')
                            self.issue(command, now)
                except ValueError as error:
                    with self.lock:
                        self.status['last_command_error'] = str(error)
                finally:
                    with self.lock:
                        self.command_in_progress = False
                if self.episode and now >= next_sample:
                    self.episode.append(snapshot, now)
                    next_sample = now + .1
                with self.lock:
                    self.status.update(recording=self.episode is not None, ready=problem is None,
                        reason=problem, pilot=pilot, pending=self.pending,
                        sample_count=self.episode.count if self.episode else 0,
                        current_episode=self.episode.directory.name if self.episode else None,
                        elapsed_seconds=round(now-self.episode.metadata['started_pc_monotonic'], 2) if self.episode else 0,
                        telemetry=stream_status(snapshot, now),
                        gripper_preparation=preparation,
                        camera_ages={name: round(now - c['pc_captured_at'], 3) if c else None
                                     for name, c in snapshot['cameras'].items()})
                self.done.wait(.02)
        except Exception as error:
            try:
                self.fail_locked(str(error))
            finally:
                with self.lock:
                    self.status.update(recording=False, ready=False, reason=str(error), fatal=True)
        finally:
            try:
                self.gate.issue('lock')
            except OSError:
                pass
            self.end('incomplete', 'recorder stopped before operator finished episode')

    def get_status(self):
        with self.lock:
            status = copy.deepcopy(self.status)
            status['camera_errors'] = {name: camera.error for name, camera in self.cameras.items()}
            if hasattr(self, 'catalog'):
                status['catalog'] = self.catalog.snapshot()
                try:
                    status.update(self.catalog.selection())
                except ValueError:
                    status.update(task=None, layout=None, prompt='')
            if hasattr(self,'library'):status['episodes']=self.library.list()
            return status

    def catalog_action(self, data):
        with self.lock:
            if (self.episode or self.pending or getattr(self, 'command_in_progress', False)
                    or not self.commands.empty() or self.status.get('pilot', {}).get('mode') in ('manual', 'waiting_for_center', 'homing')):
                raise ValueError('请先 Finish，等待当前操作完成，再修改任务或 Layout')
            frames = {name: camera.snapshot() for name, camera in self.cameras.items()} if data.get('action') == 'layout_capture' else None
            return self.catalog.apply(data, frames)

    def command(self, command):
        if command not in ('start', 'finish', 'finish_success', 'finish_failure', 'finish_discard', 'set_home', 'home','set_custom_home','custom_home', *PREPARE_ACTIONS):
            raise ValueError('unknown recorder command')
        if self.done.is_set() or self.get_status().get('fatal'):
            raise ValueError('recorder is stopped')
        if command.startswith('finish'):
            # Finish takes priority and cancels clicks queued before it.
            with self.lock:
                if command!='finish' and not self.episode:
                    raise ValueError('当前没有正在采集的记录；请在采集记录中补充标注')
                if self.finish_request or (self.pending and self.pending['action']=='lock'):
                    return
                self.finish_request = command
                while not self.commands.empty():
                    self.commands.get_nowait()
        else:
            self.commands.put_nowait(command)

    def close(self):
        self.done.set()
        if hasattr(self, 'thread'):
            self.thread.join(timeout=4)
        if self.gate:
            self.gate.close()
        if getattr(self, 'owns_cameras', True):
            for camera in self.cameras.values():
                camera.close()
        if self.reader:
            self.reader.close()
        for tail in (self.control, self.gripper):
            if tail:
                tail.close()
        if getattr(self,'owns_video_exporter',False):self.video_exporter.close()


def handler_for(recorder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, content, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def valid_host(self):
            host, port = self.server.server_address[:2]
            allowed = {host + ':' + str(port)}
            if host == '127.0.0.1':
                allowed.add('localhost:' + str(port))
            return self.headers.get('Host') in allowed

        def send_video(self, path, head=False):
            with path.open('rb') as stream:
                size=os.fstat(stream.fileno()).st_size
                start,end=0,size-1
                requested=self.headers.get('Range')
                if requested:
                    match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
                    valid=match and (match[1] or match[2])
                    if valid:
                        if match[1]:
                            start=int(match[1]);end=min(int(match[2]),size-1) if match[2] else size-1
                        else:start=max(0,size-int(match[2]))
                        valid=start<=end and start<size
                    if not valid:
                        self.send_response(416);self.send_header('Content-Range','bytes */%d'%size)
                        self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(206 if requested else 200)
                self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes')
                self.send_header('Content-Length',str(end-start+1))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                if requested:self.send_header('Content-Range','bytes %d-%d/%d'%(start,end,size))
                self.end_headers()
                if head:return
                stream.seek(start);remaining=end-start+1
                try:
                    while remaining:
                        data=stream.read(min(remaining,256*1024))
                        if not data:break
                        self.wfile.write(data);remaining-=len(data)
                except (BrokenPipeError,ConnectionResetError):pass

        def do_HEAD(self):
            path=urlparse(self.path).path
            match=re.fullmatch(r'/episodes/([a-f0-9]{20})/(external|wrist)\.mp4',path)
            if not self.valid_host() or not match:
                self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
            try:self.send_video(recorder.library.video(*match.groups()),head=True)
            except (ValueError,OSError,KeyError):
                self.send_response(404);self.send_header('Content-Length','0');self.end_headers()

        def do_GET(self):
            if not self.valid_host():
                self.send(403, b'host rejected', 'text/plain')
                return
            path = urlparse(self.path).path
            resource = asset(path)
            if resource:
                self.send(200, *resource)
            elif path == '/status':
                self.send(200, json.dumps(recorder.get_status()).encode(), 'application/json')
            elif path == '/tasks':
                self.send(200, json.dumps(recorder.catalog.snapshot()).encode(), 'application/json')
            elif path.startswith('/episodes') and hasattr(recorder, 'library'):
                try:
                    if path == '/episodes':
                        self.send(200,json.dumps(recorder.library.list()).encode(),'application/json')
                    elif re.fullmatch(r'/episodes/[a-f0-9]{20}/frames',path):
                        self.send(200,json.dumps(recorder.library.frames(path.split('/')[2])).encode(),'application/json')
                    elif re.fullmatch(r'/episodes/[a-f0-9]{20}/(external|wrist)/[0-9]{8}\.jpg',path):
                        self.send(200,recorder.library.image(*path.split('/')[2:]),'image/jpeg')
                    elif re.fullmatch(r'/episodes/[a-f0-9]{20}/(external|wrist)\.mp4',path):
                        self.send_video(recorder.library.video(path.split('/')[2],path.split('/')[3][:-4]))
                    else: self.send(404,b'not found','text/plain')
                except (ValueError, OSError, KeyError): self.send(404,b'episode unavailable','text/plain')
            elif re.fullmatch(r'/layouts/layout_[a-f0-9]{12}/(external|wrist)\.jpg', path):
                try:
                    _, _, identifier, camera = path.split('/')
                    content = recorder.catalog.image(identifier, camera[:-4])
                    self.send(200, content, 'image/jpeg')
                except (ValueError, FileNotFoundError):
                    self.send(404, b'layout not found', 'text/plain')
            elif path in ('/camera/external.jpg', '/camera/wrist.jpg'):
                camera = recorder.cameras[path.split('/')[2].split('.')[0]]
                frame = camera.snapshot()
                fresh = frame and not camera.error and 0 <= time.monotonic()-frame['pc_captured_at'] <= .35
                self.send(200 if fresh else 503, frame['preview'] if fresh else b'camera missing or stale',
                          'image/jpeg' if fresh else 'text/plain')
            else:
                self.send(404, b'not found', 'text/plain')

        def do_POST(self):
            try:
                if not self.valid_host():
                    raise ValueError('host rejected')
                if self.path not in ('/command', '/catalog', '/runtime', '/episodes') or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('expected recorder JSON command')
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + self.headers.get('Host', ''):
                    raise ValueError('cross-origin command rejected')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= (256 if self.path == '/command' else 8192):
                    raise ValueError('invalid command size')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('expected a JSON object')
                if self.path == '/runtime':
                    if not hasattr(recorder, 'runtime_action'):
                        raise ValueError('此启动模式不提供控制会话管理')
                    recorder.runtime_action(data)
                    self.send(202, b'{"accepted":true}', 'application/json')
                    return
                if self.path == '/catalog':
                    result = recorder.catalog_action(data)
                    self.send(200, json.dumps(result).encode(), 'application/json')
                    return
                if self.path == '/episodes':
                    if not hasattr(recorder, 'library'): raise ValueError('此启动模式不提供历史记录管理')
                    self.send(200,json.dumps(recorder.library.apply(data)).encode(),'application/json')
                    return
                if set(data) != {'command'}:
                    raise ValueError('unexpected command fields')
                recorder.command(data['command'])
                self.send(202, b'{"accepted":true}', 'application/json')
            except (ValueError, queue.Full) as error:
                self.send(400, json.dumps(dict(error=str(error))).encode(), 'application/json')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--cameras', type=Path, required=True)
    parser.add_argument('--task', type=Path, default=ROOT / 'reproduction/configs/tasks/block_into_cup.json')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--catalog-dir', type=Path, default=ROOT / 'reproduction/data/capture_catalog')
    parser.add_argument('--tailscale', action='store_true',
                        help='also serve directly on this machine\'s Tailscale IPv4')
    args = parser.parse_args()
    tailscale_ip = None
    if args.tailscale:
        result = subprocess.run(['tailscale', 'ip', '-4'], check=True, capture_output=True, text=True, timeout=5)
        addresses = result.stdout.strip().splitlines()
        if len(addresses) != 1 or ipaddress.ip_address(addresses[0]) not in ipaddress.ip_network('100.64.0.0/10'):
            parser.error('expected one active Tailscale IPv4 address')
        tailscale_ip = addresses[0]
    session = args.session.resolve()
    if session.parent != ROOT / 'reproduction/logs' or not re.fullmatch(r'spacemouse-manual-\d{8}T\d{6}Z', session.name):
        parser.error('session must be an existing dedicated manual log directory')
    settings, task = json.loads(args.cameras.read_text()), json.loads(args.task.read_text())
    if set(settings) != set(CAMERAS) or not isinstance(task.get('name'), str) or not task['name'].strip():
        parser.error('expected a named task and two explicitly assigned cameras')
    devices = [str(Path(value['device']).resolve()) for value in settings.values()]
    if len(set(devices)) != 2:
        parser.error('the two camera roles must refer to different devices')
    for device in devices:
        if not os.access(device, os.R_OK | os.W_OK):
            parser.error('camera is missing or inaccessible: ' + device)
    os.umask(0o077)
    directory = ROOT / 'reproduction/data/manual_capture' / (
        'pilot_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=False)
    recorder = server = remote_server = None
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)
    try:
        recorder = Recorder(directory, session, settings, task, args.catalog_dir)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(recorder))
        tailscale_url = None
        if tailscale_ip:
            remote_server = ThreadingHTTPServer((tailscale_ip, args.port), handler_for(recorder))
            tailscale_url = 'http://%s:%d/' % (tailscale_ip, remote_server.server_port)
            threading.Thread(target=remote_server.serve_forever, kwargs=dict(poll_interval=.2), daemon=True).start()
        print(json.dumps(dict(url='http://127.0.0.1:%d' % server.server_port,
                              tailscale_url=tailscale_url,
                              data_directory=str(directory), starts_recording=False,
                              control_gate=True, initial_motion_enabled=False)), flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        if remote_server:
            remote_server.shutdown()
            remote_server.server_close()
        if server:
            server.server_close()
        if recorder:
            recorder.close()


if __name__ == '__main__':
    main()
