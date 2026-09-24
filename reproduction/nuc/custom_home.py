"""Independent, persistent operator-taught joint target. Never changes DROID Home."""
import json
import math
import os
from pathlib import Path
import tempfile
import time


def checked_joints(q, lower, upper):
    if not isinstance(q,(list,tuple)) or len(q)!=7:
        raise ValueError('自定义 Home 必须包含七个关节角')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in q):
        raise ValueError('自定义 Home 关节角无效')
    if any(not low<v<high for v,low,high in zip(q,lower,upper)):
        raise ValueError('自定义 Home 超出当前机器人关节范围')
    return [float(v) for v in q]


class CustomHomeStore:
    def __init__(self, path):self.path=Path(path)

    def load(self, lower, upper):
        try:value=json.loads(self.path.read_text())
        except FileNotFoundError:return None
        if not isinstance(value,dict) or value.get('schema')!='hilserl_custom_joint_home_v1' or value.get('robot')!='fr3':
            raise ValueError('保存的自定义 Home 格式无效')
        saved=value.get('saved_unix')
        if type(saved) not in (float,int) or not math.isfinite(saved) or saved<=0:
            raise ValueError('保存的自定义 Home 时间无效')
        return dict(q=checked_joints(value.get('q'),lower,upper),saved_unix=saved)

    def save(self, q, lower, upper):
        value=dict(q=checked_joints(q,lower,upper),saved_unix=time.time(),
                   schema='hilserl_custom_joint_home_v1',robot='fr3')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,prefix='.custom-home-',delete=False) as stream:
                temporary=Path(stream.name)
                json.dump(value,stream,allow_nan=False,indent=2)
                stream.write('\n');stream.flush();os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary:temporary.unlink(missing_ok=True)
        return dict(q=value['q'],saved_unix=value['saved_unix'])
