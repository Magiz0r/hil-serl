"""Local file mailbox for a persistent training actor and the capture portal."""
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import time

RUNTIME=Path(__file__).resolve().parents[2]/'reproduction/runtime'


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False)+'\n');tmp.replace(path)


def validate_training_settings(data):
    if (not isinstance(data,dict) or set(data)!={'time_limit_seconds'} or
            type(data['time_limit_seconds']) is not int or data['time_limit_seconds']<0):
        raise ValueError('每轮时限须为非负整数秒；0 表示不限时')
    return dict(data)


def training_settings(runtime=RUNTIME):
    path=Path(runtime)/'training-settings.json'
    if not path.exists():return {'time_limit_seconds':0}
    return validate_training_settings(json.loads(path.read_text()))


def save_training_settings(data,runtime=RUNTIME):
    value=validate_training_settings(data)
    with mailbox_lock(runtime) as root:atomic_json(root/'training-settings.json',value)
    return value


def session_status(runtime=RUNTIME,now=None):
    try:
        value=json.loads((Path(runtime)/'training-status.json').read_text())
        age=(time.time() if now is None else now)-value['heartbeat_unix']
        value['available']=0<=age<3. and value['phase'] not in ('stopped','completed')
        return value
    except (OSError,ValueError,KeyError):return {'available':False}


@contextmanager
def mailbox_lock(runtime):
    runtime=Path(runtime);runtime.mkdir(parents=True,exist_ok=True)
    with (runtime/'training-mailbox.lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        yield runtime


def session_command(data,runtime=RUNTIME):
    if (set(data)!={'action','session_id','episode'} or
            data['action'] not in ('success','failure','pause','resume')):
        raise ValueError('Invalid training control')
    with mailbox_lock(runtime) as root:
        status=session_status(root)
        if not status['available']:raise ValueError('连续训练程序未运行，请检查训练进程')
        if data['session_id']!=status['session_id'] or (data['action'] in ('success','failure') and data['episode']!=status['episode']):
            raise ValueError('回合已改变，请刷新后再标记')
        if data['action'] in ('success','failure') and status['phase'] not in ('running','awaiting_label','awaiting_reset','paused','countdown','waiting_connection'):
            raise ValueError('当前没有可标记的训练回合')
        if data['action'] in ('success','failure') and not status.get('can_label'):
            raise ValueError('当前没有可标记的训练回合')
        path=root/'training-commands.json'
        try:mail=json.loads(path.read_text())
        except (OSError,ValueError):mail={'sequence':0,'commands':[]}
        mail['sequence']+=1
        item=dict(data,sequence=mail['sequence'],time_unix=time.time())
        mail['commands']=(mail['commands']+[item])[-128:]
        atomic_json(path,mail)
        return dict(accepted=True,sequence=item['sequence'])


def read_commands(session_id,after,runtime=RUNTIME):
    try:mail=json.loads((Path(runtime)/'training-commands.json').read_text())
    except (OSError,ValueError):return after,[]
    return mail['sequence'],[x for x in mail['commands'] if x['sequence']>after and x['session_id']==session_id]


def ready_at_home(data,status,plan,after_command=None):
    """Readiness only; never commands withdrawal or Home."""
    p=data.get('pilot') or {};home=p.get('custom_home') or {};state=data.get('state') or {}
    if (not status.get('ready') or status.get('recording') or status.get('pending') or
            p.get('mode')!='locked' or p.get('joint_phase')!='idle' or home.get('saving') or
            not home.get('available') or home.get('joint_error_rad',99)>=.01):return False
    if after_command is not None and (p.get('command')!='custom_home' or
            p.get('command_id',-1)<=after_command or p.get('active_home')!='custom'):return False
    q=state.get('q',[]);dq=state.get('dq',[]);xyz=state.get('xyz',[])
    if len(q)!=7 or len(dq)!=7 or len(xyz)!=3:return False
    if not all(math.isfinite(x) for x in q+dq+xyz):return False
    wrench=state.get('K_F_ext_hat_K')
    if wrench is not None and (len(wrench)!=6 or not all(math.isfinite(v) for v in wrench)
            or sum(v*v for v in wrench[:3])>plan['force_limit_N']**2
            or sum(v*v for v in wrench[3:])>plan['torque_limit_Nm']**2):return False
    return (max(abs(a-b) for a,b in zip(q,plan['custom_home_q']))<.01 and
            max(abs(v) for v in dq)<.03 and
            sum((a-b)**2 for a,b in zip(xyz,plan['initial_pose'][:3]))<.003**2 and
            state.get('success',0)>=.95)
