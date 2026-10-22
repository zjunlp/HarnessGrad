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
    if 'trial_protocol' not in verifier:
        raise ValueError('trial_evidence requires trial_protocol')
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
