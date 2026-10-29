"""Opt-in per-trial archives, independent of scores and method feedback.

Harness output is untrusted diagnostic material. The archive keeps it alongside
the full platform result; only the existing verifier decides the trial's score.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat

from data.scaffolds.runtime_extensions import validate_trial_evidence, validate_trials
from eval.run.repeated_trials import immutable_json


def enabled(verifier):
    if 'trial_evidence' not in (verifier or {}):
        return False
    validate_trial_evidence(verifier['trial_evidence'])
    if 'trial_protocol' in verifier:
        validate_trials(verifier['trial_protocol'])
    return True


def admit_execution(*, sandbox, setup, env):
    # Version 1 collects a native task directory. Container state and worker
    # lifecycles need their own collectors before they can opt in.
    if (not sandbox or (env or {}).get('kind', 'files') != 'files'
            or (setup or {}).get('workers') is not None):
        raise ValueError('trial_evidence v1 requires sandboxed files without workers')


def admit_archive(repo, progress_dir):
    if progress_dir is None:
        raise ValueError('trial_evidence requires progress_dir for persistent evidence')
    root = (Path(progress_dir) / 'trial-evidence').resolve()
    work = Path(os.environ.get('HARNESSGRAD_WORK_ROOT') or Path(repo).resolve().parent.parent).resolve()
    for visible in (Path(repo).resolve(), work):
        if root.is_relative_to(visible):
            raise ValueError('trial_evidence must be outside the harness and its visible work root')


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value


def capture_output(workdir, *, attempted, process, error):
    """Collect before cleanup, including the relay-fault early-return path.

    Never follow actor links or block on a FIFO. Read only the observed file size;
    unavailable files are recorded explicitly. The verifier still owns scoring.
    """
    output = {'launch_attempted': attempted,
              'exit_code': process.returncode if process is not None else None,
              'stdout': _text(process.stdout if process is not None else getattr(error, 'stdout', None)),
              'stderr': _text(process.stderr if process is not None else getattr(error, 'stderr', None)),
              'launch_error': {'type': type(error).__name__, 'detail': str(error)} if error else None}
    for name, filename in (('trace', 'trace.jsonl'), ('answer', 'answer.txt')):
        try:
            fd = os.open(Path(workdir) / filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(f'{name} must be a regular file with one link')
                data = stream.read(info.st_size)
            try:
                output[name] = data.decode('utf-8')
            except UnicodeDecodeError:
                output[name] = data.decode('utf-8', errors='replace')
                output[name + '_bytes_base64'] = base64.b64encode(data).decode('ascii')
            output[name + '_status'] = 'captured'
        except FileNotFoundError:
            output.update({name: None, name + '_status': 'missing' if attempted else 'not_started'})
        except (OSError, ValueError) as exc:
            output.update({name: None, name + '_status': 'unreadable', name + '_error': str(exc)})
    return output


def _binding(plan, row):
    return {**{k: plan[k] for k in ('run_id', 'round', 'harness_sha')},
            **{k: row[k] for k in ('task_id', 'trial_id', 'seed')}}


def _relative_path(binding):
    def digest(value):
        return hashlib.sha256(str(value).encode()).hexdigest()
    return (Path('trial-evidence') / f"round-{binding['round']}" /
            digest(binding['harness_sha'])[:16] / digest(binding['task_id']) /
            (digest(binding['trial_id']) + '.json'))


def save(progress_dir, plan, row, result):
    binding = _binding(plan, row)
    relative = _relative_path(binding)
    path = Path(progress_dir) / relative
    record = {'schema': 'harnessgrad.trial-evidence.v1', 'binding': binding,
              'configuration': plan['configuration'], 'outcome': row, 'result': result}
    # A write failure must stop publication, not silently promise missing evidence.
    # Earlier trial files and the existing in-flight marker remain for inspection.
    immutable_json(path, record, reuse=False)
    return {'path': relative.as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_saved(progress_dir, plan, row):
    binding = _binding(plan, row)
    relative = _relative_path(binding)
    reference = row.get('trial_evidence') or {}
    if not isinstance(reference, dict) or reference.get('path') != relative.as_posix():
        raise ValueError('trial_evidence reference missing or differs from trial identity')
    root = Path(progress_dir).resolve()
    path = root / relative
    if path.resolve() != path:
        raise ValueError('trial_evidence archive must not use symlinks')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.get('sha256'):
        raise ValueError('trial_evidence archive hash differs')
    record = json.loads(content)
    outcome = {k: v for k, v in row.items() if k != 'trial_evidence'}
    if (not isinstance(record, dict) or record.get('schema') != 'harnessgrad.trial-evidence.v1'
            or record.get('binding') != binding
            or record.get('configuration') != plan['configuration']
            or record.get('outcome') != outcome or not isinstance(record.get('result'), dict)):
        raise ValueError('trial_evidence archive differs from its trial report')


def ordinary_plan(tasks, *, trials, run_seed, run_id, round_no, harness_sha):
    """Archive identities only; ordinary scoring and stopping stay in the runner."""
    return {'configuration': {'version': 1, 'execution': 'ordinary', 'trials': trials,
                              'run_seed': str(run_seed)},
            'run_id': run_id, 'round': round_no, 'harness_sha': harness_sha,
            'manifest': [{'task_id': task['task_id'],
                          'trial_id': f'round-{round_no}.harness-{harness_sha}.trial-{i + 1}',
                          'seed': str(run_seed)} for task in tasks for i in range(trials)]}


def ordinary_root(progress_dir, round_no, harness_sha):
    return (Path(progress_dir) / 'trial-evidence' / 'ordinary' / f'round-{round_no}'
            / hashlib.sha256(harness_sha.encode()).hexdigest()[:16])


def ordinary_index(plan, rows):
    expected = {(row['task_id'], row['trial_id']): row for row in plan['manifest']}
    if len(expected) != len(plan['manifest']):
        raise ValueError('duplicate ordinary trial evidence identity')
    found = {}
    for row in rows:
        key = (row['task_id'], row['trial_id'])
        if key not in expected or key in found or row['seed'] != expected[key]['seed']:
            raise ValueError('ordinary trial evidence differs from its plan')
        found[key] = row
    return {'schema': 'harnessgrad.trial-evidence-index.v1', **plan,
            'results': [found[key] for key in expected if key in found],
            'not_returned': [expected[key] for key in expected if key not in found]}


def ordinary_row(identity, result):
    # pass^k intentionally excludes some outcomes that ordinary evaluation keeps.
    # Do not borrow its validity or binary-score rules for this archival index.
    return {**identity, 'status': 'returned',
            'raw_verdict_score': (result.get('verdict') or {}).get('score')}


def save_ordinary_task(progress_dir, plan, rows):
    from eval.observe.tracing import task_file
    task_id = plan['manifest'][0]['task_id']
    progress = task_file(Path(progress_dir) / 'progress' / f"round-{plan['round']}", task_id + '.json')
    report = ordinary_index(plan, rows)
    report['progress_sha256'] = hashlib.sha256(progress.read_bytes()).hexdigest()
    root = ordinary_root(progress_dir, plan['round'], plan['harness_sha'])
    immutable_json(root / 'tasks' / (hashlib.sha256(task_id.encode()).hexdigest() + '.json'), report)


def persist_ordinary(progress_dir, report):
    root = ordinary_root(progress_dir, report['round'], report['harness_sha'])
    immutable_json(root / 'index.json', report)


def resume_ordinary(progress_dir, full_plan, prior):
    """Validate archived attempts before ordinary eval skips completed tasks."""
    from eval.observe.tracing import task_file
    root = ordinary_root(progress_dir, full_plan['round'], full_plan['harness_sha'])
    if any((root / 'in-flight').glob('*.json')):
        raise ValueError('ordinary trial evidence has an interrupted in-flight task; use a fresh run')
    expected_tasks = {row['task_id'] for row in full_plan['manifest']}
    stored = {}
    for path in (root / 'tasks').glob('*.json'):
        if path.resolve() != path.absolute():
            raise ValueError('ordinary trial evidence index must not use symlinks')
        report = json.loads(path.read_text())
        ids = {row['task_id'] for row in report['manifest']}
        if len(ids) != 1:
            raise ValueError('ordinary trial evidence index must name one task')
        task_id = next(iter(ids))
        if task_id in stored or task_id not in expected_tasks:
            raise ValueError('unexpected ordinary trial evidence index')
        stored[task_id] = report
    if set(stored) != (set(prior) & expected_tasks):
        raise ValueError('ordinary trial evidence and completed progress disagree; use a fresh run')
    rows = []
    for task_id, report in stored.items():
        manifest = [row for row in full_plan['manifest'] if row['task_id'] == task_id]
        if (report.get('manifest') != manifest or any(report.get(key) != full_plan[key]
                for key in ('configuration', 'run_id', 'round', 'harness_sha'))):
            raise ValueError('ordinary trial evidence execution identity differs')
        checked = ordinary_index({key: report[key] for key in (
            'configuration', 'run_id', 'round', 'harness_sha', 'manifest')}, report['results'])
        if (report.get('schema') != checked['schema']
                or report.get('not_returned') != checked['not_returned']
                or prior[task_id].get('trial_evidence') != report['results']):
            raise ValueError('ordinary trial evidence index differs from progress')
        progress = task_file(Path(progress_dir) / 'progress' / f"round-{full_plan['round']}", task_id + '.json')
        if hashlib.sha256(progress.read_bytes()).hexdigest() != report.get('progress_sha256'):
            raise ValueError('ordinary trial evidence progress hash differs')
        for row in checked['results']:
            validate_saved(progress_dir, full_plan, row)
        rows.extend(checked['results'])
    published = root / 'index.json'
    if os.path.lexists(published):
        if published.resolve() != published.absolute():
            raise ValueError('ordinary trial evidence index must not use symlinks')
        if json.loads(published.read_text()) != ordinary_index(full_plan, rows):
            raise ValueError('published ordinary trial evidence differs from saved trials')
        if expected_tasks - set(prior):
            raise ValueError('ordinary trial evidence already published with unfinished tasks; use a fresh run')
    return rows
