"""Fixed native benchmark scorers with private, bounded artifact export.

This route is platform code, never an arbitrary host command supplied by a
harness. Private cases and sealed relay records are read from trusted VERIFY
inputs; only declared deliverables are copied out of actor storage. Individual
scorers own native runtime admission, independent grading and cleanup receipts.
This bridge alone does not attest pristine-offline image/ABI closure.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from eval.verification.isolation import record_class

#: `dataset -> (the module that runs its native scorer, the sealed-episode case file)`.
#: The module is imported lazily inside `_native_module` and named by a literal `from`
#: statement there: `INTERFACE.md` R1 forbids the platform from reaching a *method* by a
#: dynamic import, and a rule that has to distinguish "dataset scorer" from "method" by
#: reading a string cannot, so the dispatch is spelled out instead of built from a name.
ROUTES = {
    'swe_bench': ('swe_native', 'swe_episode'),
    'harvey_lab': ('harvey_native', None),
    'deepswe': ('harbor_verify', 'native_episode'),
    'jobbench': ('harbor_verify', 'native_episode'),
    'swe_bench_pro': ('harbor_verify', 'native_episode'),
}


def _native_module(key: str):
    """The scorer module for one route, imported on demand and by name.

    Imported here rather than at module load because a native scorer pulls its own
    dependencies (pandas, a pinned evaluator), and the verification path is only taken by
    the five datasets above.
    """
    if key in ('swe_native', 'harvey_native'):
        from data.scaffolds.native_research import harvey_native, swe_native
        return {'swe_native': swe_native, 'harvey_native': harvey_native}[key]
    from data.scaffolds.native_harbor_runtime import verify as harbor_verify
    return harbor_verify
MAX_BYTES, MAX_FILES = 64 << 20, 10000
MAX_RECORD_BYTES = 128 << 20  # Native artifacts are base64 inside sealed JSON.


def _relative(value):
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or any(p in ('', '.', '..') for p in value.split('/')) or '\\' in value):
        raise ValueError('native input path must be a safe relative path')
    return value


def _read(path, limit=16 << 20):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('native input must be a bounded regular single-link file')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('native input changed during export')
        return data


def export_output(workdir, destination, *, deadline):
    """Copy output/** using dirfds: no symlinks, devices, hardlinks or traversal."""
    evidence = []
    total = 0
    entries = 0
    def visit(source_fd, target, relative, depth=0):
        nonlocal total, entries
        if depth > 32:
            raise ValueError('native output directory depth exceeds contract')
        target.mkdir(mode=0o700)
        # scandir is incremental, so a huge single directory is bounded too.
        with os.scandir(source_fd) as listing:
            names = []
            for item in listing:
                entries += 1
                if entries > MAX_FILES or time.monotonic() >= deadline:
                    raise ValueError('native output export exceeds entry/deadline contract')
                names.append(item.name)
        for name in sorted(names):
            if time.monotonic() >= deadline:
                raise TimeoutError('native artifact export timed out')
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            rel = relative + '/' + name
            if stat.S_ISDIR(info.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
                try:
                    visit(fd, target / name, rel, depth + 1)
                finally:
                    os.close(fd)
            elif stat.S_ISREG(info.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_size > MAX_BYTES - total or len(evidence) >= MAX_FILES):
                        raise ValueError('native output export exceeds file/byte contract')
                    raw = stream.read(MAX_BYTES - total + 1)
                    after = os.fstat(stream.fileno())
                    if (len(raw) > MAX_BYTES - total or len(raw) != info.st_size
                            or (info.st_mtime_ns, info.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
                        raise ValueError('native output changed during export')
                (target / name).write_bytes(raw)
                total += len(raw)
                evidence.append({'path': rel, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            else:
                raise ValueError('native output contains a linked or special file')
    base_fd = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            output_fd = os.open('output', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        except FileNotFoundError:
            (destination / 'output').mkdir(mode=0o700)
        else:
            try:
                visit(output_fd, destination / 'output', 'output')
            finally:
                os.close(output_fd)
    finally:
        os.close(base_fd)
    return {'files': evidence, 'bytes': total, 'entries': entries,
            'limits': {'bytes': MAX_BYTES, 'entries': MAX_FILES, 'depth': 32}}


def verify(workdir, spec, *, sandbox, task_id, run_id=None, trial_id=None,
           timeout_s, env=None, service_specs=None, running=None, **options):
    run_id, trial_id = run_id or 'direct', trial_id or 'trial-1'
    evidence = {'measured': False}
    verdict = {'kind': 'command', 'passed': False, 'score': None, 'outcome': 'unmeasured', 'evidence': evidence}
    try:
        from data.scaffolds.runtime_extensions import validate_native_benchmark
        validate_native_benchmark(spec)
        declaration = spec['native_benchmark']
        name = declaration['dataset']
        if name not in ROUTES or not sandbox or (env or {}).get('kind', 'files') != 'files':
            raise ValueError('native benchmark requires a supported sandboxed files route')
        # The pure fixed-dispatch contract above owns argv, binding and spec
        # conflicts; keep only live execution-state restrictions at this layer.
        if service_specs or running is not None:
            raise ValueError('native benchmark cannot combine with another execution route')
        binding = spec['scorer_input']
        if not task_id or binding['task_id'] != task_id or binding['sha256'] != declaration['case_sha256']:
            raise ValueError('native scorer case does not belong to selected task')
        case_path = _relative(binding['path'])
        inputs = {}
        for item in spec.get('inputs', []):
            key = _relative(item['path'])
            if key in inputs:
                raise ValueError('duplicate native verifier input')
            if ('from' in item) == ('content' in item):
                raise ValueError('private native input requires exactly one trusted source')
            if 'from' in item:
                source = Path(item['from'])
                if (not source.is_absolute() or any(p.is_symlink() for p in (source, *source.parents))
                        or source.resolve().is_relative_to(Path(workdir).resolve())):
                    raise ValueError('private native input cannot come from actor storage')
            elif not isinstance(item['content'], str) or len(item['content'].encode()) > 16 << 20:
                raise ValueError('private native content must be bounded text')
            inputs[key] = item
        case_entry = inputs[case_path]
        raw = _read(case_entry['from']) if 'from' in case_entry else case_entry['content'].encode()
        sha = hashlib.sha256(raw).hexdigest()
        case = json.loads(raw)
        if sha != binding['sha256'] or case.get('task_id') != task_id:
            raise ValueError('native private case digest/task mismatch')
        evidence['scorer_input'] = {**binding, 'expected_sha256': sha, 'verified': True}
        deadline = time.monotonic() + timeout_s
        module_name, record_path = ROUTES[name]
        with tempfile.TemporaryDirectory(prefix='hg-native-benchmark-') as temp:
            destination = Path(temp)
            target = destination / case_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if record_path is not None:
                entry = inputs[record_path]
                if entry.get('immutable') is not True:
                    raise ValueError('native episode must be a sealed immutable verifier input')
                episode = _read(Path(entry['from']) / 'episode.json', MAX_RECORD_BYTES)
                sealed = json.loads(episode)
                expected = {'task_id': task_id, 'run_id': run_id or 'direct', 'trial_id': trial_id or 'trial-1'}
                if (sealed.get('schema') != 'harnessgrad.interaction-record.v1'
                        or any(sealed.get('binding', {}).get(k) != v for k, v in expected.items())):
                    raise ValueError('native episode execution binding mismatch')
                (destination / record_path).mkdir()
                (destination / record_path / 'episode.json').write_bytes(episode)
                evidence['export'] = {'path': record_path + '/episode.json', 'bytes': len(episode),
                    'sha256': hashlib.sha256(episode).hexdigest(), 'binding': expected}
            else:
                evidence['export'] = export_output(workdir, destination, deadline=deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('native export exhausted verifier deadline')
            module = _native_module(module_name)
            result = module.run(spec, destination, task_id=task_id, run_id=run_id,
                                trial_id=trial_id, timeout_s=remaining)
            if not isinstance(result, dict):
                raise ValueError('native scorer returned no verdict')
            detail = result.get('evidence', {})
            if not isinstance(detail, dict):
                raise ValueError('native scorer returned malformed evidence')
            verdict.update(result)
            verdict['evidence'] = {**detail, **evidence, 'measured': detail.get('measured') is True}
            if detail.get('measured') is not True:
                verdict.update(score=None, passed=False, outcome='unmeasured',
                    unmeasured=result.get('unmeasured') or 'native evaluation did not complete')
    except Exception as exc:
        # Evaluator exceptions may contain hidden labels; only the type is public.
        verdict.update(score=None, passed=False, outcome='unmeasured',
                       unmeasured='native benchmark unavailable: ' + type(exc).__name__)
        verdict['evidence'] = {**evidence, 'measured': False, 'error': type(exc).__name__}
    return record_class(verdict, spec, 'unknown')
