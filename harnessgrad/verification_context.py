"""Private, content-bound measurement inputs; no source bytes leave this module.

This is an equality commitment, not authentication of code, data or runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

KEY = 'verification_context'
ROOT = Path(__file__).resolve().parents[1]
# Private-input restoration now replaces destination inodes. Measurements made
# under the previous staging contract cannot prove the same scoring conditions.
MEASUREMENT_PROTOCOL = 'harnessgrad.measurement.v2'
_HASH = re.compile(r'[0-9a-f]{64}\Z')


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


def _stamp(path):
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _source(path, memo):
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
    active = set()

    def walk(p):
        before = _stamp(p)
        mode = before[2]
        if stat.S_ISLNK(mode):
            target = p.resolve(strict=True)
            if not target.is_relative_to(boundary):
                raise ValueError('verification source symlink escapes its source root')
            result = {'link': os.readlink(p), 'target': walk(target)}
        elif stat.S_ISREG(mode):
            with p.open('rb') as stream:
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            result = {'file': digest.hexdigest(), 'executable': bool(mode & 0o111)}
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


def _sources(value, memo, *, relay=False):
    if isinstance(value, list):
        return [_sources(v, memo) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: _sources(v, memo, relay=(k == 'tool_relay')) for k, v in value.items()}
    if ('from' in value and ('path' in value or 'dst' in value)
            and value.get('content') is None):
        if not isinstance(value['from'], str):
            raise ValueError('invalid verification source declaration')
        result['__source_content_sha256'] = _source(value['from'], memo)
    if 'read_paths' in value:
        if not isinstance(value['read_paths'], list) or any(not isinstance(x, str) for x in value['read_paths']):
            raise ValueError('invalid verification read paths')
        binding = value.get('python_runtime')
        venv = None
        if binding is not None:
            from harnessgrad.dataset_runtime import validate_binding
            binding = validate_binding(binding, 'service', value.get('python'))
            venv = Path(binding['path'])
        commitments = []
        system = tuple(Path(p) for p in ('/usr', '/bin', '/lib', '/lib64', '/etc'))
        for raw in value['read_paths']:
            path = Path(raw)
            if venv is not None and (path == venv or path.is_relative_to(venv)):
                # Admission inventories the venv; base-prefix contents are not sealed.
                commitments.append({'bound_runtime': binding['runtime_id']})
            else:
                if any(path == root or path.is_relative_to(root) for root in system):
                    raise ValueError('unbound system read path cannot be committed as source')
                commitments.append({'source_sha256': _source(raw, memo)})
        result['__read_path_sha256'] = commitments
    if relay and isinstance(value.get('program'), str):
        result['__program_sha256'] = _source(value['program'], memo)
    return result


def capture(tasks, scorable, setups=None, verifiers=None, envs=None, *, sandbox=True):
    if not isinstance(tasks, list) or not isinstance(scorable, dict):
        raise ValueError('invalid verification context inputs')
    if type(sandbox) is not bool:
        raise ValueError('verification context requires an explicit sandbox boolean')
    mappings = (setups or {}, verifiers or {}, envs or {})
    if any(not isinstance(x, dict) for x in mappings):
        raise ValueError('invalid verification context declarations')
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
                     'setup': _sources(mappings[0].get(tid), memo),
                     'verifier': _sources(mappings[1].get(tid), memo),
                     'env': _sources(mappings[2].get(tid), memo)}
            result[tid] = _digest(value)
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise ValueError(f'verification context unavailable for task {tid}: {type(exc).__name__}') from exc
    return create(result)
