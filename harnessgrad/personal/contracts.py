"""Bounded, explicit synthetic imports for the read-only personal foundation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re

from harnessgrad.improvement.contracts import (
    is_relative_path,
    reject_json_constant,
    unique_json_object,
)

MAX_BYTES = 1024 * 1024
MAX_EVENTS = 200
MAX_DRAFT_BYTES = 16 * 1024


@dataclass(frozen=True)
class SurfaceRef:
    """One file or directory below a caller-authorized project root."""

    root_id: str
    path: str

    def __post_init__(self):
        identifier(self.root_id)
        if not is_relative_path(self.path):
            raise ValueError('surface path must be a relative POSIX path')

    def as_dict(self) -> dict:
        return {'root_id': self.root_id, 'path': self.path}

    @classmethod
    def from_dict(cls, value: dict) -> 'SurfaceRef':
        if not isinstance(value, dict) or set(value) != {'root_id', 'path'}:
            raise ValueError('surface reference must contain exactly root_id and path')
        try:
            return cls(**value)
        except TypeError:
            raise ValueError('invalid surface reference') from None


@dataclass(frozen=True)
class FileChange:
    """A rendered candidate change; applying it is deliberately out of scope."""

    surface: SurfaceRef
    resource_ids: tuple[str, ...]
    before_sha256: str | None
    after_text: str | None
    before_mode: int | None
    after_mode: int | None

    def __post_init__(self):
        if not isinstance(self.surface, SurfaceRef):
            raise ValueError('file change surface must be a SurfaceRef')
        if (not isinstance(self.resource_ids, tuple) or not self.resource_ids
                or any(not isinstance(value, str) for value in self.resource_ids)):
            raise ValueError('file change needs resource ids')
        normalized = tuple(sorted(self.resource_ids))
        if len(set(normalized)) != len(normalized):
            raise ValueError('file change resource ids must be unique')
        for value in normalized:
            identifier(value)
        object.__setattr__(self, 'resource_ids', normalized)
        if self.before_sha256 is not None and not re.fullmatch(r'[0-9a-f]{64}', self.before_sha256):
            raise ValueError('invalid before digest')
        if self.after_text is not None and not isinstance(self.after_text, str):
            raise ValueError('after text must be text or null')
        for value in (self.before_mode, self.after_mode):
            if value is not None and (type(value) is not int or not 0 <= value <= 0o7777):
                raise ValueError('file mode must be an integer permission mode or null')

    def as_dict(self) -> dict:
        return {
            'surface': self.surface.as_dict(),
            'resource_ids': list(self.resource_ids),
            'before_sha256': self.before_sha256,
            'after_text': self.after_text,
            'before_mode': self.before_mode,
            'after_mode': self.after_mode,
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'FileChange':
        fields = {'surface', 'resource_ids', 'before_sha256', 'after_text',
                  'before_mode', 'after_mode'}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('file change contains unknown or missing fields')
        if not isinstance(value['resource_ids'], list):
            raise ValueError('file change resource_ids must be a list')
        try:
            return cls(
                surface=SurfaceRef.from_dict(value['surface']),
                resource_ids=tuple(value['resource_ids']),
                before_sha256=value['before_sha256'],
                after_text=value['after_text'],
                before_mode=value['before_mode'],
                after_mode=value['after_mode'],
            )
        except TypeError:
            raise ValueError('invalid file change') from None


def decode(raw: bytes | str) -> dict:
    if len(raw if isinstance(raw, bytes) else raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('personal input exceeds 1 MiB')
    try:
        value = json.loads(raw, object_pairs_hook=unique_json_object,
                           parse_constant=reject_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('personal input must be JSON with unique keys') from None
    if not isinstance(value, dict):
        raise ValueError('personal input must contain an object')
    return value


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('invalid personal identifier')
    return value


@dataclass(frozen=True)
class SourceEvent:
    event_id: str
    session_id: str
    source_instance: str
    timestamp: str
    role: str
    text: str
    truncated: bool
    source: str = 'synthetic'

    def __post_init__(self):
        for value in (self.event_id, self.session_id, self.source_instance):
            identifier(value)
        if self.source != 'synthetic':
            raise ValueError('stage A imports only explicitly synthetic sessions')
        if self.role not in ('user', 'assistant', 'tool'):
            raise ValueError('unsupported source role')
        if not isinstance(self.text, str) or len(self.text.encode('utf-8')) > 16384:
            raise ValueError('source text exceeds 16 KiB')
        if type(self.truncated) is not bool:
            raise ValueError('truncated must be explicit true or false')
        if not isinstance(self.timestamp, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', self.timestamp):
            raise ValueError('source timestamp must use UTC ISO notation')
        datetime.fromisoformat(self.timestamp)

    def as_dict(self) -> dict:
        return asdict(self)


def source_events(value: dict) -> tuple[SourceEvent, ...]:
    if set(value) != {'schema', 'events'} or value['schema'] != 'harnessgrad.personal.import.v1':
        raise ValueError('expected harnessgrad.personal.import.v1')
    rows = value['events']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EVENTS:
        raise ValueError('import needs 1 to 200 source events')
    try:
        result = tuple(SourceEvent(**row) for row in rows)
    except TypeError:
        raise ValueError('each source event must contain exactly the documented fields') from None
    if len(encode(value).encode('utf-8')) > MAX_BYTES:
        raise ValueError('personal import exceeds 1 MiB')
    return result
