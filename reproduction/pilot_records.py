"""Read and review saved capture episodes; never reads active recordings."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from pilot_dataset import SCHEMA
from pilot_video import video_status


def replace_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.review-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists(): temporary.unlink()


class EpisodeLibrary:
    def __init__(self, roots, video_exporter=None):
        self.roots = [Path(root).resolve() for root in roots]
        self.video_exporter = video_exporter
        self.lock = threading.RLock()
        self.entries, self.refreshed = {}, -float('inf')

    def refresh(self, force=False):
        if not force and time.monotonic()-self.refreshed < 2: return
        entries = {}
        for root in self.roots:
            for path in root.glob('pilot_*/episode_*/episode.json'):
                try:
                    path.resolve().relative_to(root)
                    metadata = json.loads(path.read_text())
                    if metadata.get('schema') != SCHEMA or metadata.get('status') == 'recording' or metadata.get('deleted'): continue
                    if 'ended_pc_monotonic' not in metadata: continue
                    identifier = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:20]
                    entries[identifier] = (path, metadata)
                except (OSError, ValueError): continue
        self.entries, self.refreshed = entries, time.monotonic()

    def summary(self, identifier, path, metadata):
        videos=video_status(path.parent)
        if self.video_exporter and self.video_exporter.is_pending(path.parent) and videos['status'] in ('missing','error'):
            videos=dict(status='queued')
        if videos['status']=='ready':
            videos=dict(videos,urls={camera:'/episodes/'+identifier+'/'+camera+'.mp4' for camera in ('external','wrist')})
        storage_name=path.parent.name
        name=('episode_'+path.parent.parent.name.removeprefix('pilot_')+'_'+storage_name.removeprefix('episode_')
              if re.fullmatch(r'episode_\d+',storage_name) else storage_name)
        return dict(id=identifier, name=name, storage_name=storage_name, session=path.parent.parent.name,
            directory=str(path.parent), task=metadata.get('task'), task_id=metadata.get('task_id'),
            task_display_name=metadata.get('task_display_name') or metadata.get('task'),
            prompt=metadata.get('prompt',''), layout=metadata.get('layout'), notes=metadata.get('notes',''),
            samples=metadata.get('sample_count',0), outcome=metadata.get('outcome','incomplete'),
            reason=metadata.get('reason'), created_unix=metadata.get('created_unix'), videos=videos,
            duration_seconds=round(metadata['ended_pc_monotonic']-metadata['started_pc_monotonic'],3))

    def list(self):
        with self.lock:
            self.refresh()
            return copy.deepcopy(sorted([self.summary(key,*value) for key,value in self.entries.items()],key=lambda e:e['created_unix'] or 0))

    def entry(self, identifier):
        if not re.fullmatch('[a-f0-9]{20}',identifier): raise ValueError('invalid episode id')
        self.refresh()
        if identifier not in self.entries: raise ValueError('记录不存在或已删除')
        return self.entries[identifier]

    def frames(self, identifier):
        with self.lock:
            path, metadata = self.entry(identifier)
            frames = []
            with (path.parent/'samples.jsonl').open() as stream:
                for line in stream:
                    sample = json.loads(line)
                    frame = dict(seconds=sample['pc_sampled_at']-metadata['started_pc_monotonic'])
                    for camera in ('external','wrist'):
                        relative = sample['cameras'][camera]['path']
                        if not re.fullmatch(camera+r'/[0-9]{8}\.jpg',relative): raise ValueError('invalid recorded frame path')
                        frame[camera] = '/episodes/'+identifier+'/'+relative
                    frames.append(frame)
            return frames

    def image(self, identifier, camera, name):
        if camera not in ('external','wrist') or not re.fullmatch(r'[0-9]{8}\.jpg',name): raise ValueError('invalid image path')
        with self.lock:
            path, _ = self.entry(identifier)
            image = (path.parent/camera/name).resolve()
            if image.parent != path.parent.resolve()/camera: raise ValueError('invalid image location')
            return image.read_bytes()

    def video(self, identifier, camera):
        if camera not in ('external','wrist'):raise ValueError('invalid camera')
        with self.lock:
            path,_=self.entry(identifier)
            if video_status(path.parent)['status']!='ready':raise ValueError('MP4 尚未生成')
            video=(path.parent/(camera+'.mp4')).resolve()
            if video.parent!=path.parent.resolve():raise ValueError('invalid video location')
            return video

    def queue_missing_videos(self):
        if not self.video_exporter:return
        with self.lock:
            self.refresh(force=True)
            for path,_ in self.entries.values():
                if video_status(path.parent)['status']!='ready':self.video_exporter.submit(path.parent)

    def apply(self, data):
        with self.lock:
            self.refresh(force=True)
            path, saved = self.entry(data.get('id',''))
            if data.get('action')=='export_video':
                if not self.video_exporter:raise ValueError('此预览服务不提供视频生成')
                self.video_exporter.submit(path.parent)
                return self.list()
            metadata = copy.deepcopy(saved)
            if data.get('action') == 'delete':
                metadata['deleted'] = True
            elif data.get('action') == 'update':
                for key,limit in (('notes',4000),('prompt',2000)):
                    value = data.get(key,metadata.get(key,''))
                    if not isinstance(value,str) or len(value)>limit: raise ValueError('invalid '+key)
                    metadata[key] = value.strip()
                outcome = data.get('outcome',metadata.get('outcome'))
                if outcome not in ('success','failure','unlabeled','discard','incomplete'): raise ValueError('invalid outcome')
                if saved.get('outcome')=='incomplete' and outcome!='incomplete':
                    raise ValueError('采集中断记录保留 incomplete 状态，可补充备注')
                if outcome=='incomplete' and saved.get('outcome')!='incomplete': raise ValueError('incomplete 由采集器设置')
                if outcome!=saved.get('outcome'):
                    metadata.setdefault('original_outcome',saved.get('outcome'))
                    metadata.update(outcome=outcome,outcome_source='operator_review',status='complete' if outcome in ('success','failure') else outcome)
            else: raise ValueError('unknown episode action')
            metadata['reviewed_unix'] = time.time()
            replace_json(path,metadata)
            self.refresh(force=True)
            return self.list()
