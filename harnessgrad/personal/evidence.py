"""Bounded retrieval and immutable excerpts from explicitly imported sessions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import sqlite3

from harnessgrad.personal.contracts import decode, digest, identifier

MAX_SELECTIONS = 32
MAX_EVIDENCE_BYTES = 128 * 1024
INDEX = 'personal_source_fts'
INDEX_TABLES = {INDEX, *(INDEX + suffix for suffix in ('_data', '_idx', '_content', '_docsize', '_config'))}
PROVENANCE = {'user': 'user_quote', 'tool': 'tool_observation', 'assistant': 'assistant_output'}


@dataclass(frozen=True)
class EvidenceSelection:
    source_instance: str
    session_id: str
    event_id: str
    sha256: str
    start: int
    end: int

    def __post_init__(self):
        for value in (self.source_instance, self.session_id, self.event_id):
            identifier(value)
        if not isinstance(self.sha256, str) or not re.fullmatch('[a-f0-9]{64}', self.sha256):
            raise ValueError('evidence needs its original source digest')
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise ValueError('evidence offsets must select a nonempty Unicode character range')

    def as_dict(self) -> dict:
        return asdict(self)


def source_text(body: str) -> str:
    return decode(body)['text']


def excerpt(row, selection: EvidenceSelection) -> dict:
    value = decode(row['body'])
    if (row['sha256'] != selection.sha256 or digest(value) != row['sha256']
            or value['session_id'] != selection.session_id or selection.end > len(value['text'])):
        raise ValueError('selected evidence changed or is outside its source')
    return {**selection.as_dict(), 'text': value['text'][selection.start:selection.end],
            'role': value['role'], 'provenance': PROVENANCE[value['role']],
            'source': value['source'], 'timestamp': value['timestamp'],
            'truncated': value['truncated'], 'context_incomplete': value['truncated'],
            'partial': selection.start != 0 or selection.end != len(value['text'])}


class EvidenceStore:
    """The optional FTS table is a derived cache; original source rows stay authoritative."""

    def __init__(self, store):
        self.store = store
        self.fts = False
        try:
            with store.connection() as db, store.transaction(db):
                db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {INDEX} USING fts5(text, tokenize='trigram')")
                db.execute(f'INSERT INTO {INDEX}(rowid,text) SELECT seq,personal_source_text(body) '
                           f'FROM source_events WHERE seq>(SELECT coalesce(max(rowid),0) FROM {INDEX})')
            self.fts = True
        except sqlite3.OperationalError:
            # An unavailable tokenizer/extension or unreadable cache must not turn
            # imported evidence into a successful full-text search claim.
            pass

    def index(self, db, seq: int, text: str) -> None:
        if self.fts:
            db.execute(f'INSERT INTO {INDEX}(rowid,text) VALUES (?,?)', (seq, text))

    def search(self, project_id: str, source_instance: str, session_id: str, *,
               query: str = '', after: int = 0, before: int | None = None, limit: int = 50) -> dict:
        identifier(source_instance); identifier(session_id)
        if (not isinstance(query, str) or len(query.encode('utf-8')) > 256 or '\x00' in query
                or type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 50
                or before is not None and (type(before) is not int or before <= after)):
            raise ValueError('invalid selected session search window')
        use_fts = self.fts and len(query) >= 3
        with self.store.connection() as db:
            self.store.require_project(db, project_id)
            if not db.execute('SELECT 1 FROM sessions WHERE project_id=? AND source_instance=? AND id=?',
                              (project_id, source_instance, session_id)).fetchone():
                raise ValueError('session was not explicitly imported for this project')
            where = 'e.project_id=? AND e.source_instance=? AND e.session_id=? AND e.seq>?'
            args = [project_id, source_instance, session_id, after]
            if before is not None:
                where += ' AND e.seq<?'; args.append(before)
            if query:
                where += ' AND instr(personal_source_text(e.body),?)>0'; args.append(query)
            if use_fts:
                where += f' AND e.seq IN (SELECT rowid FROM {INDEX} WHERE {INDEX} MATCH ?)'
                args.append('"' + query.replace('"', '""') + '"')
            rows = db.execute('SELECT e.seq,e.body,e.sha256 FROM source_events e WHERE ' + where
                              + ' ORDER BY e.seq DESC LIMIT ?', (*args, limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        events = [{**decode(row['body']), 'seq': row['seq'], 'sha256': row['sha256'],
                   'provenance': PROVENANCE[decode(row['body'])['role']]} for row in rows]
        return {'events': events, 'has_more': has_more, 'next_before': rows[0]['seq'] if rows else before,
                'mode': 'fts5' if use_fts else 'session_filter', 'fts_available': self.fts,
                'source_instance': source_instance, 'session_id': session_id, 'query': query,
                'window': {'after': after, 'before': before}, 'order': 'chronological_latest_window'}
