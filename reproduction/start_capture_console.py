#!/usr/bin/env python3
"""Live capture console with explicit controller connection and persistent tasks.

Opening the page does not start a controller. --connect or the explicit
Connect control button runs the fixed, attended pilot-gated startup sequence.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from http.server import ThreadingHTTPServer
from pilot_catalog import CaptureCatalog
from pilot_dataset import CAMERAS
from pilot_records import EpisodeLibrary, replace_json
from pilot_video import VideoExporter
from record_manual_pilot import ROOT, Camera, Recorder, handler_for

SSH = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-o', 'StrictHostKeyChecking=yes', 'FrankaNUC']


def control_failure(log_path):
    """Surface structured startup errors without hiding them behind a log path."""
    detail = '控制进程退出'
    try:
        with log_path.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell()-65536))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        for line in reversed(lines):
            try: event = json.loads(line)
            except ValueError: continue
            if isinstance(event, dict) and isinstance(event.get('error'), str) and event['error']:
                detail = event['error'][:500]
                match = re.search(r'joint (\d+) margin (-?[\d.]+) rad is below ([\d.]+) rad', detail)
                if match and float(match[2]) < 0:
                    detail = ('J%s 实测关节角超出当前模型限位 %.6f rad；'
                              '请在现场将机械臂移回允许范围后重连' % (match[1], -float(match[2])))
                break
    except OSError:
        pass
    return detail+'。日志：'+str(log_path)


class MissingCamera:
    def __init__(self, error): self.error = str(error)
    def snapshot(self): return None
    def close(self): pass


class CaptureRuntime:
    def __init__(self, settings, task, runtime):
        self.settings, self.initial_task, self.runtime = settings, task, runtime
        self.catalog = CaptureCatalog(ROOT/'reproduction/data/capture_catalog', task)
        self.video_exporter=VideoExporter()
        self.library = EpisodeLibrary([ROOT/'reproduction/data/manual_capture', ROOT/'reproduction/data/block_into_cup'],self.video_exporter)
        self.library.queue_missing_videos()
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.connect_cancel = threading.Event()
        self.recorder = self.control = self.worker = None
        self.phase, self.error = 'disconnected', None
        self.cameras = {}
        self.open_cameras()
        self.monitor = threading.Thread(target=self.watch, daemon=True)
        self.monitor.start()

    def open_cameras(self):
        for name in CAMERAS:
            previous = self.cameras.get(name)
            if previous and not previous.error:
                continue
            if previous: previous.close()
            try: self.cameras[name] = Camera(name, self.settings[name])
            except Exception as error: self.cameras[name] = MissingCamera(error)

    def get_status(self):
        with self.lock:
            if self.recorder:
                status = self.recorder.get_status()
            else:
                now = time.monotonic()
                frames = {name: camera.snapshot() for name, camera in self.cameras.items()}
                status = dict(recording=False, ready=False, episodes=[], sample_count=0, sample_hz=10,
                    elapsed_seconds=0, pilot={}, pending=None, directory='',
                    reason=self.error or ('控制会话正在启动，等待真实状态' if self.phase=='connecting' else '控制会话未连接；连接后才能采集和 Home'),
                    camera_ages={name:round(now-frame['pc_captured_at'],3) if frame else None for name,frame in frames.items()})
                status['catalog'] = self.catalog.snapshot()
                try: status.update(self.catalog.selection())
                except ValueError: status.update(task=None,prompt='',layout=None)
            status['camera_errors'] = {name: camera.error for name, camera in self.cameras.items()}
            status['runtime'] = dict(phase=self.phase, error=self.error,
                can_connect=self.phase in ('disconnected','error') and not self.recorder and not self.control and not (self.worker and self.worker.is_alive()),
                can_disconnect=self.phase=='connecting' or (self.phase in ('connected','error') and not (self.worker and self.worker.is_alive())),
                home_source='droid_default', home_joint_degrees=[0,-36,0,-144,0,108,0])
            status['next_episode_number'] = len(status['episodes'])+1
            revision=(status.get('directory',''),len(status['episodes']))
            if revision!=getattr(self,'record_revision',None):
                self.library.refresh(force=True);self.record_revision=revision
            status['episodes'] = self.library.list()
            return status

    def command(self, command):
        with self.lock:
            if not self.recorder or self.phase!='connected':
                raise ValueError('控制会话尚未连接，请先连接控制')
            self.recorder.command(command)

    def catalog_action(self, data):
        with self.lock:
            if self.recorder: return self.recorder.catalog_action(data)
            frames={name:camera.snapshot() for name,camera in self.cameras.items()} if data.get('action')=='layout_capture' else None
            return self.catalog.apply(data, frames)

    def runtime_action(self, data):
        if set(data)!={'action'} or data['action'] not in ('connect','disconnect','retry_cameras'):
            raise ValueError('unknown runtime action')
        with self.lock:
            if data['action']=='disconnect' and self.phase=='connecting':
                self.connect_cancel.set();self.phase='disconnecting';return
            if data['action']=='retry_cameras':
                if self.phase not in ('disconnected','error') or self.recorder:
                    raise ValueError('请先断开控制会话再重新打开相机')
                self.open_cameras(); return
            if self.worker and self.worker.is_alive():
                raise ValueError('正在处理前一个连接操作')
            if data['action']=='connect':
                if self.phase not in ('disconnected','error') or self.recorder or self.control:
                    raise ValueError('请先断开旧控制会话')
                self.phase, self.error='connecting',None
                self.connect_cancel.clear()
                target=self.connect
            else:
                if self.recorder and self.recorder.get_status().get('recording'):
                    raise ValueError('请先 Finish 保存当前条目，再断开控制')
                self.phase='disconnecting';target=self.disconnect
            self.worker=threading.Thread(target=target,daemon=True);self.worker.start()

    def connect(self):
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run=self.runtime/(stamp+'_'+uuid.uuid4().hex[:6]);run.mkdir()
        output=None
        try:
            self.open_cameras()
            if any(camera.error for camera in self.cameras.values()):
                raise ValueError('相机未就绪：'+str({n:c.error for n,c in self.cameras.items() if c.error}))
            source=(ROOT/'reproduction/nuc/readonly_preflight.py').read_text()
            result=subprocess.run(SSH+['python3','-'],input=source,text=True,capture_output=True,timeout=40)
            (run/'preflight.log').write_text(result.stdout+result.stderr)
            if self.done.is_set() or self.connect_cancel.is_set():self.disconnect();return
            if result.returncode or 'READ_ONLY_PREFLIGHT_OK' not in result.stdout:
                detail=(result.stderr or result.stdout).strip().splitlines()
                raise ValueError('NUC / Franka Desk (172.16.0.1) 预检未通过：'+(detail[-1][:250] if detail else '无状态反馈')+'。日志：'+str(run/'preflight.log'))
            before=set((ROOT/'reproduction/logs').glob('spacemouse-manual-*'))
            output=(run/'control.log').open('x')
            command=[sys.executable,'-u',str(ROOT/'reproduction/spacemouse_upward_trial.py'),
                '--execute-attended-manual','--no-trial-bounds','--official-input','--with-gripper',
                '--speed-scale','2','--translation-scale','1.6','--rotation-scale','3',
                '--rotation-response','responsive','--gripper-speed','192','--pilot-gate','--official-home']
            with self.lock:
                if self.done.is_set() or self.connect_cancel.is_set():self.disconnect();return
                self.control=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT,
                    start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
            deadline=time.monotonic()+50;session=None
            while not self.done.is_set() and not self.connect_cancel.is_set() and time.monotonic()<deadline:
                if self.control.poll() is not None:
                    raise ValueError(control_failure(run/'control.log'))
                candidates=set((ROOT/'reproduction/logs').glob('spacemouse-manual-*'))-before
                if len(candidates)>1: raise ValueError('发现多个新控制会话，停止本次连接')
                if candidates:
                    candidate=next(iter(candidates));path=candidate/'events.jsonl'
                    if path.exists():
                        lines=path.read_text().splitlines()
                        try: last=json.loads(lines[-1]) if lines else {}
                        except json.JSONDecodeError: last={}
                        if last.get('phase')=='ready' and last.get('pilot',{}).get('mode')=='locked':
                            session=candidate;break
                self.done.wait(.15)
            if self.done.is_set() or self.connect_cancel.is_set():self.disconnect();return
            if session is None: raise ValueError('等待 READY LOCKED 超时，详情：'+str(run/'control.log'))
            directory=ROOT/'reproduction/data/manual_capture'/('pilot_'+stamp+'_'+uuid.uuid4().hex[:6])
            directory.mkdir(parents=True)
            recorder=Recorder(directory,session,self.settings,self.initial_task,cameras=self.cameras,catalog=self.catalog,video_exporter=self.video_exporter)
            with self.lock:
                self.recorder=recorder
                if self.done.is_set() or self.connect_cancel.is_set():self.disconnect();return
                self.phase='connected';self.error=None
            (run/'connected.json').write_text(json.dumps(dict(session=str(session),directory=str(directory),control_pid=self.control.pid),indent=2))
        except Exception as error:
            self.disconnect()
            with self.lock: self.phase='error';self.error=str(error)
        finally:
            if output: output.close()

    def disconnect(self):
        with self.lock:
            recorder, control = self.recorder,self.control
            self.recorder=self.control=None
        if recorder: recorder.close()
        if control and control.poll() is None:
            # Signal only the owned local input process; it forwards stop and cleans its NUC children.
            control.send_signal(signal.SIGINT)
            try: control.wait(timeout=25)
            except subprocess.TimeoutExpired:
                control.terminate()
                try: control.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with self.lock:self.phase='error';self.error='控制进程未按时退出，请检查本次会话';self.control=control
                    return
        with self.lock:self.phase='disconnected'

    def watch(self):
        while not self.done.wait(.5):
            with self.lock:
                if self.phase=='connected' and (self.control.poll() is not None or self.recorder.get_status().get('fatal')):
                    self.phase='error';self.error='控制或采集进程已停止，请查看日志，断开后重新连接'

    def close(self):
        self.done.set()
        if self.worker and self.worker.is_alive():self.worker.join(timeout=45)
        self.disconnect()
        for camera in self.cameras.values():camera.close()
        self.video_exporter.close()
        stopped=self.control is None or self.control.poll() is not None
        return dict(control_stopped=stopped, error=None if stopped else self.error)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--tailscale',action='store_true')
    parser.add_argument('--connect',action='store_true',help='explicitly attempt attended controller startup')
    args=parser.parse_args()
    os.umask(0o077)
    directory=ROOT/'reproduction/runtime/capture-console';directory.mkdir(parents=True,exist_ok=True)
    claim=(directory/'service.lock').open('a')
    fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
    settings=json.loads((ROOT/'reproduction/configs/cameras.json').read_text())
    task=json.loads((ROOT/'reproduction/configs/tasks/block_into_cup.json').read_text())
    runtime=CaptureRuntime(settings,task,directory)
    servers=[]
    def stop(*_):raise KeyboardInterrupt()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    try:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(runtime));servers.append(server)
        tailscale_url=None
        if args.tailscale:
            address=subprocess.check_output(['tailscale','ip','-4'],text=True,timeout=5).strip()
            if ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):raise ValueError('invalid Tailscale IP')
            remote=ThreadingHTTPServer((address,args.port),handler_for(runtime));servers.append(remote)
            threading.Thread(target=remote.serve_forever,daemon=True).start()
            tailscale_url='http://%s:%s/'%(address,args.port)
        startup=dict(local_url='http://127.0.0.1:%s/'%args.port,tailscale_url=tailscale_url,pid=os.getpid(),automatic_motion=False)
        (directory/'service.json').write_text(json.dumps(startup,indent=2))
        print(json.dumps(startup),flush=True)
        if args.connect:runtime.runtime_action({'action':'connect'})
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        for item in servers:item.server_close()
        result=runtime.close()
        replace_json(directory/'shutdown.json',dict(result,pid=os.getpid(),stopped_unix=time.time()))
        claim.close()


if __name__=='__main__':main()
