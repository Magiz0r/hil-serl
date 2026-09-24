"""Verify and expose only the independently built orientation-response plugin."""
import hashlib
import json
import os
from pathlib import Path

CONTROLLER_TYPE='hil_serl_rotation/ResponsiveCartesianImpedanceController'


def activate_rotation_controller():
    source=Path(__file__).parent
    manifest=json.loads((source/'rotation_controller_artifacts.json').read_text())
    prefix=Path(manifest['prefix'])
    prefix.relative_to('/hil-serl-state/rotation-controller')
    for name,digest in manifest['source_sha256'].items():
        if Path(name).name!=name or hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('rotation controller source does not match its build')
    for relative,digest in manifest['files'].items():
        path=(Path('/hil-serl-state')/relative).resolve()
        path.relative_to(prefix)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('rotation controller artifact changed: '+relative)
    for key,path in [('ROS_PACKAGE_PATH',prefix/'share'),('CMAKE_PREFIX_PATH',prefix),('LD_LIBRARY_PATH',prefix/'lib')]:
        os.environ[key]=str(path)+':'+os.environ.get(key,'')
    os.environ['HIL_SERL_ROTATION_PREFIX']=str(prefix)
    return manifest
