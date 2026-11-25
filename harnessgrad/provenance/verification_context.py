"""Private, content-bound measurement inputs; no source bytes leave this module.

This is an equality commitment, not authentication of code, data or runtime.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat

KEY = 'verification_context'
ROOT = Path(__file__).resolve().parents[2]
# Private-input restoration now replaces destination inodes. Measurements made
# under the previous staging contract cannot prove the same scoring conditions.
MEASUREMENT_PROTOCOL = 'harnessgrad.measurement.v2'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_CACHE_ROOTS_ENV = 'HG_VERIFICATION_FILE_CACHE_ROOTS'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def create(tasks):
    if not isinstance(tasks, dict) or any(not isinstance(k, str) or not k or
            not isinstance(v, str) or not _HASH.fullmatch(v) for k, v in tasks.items()):
        raise ValueError('invalid verification context task digests')
    payload = {'version': 1, 'tasks': dict(sorted(tasks.items()))}
    return {**payload, 'sha256': _digest(payload)}


def validate(value):
    if not isinstance(value, dict) or set(value) != {'version', 'tasks', 'sha256'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid verification context envelope')
    result = create(value['tasks'])
    if value != result:
        raise ValueError('verification context digest mismatch')
    return result


def project(value, task_ids):
    if value is None:
        return None
    value = validate(value)
    ids = list(task_ids)
    if len(ids) != len(set(ids)) or any(tid not in value['tasks'] for tid in ids):
        raise ValueError('verification context missing selected task')
    return create({tid: value['tasks'][tid] for tid in ids})


def problems(reference, current):
    for name, value in (('reference', reference), ('current', current)):
        if value is None:
            return [f'verification_context missing from {name}']
        try:
            validate(value)
        except (TypeError, ValueError):
            return [f'invalid verification_context on {name}']
    return [] if reference == current else ['verification_context changed']


def fields(value):
    return {} if value is None else {KEY: validate(value)}


def _stat_stamp(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _stamp(path):
    return _stat_stamp(path.lstat())


def _cache_roots(setting):
    roots = []
    for raw in filter(None, setting.split(os.pathsep)):
        path = Path(raw)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(f'{_CACHE_ROOTS_ENV} requires absolute directory paths')
        roots.append(path.resolve(strict=True))
    return tuple(roots)


def _file_digest(path, *, expected=None):
    with path.open('rb') as stream:
        if expected is not None and _stat_stamp(os.fstat(stream.fileno())) != expected:
            raise ValueError('verification source changed during capture')
        digest = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
        if expected is not None and _stat_stamp(os.fstat(stream.fileno())) != expected:
            raise ValueError('verification source changed during capture')
        return digest.hexdigest()


@lru_cache(maxsize=262_144)
def _cached_file_digest(path, stamp):
    return _file_digest(path, expected=stamp)


def _source(path, memo, *, portable=False, cache_roots=()):
    source = Path(path)
    if not source.is_absolute():
        source = ROOT / source
    source = Path(os.path.abspath(source))
    if source in memo:
        return memo[source]
    resolved = source.resolve(strict=True)
    forbidden = {Path('/'), Path('/tmp'), Path('/var/tmp'), Path('/home'), Path.home(),
                 Path('/usr'), Path('/bin'), Path('/lib'), Path('/lib64'), Path('/etc')}
    if resolved in forbidden:
        raise ValueError('verification source is an overly broad root')
    boundary = resolved if resolved.is_dir() else resolved.parent
    use_file_cache = any(resolved.is_relative_to(root) for root in cache_roots)
    active = set()

    def walk(p):
        before = _stamp(p)
        mode = before[2]
        if stat.S_ISLNK(mode):
            target = p.resolve(strict=True)
            if not target.is_relative_to(boundary):
                raise ValueError('verification source symlink escapes its source root')
            link = os.readlink(p)
            if portable and Path(link).is_absolute():
                link = {'source_relative': target.relative_to(boundary).as_posix()}
            result = {'link': link, 'target': walk(target)}
        elif stat.S_ISREG(mode):
            digest = (_cached_file_digest(p, before) if use_file_cache else _file_digest(p))
            result = {'file': digest, 'executable': bool(mode & 0o111)}
        elif stat.S_ISDIR(mode):
            real = p.resolve(strict=True)
            if real in active:
                raise ValueError('verification source directory cycle')
            active.add(real)
            try:
                children = sorted(p.iterdir(), key=lambda x: x.name)
                result = {'directory': [[child.name, walk(child)] for child in children]}
            finally:
                active.remove(real)
        else:
            raise ValueError('verification source is not an ordinary file or directory')
        if _stamp(p) != before:
            raise ValueError('verification source changed during capture')
        return result

    result = _digest(walk(source))
    memo[source] = result
    return result


def _sources(value, memo, *, relay=False, runtimes=None, cache_roots=()):
    if isinstance(value, list):
        return [_sources(v, memo, runtimes=runtimes, cache_roots=cache_roots) for v in value]
    if not isinstance(value, dict):
        return value
    portable = runtimes is not None
    result = {k: _sources(v, memo, relay=(k == 'tool_relay'), runtimes=runtimes, cache_roots=cache_roots)
              for k, v in value.items()}
    if portable and 'python_runtime' in value:
        from harnessgrad.datasets.runtime import validate_binding
        binding = value['python_runtime']
        if not isinstance(binding, dict):
            raise ValueError('invalid verification runtime binding')
        binding = validate_binding(binding, binding.get('role'))
        artifact = runtimes.get(('execution', binding['runtime_id']))
        if artifact is None:
            raise ValueError('verification runtime lacks descriptor and requirements content evidence')
        result['python_runtime'] = {'role': binding['role'], 'content': artifact}
        if value.get('python') == binding['python']:
            result['python'] = {'runtime_executable': 'bin/python'}
        if isinstance(value.get('argv'), list) and value['argv'] and value['argv'][0] == binding['python']:
            result['argv'][0] = {'runtime_executable': 'bin/python'}
        if value.get('runtime_paths') == binding['runtime_paths']:
            result['runtime_paths'] = {'bound_runtime_prefixes': len(binding['runtime_paths'])}
    if ('from' in value and ('path' in value or 'dst' in value)
            and value.get('content') is None):
        if not isinstance(value['from'], str):
            raise ValueError('invalid verification source declaration')
        result['__source_content_sha256'] = _source(value['from'], memo, portable=portable,
                                                     cache_roots=cache_roots)
        if portable and Path(value['from']).is_absolute():
            del result['from']
    if 'read_paths' in value:
        if not isinstance(value['read_paths'], list) or any(not isinstance(x, str) for x in value['read_paths']):
            raise ValueError('invalid verification read paths')
        binding = value.get('python_runtime')
        venv = None
        if binding is not None:
            from harnessgrad.datasets.runtime import validate_binding
            binding = validate_binding(binding, 'service', value.get('python'))
            venv = Path(binding['path'])
        commitments = []
        system = tuple(Path(p) for p in ('/usr', '/bin', '/lib', '/lib64', '/etc'))
        for raw in value['read_paths']:
            path = Path(raw)
            if venv is not None and (path == venv or path.is_relative_to(venv)):
                # Admission inventories the venv; base-prefix contents are not sealed.
                if portable:
                    commitments.append({'runtime_relative': path.relative_to(venv).as_posix()})
                else:
                    commitments.append({'bound_runtime': binding['runtime_id']})
            else:
                if any(path == root or path.is_relative_to(root) for root in system):
                    raise ValueError('unbound system read path cannot be committed as source')
                commitment = {'source_sha256': _source(raw, memo, portable=portable, cache_roots=cache_roots)}
                if portable:
                    commitment['name'] = path.name if path.is_absolute() else raw
                commitments.append(commitment)
        result['__read_path_sha256'] = commitments
        if portable:
            del result['read_paths']
    if relay and isinstance(value.get('program'), str):
        program = Path(value['program'])
        result['__program_sha256'] = _source(program, memo, portable=portable, cache_roots=cache_roots)
        if portable and program.is_absolute():
            result['program'] = {'name': program.name}
    return result


def capture(tasks, scorable, setups=None, verifiers=None, envs=None, *, sandbox=True):
    return _capture(tasks, scorable, setups, verifiers, envs, sandbox=sandbox, runtimes=None)


def capture_content(tasks, scorable, setups=None, verifiers=None, envs=None, *, sandbox=True, runtimes=None):
    """Commit to known measurement inputs while normalizing verified host paths."""
    return _capture(tasks, scorable, setups, verifiers, envs, sandbox=sandbox,
                    runtimes={} if runtimes is None else runtimes)


def _capture(tasks, scorable, setups, verifiers, envs, *, sandbox, runtimes):
    if not isinstance(tasks, list) or not isinstance(scorable, dict):
        raise ValueError('invalid verification context inputs')
    if type(sandbox) is not bool:
        raise ValueError('verification context requires an explicit sandbox boolean')
    mappings = (setups or {}, verifiers or {}, envs or {})
    if any(not isinstance(x, dict) for x in mappings):
        raise ValueError('invalid verification context declarations')
    # Portable attestations read every byte even in a locally optimized experiment.
    cache_roots = _cache_roots(os.environ.get(_CACHE_ROOTS_ENV, '')) if runtimes is None else ()
    memo, result = {}, {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get('task_id'), str) or not task['task_id'] or task['task_id'] in result:
            raise ValueError('invalid verification context task')
        tid = task['task_id']
        try:
            policy = {'sandbox': sandbox}
            if sandbox and (mappings[2].get(tid) or {}).get('kind', 'files') == 'files':
                from eval.containers.sandbox import PLAN_VERSION
                policy['native_sandbox_plan'] = PLAN_VERSION
            value = {'measurement_protocol': MEASUREMENT_PROTOCOL,
                     'execution_policy': policy,
                     'task': task, 'scorable_present': tid in scorable, 'scorable': scorable.get(tid),
                     'setup': _sources(mappings[0].get(tid), memo, runtimes=runtimes, cache_roots=cache_roots),
                     'verifier': _sources(mappings[1].get(tid), memo, runtimes=runtimes, cache_roots=cache_roots),
                     'env': _sources(mappings[2].get(tid), memo, runtimes=runtimes, cache_roots=cache_roots)}
            if runtimes is not None:
                value['projection'] = 'harnessgrad.verification-content.v1'
            result[tid] = _digest(value)
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise ValueError(f'verification context unavailable for task {tid}: {type(exc).__name__}') from exc
    return create(result)
