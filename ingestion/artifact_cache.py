"""Bounded, request-scoped snapshots of verified immutable artifact bytes."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock


class ArtifactCache:
    def __init__(self, max_bytes=32 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.size = 0
        self.values = OrderedDict()
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            value = self.values.get(key)
            if value is not None:
                self.values.move_to_end(key)
            return value

    def put(self, key, value):
        if len(value) > self.max_bytes:
            return
        with self.lock:
            old = self.values.pop(key, b'')
            self.size += len(value) - len(old)
            self.values[key] = value
            while self.size > self.max_bytes:
                self.size -= len(self.values.popitem(last=False)[1])

    def forget(self, key):
        with self.lock:
            self.size -= len(self.values.pop(key, b''))


_current = ContextVar('artifact_cache', default=None)


def active_session():
    return _current.get() is not None


@contextmanager
def artifact_session():
    """Never reuse a snapshot across separate requests / forensic reads."""
    token = _current.set(ArtifactCache())
    try:
        yield
    finally:
        _current.reset(token)


def cached(owner, key):
    cache = _current.get()
    return cache.get((owner, key)) if cache is not None else None


def remember(owner, key, value):
    cache = _current.get()
    if cache is not None:
        cache.put((owner, key), value)


def forget(owner, key):
    cache = _current.get()
    if cache is not None:
        cache.forget((owner, key))
        underlying = getattr(owner, 'storage', None)
        if underlying is not None:
            cache.forget((underlying, key))
