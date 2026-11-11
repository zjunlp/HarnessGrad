"""Lazy task declarations and short-lived metadata reuse; never execution admission."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

_CACHE = ContextVar('dataset_declaration_metadata', default=None)


@contextmanager
def scope():
    """One synchronous metadata operation; nested readers share its snapshot."""
    if _CACHE.get() is not None:
        yield
        return
    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


def once(key, build):
    """Reuse trusted metadata only while its declaration scope is active."""
    cache = _CACHE.get()
    if cache is None:
        return build()
    if key not in cache:
        cache[key] = build()
    return cache[key]


class TaskDeclarations(Mapping):
    """Enumerate keys without building values; construct only requested tasks."""
    def __init__(self, records, build):
        self._records = {record['task_id']: record for record in records}
        self._build = build

    def __iter__(self):
        return iter(self._records)

    def __len__(self):
        return len(self._records)

    def __contains__(self, key):
        return key in self._records

    def __getitem__(self, key):
        return self._build(self._records[key])
