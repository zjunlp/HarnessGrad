"""Explicitly selected synthetic imports; no discovery, hooks or host I/O."""
from __future__ import annotations

from harnessgrad.personal.contracts import digest, identifier
from harnessgrad.personal.learning.contracts import (
    LearningScope,
    SourceCapability,
    SourceConflict,
    SourceItem,
    SourcePage,
)


class SelectedImportSource:
    """An append-only fixture stream for one selected project, profile and source."""

    def __init__(self, scope: LearningScope, source_id: str, events: tuple[SourceItem, ...]):
        self.scope = scope
        self.source_id = identifier(source_id)
        self._events = tuple(events)
        if any(item.source_id != source_id for item in self._events):
            raise ValueError('fixture events must belong to the selected source')
        self._cursor_prefix = 'import_' + digest([*scope.key(), source_id])[:24]
        previous = self._cursor_prefix
        prefixes = [previous]
        for item in self._events:
            previous = digest([previous, item.event_sha256])
            prefixes.append(previous)
        self._prefix_digests = tuple(prefixes)

    def probe_source(self, scope: LearningScope, source_id: str) -> SourceCapability:
        if scope != self.scope or source_id != self.source_id:
            return SourceCapability(source_id, 'selected_import', 'refused', 'out_of_scope')
        return SourceCapability(source_id, 'selected_import', 'ready')

    def read_source(
        self, scope: LearningScope, source_id: str, *, cursor: str | None, limit: int = 50,
    ) -> SourcePage:
        if self.probe_source(scope, source_id).status != 'ready':
            raise ValueError('source is outside the selected scope')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('source page limit must be between 1 and 50')
        offset = self._offset(cursor)
        events = self._events[offset:offset + limit]
        end = offset + len(events)
        next_cursor = f'{self._cursor_prefix}:{end}:{self._prefix_digests[end]}' if events else cursor
        return SourcePage(events, next_cursor, end < len(self._events))

    def _offset(self, cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not isinstance(cursor, str):
            raise ValueError('invalid cursor for the selected source')
        prefix, _, remainder = cursor.partition(':')
        position, _, fingerprint = remainder.partition(':')
        if (prefix != self._cursor_prefix or not position.isascii()
                or not position.isdigit() or len(position) > 19 or position.startswith('0')):
            raise ValueError('invalid cursor for the selected source')
        offset = int(position)
        if offset > len(self._events) or fingerprint != self._prefix_digests[offset]:
            raise SourceConflict('fixture stream changed before its saved cursor')
        return offset
