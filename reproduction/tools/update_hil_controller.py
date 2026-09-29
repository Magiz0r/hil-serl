"""Build an offline bundle, or apply it on an idle NUC without starting control."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

OLD_SHA = '4d3bb61ba1518cb399cbfc3f34b85e28b14c4ff1abd09fe927986186132194cb'
NAME = 'online_motion.py'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def prepare(output):
    root=Path(__file__).resolve().parents[2]
    content=(root/'reproduction/nuc'/NAME).read_bytes()
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/NAME).write_bytes(content)
    shutil.copyfile(__file__,output/'install.py')
    (output/'bundle.json').write_text(json.dumps(dict(schema='hil_controller_update_v1',
        file=NAME,previous_sha256=OLD_SHA,sha256=sha(content)),indent=2)+'\n')
    return dict(bundle=str(output),robot_io=False,deployed=False,sha256=sha(content))


def replace(path,content):
    temporary=path.with_name(path.name+'.hil-update')
    with temporary.open('xb') as stream:stream.write(content)
    temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)


def update(runtime,bundle,*,apply=False):
    runtime=Path(runtime);bundle=Path(bundle)
    if not runtime.is_absolute() or runtime.resolve()!=runtime or not runtime.is_dir():
        raise ValueError('Use the existing absolute NUC runtime directory without symlinks')
    metadata=json.loads((bundle/'bundle.json').read_text());content=(bundle/NAME).read_bytes()
    if (metadata.get('schema')!='hil_controller_update_v1' or metadata.get('file')!=NAME
            or metadata.get('previous_sha256')!=OLD_SHA or sha(content)!=metadata.get('sha256')):
        raise ValueError('Invalid update bundle')
    manifest_path=runtime/'source-sha256.json';original_manifest=manifest_path.read_bytes()
    manifest=json.loads(original_manifest);source=runtime/'source';target=source/NAME
    if manifest_path.is_symlink() or source.is_symlink():raise ValueError('Symlinked runtime sources')
    for name,digest in manifest.items():
        path=source/name
        if not path.resolve().is_relative_to(source) or path.is_symlink() or sha(path.read_bytes())!=digest:
            raise ValueError('Deployed source differs from manifest: '+name)
    old=target.read_bytes()
    if manifest.get(NAME) not in (OLD_SHA,sha(content)):
        raise ValueError('Controller version is not the expected pre-HIL revision')
    # Updating a file mounted into a running controller is never allowed.
    ids=subprocess.check_output(['docker','ps','-q'],text=True,timeout=5).split()
    if ids:
        containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True,timeout=5))
        for container in containers:
            for mount in container.get('Mounts',[]):
                path=Path(mount.get('Source','/')).resolve()
                if path==runtime or path.is_relative_to(runtime):
                    raise ValueError('Stop the portal control and runtime containers before updating')
    result=dict(changed=old!=content,applied=False,robot_io=False,sha256=sha(content))
    if not apply or old==content:return result
    backup=runtime/'state/controller-backups'/str(time.time_ns());backup.mkdir(parents=True)
    (backup/NAME).write_bytes(old);(backup/'source-sha256.json').write_bytes(original_manifest)
    manifest[NAME]=sha(content)
    try:
        replace(target,content)
        replace(manifest_path,(json.dumps(manifest,indent=2)+'\n').encode())
    except Exception:
        # A mismatch would block startup, but restore the previous pair as well.
        replace(target,old);replace(manifest_path,original_manifest)
        raise
    return dict(result,applied=True,backup=str(backup))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',type=Path,help='Create a local bundle; no SSH or hardware access')
    parser.add_argument('--runtime',type=Path,help='Existing NUC runtime, only when running on the NUC')
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--apply',action='store_true',help='Apply after checks; default only verifies')
    args=parser.parse_args()
    if bool(args.prepare)==bool(args.runtime) or (args.prepare and args.apply):
        parser.error('Choose --prepare OUTPUT, or --runtime DIRECTORY [--apply]')
    print(json.dumps(prepare(args.prepare) if args.prepare else update(args.runtime,args.bundle,apply=args.apply)))


if __name__=='__main__':main()
