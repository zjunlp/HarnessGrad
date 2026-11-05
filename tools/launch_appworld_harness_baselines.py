#!/usr/bin/env python3
"""Launch one reviewed, identity-bound AppWorld H0 attempt (dry-run by default).

Model fingerprints are operator-provided provenance; equality is not a live
endpoint attestation. Reviewed runtime files are rehashed locally. Unknown
fingerprints refuse execution/recovery instead of silently matching aliases.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import platform
import re
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.appworld_campaign_state import (IDENTITY_SCHEMA, POLICY, POOLS, RecoveryError,
    digest, file_hash, read, selection_identity, validate_identity)
from tools.validate_harness import source_identity


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def select_task_ids(cohort, requested):
    if not requested or any(not tid for tid in requested):
        raise RecoveryError('task ID subset must not be empty')
    if len(requested) != len(set(requested)):
        raise RecoveryError('task ID subset contains a duplicate')
    if set(requested) - set(cohort):
        raise RecoveryError('task IDs are outside the fixed pilot cohort')
    return [tid for tid in cohort if tid in set(requested)]


def current_identity(expected, bindings):
    """Recompute local code/data pins and require declared external identities."""
    from data.datasets.appworld import BUNDLE, split_ids, SERVICE_IMAGE, VERIFIER_IMAGE, IMAGE
    actual = copy.deepcopy(expected)
    harness = ROOT / 'base_harness' / expected['harness']
    manifest = read(harness / 'harness.json')
    if bindings.get('model') != expected['model']:
        raise RecoveryError('model deployment binding differs from reviewed identity')
    runtime = runtime_binding(manifest, bindings.get('harness_runtimes', {}).get(expected['harness']))
    # These are explicit external provenance documents, not fetched service attestations.
    actual.update(schema=IDENTITY_SCHEMA, policy=POLICY,
                  platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, encoding='ascii', errors='strict').strip(),
                  source_sha256=file_hash(BUNDLE), pool_ids_sha256=digest(split_ids(BUNDLE)[expected['pool']]),
                  harness_sha256=source_identity(harness)['submission_sha256'],
                  manifest_sha256=file_hash(harness/'harness.json'), runtime_sha256=digest(runtime))
    if any(not isinstance(image, str) or not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image) for image in (IMAGE, SERVICE_IMAGE, VERIFIER_IMAGE)):
        raise RecoveryError('AppWorld actor/service/verifier images must be immutable digests')
    verify = ROOT / 'data/datasets/appworld/verify'
    actual['scorer_sha256'] = digest({'files': {str(p.relative_to(verify)): file_hash(p)
        for p in sorted(verify.rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
        'actor_image': IMAGE, 'service_image': SERVICE_IMAGE, 'verifier_image': VERIFIER_IMAGE})
    if manifest.get('role') == 'control':
        raise RecoveryError('oracle/control cannot enter a harness campaign')
    validate_identity(actual)
    return actual


def check_identity(expected, actual):
    if expected != actual:
        fields = sorted(k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k))
        raise RecoveryError('measurement identity mismatch: ' + ', '.join(fields))


def runtime_binding(manifest, binding):
    """Rehash the reviewed entry/version/lock files; never just compare claims.

    Scope is selected files, not complete transitive dependency attestation. The
    manifest must review which files pin this runtime. Every declared runtime
    root needs coverage, and Python itself is always pinned automatically.
    """
    if not isinstance(binding, dict) or set(binding) != {'files', 'environment'}:
        raise RecoveryError('runtime needs reviewed file inventory and environment')
    files, environment = binding['files'], binding['environment']
    allowed = {'HG_BUN_BINARY', 'HG_BUN_CACHE', 'HG_OVERLAY_ROOT', 'HG_AGENT_NODE',
               'HG_AGENT_DSH', 'HG_AGENT_OPENCODE_BUN', 'HG_AGENT_CODEX',
               'HG_EGRESS_PROXY', 'HG_EGRESS_BIND', 'HG_EGRESS_PORT', 'HG_EGRESS_UPSTREAM'}
    if not isinstance(environment, dict) or set(environment) - allowed or any(not isinstance(v, str) or not v for v in environment.values()):
        raise RecoveryError('runtime environment must use explicitly reviewed runtime/proxy keys')
    if not isinstance(files, list):
        raise RecoveryError('runtime files must be an inventory')
    observed, seen = [], set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise RecoveryError('runtime entry requires path and sha256')
        path = Path(item['path']).expanduser()
        if not path.is_absolute() or str(path) in seen or not path.is_file():
            raise RecoveryError('runtime path must be unique, absolute and readable')
        seen.add(str(path))
        actual = file_hash(path)
        if actual != item['sha256']:
            raise RecoveryError(f'runtime file content mismatch: {path}')
        observed.append({'path': str(path), 'resolved_path': str(path.resolve()), 'sha256': actual})
    for root in manifest.get('runtime_paths') or []:
        path = Path(root).expanduser().resolve()
        if not any(Path(item['resolved_path']) == path or path in Path(item['resolved_path']).parents for item in observed):
            raise RecoveryError(f'no reviewed entry/version/lock file for runtime root: {path}')
    if manifest.get('install') and not any(Path(item['path']).name == 'PROVENANCE.json' for item in observed):
        raise RecoveryError('dependency overlay requires its reviewed PROVENANCE.json inventory entry')
    for key, value in environment.items():
        if key in {'HG_BUN_BINARY', 'HG_AGENT_NODE', 'HG_AGENT_DSH', 'HG_AGENT_OPENCODE_BUN', 'HG_AGENT_CODEX'}:
            if not any(Path(item['resolved_path']) == Path(value).expanduser().resolve() for item in observed):
                raise RecoveryError(f'configured runtime executable is outside the inventory: {key}')
    return {'scope': 'reviewed-runtime-files-v1; transitive closure not asserted',
            'files': sorted(observed, key=lambda x: x['path']), 'environment': environment,
            'python': {'executable': str(Path(sys.executable).resolve()),
                       'sha256': file_hash(Path(sys.executable)), 'version': platform.python_version()}}


def dataset_environment():
    from data.datasets import appworld
    return {'HG_DATA_APPWORLD': str(appworld.DATASET_ROOT.resolve()),
            'HG_APPWORLD_IMAGE': appworld.IMAGE, 'HG_APPWORLD_SERVICE_IMAGE': appworld.SERVICE_IMAGE,
            'HG_APPWORLD_VERIFIER_IMAGE': appworld.VERIFIER_IMAGE}


def execution_environment(identity, bindings):
    # Start from a small OS allowlist. Dataset study selectors, saved native
    # credentials/settings, stale CLI overrides, PYTHONPATH and budgets cannot
    # silently change a reviewed run. Explicit runtime settings are hash-bound.
    os_keys = ('HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')
    env = {key: os.environ[key] for key in os_keys if key in os.environ}
    env['PATH'] = str(Path(sys.executable).parent) + ':/usr/local/bin:/usr/bin:/bin'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # This reviewed campaign requires actual per-task verifier evidence before
    # a round can finish; scalar progress alone is never recoverable scoring.
    env['HG_TASK_VERDICT_JOURNAL'] = '1'
    env.update(dataset_environment())
    manifest = read(ROOT/'base_harness'/identity['harness']/'harness.json')
    runtime = runtime_binding(manifest, bindings.get('harness_runtimes', {}).get(identity['harness']))
    if digest(runtime) != identity['runtime_sha256']:
        raise RecoveryError('runtime changed after identity review')
    env.update(runtime['environment'])
    model, budget = identity['model'], identity['budget']
    env.update(TMPDIR='/tmp', HG_AGENT_BACKEND=model['backend'], HG_AGENT_MODEL=model['name'],
               HG_AGENT_BASE_URL=model['endpoint'], HG_AGENT_API_KEY=os.environ.get('HG_AGENT_API_KEY') or 'local',
               HG_AGENT_MAX_OUTPUT_TOKENS=str(budget['output_tokens']),
               HG_AGENT_MAX_TOKENS=str(budget['output_tokens']),
               HG_AGENT_DEADLINE_S=str(budget['deadline_s']),
               HG_AGENT_TIMEOUT_S=str(budget['request_timeout_s']),
               HG_APPWORLD_POOL='test' if identity['pool'] == 'train' else identity['pool'])
    env.update(budget['native_settings'])
    return env


def command_for(identity, selected, receipt):
    budget = identity['budget']
    return [sys.executable, str(ROOT/'driver.py'), '--dataset', 'appworld',
            '--harness-path', str(ROOT/'base_harness'/identity['harness']),
            '--side', identity['side'], '--sampling', 'all', '--tasks', ','.join(selected),
            '--rounds', '0', '--trials', str(budget['trials']), '--seed', str(budget['seed']),
            '--jobs', str(budget['jobs']), '--improver', 'none', '--fail-on-incomplete',
            '--run-id', receipt.name, '--runs-root', str(receipt/'runs'),
            '--work-root', str(Path('/tmp') / ('hg-' + receipt.name + '-work'))]


def ensure_frozen():
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=ROOT, text=True, errors='surrogateescape')
    if dirty.strip():
        raise RecoveryError('execution requires a clean frozen worktree')


def observe_service(identity):
    with urllib.request.urlopen(identity['model']['endpoint'] + '/models', timeout=3) as response:
        catalog = json.load(response)
    if identity['model']['name'] not in [row.get('id') for row in catalog.get('data', [])]:
        raise RecoveryError('configured model is absent from endpoint catalog')


def run_driver(command, identity, bindings, log, lock_fd):
    return subprocess.run(command, cwd=ROOT, env=execution_environment(identity, bindings),
                          stdout=log, stderr=subprocess.STDOUT, pass_fds=(lock_fd,))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--bindings', type=Path, required=True, help='reviewed external model/runtime fingerprints')
    p.add_argument('--harness', required=True)
    p.add_argument('--pool', choices=POOLS, required=True)
    p.add_argument('--task-ids', required=True)
    p.add_argument('--receipt-dir', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        selection = read(args.selection)
        expected = selection_identity(selection, args.harness, args.pool)
        selected = select_task_ids(selection['pools'][args.pool]['selected'], args.task_ids.split(','))
        bindings = read(args.bindings)
        check_identity(expected, current_identity(expected, bindings))
        receipt = args.receipt_dir.resolve()
        if not receipt.name.startswith('appworld-') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in receipt.name):
            raise RecoveryError('receipt basename must be a fresh safe appworld run ID')
        command = command_for(expected, selected, receipt)
        plan = {'schema': 'harnessgrad.appworld-launch.v2', 'run_id': receipt.name,
                'harness': args.harness, 'pool': args.pool, 'selected': selected,
                'selection_sha256': digest(selection), 'measurement_identity': expected,
                'measurement_identity_sha256': digest(expected), 'command': command,
                'bindings_sha256': file_hash(args.bindings),
                'external_identity_evidence': 'operator-declared fingerprints; not live attestation'}
        if not args.execute:
            print(json.dumps(plan, indent=2)); return 0
        ensure_frozen()
        observe_service(expected)
        receipt.mkdir(parents=True, exist_ok=False)
        with (receipt/'.attempt.lock').open('x') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            write(receipt/'launch.json', plan)
            with (receipt/'driver.log').open('x') as log:
                result = run_driver(command, expected, bindings, log, lock.fileno())
            write(receipt/'exit.json', {'exit_code': result.returncode})
        return result.returncode
    except (RecoveryError, OSError, ValueError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
