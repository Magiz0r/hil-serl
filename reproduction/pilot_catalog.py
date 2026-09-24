"""Persistent task/layout catalog. No robot operations or controller dependencies."""
import copy
import json
from pathlib import Path
import re
import threading
import time
import uuid

from pilot_dataset import atomic_json, CAMERAS


def text_field(value, label, limit=2000, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(label + ' must be nonempty text, at most %d characters' % limit)
    return value.strip()


class CaptureCatalog:
    def __init__(self, root, initial_task):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'catalog.json'
        self.lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            task = copy.deepcopy(initial_task)
            task['id'] = 'task_' + uuid.uuid4().hex[:12]
            task.setdefault('display_name', task['name'])
            task.setdefault('prompt', task.get('success_definition', ''))
            self.data = dict(version=1, tasks=[task], layouts=[])
            atomic_json(self.path, self.data)
        available = [task for task in self.data['tasks'] if not task.get('deleted')]
        self.task_id = available[0]['id'] if available else None
        self.layout_id = None
        self.prompt = available[0].get('prompt', '') if available else ''
        selected = self.data.get('selection')
        if selected and self.task(selected.get('task_id')):
            self.task_id = selected['task_id']
            self.prompt = selected.get('prompt', self.task(self.task_id).get('prompt',''))
            layout = self.layout(selected.get('layout_id'))
            self.layout_id = layout['id'] if layout and layout['task_id']==self.task_id else None

    def task(self, identifier):
        return next((t for t in self.data['tasks'] if t['id'] == identifier and not t.get('deleted')), None)

    def layout(self, identifier):
        return next((t for t in self.data['layouts'] if t['id'] == identifier and not t.get('deleted')), None)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(dict(tasks=[t for t in self.data['tasks'] if not t.get('deleted')],
                layouts=[t for t in self.data['layouts'] if not t.get('deleted')],
                task_id=self.task_id, layout_id=self.layout_id, prompt=self.prompt))

    def selection(self):
        with self.lock:
            task = copy.deepcopy(self.task(self.task_id))
            if not task:
                raise ValueError('请先选择或创建任务')
            return dict(task=task, prompt=self.prompt, layout=copy.deepcopy(self.layout(self.layout_id)))

    def apply(self, data, frames=None):
        with self.lock:
            before = copy.deepcopy(self.data)
            previous = (self.task_id, self.layout_id, self.prompt)
            try:
                result = self._apply(data, frames)
                self.data['selection'] = dict(task_id=self.task_id, layout_id=self.layout_id, prompt=self.prompt)
                atomic_json(self.path, self.data)
                return result
            except Exception:
                self.data = before
                self.task_id, self.layout_id, self.prompt = previous
                raise

    def _apply(self, data, frames):
        action = data.get('action')
        if action == 'task_create':
            name = text_field(data.get('name'), '任务名', 100)
            prompt = text_field(data.get('prompt'), 'Prompt')
            if any(t['name'] == name and not t.get('deleted') for t in self.data['tasks']):
                raise ValueError('任务名已存在')
            task = dict(id='task_'+uuid.uuid4().hex[:12], name=name, display_name=name,
                        prompt=prompt, success_definition=text_field(data.get('success_definition', ''), '成功标准', required=False),
                        created_unix=time.time())
            self.data['tasks'].append(task)
            self.task_id, self.layout_id, self.prompt = task['id'], None, prompt
        elif action == 'select':
            task = self.task(data.get('task_id'))
            if task is None:
                raise ValueError('任务不存在或已删除')
            layout_id = data.get('layout_id') or None
            layout = self.layout(layout_id) if layout_id else None
            if layout_id and (not layout or layout['task_id'] != task['id']):
                raise ValueError('Layout 不属于所选任务')
            self.prompt = text_field(data.get('prompt', task.get('prompt', '')), 'Prompt')
            self.task_id, self.layout_id = task['id'], layout_id
        elif action in ('task_update', 'task_delete'):
            task = self.task(data.get('task_id'))
            if task is None:
                raise ValueError('任务不存在')
            if action == 'task_delete':
                task['deleted'] = True
                if self.task_id == task['id']:
                    self.task_id, self.layout_id, self.prompt = None, None, ''
            else:
                task['prompt'] = text_field(data.get('prompt'), 'Prompt')
                task['success_definition'] = text_field(data.get('success_definition', ''), '成功标准', required=False)
                self.prompt = task['prompt'] if self.task_id == task['id'] else self.prompt
        elif action == 'layout_capture':
            task = self.task(self.task_id)
            if not task:
                raise ValueError('请先选择任务')
            name = text_field(data.get('name'), 'Layout 名称', 100)
            if not frames or any(not frames.get(camera) or not frames[camera].get('jpeg')
                or not 0 <= time.monotonic()-frames[camera]['pc_captured_at'] <= .35 for camera in CAMERAS):
                raise ValueError('两路相机均需提供新鲜画面才能拍摄 Layout')
            identifier = 'layout_'+uuid.uuid4().hex[:12]
            directory = self.root / identifier
            directory.mkdir()
            for camera in CAMERAS:
                (directory / (camera+'.jpg')).write_bytes(frames[camera]['jpeg'])
            layout = dict(id=identifier, task_id=task['id'], name=name, domain='real',
                          ood=bool(data.get('ood', False)), created_unix=time.time())
            self.data['layouts'].append(layout)
            self.layout_id = identifier
        elif action in ('layout_rename', 'layout_delete'):
            layout = self.layout(data.get('layout_id'))
            if layout is None:
                raise ValueError('Layout 不存在')
            if action == 'layout_rename':
                layout['name'] = text_field(data.get('name'), 'Layout 名称', 100)
            else:
                layout['deleted'] = True
                if self.layout_id == layout['id']:
                    self.layout_id = None
        else:
            raise ValueError('unknown catalog action')
        return self.snapshot()

    def image(self, identifier, camera):
        if not re.fullmatch(r'layout_[a-f0-9]{12}', identifier) or camera not in CAMERAS:
            raise ValueError('invalid layout image')
        with self.lock:
            if not self.layout(identifier):
                raise ValueError('Layout 不存在')
            return (self.root / identifier / (camera+'.jpg')).read_bytes()
