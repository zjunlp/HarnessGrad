"""Private, durable journal of a method submission before task evaluation.

This preserves the exact reply, trajectory and all owned-source candidate
steps. It is not a public export and does not authorize execution or resumption.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile

from harnessgrad import snapshot


class SubmissionJournalError(ValueError):
    """A submission could not be saved or restored without loss."""


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SubmissionJournalError('submission journal directory is not private')


def capture(run_dir: Path, method_root: Path, *, run_id: str, round_no: int,
            previous_tree: str, reply: dict, trajectory: dict, generation_s: float,
            accepted_step_count: int | None = None, attempt_usage: dict | None = None) -> Path:
    """Atomically preserve an exact method reply and every candidate step."""
    run_dir = Path(run_dir).resolve(strict=True)
    method_root = Path(method_root).resolve(strict=True)
    if (not isinstance(run_id, str) or not run_id or run_dir.name != run_id or
            isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 1 or
            not isinstance(previous_tree, str) or not previous_tree or
            not isinstance(reply, dict) or not isinstance(trajectory, dict) or
            isinstance(generation_s, bool) or not isinstance(generation_s, (int, float)) or
            not math.isfinite(generation_s) or generation_s < 0):
        raise SubmissionJournalError('invalid submission metadata')
    steps = trajectory.get('steps')
    if not isinstance(steps, list) or not steps or any(not isinstance(step, dict) for step in steps):
        raise SubmissionJournalError('submission has no valid steps')
    if accepted_step_count is None:
        accepted_step_count = len(steps)
    if (isinstance(accepted_step_count, bool) or not isinstance(accepted_step_count, int) or
            not 1 <= accepted_step_count <= len(steps)):
        raise SubmissionJournalError('invalid accepted submission step count')
    if attempt_usage is not None and (
            not isinstance(attempt_usage, dict) or
            type(attempt_usage.get('generation_tokens_lower_bound')) is not int or
            attempt_usage['generation_tokens_lower_bound'] < 0 or
            type(attempt_usage.get('complete')) is not bool or
            ('generation_s_complete' in attempt_usage and type(attempt_usage['generation_s_complete']) is not bool) or
            isinstance(attempt_usage.get('generation_s'), bool) or
            not isinstance(attempt_usage.get('generation_s'), (int, float)) or
            not math.isfinite(attempt_usage['generation_s']) or attempt_usage['generation_s'] < 0):
        raise SubmissionJournalError('invalid attempt usage')
    parent = run_dir / '.pending-method-submissions'
    _private_directory(parent)
    target = parent / f'round-{round_no}'
    if os.path.lexists(target):
        raise SubmissionJournalError('submission journal already exists')
    staging = Path(tempfile.mkdtemp(prefix=f'.round-{round_no}-', dir=parent))
    try:
        entries = []
        for index, step in enumerate(steps, start=1):
            raw = step.get('harness_dir')
            if not isinstance(raw, str) or not raw:
                raise SubmissionJournalError('candidate path missing')
            source = Path(raw)
            resolved = source.resolve(strict=True)
            if (not source.is_absolute() or source != resolved or not resolved.is_dir() or
                    not resolved.is_relative_to(method_root)):
                raise SubmissionJournalError('candidate is outside the trusted method scratch')
            store = staging / f'step-{index}'
            store.mkdir(mode=0o700)
            artifact = snapshot.write(store, index, resolved, run_id=run_id, harness_sha=None)
            snapshot.verify(artifact, resolved)
            entries.append({'index': index, 'snapshot_id': snapshot.load(artifact)['id']})
        manifest = {'schema': 'harnessgrad.method-submission-journal/v1', 'run_id': run_id,
                    'round': round_no, 'previous_tree': previous_tree, 'reply': reply,
                    'trajectory': trajectory, 'generation_s': generation_s, 'steps': entries,
                    'accepted_step_count': accepted_step_count,
                    'private_only': True}
        if attempt_usage is not None:
            manifest['attempt_usage'] = attempt_usage
        # Fail before publication when any method result cannot be serialized.
        payload = json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n'
        fd = os.open(staging / 'manifest.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.rename(staging, target)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return target
    except (OSError, ValueError, TypeError, snapshot.SnapshotInvalid) as exc:
        if isinstance(exc, SubmissionJournalError):
            raise
        raise SubmissionJournalError('cannot persist method submission') from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def restore(journal_dir: Path, destination: Path, *, run_id: str, round_no: int,
            previous_tree: str) -> tuple[dict, dict, float]:
    """Validate a journal and materialize its candidates in fresh scratch space."""
    journal_dir = Path(journal_dir).resolve(strict=True)
    _private_directory(journal_dir)
    try:
        manifest_path = journal_dir / 'manifest.json'
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise SubmissionJournalError('submission manifest missing or unsafe')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (manifest.get('schema') != 'harnessgrad.method-submission-journal/v1' or
                manifest.get('private_only') is not True or manifest.get('run_id') != run_id or
                manifest.get('round') != round_no or manifest.get('previous_tree') != previous_tree or
                not isinstance(manifest.get('reply'), dict) or not isinstance(manifest.get('trajectory'), dict)):
            raise SubmissionJournalError('submission manifest identity mismatch')
        steps = manifest['trajectory'].get('steps')
        saved = manifest.get('steps')
        if not isinstance(steps, list) or not steps or not isinstance(saved, list) or len(saved) != len(steps):
            raise SubmissionJournalError('submission step list invalid')
        destination = Path(destination)
        if os.path.lexists(destination):
            raise SubmissionJournalError('restore destination already exists')
        destination.mkdir(mode=0o700, parents=True)
        trajectory = copy.deepcopy(manifest['trajectory'])
        for index, (step, entry) in enumerate(zip(trajectory['steps'], saved), start=1):
            artifact = journal_dir / f'step-{index}' / 'snapshots' / f'round-{index}.json'
            payload = snapshot.load(artifact)
            if (not isinstance(entry, dict) or entry.get('index') != index or
                    payload['id'] != entry.get('snapshot_id') or payload['run_id'] != run_id or
                    payload['round'] != index):
                raise SubmissionJournalError('candidate snapshot identity mismatch')
            restored = destination / f'step-{index}'
            snapshot.check_out(artifact, restored)
            snapshot.verify(artifact, restored)
            step['harness_dir'] = str(restored.resolve())
        duration = manifest.get('generation_s')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise SubmissionJournalError('invalid generation duration')
        return manifest['reply'], trajectory, duration
    except (OSError, ValueError, TypeError, KeyError, snapshot.SnapshotInvalid) as exc:
        if isinstance(exc, SubmissionJournalError):
            raise
        raise SubmissionJournalError('cannot restore method submission') from exc


def pending(run_dir: Path, curve: list[dict], destination: Path) -> dict | None:
    """Find one unfinished saved submission and restore its remaining steps.

    Committed steps in the submission must still match both their recorded
    snapshots and saved round states. This never evaluates a task or writes a
    curve, and the caller must still admit the model, seed and runtime.
    """
    run_dir = Path(run_dir).resolve(strict=True)
    parent = run_dir / '.pending-method-submissions'
    if not os.path.lexists(parent):
        return None
    _private_directory(parent)
    if not isinstance(curve, list) or not curve:
        raise SubmissionJournalError('missing published history')
    points = {}
    for point in curve:
        number = point.get('round') if isinstance(point, dict) else None
        if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number in points:
            raise SubmissionJournalError('invalid or duplicate published round')
        points[number] = point
    last = max(points)
    candidates = []
    for directory in parent.iterdir():
        if directory.name.startswith('.'):
            continue
        if not directory.name.startswith('round-') or not directory.name[6:].isdigit():
            raise SubmissionJournalError('unexpected submission journal entry')
        start = int(directory.name[6:])
        if directory.is_symlink() or not directory.is_dir():
            raise SubmissionJournalError('unsafe submission journal entry')
        _private_directory(directory)
        try:
            manifest_path = directory / 'manifest.json'
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise SubmissionJournalError('submission manifest missing or unsafe')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            steps = manifest['trajectory']['steps']
            accepted = manifest['accepted_step_count']
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SubmissionJournalError('invalid submission journal') from exc
        if (not isinstance(steps, list) or isinstance(accepted, bool) or not isinstance(accepted, int)
                or not 1 <= accepted <= len(steps)):
            raise SubmissionJournalError('invalid accepted submission steps')
        if start <= last + 1 <= start + accepted - 1:
            candidates.append((directory, start, accepted, manifest))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise SubmissionJournalError('ambiguous pending submissions')
    directory, start, accepted, manifest = candidates[0]
    previous = points.get(start - 1) or {}
    previous_tree = (previous.get('identity') or {}).get('harness_sha')
    if not previous_tree or previous_tree != manifest.get('previous_tree'):
        raise SubmissionJournalError('submission parent differs from published history')
    consumed = last - start + 1
    for index in range(1, consumed + 1):
        round_no = start + index - 1
        if round_no not in points:
            raise SubmissionJournalError('committed submission history has a gap')
        artifact = directory / f'step-{index}' / 'snapshots' / f'round-{index}.json'
        try:
            snapshot.verify(artifact, run_dir / f'state_after_round{round_no}')
        except (OSError, ValueError, snapshot.SnapshotInvalid) as exc:
            raise SubmissionJournalError('committed step differs from submitted candidate') from exc
    reply, trajectory, generation_s = restore(directory, destination, run_id=run_dir.name,
        round_no=start, previous_tree=previous_tree)
    return {'start_round': start, 'accepted_step_count': accepted, 'consumed_steps': consumed,
            'reply': reply, 'trajectory': trajectory, 'generation_s': generation_s,
            'attempt_usage': manifest.get('attempt_usage'),
            'remaining_steps': trajectory['steps'][consumed:accepted]}
