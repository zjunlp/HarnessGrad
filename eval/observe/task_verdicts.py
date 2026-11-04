"""Persist trusted verifier results per task before the enclosing round finishes.

This is an audit journal, outside actor/method feedback. It copies actual verdicts;
it never grades artifacts, derives a score from progress, or changes inclusion rules.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


SCHEMA = 'harnessgrad.task-verdict.v1'


def relative_path(round_no, task_id):
    return Path('task-verdicts') / f'round-{round_no}' / (hashlib.sha256(task_id.encode()).hexdigest() + '.json')


def _publish(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.verdict-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or json.loads(path.read_text()) != record:
                raise ValueError(f'task verifier journal already differs: {path}')
    finally:
        os.unlink(temporary)


def replace_progress(path, text):
    """Publish a complete index only after its immutable verdict journal exists."""
    fd, temporary = tempfile.mkstemp(prefix='.progress-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def start(run, round_no, task_id):
    """Write before run_one; a surviving start without verdict needs review."""
    run = Path(run)
    relative = Path('task-starts') / f'round-{round_no}' / relative_path(round_no, task_id).name
    _publish(run / relative, {'schema': 'harnessgrad.task-start.v1',
                             'run_id': run.name, 'round': round_no, 'task_id': task_id})


def save(run, round_no, task_id, measured):
    run = Path(run)
    relative = relative_path(round_no, task_id)
    path = run / relative
    record = {'schema': SCHEMA, 'run_id': run.name, 'round': round_no,
              'task_id': task_id, 'trials': [
                  {'verdict': result.get('verdict'), 'exit_code': result.get('exit_code'),
                   'stderr': result.get('stderr'), 'sandbox': result.get('sandbox')}
                  for result in measured]}
    _publish(path, record)
    return {'path': relative.as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def load(run, round_no, task_id, reference=None):
    run = Path(run).resolve()
    relative = relative_path(round_no, task_id)
    path = run / relative
    if path.resolve() != path or not path.is_file():
        raise ValueError('task verifier journal is missing or uses a symlink')
    content = path.read_bytes()
    if reference is not None and reference != {
            'path': relative.as_posix(), 'sha256': hashlib.sha256(content).hexdigest()}:
        raise ValueError('task verifier journal differs from its progress reference')
    record = json.loads(content)
    if (not isinstance(record, dict) or record.get('schema') != SCHEMA
            or record.get('run_id') != run.name or record.get('round') != round_no
            or record.get('task_id') != task_id or not isinstance(record.get('trials'), list)
            or not record['trials'] or any(not isinstance(row, dict) for row in record['trials'])):
        raise ValueError('task verifier journal binding is invalid')
    return record, path
