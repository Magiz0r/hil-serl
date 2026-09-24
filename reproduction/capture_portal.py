#!/usr/bin/env python3
"""Start/reuse and gracefully stop the owned capture portal from Bash."""
import argparse
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'reproduction/runtime/capture-console'
SERVER = ROOT/'reproduction/start_capture_console.py'
SERVICE = RUNTIME/'service.json'


def say(message):
    print(message, flush=True)


def owns_process(pid):
    """A PID file alone never authorizes stopping an unrelated process."""
    try:
        process = Path('/proc')/str(int(pid))
        if process.stat().st_uid != os.getuid(): return False
        fields = (process/'stat').read_text().rsplit(')',1)[1].split()
        if fields[0] == 'Z': return False
        arguments = (process/'cmdline').read_bytes().decode().split('\0')[1:]
        cwd = (process/'cwd').resolve()
        return any(arg and not arg.startswith('-') and (cwd/arg).resolve()==SERVER for arg in arguments)
    except (OSError, ValueError):
        return False


def existing_service():
    try:
        service = json.loads(SERVICE.read_text())
        if not owns_process(service['pid']): return None
        parsed = urlparse(service['local_url'])
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port:
            raise ValueError('invalid local service URL')
        return service
    except FileNotFoundError:
        return None


def request(service, path, data=None):
    headers = {'Content-Type':'application/json', 'Origin':service['local_url'].rstrip('/')}
    body = json.dumps(data).encode() if data is not None else None
    query = urllib.request.Request(service['local_url'].rstrip('/')+path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(query,timeout=3) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try: message=json.load(error).get('error',str(error))
        except (ValueError,AttributeError): message=str(error)
        raise RuntimeError(message) from error


def status(service):
    value=request(service,'/status')
    if not isinstance(value.get('runtime'),dict) or not isinstance(value.get('ready'),bool):
        raise RuntimeError('该端口未返回采集工作台的真实状态')
    return value


def tailscale_available():
    if not shutil.which('tailscale'): return False
    try:
        value=subprocess.check_output(['tailscale','ip','-4'],text=True,timeout=5).strip()
        return ipaddress.ip_address(value) in ipaddress.ip_network('100.64.0.0/10')
    except (OSError,ValueError,subprocess.SubprocessError):
        return False


def ensure_server(port):
    service=existing_service()
    if service:
        if urlparse(service['local_url']).port!=port:
            raise RuntimeError('已有工作台运行在 '+service['local_url']+'；请使用该端口或先停止')
        status(service)
        say('[WEB] 复用已运行的网页和相机。')
        return service
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex(('127.0.0.1',port))==0:
            raise RuntimeError('端口 %d 已被其他进程使用；未停止或接管该进程'%port)
    command=[sys.executable,'-u',str(SERVER),'--port',str(port)]
    if tailscale_available(): command.append('--tailscale')
    else: say('[WEB] Tailscale 暂不可用，本次提供本机地址。')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log_path=RUNTIME/('portal-'+stamp+'.log')
    with log_path.open('x') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,
            stderr=subprocess.STDOUT,start_new_session=True,close_fds=True,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    say('[WEB] 正在启动相机和网页，日志：'+str(log_path))
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError('网页进程已退出，请查看 '+str(log_path))
        try:
            service=existing_service()
            if service and service['pid']==process.pid:
                status(service)
                say('[WEB] 网页已响应。')
                return service
        except (OSError,ValueError,RuntimeError): pass
        time.sleep(.2)
    raise RuntimeError('网页尚未确认就绪，进程可能仍在启动。请查看 '+str(log_path))


def show_urls(service):
    say('本机网页：'+service['local_url'])
    if service.get('tailscale_url'): say('Tailscale：'+service['tailscale_url'])


def open_browser(service):
    if (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) and shutil.which('xdg-open'):
        subprocess.Popen(['xdg-open',service['local_url']],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)


def connect_control(service, timeout=120):
    current=status(service)
    phase=current['runtime']['phase']
    if phase in ('disconnected','error'):
        if not current['runtime'].get('can_connect'):
            raise RuntimeError('旧控制会话仍需清理，请在网页断开控制后重试')
        say('[ROBOT] 检查 NUC / Franka Desk，并连接控制。请确保机械臂已开机、解锁且 FCI 已激活。')
        request(service,'/runtime',{'action':'connect'})
    deadline=time.monotonic()+timeout
    previous=None
    while time.monotonic()<deadline:
        current=status(service)
        phase=current['runtime']['phase']
        pilot=current.get('pilot',{})
        if current.get('recording'):
            say('[CAPTURE] 当前正在采集，继续使用网页即可。');return 0
        if phase=='connected' and current.get('ready') and pilot.get('mode')=='locked' and not current.get('pending'):
            say('[READY] 机器人、夹爪和相机反馈已就绪。选择 Task / Layout，点击 Start 开始采集。')
            return 0
        if phase=='connected' and pilot.get('mode')=='homing':
            say('[ROBOT] 当前正在 Home，网页中可查看进度。');return 0
        if phase in ('error','disconnected','disconnecting'):
            raise RuntimeError(current['runtime'].get('error') or current.get('reason') or '控制连接已结束')
        message=current.get('reason') or phase
        if message!=previous:
            say('[ROBOT] '+message);previous=message
        time.sleep(.5)
    raise RuntimeError('等待真实控制就绪超时，请查看网页中的独立设备状态')


def start(args):
    with (RUNTIME/'launcher.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        service=ensure_server(args.port)
    show_urls(service)
    if not args.no_open: open_browser(service)
    if args.web_only:
        say('[WEB] 仅启动网页；需要时点击“连接控制”。');return 0
    try:
        return connect_control(service)
    except (RuntimeError,OSError,ValueError) as error:
        say('[PARTIAL] 网页仍在运行，控制尚未就绪：'+str(error))
        say('完成现场准备后，在网页点击“连接控制”，或重新运行 start_capture.sh。')
        return 2


def finish_before_stop(service, current):
    moving=current.get('pilot',{}).get('mode') in ('manual','waiting_for_center','homing')
    if not (current.get('recording') or current.get('pending') or moving): return
    was_recording=current.get('recording',False)
    say('[STOP] 先 Finish，等待保持和记录落盘…')
    request(service,'/command',{'command':'finish'})
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        current=status(service)
        if current.get('fatal'):
            raise RuntimeError('采集器异常，未确认记录已保存；请检查网页和数据目录后再关闭')
        if not current.get('recording') and not current.get('pending') and current.get('pilot',{}).get('mode')=='locked':
            say('[STOP] '+('记录已保存。' if was_recording else '机械臂已保持。'));return
        time.sleep(.2)
    raise RuntimeError('Finish 未确认完成，网页保留运行；请检查保存状态后重试')


def stop():
    with (RUNTIME/'launcher.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        service=existing_service()
        if not service:
            say('[STOPPED] 采集网页未运行。');return 0
        current=status(service)
        finish_before_stop(service,current)
        if not owns_process(service['pid']):
            raise RuntimeError('网页进程身份已变化，未发送退出信号')
        say('[STOP] 关闭本工作台的控制连接、相机和网页…')
        os.kill(service['pid'],signal.SIGTERM)
        deadline=time.monotonic()+95
        while time.monotonic()<deadline:
            if not owns_process(service['pid']):
                try: result=json.loads((RUNTIME/'shutdown.json').read_text())
                except (OSError,ValueError): result={}
                if result.get('pid')==service['pid']:
                    if not result.get('control_stopped'):
                        raise RuntimeError('网页已退出，但控制进程未确认退出：'+str(result.get('error')))
                elif not (current['runtime']['phase']=='disconnected' and current['runtime'].get('can_connect') and not current.get('recording')):
                    raise RuntimeError('网页已退出，缺少控制清理确认；请检查服务日志')
                say('[STOPPED] 采集工作台已关闭。');return 0
            time.sleep(.2)
        raise RuntimeError('网页进程仍未退出，请检查最新 portal 日志；未强制终止其他进程')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    begin=sub.add_parser('start',help='start/reuse the portal and connect control')
    begin.add_argument('--port',type=int,default=8765)
    begin.add_argument('--web-only',action='store_true',help='start/reuse the web page without connecting control')
    begin.add_argument('--no-open',action='store_true',help='print URLs without opening a desktop browser')
    sub.add_parser('stop',help='Finish active capture, then stop the owned portal')
    args=parser.parse_args()
    if args.action=='start' and not 1<=args.port<=65535: parser.error('invalid port')
    os.umask(0o077);RUNTIME.mkdir(parents=True,exist_ok=True)
    try:
        return start(args) if args.action=='start' else stop()
    except KeyboardInterrupt:
        say('等待已中断，后台服务可能仍在运行。完整关闭请运行 stop_capture.sh。');return 130
    except (OSError,RuntimeError,ValueError) as error:
        say('[ERROR] '+str(error));return 1


if __name__=='__main__':raise SystemExit(main())
