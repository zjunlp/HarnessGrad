"""Admission for uploaded harnesses; HTTP and execution belong to the caller."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from collections import deque
import gzip
import hashlib
import io
import json
import importlib
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import re
import shutil
import struct
import tarfile
import tempfile
import sys
import subprocess
import threading
import time
import zipfile
import zlib
from uuid import uuid4

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from tools import validate_harness as validator
from scenarios import load as load_scenario
MAX_PAYLOAD = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_EXPANDED = 200 * 1024 * 1024
_METADATA = 2 * 1024 * 1024
_CHUNK = 64 * 1024


class SubmissionRejected(ValueError):
    """Structured admission problems, ready for the later HTTP adapter."""

    def __init__(self, problems: list[dict]) -> None:
        self.problems = problems
        super().__init__('; '.join(p['detail'] for p in problems))


def _reject(code: str, detail: str, field: str | None = None):
    problem = {'code': code, 'detail': detail}
    if field is not None:
        problem['field'] = field
    raise SubmissionRejected([problem])


def _member_path(name: str) -> str:
    try:
        name.encode('utf-8')
    except UnicodeError:
        _reject('unsafe-path', f'archive member is not a valid UTF-8 path: {name!r}')
    if (not name or '\0' in name or '\\' in name or ':' in name
            or PureWindowsPath(name).anchor or name.startswith('/')
            or '..' in PurePosixPath(name).parts):
        _reject('unsafe-path', f'archive member must stay inside the harness: {name!r}')
    parts = PurePosixPath(name).parts
    reserved = {'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if any(p.endswith(('.', ' ')) or p.split('.')[0].upper() in reserved for p in parts):
        _reject('unsafe-path', f'archive member has an ambiguous Windows path: {name!r}')
    return '/'.join(parts)


def _tar_headers(source):
    """Bound extension headers before tarfile can allocate their declared payload."""
    source.seek(0, 2)
    length = source.tell()
    source.seek(0)
    headers = metadata = 0
    while True:
        block = source.read(512)
        if len(block) != 512:
            _reject('bad-archive', 'tar is truncated or lacks its end markers')
        if block == b'\0' * 512:
            if source.read(512) != b'\0' * 512:
                _reject('bad-archive', 'tar is missing its second end marker')
            while chunk := source.read(_CHUNK):
                if chunk.strip(b'\0'):
                    _reject('bad-archive', 'nonzero data follows the tar end markers')
            source.seek(0)
            return
        info = tarfile.TarInfo.frombuf(block, 'utf-8', 'surrogateescape')
        headers += 1
        if headers > MAX_ENTRIES * 2 + 1:
            _reject('too-many-entries', f'tar has too many headers; limit {MAX_ENTRIES} entries')
        if info.size < 0:
            _reject('bad-archive', f'negative tar size for {info.name!r}')
        if info.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                         tarfile.GNUTYPE_LONGLINK):
            metadata += info.size
            if info.size > _CHUNK or metadata > _METADATA:
                _reject('bad-archive', 'tar extension metadata exceeds the bounded parser limit')
        elif info.size > MAX_EXPANDED:
            _reject('archive-too-large', f'tar member {info.name!r} exceeds 200 MiB')
        end = source.tell() + (info.size + 511) // 512 * 512
        if end > length:
            _reject('bad-archive', f'tar member {info.name!r} is truncated')
        source.seek(end)


def _zip_headers(source):
    source.seek(0, 2)
    length = source.tell()
    source.seek(max(0, length - 65557))
    tail = source.read()
    pos = tail.rfind(b'PK\x05\x06')
    if pos < 0 or len(tail) - pos < 22:
        _reject('bad-archive', 'zip end record is missing')
    disk, start_disk, count_disk, count, size, offset, comment = struct.unpack_from('<4H2LH', tail, pos + 4)
    if disk or start_disk or count_disk != count or pos + 22 + comment != len(tail):
        _reject('bad-archive', 'invalid or multi-volume zip end record')
    if count > MAX_ENTRIES:
        _reject('too-many-entries', f'zip exceeds {MAX_ENTRIES} entries')
    if size > _METADATA or offset + size > length:
        _reject('bad-archive', 'zip central metadata exceeds the bounded parser limit')
    source.seek(0)


def _charge(budget, amount):
    if budget is not None:
        budget(amount)


def _mkdir(path, budget=None):
    if path.is_dir():
        return
    _mkdir(path.parent,budget)
    _charge(budget,4096)
    path.mkdir(exist_ok=True)


@contextmanager
def _archive(path: Path, *, scratch_root=None, io_budget=None):
    with path.open('rb') as source:
        source.seek(0, 2)
        if source.tell() > MAX_PAYLOAD:
            _reject('payload-too-large', 'upload exceeds 20 MiB (20971520 bytes)')
        source.seek(0)
        magic = source.read(4)
        source.seek(0)
        if magic in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'):
            _zip_headers(source)
            with zipfile.ZipFile(source) as container:
                yield container, container.infolist()
        else:
            charged = 0
            try:
                with tempfile.TemporaryFile(prefix='hg-submission-tar-',dir=scratch_root) as plain:
                    if magic[:2] == b'\x1f\x8b':
                        with gzip.GzipFile(fileobj=source) as expanded:
                            total = 0
                            while chunk := expanded.read(_CHUNK):
                                total += len(chunk)
                                if total > MAX_EXPANDED + _METADATA + MAX_ENTRIES * 1024:
                                    _reject('archive-too-large', 'expanded tar stream exceeds its bounded size')
                                _charge(io_budget,len(chunk))
                                charged += len(chunk)
                                plain.write(chunk)
                        plain.seek(0)
                        payload = plain
                    else:
                        payload = source
                    _tar_headers(payload)
                    with tarfile.open(fileobj=payload, mode='r:') as container:
                        members = []
                        for member in container:
                            members.append(member)
                            if len(members) > MAX_ENTRIES:
                                _reject('too-many-entries', f'archive exceeds {MAX_ENTRIES} entries')
                        yield container, members
            finally:
                # The anonymous scratch file is closed before its reservation is released.
                _charge(io_budget,-charged)


def _harness_root(paths: dict[str, bool]) -> str:
    if 'harness.json' in paths:
        return ''
    roots = {p.split('/')[0] for p in paths if p}
    if len(roots) == 1:
        root = next(iter(roots))
        if paths.get(root, True):
            return root
    _reject('ambiguous-root', 'put harness.json at archive root or use one top-level directory')


def _member_chunks(container, member):
    if isinstance(container, tarfile.TarFile):
        with container.extractfile(member) as source:
            while chunk := source.read(_CHUNK):
                yield chunk
        return
    # ZipExtFile truncates output to the advertised file_size, even when the
    # deflate stream expands further and the CRC is forged for that prefix.
    # Let zipfile validate local headers/overlap, then check the whole bounded
    # compressed stream ourselves using the same standard-library codecs.
    with container.open(member):
        pass
    source = container.fp
    source.seek(member.header_offset)
    header = source.read(30)
    name_size, extra_size = struct.unpack_from('<HH', header, 26)
    source.seek(name_size + extra_size, 1)
    remaining, crc, count = member.compress_size, 0, 0
    inflater = zlib.decompressobj(-zlib.MAX_WBITS) if member.compress_type == zipfile.ZIP_DEFLATED else None
    while remaining:
        data = source.read(min(_CHUNK, remaining))
        if not data:
            _reject('bad-archive', f'zip compressed member is truncated: {member.filename!r}')
        remaining -= len(data)
        while data:
            chunk = inflater.decompress(data, _CHUNK) if inflater else data
            data = inflater.unconsumed_tail if inflater else b''
            count += len(chunk)
            crc = zlib.crc32(chunk, crc)
            if count > MAX_EXPANDED:
                _reject('archive-too-large', f'actual zip contents exceed 200 MiB: {member.filename!r}')
            if count > member.file_size:
                _reject('bad-archive', f'zip expands beyond its declared size: {member.filename!r}')
            if chunk:
                yield chunk
            if inflater and inflater.unused_data:
                _reject('bad-archive', f'extra data follows the zip deflate stream: {member.filename!r}')
    if inflater and not inflater.eof:
        _reject('bad-archive', f'zip deflate stream is incomplete: {member.filename!r}')
    if count != member.file_size or crc != member.CRC:
        _reject('bad-archive', f'zip size or CRC mismatch: {member.filename!r}')


def _scan_archive(path: Path, destination: Path | None = None, *, scratch_root=None, io_budget=None) -> str:
    """Scan all metadata before writing; stream/CRC-check every regular file."""
    with _archive(path,scratch_root=scratch_root,io_budget=io_budget) as (container, members):
        if len(members) > MAX_ENTRIES:
            _reject('too-many-entries', f'archive exceeds {MAX_ENTRIES} actual entries')
        paths, aliases, prefixes, entries = {}, set(), {}, []
        declared = 0
        for member in members:
            is_zip = isinstance(member, zipfile.ZipInfo)
            name = member.orig_filename if is_zip else member.name
            name = _member_path(name)
            directory = member.is_dir() if is_zip else member.isdir()
            if is_zip:
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                unsafe = kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                unsafe |= (kind == stat.S_IFDIR) != directory if kind else False
                if member.flag_bits & 1 or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _reject('bad-archive', f'encrypted or unsupported zip member: {name!r}')
                size = member.file_size
            else:
                mode = member.mode
                unsafe = not (member.isfile() or member.isdir()) or member.issparse()
                size = member.size
            if unsafe or mode & (stat.S_ISUID | stat.S_ISGID):
                _reject('unsafe-entry', f'only ordinary files/directories without setuid are allowed: {name!r}')
            if not name and directory:
                continue  # common explicit ./ root directory
            if not name or name.casefold() in aliases:
                _reject('unsafe-path', f'duplicate or ambiguous archive member: {name!r}')
            components = name.split('/')
            for i in range(1, len(components) + 1):
                prefix = '/'.join(components[:i])
                previous = prefixes.setdefault(prefix.casefold(), prefix)
                if previous != prefix:
                    _reject('unsafe-path', f'archive directory aliases differ only by case: {previous!r}, {prefix!r}')
            for existing in paths:
                if (existing.casefold() == name.casefold()
                        or (name.casefold().startswith(existing.casefold() + '/') and not paths[existing])
                        or (existing.casefold().startswith(name.casefold() + '/') and not directory)):
                    _reject('unsafe-path', f'archive file/directory collision: {name!r}')
            aliases.add(name.casefold())
            paths[name] = directory
            if size < 0 or (directory and size):
                _reject('bad-archive', f'invalid member size: {name!r}')
            declared += size
            if declared > MAX_EXPANDED:
                _reject('archive-too-large', 'declared archive contents exceed 200 MiB')
            entries.append((name, directory, member, size, mode & 0o111))
        root = _harness_root(paths)
        actual = 0
        for name, directory, member, size, executable in entries:
            target = destination / name if destination is not None else None
            if target is not None:
                if not target.resolve().is_relative_to(destination.resolve()):
                    _reject('unsafe-path', f'extraction target leaves staging: {name!r}')
                _mkdir(target.parent,io_budget)
            if directory:
                if target is not None:
                    _mkdir(target,io_budget)
                continue
            if target is not None:
                _charge(io_budget,4096)  # File/block rounding before opening even an empty file.
            with (target.open('xb') if target is not None else io.BytesIO()) as output:
                count = 0
                for chunk in _member_chunks(container, member):
                    count += len(chunk)
                    actual += len(chunk)
                    if actual > MAX_EXPANDED:
                        _reject('archive-too-large', 'actual extracted contents exceed 200 MiB')
                    if target is not None:
                        _charge(io_budget,len(chunk))
                        output.write(chunk)
                if count != size:
                    _reject('bad-archive', f'archive member has inconsistent size: {name!r}')
            if target is not None:
                target.chmod(0o644 | executable)
    return root


def inspect_archive(path: Path) -> list[dict]:
    """Read-only bounded admission of tar, tar.gz or zip; no user code executes."""
    try:
        _scan_archive(Path(path))
    except SubmissionRejected as exc:
        return exc.problems
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, zlib.error,
            RuntimeError, NotImplementedError) as exc:
        return [{'code': 'bad-archive', 'detail': f'archive could not be read: {exc}'}]
    return []


def _manifest_problems(root: Path, scenario: dict | None) -> tuple[dict, list[dict]]:
    problems = validator.check(root)
    for problem in problems:
        if problem['code'] == 'unsafe-path' and problem.get('field') in ('entrypoint', 'install'):
            problem['code'] = problem['field'] + '-missing'
    if problems:
        return {}, problems
    man = json.loads((root / 'harness.json').read_text(encoding='utf-8'))
    base = root / man.get('path', '.')
    for field in ('entrypoint', 'install'):
        if field not in man or (field == 'install' and man[field] is None):
            continue
        value = man[field]
        # Static A1 checks resolve ownership; submission paths must also be portable
        # ordinary files, with no symlink even when its target is inside this tree.
        if (not isinstance(value, str) or not value):
            problems.append({'code': 'manifest-invalid', 'detail': f'{field} must be a non-empty string', 'field': field})
        elif ('\\' in value or ':' in value or (base/value).is_symlink()
              or not (base/value).is_file()):
            problems.append({'code': field + '-missing', 'detail': f'{field} must name an ordinary file inside the harness: {value!r}', 'field': field})
    if scenario is not None:
        missing = set(scenario['env_kinds']) - set(man.get('env_kinds', ['files']) or [])
        if missing:
            problems.append({'code': 'env-kind-unsupported', 'detail': f'harness env_kinds must include scenario requirements: {sorted(missing)}', 'field': 'env_kinds'})
    for path in [root, *root.rglob('*')]:
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            problems.append({'code': 'unsafe-entry', 'detail': f'harness contains a nonordinary file: {path.relative_to(root)}'})
            break
    return man, problems


def inspect_harness(root: Path, *, scenario: dict | None = None,
                    platform_root: Path = PLATFORM_ROOT,
                    work_root: Path | None = None, io_budget=None) -> dict:
    """Return the original manifest, refusing static problems before smoke."""
    root = Path(root).resolve()
    man, problems = _manifest_problems(root, scenario)
    if problems:
        raise SubmissionRejected(problems)
    before = validator.source_identity(root)
    if work_root is None:
        with validator._smoke_temporary(None,'hg-submission-work-',io_budget) as (work,_):
            problems = validator.smoke(root, platform=platform_root, work_root=work,io_budget=io_budget)
    else:
        problems = validator.smoke(root, platform=platform_root, work_root=Path(work_root),io_budget=io_budget)
    if problems:
        raise SubmissionRejected(problems)
    if validator.source_identity(root) != before:
        _reject('unsafe-entry', 'harness source changed while running smoke')
    return man


def _extract_checked(snapshot: Path, destination: Path, *, scratch_root=None, io_budget=None) -> Path:
    try:
        root = _scan_archive(snapshot, destination,scratch_root=scratch_root,io_budget=io_budget)
    except SubmissionRejected:
        raise
    except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, zlib.error,
            RuntimeError, NotImplementedError) as exc:
        _reject('bad-archive', f'archive could not be extracted: {exc}')
    return destination / root


def _driver_argv(root: Path, scenario: str, run_id: str, *, platform: Path,
                 runs_root: Path, work_root: Path, budget_dir: Path, budget_capacity: int = 1) -> list[str]:
    return [sys.executable, str(platform/'driver.py'), '--harness-path', str(root),
            '--scenario', scenario, '--run-id', run_id, '--runs-root', str(runs_root),
            '--work-root', str(work_root), '--gpu-budget', str(budget_capacity),
            '--gpu-budget-dir', str(budget_dir), '--improver', 'none']


def new_submission_id() -> str:
    return 'sub-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8]


def archive_sha256(path: Path, *, snapshot: Path | None = None, io_budget=None) -> str:
    """One bounded original-byte identity for admission and private snapshots."""
    digest, total = hashlib.sha256(), 0
    try:
        source = Path(path).open('rb')
    except OSError as exc:
        _reject('bad-archive', f'upload could not be read: {exc}')
    if snapshot is not None:
        _charge(io_budget,4096)
    with source, (snapshot.open('xb') if snapshot is not None else io.BytesIO()) as output:
        while chunk := source.read(_CHUNK):
            total += len(chunk)
            if total > MAX_PAYLOAD:
                _reject('payload-too-large', 'upload exceeds 20 MiB (20971520 bytes)')
            digest.update(chunk)
            if snapshot is not None:
                _charge(io_budget,len(chunk))
                output.write(chunk)
    return digest.hexdigest()


def plan_submission(archive: Path, scenario_name: str, *, staging_root: Path,
                    runs_root: Path, work_root: Path, budget_dir: Path,
                    budget_capacity: int = 1, submission_id: str | None = None,
                    platform_root: Path = PLATFORM_ROOT, scratch_root=None, io_budget=None) -> dict:
    """Admit one fixed upload and return a CLI plan, never launch a driver.

    The caller owns the successful private stage (harness_dir's enclosing tree).
    On failure only this call's newly-created stage is removed. Availability,
    HTTP, queueing, deduplication and run receipts belong to the service layer.
    """
    platform = Path(platform_root).resolve()
    if type(budget_capacity) is not int or not 1 <= budget_capacity <= 9999:
        _reject('service-not-ready', 'shared budget capacity must be an integer from 1 to 9999')
    try:
        scenario, receipt = load_scenario(scenario_name, platform/'scenarios')
    except (ValueError, OSError) as exc:
        _reject('unknown-scenario', f'scenario {scenario_name!r} could not be loaded: {exc}', 'scenario')
    identity = submission_id or new_submission_id()
    if not isinstance(identity, str) or not re.fullmatch(r'sub-\d{8}T\d{6}Z-[0-9a-f]{8}', identity):
        _reject('service-not-ready', 'submission_id must use sub-YYYYMMDDTHHMMSSZ-<8 hex>')
    staging, runs, work, budget = (Path(p).resolve() for p in (staging_root, runs_root, work_root, budget_dir))
    if any(p == platform or p.is_relative_to(platform) for p in (staging, runs, work, budget)):
        _reject('service-not-ready', 'submission storage and execution roots must be outside the platform')
    if staging == work or staging.is_relative_to(work) or work.is_relative_to(staging):
        _reject('service-not-ready', 'staging and smoke work roots must be separate trees')
    stage = None
    try:
        _mkdir(staging,io_budget)
        _charge(io_budget,4096)
        stage = Path(tempfile.mkdtemp(prefix=identity + '-', dir=staging))
        snapshot = stage/'upload.bin'
        digest = archive_sha256(archive, snapshot=snapshot,io_budget=io_budget)
        payload = stage/'payload'
        _charge(io_budget,4096)
        payload.mkdir()
        if scratch_root is not None:
            scratch_root = Path(scratch_root)
            _mkdir(scratch_root,io_budget)
        root = _extract_checked(snapshot, payload,scratch_root=scratch_root,io_budget=io_budget)
        manifest = inspect_harness(root, scenario=scenario, platform_root=platform, work_root=work,io_budget=io_budget)
        tree = validator.source_identity(root)
        return {'submission_id': identity, 'run_id': identity, 'harness_dir': root, 'stage_dir': stage,
                'harness': manifest, 'scenario': scenario_name, 'scenario_receipt': receipt,
                'archive_sha256': digest, 'tree_sha256': tree['submission_sha256'],
                'argv': _driver_argv(root, scenario_name, identity, platform=platform,
                                     runs_root=runs, work_root=work, budget_dir=budget,
                                     budget_capacity=budget_capacity)}
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage)
        raise


def _scenario(name: str, platform: Path) -> tuple[dict, dict]:
    try:
        spec, receipt = load_scenario(name, platform/'scenarios')
    except (OSError, ValueError) as exc:
        _reject('unknown-scenario', f'scenario {name!r} is not readable: {exc}', 'scenario')
    if spec['rounds'] != 0 or spec.get('limits'):
        _reject('service-not-ready', f'scenario {name!r} needs unsupported rounds/limits; review configuration first', 'scenario')
    return spec, receipt


def _metadata(spec: dict) -> dict:
    """Trusted offline metadata only: never materialize tasks or invoke a scorer."""
    from data.scaffolds import registry
    tasks, _, split = registry.load_split(spec['dataset'])
    # Match the driver's explicit divisions. Declared splits are already checked
    # by load_split; an absent declaration must not create a study division.
    split_groups = (registry.load_split_groups(spec['dataset'], tasks)
                    if spec['split'] != 'declared' else None)
    if spec['split'] == 'default':
        split = registry.validate_split(
            spec['dataset'], tasks, registry.default_split(tasks, split_groups=split_groups),
            require_nonempty=(spec['side'],), split_groups=split_groups)
    elif spec['split'] == 'official':
        split = registry.validate_split(
            spec['dataset'], tasks, registry.official_split(spec['dataset'], tasks),
            require_nonempty=(spec['side'],), split_groups=split_groups)
    elif spec['split'] != 'declared':
        path = (PLATFORM_ROOT/spec['split']).resolve(strict=True)
        if not path.is_relative_to(PLATFORM_ROOT):
            raise ValueError('split file leaves platform')
        split = registry.validate_split(
            spec['dataset'], tasks, json.loads(path.read_text()),
            require_nonempty=(spec['side'],), split_groups=split_groups)
    ids = split[spec['side']] if split else [t['task_id'] for t in tasks]
    envs = registry.load_envs(spec['dataset'], task_ids=ids)
    kinds = {env.get('kind', 'files') for env in envs.values()}
    images = {env['image'] for env in envs.values() if env.get('kind') == 'exec'}
    return {'tasks': len(ids), 'env_kinds': sorted(kinds), 'images': sorted(images)}


def _probe_metadata(spec: dict, *, platform: Path, work: Path, env: dict) -> dict:
    from data.scaffolds import registry
    from eval.containers import sandbox
    module = importlib.import_module(registry.module_name(spec['dataset']))
    data_root = getattr(module, 'DATASET_ROOT', None)
    visible = (Path(data_root).resolve(),) if data_root is not None and Path(data_root).exists() else ()
    safe = {'PATH':'/usr/bin:/bin', 'HOME':'/tmp', 'TMPDIR':'/tmp', 'PYTHONDONTWRITEBYTECODE':'1',
            'HG_AGENT_BACKEND':'mock', 'HF_HUB_OFFLINE':'1', 'HF_DATASETS_OFFLINE':'1'}
    safe.update({k:v for k,v in env.items() if k.startswith('HG_') and not any(
        secret in k for secret in ('KEY','TOKEN','SECRET','PASSWORD')) and not k.startswith(('HG_AGENT_', 'HG_METHOD_', 'HG_IMPROVER_'))})
    safe = scenario_environment(spec, safe)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hg-submission-probe-', dir=work.parent) as task:
        proc = sandbox.run([sys.executable, str(platform/'console/submissions.py'),
                            '--probe-metadata', json.dumps(spec)], platform=platform,
                           work_root=work, task_dir=Path(task), repo=platform,
                           visible_paths=visible, env=safe, timeout_s=30, cwd=Path(task), network='none')
    if proc.returncode:
        if proc.stderr.startswith('bwrap:'):
            raise sandbox.SandboxUnavailable(proc.stderr[-1000:])
        raise ValueError('offline dataset load failed: ' + proc.stderr[-1000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def scenario_environment(spec: dict, env: dict) -> dict:
    """Bind reviewed pool once for both trusted readiness and real execution."""
    result = dict(env)
    selector = {'appworld':'HG_APPWORLD_POOL', 'alfworld':'HG_ALFWORLD_POOL'}.get(spec['dataset'])
    if selector and spec.get('pool'):
        result[selector] = spec['pool']
    elif spec.get('pool') not in (None,'train','eval'):
        _reject('service-not-ready', f"dataset {spec['dataset']} has no selector for pool {spec['pool']!r}")
    return result


def scenario_status(name: str, *, platform_root: Path = PLATFORM_ROOT,
                    work_root: Path, driver_env: dict[str, str]) -> dict:
    from data.scaffolds import registry
    from harnessgrad import config
    from eval.containers import sandbox, container
    platform, work = Path(platform_root).resolve(), Path(work_root).resolve()
    spec, _ = _scenario(name, platform)
    row = {k:spec[k] for k in ('name','dataset','split','side','sampling','rounds','trials','pool','note') if k in spec}
    row.update(tasks=None, expected_minutes=None, available=False, reason='dataset-not-provisioned')
    agent = config.load(driver_env).agent
    if spec['model']['backend'] == 'live' and (agent.backend == 'mock' or not all((agent.model, agent.base_url, agent.api_key))):
        row['reason'] = 'no-model-endpoint'
        return row
    demo = (spec['dataset'] == 'demo' and name == 'demo-smoke' and spec['side'] == 'train'
            and spec['model']['backend'] == 'mock')
    if not demo and spec['dataset'] not in registry.published():
        return row
    try:
        info = _probe_metadata(spec, platform=platform, work=work, env=driver_env)
        if info.get('images'):
            if not container.available():
                row['reason'] = 'sandbox-unavailable'
                return row
            for image in info['images']:
                proc = subprocess.run(['docker','image','inspect',image], capture_output=True, timeout=10)
                if proc.returncode:
                    row['reason'] = 'sandbox-unavailable'
                    return row
        row.update(tasks=info['tasks'], available=True, reason='ok')
    except sandbox.SandboxUnavailable:
        row['reason'] = 'sandbox-unavailable'
    except (OSError, ValueError, ImportError, subprocess.TimeoutExpired, IndexError):
        pass  # Unknown task count remains None, never a made-up zero.
    return row


def _object(path: Path, *, root: Path) -> dict:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('artifact path leaves its record')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must contain an object')
    return value


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _read_measured_point(run_dir: Path, *, receipt: dict) -> tuple[dict, dict]:
    """Validate one original point once for result and measured-time readers."""
    run = Path(run_dir)
    try:
        meta = _object(run/'run_meta.json', root=run)
        curve = run/'curve.jsonl'
        if not curve.resolve().is_relative_to(run.resolve()):
            raise ValueError('curve leaves run record')
        with curve.open(encoding='utf-8') as stream:
            points = [json.loads(line) for line in stream if line.strip()]
        if len(points) != 1 or not isinstance(points[0], dict):
            raise ValueError('expected exactly one round0 platform point')
        point = points[0]
        if not isinstance(point.get('identity',{}),dict):
            raise ValueError('point identity must be an object')
        source = meta.get('harness_source', {})
        source_digest = source.get('submission_sha256', source.get('sha256'))
        if ('submission_sha256' in source and 'sha256' in source
                and source['submission_sha256'] != source['sha256']):
            raise ValueError('conflicting directory identity fields')
        if ('harness_sha' in source
                and source['harness_sha'] != point.get('identity', {}).get('harness_sha')):
            raise ValueError('staged harness identity disagrees with the measured point')
        if (meta.get('run_id') != receipt['run_id'] or point.get('run_id') != receipt['run_id']
                or meta.get('scenario', {}).get('sha256') != receipt['scenario_receipt']['sha256']
                or source_digest != receipt['tree_sha256']
                or type(point.get('round')) is not int or point['round'] != 0 or point.get('side') != receipt['scenario']['side']
                or point.get('measured_by_platform') is not True or not _finite(point.get('score'))):
            raise ValueError('measurement identity, side or measured point is missing/mismatched')
        per_task = point.get('per_task')
        if not isinstance(per_task, dict) or not per_task or not all(_finite(v) for v in per_task.values()):
            raise ValueError('no finite measured per_task scores')
        invalid = point.get('invalid', {})
        if not isinstance(invalid, dict) or set(invalid) & set(per_task):
            raise ValueError('invalid tasks must be separate from scored tasks')
        for counter, mapping in (('n_scored', per_task), ('n_invalid', invalid),
                                 ('n_harness_failed', point.get('harness_failed', {})),
                                 ('n_timeout', point.get('timeouts', {}))):
            if not isinstance(mapping, dict) or type(point.get(counter)) is not int or point[counter] != len(mapping):
                raise ValueError(f'{counter} is missing or disagrees with its recorded map')
        for task_id, score in per_task.items():
            verdict = _object(run/'verdicts/round-0'/f'{task_id}.json', root=run)
            if verdict.get('task_id') != task_id or verdict.get('round') != 0 or verdict.get('score') != score:
                raise ValueError(f'verdict does not match measured task {task_id!r}')
        return meta,point
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        _reject('internal-error', f'run artifacts cannot establish a measured result: {exc}')


def read_submission_result(run_dir: Path, *, receipt: dict) -> dict:
    """Project one platform-owned point; never calculate a score or fill zeros."""
    meta,point=_read_measured_point(run_dir,receipt=receipt)
    warnings = []
    for counter in ('n_invalid','n_timeout','n_harness_failed'):
        if point[counter]:
            warnings.append(f"{counter}={point[counter]} in platform curve; preserve the recorded measurement semantics")
    if set(point.get('harness_failed', {})) - set(point['per_task']):
        warnings.append('H0 harness-failure diagnostics include tasks outside the scored selection')
    identity = {k:point.get('identity', {})[k] for k in
                ('agent_model','agent_backend','harness_sha','framework_version') if k in point.get('identity', {})}
    for key in ('platform_content_sha','framework_version','agent_backend'):
        if key in meta:
            identity.setdefault(key, meta[key])
    pool = receipt['scenario'].get('pool') or receipt['scenario']['side']
    return {'pools':{pool:{'n':len(set(point['per_task'])|set(point.get('invalid',{}))), 'measured':point['n_scored'],
                          'invalid':point['n_invalid'], 'harness_failed':point['n_harness_failed'],
                          'score':point['score']}}, 'warnings':warnings, 'identity':identity}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


_ID = re.compile(r'sub-\d{8}T\d{6}Z-[0-9a-f]{8}')


def _lease(path):
    """An open-file-description lease, retained by a driver via pass_fds."""
    import fcntl
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


def _process_identity(pid):
    path = Path('/proc')/str(pid)
    # comm can contain spaces and parentheses; fields after its final ')' are stable.
    fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid':int(pid), 'start_ticks':fields[19],
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'argv':(path/'cmdline').read_bytes().decode(errors='replace').split('\0')[:-1]}


def _process_matches(identity):
    if not isinstance(identity, dict):
        return False
    try:
        return _process_identity(identity['pid']) == identity
    except (FileNotFoundError, ProcessLookupError, KeyError, ValueError):
        return False


def _owner_state(identity):
    """Missing evidence is unknown, not proof that an owner has exited."""
    if (not isinstance(identity,dict) or type(identity.get('pid')) is not int
            or identity['pid']<=0 or not isinstance(identity.get('boot_id'),str)
            or not identity['boot_id'] or not isinstance(identity.get('start_ticks'),str)
            or not identity['start_ticks'].isdigit() or not isinstance(identity.get('argv'),list)
            or not identity['argv'] or not all(isinstance(arg,str) for arg in identity['argv'])):
        return 'unknown'
    try:
        current = _process_identity(identity['pid'])
    except (FileNotFoundError,ProcessLookupError):
        return 'gone'
    except (OSError,ValueError,IndexError):
        return 'unknown'
    if (current['boot_id'],current['start_ticks']) != (identity['boot_id'],identity['start_ticks']):
        return 'gone'
    return 'live' if current==identity else 'unknown'


def _referencing_processes(paths):
    """Conservative orphan-gate check, including surviving sandbox children."""
    paths = [str(path) for path in paths]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if any(path in arg for path in paths for arg in argv):
                return True
        except (FileNotFoundError,ProcessLookupError):
            continue
    return False


_TERMINAL = {'scored','failed','rejected','cancelled'}


def _time(value):
    if not isinstance(value,str):
        raise ValueError('timestamp is absent or not a string')
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp has no timezone')
    return result


def _atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                     prefix='.receipt-',delete=False) as file:
        temporary=Path(file.name)
        try:
            json.dump(data,file,ensure_ascii=False,allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _bytes(paths, *, excluded=()):
    """Count owned trees without following links or counting hardlinks twice."""
    seen = set()
    def count(path):
        if path in excluded:
            return 0
        try:
            info = path.lstat()
        except FileNotFoundError:
            return 0
        inode = info.st_dev,info.st_ino
        if inode in seen:
            return 0
        seen.add(inode)
        size = max(info.st_size,getattr(info,'st_blocks',0)*512)
        if stat.S_ISDIR(info.st_mode):
            try:
                entries = list(path.iterdir())
            except FileNotFoundError:
                return 0  # A concurrent sweeper already removed this owned tree.
            children = [child for child in entries if child not in excluded]
            return (0 if entries and not children else size) + sum(count(child) for child in children)
        return size
    return sum(count(path) for path in paths)


class SubmissionService:
    """A5 single-inflight runtime. A6 adds shared scheduling and retention."""

    def __init__(self, *, platform_root: Path, submissions_root: Path,
                 runs_root: Path, work_root: Path, budget_dir: Path, liveness=None,
                 budget_capacity=1, max_queue=16, quota_bytes=5*1024**3, retention_seconds=30*86400,
                 sweep_seconds=1, env_resolver=None):
        self.platform = Path(platform_root).resolve()
        self.root, self.runs, self.work, self.budget = (
            Path(p).resolve() for p in (submissions_root, runs_root, work_root, budget_dir))
        if type(budget_capacity) is not int or not 1 <= budget_capacity <= 9999:
            raise ValueError('budget_capacity must be an integer from 1 to 9999')
        self.budget_capacity = budget_capacity
        self._liveness = liveness
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._admission_lock = threading.Lock()
        self._leader = None
        self._started = False
        self._closing = False
        self._threads = []
        self._active = None
        self._orphans = {}
        self._queue = deque()
        #: Incoming directories whose cleanup a thread already owns. `sweep()` can be called
        #: by the maintenance thread and by a test or an operator at the same time, and two
        #: interleaved `_clear_incoming` calls on one directory can leave the audit saying
        #: `cleanup_done=False` for files that are already gone -- a later sweep completes it,
        #: but the record is briefly wrong. Serialised per directory rather than under the
        #: service lock, because the body does blocking I/O.
        self._clearing: set[str] = set()
        #: One sweep at a time. The maintenance thread and a caller (a test, an operator, a
        #: DELETE) can otherwise sweep concurrently, and two passes interleaving on one
        #: incoming directory can skip each other's work: `_referencing_processes` refuses to
        #: delete a directory another thread is reading, so a pass can leave the file in place
        #: while the other pass reports the audit complete. Sweeps are cheap and never need
        #: to overlap.
        self._sweep_lock = threading.Lock()
        self._envs = {}
        self._tokens = {}
        self._quota_epoch = 0
        self._used = 0
        self._admission_fault = None
        self._env_resolver = env_resolver
        for name, value in (('max_queue',max_queue),('quota_bytes',quota_bytes),
                            ('retention_seconds',retention_seconds),('sweep_seconds',sweep_seconds)):
            if isinstance(value,bool) or not _finite(value) or value <= 0:
                _reject('service-not-ready', f'{name} must be positive and finite')
            if name in ('max_queue','quota_bytes') and type(value) is not int:
                _reject('service-not-ready', f'{name} must be an integer')
            setattr(self,name,value)

    def start(self):
        with self._lock:
            if self._started:
                if self._closing:
                    _reject('service-not-ready','service is closing')
                return
            self._guard()
            control = self.root/'.control'
            control.mkdir(mode=0o700,exist_ok=True)
            if control.is_symlink():
                _reject('service-not-ready','control directory must not be a symlink')
            try:
                self._leader = _lease(control/'service.lock')
            except (OSError,ImportError) as exc:
                _reject('service-not-ready',f'cannot acquire service ownership: {exc}')
            if self._leader is None:
                _reject('service-not-ready','another service owns this submissions root')
            try:
                self._closing = False
                self.recover()
                self._started = True
                self._threads = []
                for target,name in ((self._dispatch,'submission-dispatch'),(self._maintain,'submission-maintenance')):
                    worker = threading.Thread(target=target,name=name,daemon=True)
                    worker.start()
                    self._threads.append(worker)
            except BaseException:
                self._closing = True
                self._condition.notify_all()
                self._started = False
                os.close(self._leader)
                self._leader = None
                _reject('service-not-ready','submission lifecycle could not start')

    def close(self):
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        for thread in self._threads:
            thread.join()
        with self._condition:
            while self._tokens:
                self._condition.wait(.1)
            if self._leader is not None:
                os.close(self._leader)
                self._leader = None
            self._started = False

    def recover(self):
        """Only called while owning the root; never re-run an interrupted gate."""
        if self._leader is None:
            _reject('service-not-ready','recovery requires service ownership')
        self._queue.clear()
        self._orphans.clear()
        incoming = self.root/'.incoming'
        if incoming.is_symlink():
            _reject('service-not-ready','incoming directory must not be a symlink')
        if incoming.exists():
            for directory in incoming.iterdir():
                if directory.is_symlink() or not directory.is_dir():
                    continue  # Unknown ownership is quarantined and continues to count.
                try:
                    audit = _object(directory/'request.json',root=directory)
                except (OSError,ValueError):
                    continue
                if audit.get('closed_at') or audit.get('interrupted_at'):
                    continue
                owner = audit.get('owner')
                state = _owner_state(owner)
                if state=='unknown':
                    continue
                if state=='live' or _referencing_processes((directory,)):
                    _reject('service-not-ready','an earlier upload owner/child is still alive')
                audit['interrupted_at'] = _utc()
                audit['error'] = {'code':'service-not-ready','detail':'upload interrupted before acceptance'}
                # Preserve the minimal audit before removing only this proven upload.
                _atomic_json(directory/'request.json',audit)
                try:
                    self._clear_incoming(directory,audit)
                except OSError:
                    pass  # Recorded pending cleanup is retried without dropping its quota usage.
        self._retry_incoming()
        recovered = []
        for data in self._receipts():
            identity = data['submission_id']
            if data['status'] in ('received','validating'):
                owner = data.get('admission_owner')
                state = _owner_state(owner)
                if state=='unknown':
                    _reject('service-not-ready','legacy incomplete admission needs an offline ownership review')
                if state=='live' or _referencing_processes((self.root/identity,self.work/identity)):
                    _reject('service-not-ready','an admission owner/child is still alive')
                self._update(identity,status='rejected',error={
                    'code':'service-not-ready','detail':'admission interrupted before acceptance'})
                self._remove_payload(identity)
                recovered.append(identity)
            elif data['status'] == 'queued':
                if data.get('started_at') or data.get('pid') or (self.runs/identity).exists():
                    self._update(identity,status='failed',error={
                        'code':'internal-error','detail':'queued receipt has evidence of an earlier launch; refusing replay'})
                else:
                    self._queue.append(identity)
            elif data['status'] == 'running':
                stamp = data.get('process')
                if stamp and _process_matches(stamp):
                    self._orphans[identity] = stamp
                elif not self._execution_idle() or _referencing_processes((self.root/identity,self.work/identity)):
                    self._orphans[identity] = None  # E.g. parent died before persisting PID.
                else:
                    self._finish_interrupted(data)
            elif data['status'] in _TERMINAL and not data.get('expires_at'):
                finished = data.get('finished_at') or data.get('scored_at') or next(
                    (h.get('at') for h in reversed(data.get('history',[])) if h.get('status') in _TERMINAL),None)
                try:
                    expires = (_time(finished)+timedelta(seconds=self.retention_seconds)).isoformat()
                    self._update(identity,finished_at=finished,expires_at=expires)
                except (TypeError,ValueError):
                    self._update(identity,retention_review='no trustworthy terminal timestamp; payload retained')
        self._queue = deque(sorted(self._queue,key=lambda identity:self._load(identity).get('queue_seq',0)))
        return recovered

    def _finish_interrupted(self, data):
        identity = data['submission_id']
        try:
            if data.get('exit_code') != 0:
                _reject('internal-error','driver ownership ended without a persisted successful exit; no replay')
            measured = read_submission_result(self.runs/identity,receipt=data)
            self._update(identity,status='scored',scored_at=_utc(),result=measured)
        except (OSError,ValueError) as exc:
            error = exc.problems[0] if isinstance(exc,SubmissionRejected) else {'code':'internal-error','detail':str(exc)}
            self._update(identity,status='failed',error=error)

    def _environment(self, env):
        # Keys and arbitrary environment variables are never written to a receipt.
        return {'python':sys.executable, **{name:env.get(name,'') for name in (
            'HG_AGENT_BACKEND','HG_AGENT_MODEL','HG_AGENT_BASE_URL',
            'HG_APPWORLD_POOL','HG_ALFWORLD_POOL',
            'HG_SANDBOX_MEMORY_MB','HG_SANDBOX_CPUS','HG_SANDBOX_PIDS')}}

    def _execution_idle(self):
        fd = _lease(self.root/'.control/execute.lock')
        if fd is None:
            return False
        os.close(fd)
        return True

    def _queue_position(self, identity):
        try:
            return list(self._queue).index(identity) + 1 + bool(self._active or self._orphans)
        except ValueError:
            return None

    def _visible(self, data, now=None):
        if not data.get('expires_at'):
            return True
        try:
            return (now or datetime.now(timezone.utc)) < _time(data['expires_at'])
        except (ValueError,TypeError):
            return True  # Invalid legacy dates require review, never guess from mtime.

    def _public_load(self, identity):
        data = self._load(identity)
        if not self._visible(data):
            _reject('submission-not-found','submission public retention has ended; receipt remains permanent')
        return data

    def delete(self, submission_id):
        with self._condition:
            data = self._public_load(submission_id)
            status = data['status']
            if status in ('received','validating'):
                _reject('service-not-ready','admission is still validating; retry after acceptance')
            if status=='running':
                _reject('running','submission is running; DELETE does not terminate a driver')
            if status=='queued':
                try:
                    self._update(submission_id,status='cancelled',cleanup_requested_at=_utc())
                finally:
                    # replace may publish cancellation before directory fsync fails.
                    if self._load(submission_id)['status']=='cancelled':
                        if submission_id in self._queue:
                            self._queue.remove(submission_id)
                        self._envs.pop(submission_id,None)
                        self._condition.notify_all()
                status='cancelled'
            elif status in _TERMINAL:
                if not data.get('cleanup_requested_at'):
                    self._update(submission_id,cleanup_requested_at=_utc())
            else:
                _reject('service-not-ready','submission state cannot be deleted')
            self._condition.notify_all()
            return 202,{'api_version':'1','submission_id':submission_id,'status':status}

    def sweep(self, *, now=None):
        with self._sweep_lock:
            return self._sweep(now=now)

    def _sweep(self, *, now=None):
        incoming_cleaned=self._retry_incoming()
        cleaned=[]
        for data in self._receipts():
            identity=data['submission_id']
            with self._lock:
                if (data['status'] not in _TERMINAL or self._visible(data,now) or data.get('cleanup_done')
                        or identity==self._active or identity in self._orphans
                        or any(t.get('identity')==identity for t in self._tokens.values())):
                    continue
                try:
                    if data.get('process') and _owner_state(data['process'])!='gone':
                        continue
                except OSError:
                    continue
                self._update(identity,payload_expired=True,cleanup_pending=True)
            try:
                self._remove_payload(identity)
                self._update(identity,cleanup_pending=False,cleanup_done=True,cleaned_at=_utc(),cleanup_error=None)
                cleaned.append(identity)
            except OSError as exc:
                self._update(identity,cleanup_error=str(exc))
        if cleaned or incoming_cleaned:
            self.usage_bytes()
        return {'cleaned':cleaned}

    def _maintain(self):
        while True:
            with self._condition:
                if self._closing:
                    return
                self._condition.wait(self.sweep_seconds)
                if self._closing:
                    return
            try:
                self.sweep()
            except (OSError,ValueError):
                pass  # Receipt/cleanup failures are retried; GET never does this I/O.

    def _dispatch(self):
        while True:
            with self._condition:
                if self._closing:
                    return
                for identity,stamp in list(self._orphans.items()):
                    try:
                        if (not _process_matches(stamp) and self._execution_idle()
                                and not _referencing_processes((self.root/identity,self.work/identity))):
                            self._finish_interrupted(self._load(identity))
                            self._orphans.pop(identity)
                    except OSError:
                        pass  # Inability to inspect a live owner fails closed.
                if not self._queue or self._orphans:
                    self._condition.wait(.1)
                    continue
            lease_fd = None
            try:
                self._guard()
                lease_fd = _lease(self.root/'.control/execute.lock')
                if lease_fd is None:
                    time.sleep(.05)
                    continue
                with self._condition:
                    if self._closing or not self._queue:
                        continue
                    identity = self._queue[0]
                    data = self._load(identity)
                    if data['status'] != 'queued':
                        if data['status'] == 'running':
                            # A failed launch-state write can publish before dequeueing.
                            # No driver was launched for an ID still in this deque.
                            self._update(identity,status='failed',error={
                                'code':'internal-error','detail':'dispatch launch transition failed; refusing replay'})
                        self._queue.popleft()
                        self._envs.pop(identity,None)
                        continue
                    env = self._envs.pop(identity,None)
                    if env is None:
                        try:
                            env = self._env_resolver(data['scenario']['name']) if self._env_resolver else None
                        except (OSError,ValueError,KeyError,TypeError):
                            env = None
                    if not isinstance(env,dict) or self._environment(env) != data.get('environment'):
                        self._update(identity,status='failed',error={
                            'code':'scenario-not-available','detail':'reviewed execution environment changed or cannot be reconstructed'})
                        self._queue.popleft()
                        continue
                    self._update(identity,status='running',started_at=_utc())
                    self._queue.popleft()
                    self._active = identity
                plan = {'submission_id':identity,'scenario':data['scenario']['name'],
                        'scenario_receipt':data['scenario_receipt'],'tree_sha256':data['tree_sha256'],
                        'harness_dir':Path(data['harness_dir']),'argv':data['argv']}
                self._run(plan,env,lease_fd)
            except (OSError,ValueError,KeyError) as exc:
                # A failed dispatch attempt must not kill the sole dispatcher.
                with self._condition:
                    if self._active:
                        try:
                            self._update(self._active,status='failed',error={'code':'internal-error','detail':str(exc)})
                        except (OSError,ValueError):
                            pass
                time.sleep(.1)
            finally:
                if lease_fd is not None:
                    os.close(lease_fd)
                with self._condition:
                    self._active = None
                    self._condition.notify_all()

    def _remove_payload(self, identity):
        """Rebuild owned paths; never use deletion paths supplied by a receipt."""
        directory = self._path(identity)
        targets = [p for p in directory.iterdir() if p.name != 'receipt.json']
        targets.extend((self.runs/identity,self.work/identity))
        for path in targets:
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def usage_bytes(self):
        while True:
            with self._lock:
                epoch = self._quota_epoch
                excluded = {token['directory'] for token in self._tokens.values()}
                for token in self._tokens.values():
                    if token.get('identity'):
                        identity = token['identity']
                        excluded.update((self.root/identity,self.runs/identity,self.work/identity))
            # Large recursive I/O is outside the scheduling lock. Receipts/control
            # metadata are permanent and excluded from the payload allowance.
            paths = [p for p in self.root.iterdir() if p.name != '.control']
            paths += [p for base in (self.runs,self.work) if base.exists()
                      for p in base.iterdir() if _ID.fullmatch(p.name)]
            excluded.update(self._metadata_paths())
            used = _bytes(paths,excluded=excluded)
            with self._lock:
                if epoch == self._quota_epoch:
                    self._used = used
                    return used

    def _metadata_paths(self):
        # Exempt exact server-owned control paths, never user payload basenames.
        metadata = {p/'receipt.json' for p in self.root.iterdir() if _ID.fullmatch(p.name)}
        incoming = self.root/'.incoming'
        if incoming.is_dir() and not incoming.is_symlink():
            metadata.update(p/'request.json' for p in incoming.iterdir()
                            if re.fullmatch(r'[0-9a-f]{32}',p.name) and p.is_dir() and not p.is_symlink())
        return metadata

    def _quota_reserve(self, token, delta_bytes):
        with self._lock:
            if self._tokens.get(token['nonce']) is not token:
                _reject('service-not-ready','upload reservation is no longer active')
            reserved = sum(t['reserved'] for t in self._tokens.values())
            if delta_bytes > 0 and self._used + reserved + delta_bytes > self.quota_bytes:
                _reject('service-not-ready',f'quota exceeded: used={self._used} reserved={reserved} '
                        f'requested={delta_bytes} limit={self.quota_bytes}')
            if token['reserved'] + delta_bytes < 0:
                _reject('internal-error','upload accounting underflow')
            token['reserved'] += delta_bytes

    def _request_audit(self, token, **extra):
        data = {'owner':token['owner'],'created_at':token['created_at'],
                'reserved_bytes':token['reserved'],'submission_id':token.get('submission_id'),**extra}
        path = token['directory']/'request.json'
        _atomic_json(path,data)

    def reserve_upload(self, envelope_bytes):
        self.start()
        if type(envelope_bytes) is not int or envelope_bytes < 0:
            _reject('bad-multipart','upload size must be a nonnegative integer')
        self.usage_bytes()
        with self._condition:
            if self._closing:
                _reject('service-not-ready','service is closing')
            pending = len(self._queue) + sum(t.get('slot',True) for t in self._tokens.values())
            if pending >= self.max_queue:
                _reject('queue-full',f'waiting capacity {pending}/{self.max_queue}')
            nonce = uuid4().hex
            token = {'nonce':nonce,'directory':self.root/'.incoming'/nonce,'reserved':0,
                     'owner':_process_identity(os.getpid()),'created_at':_utc(),'slot':True}
            self._tokens[nonce] = token
            self._quota_epoch += 1
            try:
                self._quota_reserve(token,envelope_bytes+4096)
                incoming = self.root/'.incoming'
                if incoming.is_symlink():
                    _reject('service-not-ready','incoming directory must not be a symlink')
                incoming.mkdir(mode=0o700,exist_ok=True)
                token['directory'].mkdir(mode=0o700)
                self._request_audit(token)
                return token
            except BaseException:
                # No body has been written yet; remove only this owned allocation.
                try:
                    if token['directory'].exists():
                        shutil.rmtree(token['directory'])
                except OSError:
                    pass  # Any residue becomes ordinary usage after token removal.
                self._tokens.pop(nonce)
                self._quota_epoch += 1
                raise

    def _quota_settle(self, token):
        identity = token['identity']
        actual = _bytes((self.root/identity,self.work/identity,self.runs/identity),
                        excluded={self.root/identity/'receipt.json'})
        incoming = _bytes((token['directory'],),excluded={token['directory']/'request.json'})
        self.usage_bytes()
        with self._lock:
            self._quota_reserve(token,actual+incoming-token['reserved'])
            token['reserved'] = incoming
            token['identity'] = None
            self._used += actual
            self._quota_epoch += 1

    def _clear_incoming(self, directory, audit):
        key = str(directory)
        with self._lock:
            if key in self._clearing:
                return False                       # another thread owns this cleanup
            if audit.get('cleanup_done'):
                return False                       # already complete; never regress the audit
            self._clearing.add(key)
        try:
            return self._clear_incoming_owned(directory, audit)
        finally:
            with self._lock:
                self._clearing.discard(key)

    def _clear_incoming_owned(self, directory, audit):
        audit.update(cleanup_pending=True,cleanup_done=False)
        _atomic_json(directory/'request.json',audit)
        with self._lock:
            self._quota_epoch += 1
        try:
            for path in directory.iterdir():
                if path.name == 'request.json':
                    continue
                if path.is_symlink() or not path.is_dir():
                    path.unlink(missing_ok=True)
                else:
                    shutil.rmtree(path)
        except OSError as exc:
            audit['cleanup_error']=str(exc)
            _atomic_json(directory/'request.json',audit)
            raise
        else:
            audit.update(cleanup_pending=False,cleanup_done=True,cleanup_error=None,cleaned_at=_utc())
            _atomic_json(directory/'request.json',audit)
            return True
        finally:
            with self._lock:
                self._quota_epoch += 1

    def _retry_incoming(self):
        cleaned=[]
        incoming=self.root/'.incoming'
        if self._leader is None or incoming.is_symlink() or not incoming.is_dir():
            return cleaned
        for directory in incoming.iterdir():
            if (not re.fullmatch(r'[0-9a-f]{32}',directory.name)
                    or directory.is_symlink() or not directory.is_dir()):
                continue
            with self._lock:
                if any(t['directory']==directory for t in self._tokens.values()):
                    continue
            try:
                audit=_object(directory/'request.json',root=directory)
                if audit.get('cleanup_done') or not (audit.get('closed_at') or audit.get('interrupted_at')):
                    continue
                owner=_owner_state(audit.get('owner'))
                if owner=='unknown' or (not audit.get('closed_at') and owner!='gone'):
                    continue
                if _referencing_processes((directory,)):
                    continue
                self._clear_incoming(directory,audit)
                cleaned.append(directory.name)
            except (OSError,ValueError):
                continue  # Unknown ownership or a transient cleanup failure remains counted.
        return cleaned

    def release_upload(self, token):
        if token is None:
            return
        with self._condition:
            if self._tokens.get(token['nonce']) is not token:
                return
        error = None
        try:
            self._request_audit(token,closed_at=_utc(),cleanup_pending=True)
            audit=_object(token['directory']/'request.json',root=token['directory'])
            self._clear_incoming(token['directory'],audit)
        except OSError as exc:
            error = str(exc)  # Residue is counted by the next scan, never assumed freed.
        finally:
            with self._condition:
                self._tokens.pop(token['nonce'],None)
                self._quota_epoch += 1
                self._condition.notify_all()
        if error:
            self.usage_bytes()

    def _guard(self):
        if self._admission_fault:
            _reject('service-not-ready',self._admission_fault)
        for path in (self.root, self.runs, self.work, self.budget):
            if path == self.platform or path.is_relative_to(self.platform):
                _reject('service-not-ready', 'all submission storage must be outside the platform')
        roots = (self.root,self.runs,self.work)
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for i,a in enumerate(roots) for b in roots[i+1:]):
            _reject('service-not-ready', 'submission, runs and work roots must be separate trees')
        for path in (self.root,self.runs,self.budget):
            if not path.is_dir() or not os.access(path, os.W_OK):
                _reject('service-not-ready', f'configured directory is absent or not writable: {path}')
        stamp = self.budget/'capacity'
        if stamp.exists():
            try:
                recorded = stamp.read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError):
                _reject('service-not-ready', 'cannot read shared budget capacity')
            if recorded != str(self.budget_capacity):
                _reject('service-not-ready', 'configured capacity conflicts with shared budget stamp')
        if self._liveness is None or not self._liveness()[0]:
            _reject('service-not-ready', 'cannot read /proc or no liveness guard is configured')
        if not (self.platform/'driver.py').is_file():
            _reject('service-not-ready', 'driver.py is missing')

    def _path(self, identity):
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            _reject('submission-not-found', 'submission was not found')
        path = self.root/identity
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            _reject('submission-not-found', 'submission was not found')
        return path

    def _load(self, identity):
        path = self._path(identity)
        try:
            data = _object(path/'receipt.json', root=path)
        except FileNotFoundError:
            _reject('submission-not-found', 'submission was not found')
        except (OSError, ValueError):
            _reject('internal-error', 'submission receipt is unreadable')
        if data.get('submission_id') != identity:
            _reject('internal-error', 'submission receipt identity mismatch')
        return data

    def _save(self, data):
        directory = self._path(data['submission_id'])
        _atomic_json(directory/'receipt.json',data)

    def _update(self, identity, **changes):
        with self._lock:
            data = self._load(identity)
            if 'status' in changes:
                data.setdefault('history', []).append({'status':changes['status'], 'at':_utc()})
                if changes['status'] in _TERMINAL and not data.get('finished_at'):
                    changes['finished_at'] = _utc()
                    changes['expires_at'] = (_time(changes['finished_at']) +
                        timedelta(seconds=self.retention_seconds)).isoformat()
            data.update(changes)
            self._save(data)
            return data

    def _receipts(self):
        if not self.root.is_dir():
            return []
        return [self._load(p.name) for p in self.root.iterdir() if _ID.fullmatch(p.name)
                and p.is_dir() and (p/'receipt.json').exists()]

    def submit(self, archive: Path, scenario_name: str, *, label='', contact='', driver_env: dict,
               upload_token=None) -> tuple[int, dict]:
        digest = archive_sha256(archive)
        with self._lock:
            self._duplicate(digest,scenario_name)
        own_token = upload_token is None
        token = upload_token or self.reserve_upload(Path(archive).stat().st_size)
        try:
            with self._admission_lock:
                return self._admit(archive,scenario_name,label=label,contact=contact,
                                   driver_env=driver_env,token=token)
        finally:
            if own_token:
                self.release_upload(token)

    def _duplicate(self, digest, scenario_name):
        for prior in self._receipts():
            if prior.get('archive_sha256') == digest and prior.get('scenario',{}).get('name') == scenario_name:
                raise SubmissionRejected([{'code':'duplicate-submission',
                    'detail':'same archive and scenario already exist; receipt deduplication is permanent',
                    'existing_submission_id':prior['submission_id']}])

    def _admit(self, archive, scenario_name, *, label, contact, driver_env, token):
        self._guard()
        spec, scene_receipt = _scenario(scenario_name, self.platform)
        if ((label and not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', label)) or len(contact) > 200):
            _reject('bad-multipart', 'invalid label/contact')
        digest = archive_sha256(archive)
        identity = None
        plan = None
        with self._lock:
            self._duplicate(digest,scenario_name)
        try:
            # Readiness is not admission: it runs only trusted metadata code.
            ready = scenario_status(scenario_name, platform_root=self.platform,
                                    work_root=self.work/'readiness', driver_env=driver_env)
            if not ready['available']:
                _reject('scenario-not-available', ready['reason'], 'scenario')
            for _ in range(5):
                candidate = new_submission_id()
                directory = self._path(candidate)
                if (self.runs/candidate).exists() or (self.work/candidate).exists():
                    continue
                try:
                    directory.mkdir(mode=0o700)
                except FileExistsError:
                    continue
                identity = candidate  # Own it before any failure may mutate a receipt.
                with self._lock:
                    token['identity'] = identity
                    token['submission_id'] = identity
                    self._quota_epoch += 1
                self._request_audit(token)
                break
            if identity is None:
                _reject('service-not-ready', 'could not reserve a fresh submission ID')
            data = {'submission_id':identity, 'run_id':identity, 'created_at':_utc(),
                    'status':'received', 'scenario':spec, 'scenario_receipt':scene_receipt,
                    'archive_sha256':digest, 'label':label, 'contact':contact,
                    'admission_owner':token['owner'],
                    'history':[{'status':'received','at':_utc()}]}
            self._save(data)
            self._update(identity, status='validating')
            plan = plan_submission(archive, scenario_name, staging_root=directory/'staging',
                                   runs_root=self.runs, work_root=self.work/identity/'runtime',
                                   budget_dir=self.budget, budget_capacity=self.budget_capacity,
                                   submission_id=identity, platform_root=self.platform,
                                   scratch_root=self.work/identity/'scratch',
                                   io_budget=lambda delta:self._quota_reserve(token,delta))
            if plan['archive_sha256'] != digest or plan['scenario_receipt']['sha256'] != scene_receipt['sha256']:
                _reject('bad-archive', 'upload or reviewed scenario changed during admission')
            self._quota_settle(token)
            with self._condition:
                seq = max((r.get('queue_seq',0) for r in self._receipts()),default=0)+1
                self._update(identity, status='queued', queued_at=_utc(), queue_seq=seq,
                             tree_sha256=plan['tree_sha256'],harness=plan['harness'],
                             harness_dir=str(plan['harness_dir']),argv=plan['argv'],
                             environment=self._environment(driver_env))
                self._envs[identity] = dict(driver_env)
                self._queue.append(identity)
                token['slot'] = False
                ack = {'api_version':'1', 'submission_id':identity, 'run_id':identity, 'status':'queued',
                       'scenario':scenario_name, 'archive_sha256':digest, 'tree_sha256':plan['tree_sha256'],
                       'created_at':data['created_at'], 'queue_position':self._queue_position(identity)}
                self._condition.notify_all()
            return 202, ack
        except BaseException as exc:
            cleanup_safe = False
            try:
                if identity is not None:
                    path = self._path(identity)
                    if not (path/'receipt.json').exists():
                        # Only an owned, empty, never-persisted directory is removed.
                        try:
                            path.rmdir()
                            cleanup_safe = True
                        except OSError:
                            pass  # Preserve any incomplete artifacts for A6 recovery.
                    else:
                        try:
                            with self._condition:
                                current = self._load(identity)
                                if (current['status'] in ('received','validating') or
                                        (current['status']=='queued' and identity not in self._queue
                                         and identity!=self._active)):
                                    problem = (exc.problems[0] if isinstance(exc, SubmissionRejected) else
                                               {'code':'service-not-ready','detail':f'admission could not finish: {exc}'})
                                    self._update(identity, status='rejected', error=problem)
                                cleanup_safe = self._load(identity)['status'] in _TERMINAL
                        except (OSError,SubmissionRejected):
                            # A replace may have succeeded before directory fsync failed.
                            # Never remove a payload while its durable receipt says queued.
                            try:
                                cleanup_safe = self._load(identity)['status'] in _TERMINAL
                            except (OSError,SubmissionRejected):
                                pass
                            if not cleanup_safe:
                                self._admission_fault = 'admission persistence is uncertain; stop and review retained receipts'
                if identity is not None and cleanup_safe:
                    try:
                        self._remove_payload(identity)
                    except OSError:
                        pass  # Keep incomplete evidence; do not mask the admission error.
            finally:
                with self._condition:
                    self._condition.notify_all()
            raise

    def _run(self, plan, driver_env, lease_fd):
        identity = plan['submission_id']
        try:
            # Concurrent drivers coordinate via their shared slot budget. The
            # service's own execution lease still prevents duplicate queue owners.
            self._guard()
            # A queued receipt survives restarts. Do not execute stale lease
            # parameters after the operator changes deployment capacity/root.
            argv = plan.get('argv') or []
            try:
                if (argv.count('--gpu-budget') != 1 or argv.count('--gpu-budget-dir') != 1
                        or argv[argv.index('--gpu-budget') + 1] != str(self.budget_capacity)
                        or argv[argv.index('--gpu-budget-dir') + 1] != str(self.budget)):
                    raise ValueError('shared budget configuration changed while queued')
            except (ValueError, IndexError):
                _reject('service-not-ready', 'shared budget configuration changed or is invalid')
            _, scene = _scenario(plan['scenario'], self.platform)
            if scene['sha256'] != plan['scenario_receipt']['sha256'] or validator.source_identity(plan['harness_dir'])['submission_sha256'] != plan['tree_sha256']:
                _reject('scenario-not-available', 'admitted source/scenario identity changed')
            directory = self._path(identity)
            with (directory/'driver.log').open('ab') as log:
                process = subprocess.Popen(plan['argv'], cwd=self.platform, env=driver_env,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                           pass_fds=(lease_fd,))
                try:
                    try:
                        stamp = _process_identity(process.pid)
                    except FileNotFoundError:
                        stamp = None  # An already-exited child is still reaped below.
                    self._update(identity, pid=process.pid,process=stamp)
                finally:
                    # Child ownership survives a receipt write failure. Never release
                    # capacity while a successfully launched driver is still alive.
                    code = process.wait()
            self._update(identity, exit_code=code)
            if code:
                _reject('run-refused:driver-exit', f'driver exited {code}; see submission log')
            data = self._load(identity)
            measured = read_submission_result(self.runs/identity, receipt=data)
            self._update(identity, status='scored', scored_at=_utc(), result=measured)
        except Exception as exc:
            current = self._load(identity)
            if current['status'] == 'queued':
                self._update(identity, status='running', started_at=_utc())
            error = exc.problems[0] if isinstance(exc, SubmissionRejected) else {'code':'internal-error', 'detail':str(exc)}
            self._update(identity, status='failed', error=error)

    def get(self, submission_id):
        with self._lock:
            data = self._public_load(submission_id)
        result = {k:data[k] for k in ('submission_id','run_id','archive_sha256','created_at',
                  'queued_at','started_at','scored_at') if k in data}
        result['scenario'] = {k:data['scenario'][k] for k in
                              ('name','dataset','split','side','pool','sampling','rounds','trials') if k in data['scenario']}
        with self._lock:
            position = self._queue_position(submission_id) if data['status']=='queued' else None
        result.update(api_version='1', status=data['status'], queue_position=position, error=data.get('error'))
        if 'harness' in data:
            man = data['harness']
            result['harness'] = {k:man[k] for k in ('name','version','entrypoint') if k in man}
            result['harness'].update(declared_env_kinds=man.get('env_kinds', ['files']), tree_sha256=data['tree_sha256'])
        if data['status'] == 'scored':
            result['result'] = data['result']
        result['evidence'] = {'run_dir':str(self.runs/submission_id), 'curve':str(self.runs/submission_id/'curve.jsonl'),
                              'per_task':str(self.runs/submission_id/'verdicts'),
                              'log':f'/api/submissions/{submission_id}/log',
                              'receipt':str(self._path(submission_id)/'receipt.json')}
        return result

    def list(self, *, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 200:
            _reject('bad-query', 'limit must be in 1..200', 'limit')
        with self._lock:
            rows = sorted((r for r in self._receipts() if self._visible(r)),
                          key=lambda r:(r['created_at'],r['submission_id']), reverse=True)
        if cursor is not None:
            ids = [r['submission_id'] for r in rows]
            if cursor not in ids:
                _reject('bad-query', 'cursor does not identify a visible submission', 'cursor')
            rows = rows[ids.index(cursor)+1:]
        items = []
        for row in rows[:limit]:
            item = {k:row[k] for k in ('submission_id','status','label','created_at','scored_at') if k in row}
            item.update(scenario=row['scenario']['name'], score=None, pools={})
            if 'harness' in row:
                item['harness'] = {k:row['harness'][k] for k in ('name','version')}
            if row['status'] == 'scored':
                item['pools'] = {k:v['score'] for k,v in row['result']['pools'].items()}
            items.append(item)
        return {'api_version':'1','items':items,'next_cursor':items[-1]['submission_id'] if len(rows)>limit else None}

    def log(self, submission_id, *, tail=20000):
        if type(tail) is not int or not 1 <= tail <= 200000:
            _reject('bad-query', 'tail must be in 1..200000', 'tail')
        data = self._public_load(submission_id)
        if data['status'] in ('received','validating','rejected'):
            _reject('not-started', 'driver log is readable only after queueing')
        path = self._path(submission_id)/'driver.log'
        if not path.exists():
            return b''
        if not path.resolve().is_relative_to(self._path(submission_id)):
            _reject('internal-error', 'log leaves submission record')
        with path.open('rb') as file:
            file.seek(0, 2)
            file.seek(max(0,file.tell()-tail))
            return file.read(tail)

    def scenarios(self, *, driver_env, env_for_scenario=None):
        rows = []
        for path in sorted((self.platform/'scenarios').glob('*.json')):
            env = env_for_scenario(path.stem) if env_for_scenario is not None else driver_env
            row = scenario_status(path.stem, platform_root=self.platform,
                                  work_root=self.work/'readiness', driver_env=env)
            _, scene = _scenario(path.stem, self.platform)
            prior = sorted((r for r in self._receipts() if r['status'] == 'scored'
                            and r['scenario_receipt']['sha256'] == scene['sha256']),
                           key=lambda r:r.get('scored_at',''), reverse=True)
            if prior:
                try:
                    _,point = _read_measured_point(self.runs/prior[0]['run_id'],receipt=prior[0])
                    timing = point.get('cumulative',point.get('cost',{}))
                    seconds = timing.get('wall_clock_s') if isinstance(timing,dict) else None
                    if _finite(seconds) and seconds >= 0:
                        row['expected_minutes'] = seconds/60
                except (OSError,ValueError):
                    pass
            rows.append(row)
        return {'api_version':'1','items':rows}


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--probe-metadata':
    print(json.dumps(_metadata(json.loads(sys.argv[2]))))
