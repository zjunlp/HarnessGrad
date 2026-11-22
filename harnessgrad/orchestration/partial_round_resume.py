"""Guarded execution primitive for resuming an interrupted training round.

The caller owns the actual evaluator, method-submission restoration and full
model/runtime admission. This module neither publishes a curve nor invokes a
method. The evaluator must write ordinary per-task evidence to the same round.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Callable

from eval.observe import task_verdicts
from harnessgrad.orchestration.interrupted_measurement import (
    InterruptedMeasurementRefused,
    inspect_partial,
)


def _anchor(root: Path, before: dict) -> None:
    """Persist and validate pre-resume evidence across a process interruption."""
    number = before['round']
    path = root / f'.partial-round-{number}-resume.json'
    anchor = {'schema': 'harnessgrad.partial-round-anchor/v1', 'run_id': before['run_id'],
              'round': number, 'candidate_tree_sha': before['candidate_tree_sha'],
              'completed': before['completed'], 'missing_task_ids': before['missing_task_ids']}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('unsafe journal')
                saved = json.load(stream)
        except (OSError, ValueError, TypeError) as exc:
            raise InterruptedMeasurementRefused('invalid partial-round journal') from exc
        if (not isinstance(saved, dict) or saved.get('schema') != anchor['schema'] or
                saved.get('run_id') != anchor['run_id'] or saved.get('round') != number or
                saved.get('candidate_tree_sha') != anchor['candidate_tree_sha'] or
                not isinstance(saved.get('completed'), dict) or
                not isinstance(saved.get('missing_task_ids'), list)):
            raise InterruptedMeasurementRefused('partial-round journal identity changed')
        for tid, previous in saved['completed'].items():
            if before['completed'].get(tid) != previous:
                raise InterruptedMeasurementRefused('previously completed evidence changed')
        if set(saved['completed']) | set(saved['missing_task_ids']) != (
                set(before['completed']) | set(before['missing_task_ids'])):
            raise InterruptedMeasurementRefused('partial-round task set changed')
    except OSError as exc:
        raise InterruptedMeasurementRefused('cannot create partial-round journal') from exc
    else:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(anchor, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def resume_missing_samples(
    run_dir: Path,
    round_no: int,
    *,
    expected_candidate_tree: str,
    admit_execution: Callable[[dict], None],
    evaluate_missing: Callable[[list[str]], object],
) -> dict:
    """Evaluate only missing tasks, retaining durable progress on interruption.

    ``admit_execution`` must verify the caller's actual model, seed, runtime,
    protocol and method submission; this primitive cannot infer those from a
    historical label. It is deliberately mandatory. No scores or usage are
    invented for missing tasks, and an incomplete return is not a curve point.
    The lock coordinates callers of this primitive; the caller must also refuse
    execution while an older driver that does not honor this lock is active.
    """
    root = Path(run_dir).resolve(strict=True)
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        raise InterruptedMeasurementRefused('invalid partial-round index')
    lock_path = root / '.partial-round-resume.lock'
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise InterruptedMeasurementRefused('cannot open partial-round lock') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InterruptedMeasurementRefused('unsafe partial-round lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InterruptedMeasurementRefused('partial round is already being resumed') from exc
        before = inspect_partial(root, round_no)
        if not expected_candidate_tree or before['candidate_tree_sha'] != expected_candidate_tree:
            raise InterruptedMeasurementRefused('candidate tree does not match expected identity')
        admit_execution(before)
        missing = tuple(before['missing_task_ids'])
        # A start or verdict without durable progress is an interrupted attempt,
        # not proof that the sample was never executed. Running it against the
        # same immutable receipt path can overwrite or collide with evidence.
        for tid in missing:
            verdict = task_verdicts.relative_path(round_no, tid)
            start = Path('task-starts') / f'round-{round_no}' / verdict.name
            if os.path.lexists(root / verdict) or os.path.lexists(root / start):
                raise InterruptedMeasurementRefused(
                    'missing sample has an unfinished prior attempt; reconcile it in a separate run')
        _anchor(root, before)
        if missing:
            evaluate_missing(list(missing))
        after = inspect_partial(root, round_no)
        if after['candidate_tree_sha'] != before['candidate_tree_sha']:
            raise InterruptedMeasurementRefused('candidate changed during partial-round execution')
        for tid, previous in before['completed'].items():
            if after['completed'].get(tid) != previous:
                raise InterruptedMeasurementRefused('previously completed evidence changed')
        if not set(after['completed']) <= set(before['completed']) | set(missing):
            raise InterruptedMeasurementRefused('execution introduced an unauthorized task')
        return {'inspection': after, 'requested_task_ids': list(missing),
                'status': 'complete_not_published' if not after['missing_task_ids'] else 'incomplete'}
    finally:
        os.close(fd)
