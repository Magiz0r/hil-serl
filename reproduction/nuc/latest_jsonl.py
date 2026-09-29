"""Bounded latest-value reads from an append-only JSONL telemetry journal.

This is for replaceable state snapshots, never action or button event journals.
Incomplete appends do not update the value or its freshness timestamp.
"""
import json
import os


class LatestJSONL:
    def __init__(self, path, *, start_at_end=True, chunk_bytes=65536, max_bytes=1048576):
        self.file = open(path, 'rb', buffering=0)
        self.offset = os.fstat(self.file.fileno()).st_size if start_at_end else 0
        self.skip_fragment = False
        if self.offset:
            self.file.seek(self.offset - 1)
            self.skip_fragment = self.file.read(1) != b'\n'
        self.chunk_bytes = chunk_bytes
        self.max_bytes = max_bytes

    def poll(self):
        end = os.fstat(self.file.fileno()).st_size
        if end < self.offset:
            raise ValueError('telemetry journal was truncated')
        if end == self.offset:
            return None
        size = min(self.chunk_bytes, end - self.offset)
        while True:
            start = max(self.offset, end - size)
            self.file.seek(start)
            raw = self.file.read(end - start)  # Never chase a concurrent writer past this end.
            last = raw.rfind(b'\n')
            previous = raw.rfind(b'\n', 0, last) if last >= 0 else -1
            if last >= 0 and (previous >= 0 or start == self.offset):
                if previous < 0 and self.skip_fragment:
                    self.offset = start + last + 1
                    self.skip_fragment = False
                    return None
                value = json.loads(raw[previous + 1:last])
                self.offset = start + last + 1
                self.skip_fragment = False
                return value
            if start == self.offset:
                return None
            if size >= self.max_bytes:
                raise ValueError('telemetry record exceeds bounded reader capacity')
            size = min(size * 2, self.max_bytes, end - self.offset)

    def close(self):
        self.file.close()
