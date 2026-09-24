"""Read-only presentation helpers shared by the recorder and offline preview."""
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent
ASSETS = {
    '/': ('pilot_capture.html', 'text/html; charset=utf-8'),
    '/ui/theme.css': ('pilot_ui/theme.css', 'text/css; charset=utf-8'),
    '/ui/api.js': ('pilot_ui/api.js', 'text/javascript; charset=utf-8'),
    '/ui/demo.js': ('pilot_ui/demo.js', 'text/javascript; charset=utf-8'),
    '/ui/cameras.js': ('pilot_ui/cameras.js', 'text/javascript; charset=utf-8'),
    '/ui/catalog.js': ('pilot_ui/catalog.js', 'text/javascript; charset=utf-8'),
    '/ui/records.js': ('pilot_ui/records.js', 'text/javascript; charset=utf-8'),
    '/ui/app.js': ('pilot_ui/app.js', 'text/javascript; charset=utf-8'),
}


def asset(path):
    """Explicit allowlist: never expose session logs, tokens or datasets."""
    if path not in ASSETS:
        return None
    name, content_type = ASSETS[path]
    return (WEB_ROOT / name).read_bytes(), content_type


def stream_status(snapshot, now):
    result = {}
    for name, limit in (('arm', .5), ('control', .5), ('gripper', .7)):
        stream = snapshot.get(name)
        age = None if not stream else round(now - stream['pc_received_at'], 3)
        fresh = age is not None and 0 <= age <= limit
        detail = '数据新鲜' if fresh else '等待数据' if age is None else '数据过期'
        ok = fresh
        data = (stream or {}).get('data', {})
        if name == 'arm' and fresh:
            sample = data.get('sample', {})
            state = sample.get('state', {})
            source_age = data.get('source_read_at', 0) - state.get('received_at', 0)
            ok = (0 <= source_age <= .35 and state.get('mode') in (1, 2)
                  and not state.get('current_errors') and not state.get('last_motion_errors')
                  and sample.get('armed') and not sample.get('input', {}).get('stop'))
            detail = '状态在线' if ok else '机器人状态异常'
        elif name == 'control' and fresh:
            ok = data.get('phase') == 'ready'
            detail = '遥操作在线' if ok else '遥操作未就绪'
        elif name == 'gripper' and fresh:
            status = data.get('status', {})
            ok = data.get('phase') == 'ready' and status.get('gFLT') == 0
            detail = ('保持' if not status.get('gGTO') else
                      {0:'动作中', 1:'张开时接触物体', 2:'闭合时接触物体', 3:'已到目标位置'}.get(status.get('gOBJ'), '等待动作反馈')) if ok else '夹爪未就绪'
            result['gripper_position'] = status.get('gPO')
            result['gripper_fault'] = status.get('gFLT')
        result[name] = dict(ok=bool(ok), age=age, detail=detail)
        if name == 'gripper':
            result[name]['preparation_supported'] = data.get('preparation_supported') is True
    state = (snapshot.get('arm') or {}).get('data', {}).get('sample', {}).get('state', {})
    result['joints'] = state.get('q')
    return result
