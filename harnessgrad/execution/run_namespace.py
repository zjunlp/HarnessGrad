"""Exclusive run namespaces and one-use, configuration-bound preparations.

A preparation reserves its record; execution reserves the work root. Only a complete reservation with unchanged metadata
and no execution artifacts may be claimed; legacy preparations require a fresh ID.
A preparation binds configuration only, not dataset or verifier contents. It contains
no measurement or verification context; execution captures current inputs after
selection. Check-only neither reserves a namespace nor proves an execution context.
"""
import hashlib
import json
import os
import re
from pathlib import Path


class NamespaceRefused(ValueError):
    pass


def _paths(args, run_id):
    if not run_id or Path(run_id).name != run_id or run_id in ('.', '..'):
        raise NamespaceRefused('run ID must be a single path component')
    run = Path(args.runs_root).resolve() / run_id
    work = Path(args.work_root).resolve() / run_id
    if run == work:
        raise NamespaceRefused('run and work namespaces must be distinct')
    return run, work


def effective_hg_environment(environ=None):
    """Bind runtime choices while recording only credential presence in the digest input."""
    source = os.environ if environ is None else environ
    result = {}
    for name, value in source.items():
        if not name.startswith('HG_') or name.startswith('HG_CONSOLE_'):
            continue
        if re.search(r'(?:^|_)(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)(?:_|$)', name):
            result[name] = {'present': bool(value)}
        elif name.startswith('HG_DATA_') or name.endswith('_ROOT'):
            result[name] = str(Path(value).expanduser().resolve()) if value else ''
        else:
            result[name] = value
    return result


def fingerprint(args, effective_config=None):
    fields = dict(args)
    for key in ('check_only', 'prepare_only', 'check_net', 'run_id'):
        fields.pop(key, None)
    fields['runs_root'] = str(Path(fields['runs_root']).resolve())
    fields['work_root'] = str(Path(fields['work_root']).resolve())
    if fields.get('rounds') is None:
        fields['rounds'] = 0 if fields.get('sampling') in ('diagnostic', 'pilot_10pct') else 3
    return hashlib.sha256(json.dumps({"arguments": fields, "effective_config": effective_config,
                                      "hg_environment": effective_hg_environment()},
                                     sort_keys=True, default=str).encode()).hexdigest()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def available(args, run_id, config, allow_prepared=True):
    run, work = _paths(args, run_id)
    if not (run.exists() or run.is_symlink() or work.exists() or work.is_symlink()):
        return 'fresh'
    if allow_prepared:
        try:
            if run.is_symlink() or work.is_symlink():
                raise ValueError('symlink')
            if {p.name for p in run.iterdir()} != {'run_meta.json', 'prepared-reservation.json'}:
                raise ValueError('unexpected run files')
            if work.exists() or work.is_symlink():
                raise ValueError('workspace already exists')
            if any((run / name).is_symlink() or not (run / name).is_file()
                   for name in ('run_meta.json', 'prepared-reservation.json')):
                raise ValueError('invalid preparation files')
            reservation = json.loads((run / 'prepared-reservation.json').read_text())
            if reservation != {'version': 1, 'config': config,
                               'metadata_sha256': _digest(run / 'run_meta.json')}:
                raise ValueError('reservation mismatch')
            return 'prepared'
        except (OSError, ValueError, TypeError):
            pass
    raise NamespaceRefused(f'namespace for {run_id!r} already exists or its preparation differs; '
                           'use a fresh --run-id or a valid --resume')


def reserve(args, run_id, *, prepare=False):
    run, work = _paths(args, run_id)
    run.parent.mkdir(parents=True, exist_ok=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    try:
        run.mkdir()
    except FileExistsError as exc:
        raise NamespaceRefused(f'run namespace {run} already exists; use a fresh --run-id') from exc
    if prepare:
        if work.exists() or work.is_symlink():
            run.rmdir()
            raise NamespaceRefused(f'work namespace {work} already exists')
        return run
    try:
        work.mkdir()
    except OSError as exc:
        run.rmdir()
        raise NamespaceRefused(f'work namespace {work} cannot be reserved; use a fresh --run-id') from exc
    return run


def finish_preparation(run, config):
    reservation = {'version': 1, 'config': config, 'metadata_sha256': _digest(run / 'run_meta.json')}
    with (run / 'prepared-reservation.json').open('x') as stream:
        json.dump(reservation, stream, sort_keys=True)


def claim(args, run_id, config):
    status = available(args, run_id, config)
    if status == 'fresh':
        return reserve(args, run_id)
    run, work = _paths(args, run_id)
    try:
        work.mkdir()
    except FileExistsError as exc:
        raise NamespaceRefused(f'preparation for {run_id!r} is already claimed') from exc
    # Another claimant cannot reserve the workspace; recheck the record before use.
    try:
        reservation = json.loads((run / 'prepared-reservation.json').read_text())
        if ({p.name for p in run.iterdir()} != {'run_meta.json', 'prepared-reservation.json'}
                or reservation != {'version': 1, 'config': config,
                                   'metadata_sha256': _digest(run / 'run_meta.json')}):
            raise NamespaceRefused('prepared record changed while claiming it')
    except (OSError, ValueError) as exc:
        raise NamespaceRefused('prepared record changed while claiming it') from exc
    return run
