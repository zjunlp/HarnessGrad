"""Bounded, explicit synthetic imports for the read-only personal foundation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re

from harnessgrad.improvement.contracts import reject_json_constant, unique_json_object

MAX_BYTES = 1024 * 1024
MAX_EVENTS = 200
MAX_DRAFT_BYTES = 16 * 1024


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
