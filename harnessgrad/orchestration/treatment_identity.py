"""Operator-declared treatment commitments; no transitive-dependency or authenticity claim."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

KEY = 'treatment_identity'
SCHEMA = 'harnessgrad.treatment-manifest.v1'
COVERAGE = 'operator_declared_files_and_configuration'
_FIELDS = {'version', 'coverage', 'manifest_sha256', 'files', 'configuration_sha256',
           'runtime_sha256', 'resolved_sha256', 'sha256'}
_EARLY = ('coverage', 'manifest_sha256', 'files', 'configuration_sha256')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'[A-Za-z0-9_.-]{1,120}\Z')


class TreatmentRefused(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _open(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise TreatmentRefused('treatment files require absolute paths without traversal')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       dir_fd=parent)
    finally:
        os.close(parent)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file(path, *, contents=False):
    fd = _open(path)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise TreatmentRefused('treatment source must be a regular file')
        if contents and before.st_size > 1024 * 1024:
            raise TreatmentRefused('treatment manifest exceeds one MiB')
        digest, blocks = hashlib.sha256(), []
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
            if contents:
                blocks.append(block)
                if sum(map(len, blocks)) > 1024 * 1024:
                    raise TreatmentRefused('treatment manifest exceeds one MiB')
        check = _open(path)
        try:
            if _stamp(before) != _stamp(os.fstat(fd)) or _stamp(before) != _stamp(os.fstat(check)):
                raise TreatmentRefused('treatment source changed during capture')
        finally:
            os.close(check)
        return ({'sha256': digest.hexdigest(), 'path_sha256': _digest(str(path)),
                 'executable': bool(before.st_mode & 0o111)}, b''.join(blocks))
    finally:
        os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TreatmentRefused('duplicate treatment manifest key')
        result[key] = value
    return result


def capture(manifest_path, *, runtime=None, resolved=None, environment=None):
    if manifest_path is None:
        return None
    try:
        _, raw = _file(Path(manifest_path).absolute(), contents=True)
        manifest = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files', 'configuration', 'environment'}
                or manifest['schema'] != SCHEMA or not isinstance(manifest['files'], dict)
                or 'wrapper' not in manifest['files'] or not isinstance(manifest['configuration'], dict)
                or not isinstance(manifest['environment'], list)
                or any(not isinstance(key, str) or not key for key in manifest['environment'])
                or len(set(manifest['environment'])) != len(manifest['environment'])):
            raise TreatmentRefused('invalid treatment manifest schema')
        files = {}
        for name, path in manifest['files'].items():
            if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(path, str):
                raise TreatmentRefused('invalid named treatment file')
            try:
                files[name], _ = _file(path)
            except (OSError, ValueError) as exc:
                raise TreatmentRefused('declared treatment file unavailable or changed: ' + name) from exc
        env = os.environ if environment is None else environment
        payload = {'version': 1, 'coverage': COVERAGE, 'manifest_sha256': _digest(manifest),
            'files': files, 'configuration_sha256': _digest({
                'configuration': manifest['configuration'],
                'environment': {key: env.get(key) for key in manifest['environment']}}),
            'runtime_sha256': _digest(runtime), 'resolved_sha256': _digest(resolved)}
        return {**payload, 'sha256': _digest(payload)}
    except TreatmentRefused:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TreatmentRefused('treatment manifest invalid or unavailable') from exc


def validate(record):
    if (not isinstance(record, dict) or set(record) != _FIELDS
            or type(record['version']) is not int or record['version'] != 1
            or record['coverage'] != COVERAGE or not isinstance(record['files'], dict)
            or 'wrapper' not in record['files']):
        raise TreatmentRefused('invalid recorded treatment identity')
    for name, fact in record['files'].items():
        if (not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(fact, dict)
                or set(fact) != {'sha256', 'path_sha256', 'executable'}
                or type(fact['executable']) is not bool
                or any(not isinstance(fact[k], str) or not _HEX.fullmatch(fact[k]) for k in ('sha256', 'path_sha256'))):
            raise TreatmentRefused('invalid recorded treatment file identity')
    for key in ('manifest_sha256', 'configuration_sha256', 'runtime_sha256', 'resolved_sha256', 'sha256'):
        if not isinstance(record[key], str) or not _HEX.fullmatch(record[key]):
            raise TreatmentRefused('invalid recorded treatment digest')
    payload = {key: value for key, value in record.items() if key != 'sha256'}
    if _digest(payload) != record['sha256']:
        raise TreatmentRefused('recorded treatment digest mismatch')
    return record


def require_same(recorded, current, *, declared=False, early=False):
    if recorded is None and current is None and not declared:
        return
    if recorded is None or current is None:
        raise TreatmentRefused('treatment identity or manifest is missing; cannot reuse this treatment')
    validate(recorded)
    validate(current)
    fields = _EARLY if early else _FIELDS
    if any(recorded[key] != current[key] for key in fields):
        raise TreatmentRefused('treatment changed; resume requires original files, configuration and identity')
