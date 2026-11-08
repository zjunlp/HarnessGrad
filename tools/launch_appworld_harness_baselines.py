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
import hashlib
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
    digest, file_hash, read, selection_identity, validate_identity, runtime_commitment)
from tools.validate_harness import check as check_harness, source_identity, filesystem_identity
from harnessgrad.paths import main_checkout
from harnessgrad.env_file import REVIEWED_ENVIRONMENT_ONLY

_IMAGE_ABI_CACHE = {}


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


def harness_source(name, bindings):
    """Select a canonical prepared candidate without changing the frozen platform."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise RecoveryError('invalid harness name')
    sources = bindings.get('harness_sources', {})
    if not isinstance(sources, dict) or any(not isinstance(key, str) or not re.fullmatch(
            r'[a-z0-9][a-z0-9_-]*', key) for key in sources):
        raise RecoveryError('harness_sources must map harness names to prepared paths')
    if name not in sources:
        return ROOT / 'base_harness' / name
    value = sources[name]
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RecoveryError('prepared harness source must be an absolute canonical directory')
    path = Path(value).resolve(strict=True)
    if Path(value) != path or not path.is_dir():
        raise RecoveryError('prepared harness source must be an absolute canonical directory')
    for platform_root in {ROOT.resolve(), main_checkout(ROOT).resolve()}:
        if path.is_relative_to(platform_root) or platform_root.is_relative_to(path):
            raise RecoveryError('prepared harness source must be outside the platform checkouts')
    if (path / '.git').exists() or (path / '.git').is_symlink():
        raise RecoveryError('prepared harness source must not contain Git metadata')
    problems = check_harness(path)
    if problems:
        raise RecoveryError('invalid prepared harness: ' + '; '.join(p['detail'] for p in problems))
    return path


def harness_inputs(name, bindings):
    """Read a consistent candidate and bind external source selection into runtime."""
    path = harness_source(name, bindings)
    before = source_identity(path)
    filesystem = filesystem_identity(path)
    manifest_bytes = (path / 'harness.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('name') != name or manifest.get('role') == 'control':
        raise RecoveryError('harness manifest name/role differs from selected harness')
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    runtime = copy.deepcopy(runtime_binding(manifest, bindings.get('harness_runtimes', {}).get(name)))
    runtime['launch_entry'] = {'python': str(Path(sys.executable).resolve()),
                             'python_sha256': runtime['python']['sha256'] if isinstance(runtime.get('python'), dict) else file_hash(Path(sys.executable)),
                             'driver': str((ROOT/'driver.py').resolve()),
                             'driver_sha256': file_hash(ROOT/'driver.py')}
    runtime['harness_filesystem'] = filesystem
    if source_identity(path) != before or filesystem_identity(path) != filesystem:
        raise RecoveryError('harness source changed while binding its identity')
    runtime['harness_source'] = {'path': str(path.resolve()),
                                 'submission_sha256': before['submission_sha256'],
                                 'manifest_sha256': manifest_sha}
    return path, manifest, before, manifest_sha, runtime


def current_identity(expected, bindings):
    """Recompute local code/data pins and require declared external identities."""
    preflight_appworld_data()
    from data.datasets.appworld import BUNDLE, split_ids, SERVICE_IMAGE, VERIFIER_IMAGE, IMAGE
    if any(not isinstance(image, str) or not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image) for image in (IMAGE, SERVICE_IMAGE, VERIFIER_IMAGE)):
        raise RecoveryError('AppWorld actor/service/verifier images must be immutable digests')
    actual = copy.deepcopy(expected)
    harness, manifest, source, manifest_sha, runtime = harness_inputs(expected['harness'], bindings)
    if bindings.get('model') != expected['model']:
        raise RecoveryError('model deployment binding differs from reviewed identity')
    validate_appworld_overlay(harness, manifest, runtime, IMAGE)
    # These are explicit external provenance documents, not fetched service attestations.
    actual.update(schema=IDENTITY_SCHEMA, policy=POLICY,
                  platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, encoding='ascii', errors='strict').strip(),
                  source_sha256=file_hash(BUNDLE), pool_ids_sha256=digest(split_ids(BUNDLE)[expected['pool']]),
                  harness_sha256=source['submission_sha256'],
                  manifest_sha256=manifest_sha, runtime_sha256=digest(runtime_commitment(runtime)))
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
               'HG_AGENT_DSH', 'HG_AGENT_CODEX',
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
        if key in {'HG_BUN_BINARY', 'HG_AGENT_NODE', 'HG_AGENT_DSH', 'HG_AGENT_CODEX'}:
            if not any(Path(item['resolved_path']) == Path(value).expanduser().resolve() for item in observed):
                raise RecoveryError(f'configured runtime executable is outside the inventory: {key}')
    return {'scope': 'reviewed-runtime-files-v1; transitive closure not asserted',
            'files': sorted(observed, key=lambda x: x['path']), 'environment': environment,
            'python': {'executable': str(Path(sys.executable).resolve()),
                       'sha256': file_hash(Path(sys.executable)), 'version': platform.python_version()}}


def validate_appworld_overlay(harness, manifest, runtime, image):
    """Bind the overlay actually selected for the pinned AppWorld actor ABI.

    A random PROVENANCE.json is not evidence for the requirements hash or the
    image's interpreter. Explicitly pin the overlay root so a host cache or .env
    cannot silently select a different dependency tree after identity review.
    """
    if not manifest.get('install'):
        return
    from eval.containers import container, harness_runtime

    root = runtime['environment'].get('HG_OVERLAY_ROOT')
    if not root or not Path(root).expanduser().is_absolute():
        raise RecoveryError('an absolute HG_OVERLAY_ROOT is required for AppWorld dependencies')
    spec = harness_runtime.install_spec(harness)
    if spec is None:
        raise RecoveryError('AppWorld dependency install must resolve to a requirements file')
    if not isinstance(image, str) or not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image):
        raise RecoveryError('the AppWorld actor image must be an immutable digest')
    version = _IMAGE_ABI_CACHE.get(image)
    if version is None:
        # network=none applies inside the container; only --pull=never prevents
        # the Docker daemon itself from fetching a missing image during a plan.
        script = ("p=$(command -v python3 || command -v python) || exit 1; "
                  "\"$p\" -c 'import json,sys;print(json.dumps({\"python\":sys.executable,"
                  "\"version\":str(sys.version_info.major)+\".\"+str(sys.version_info.minor)}))'")
        try:
            probe = container.docker('run', '--rm', '--pull=never', '--network', 'none',
                                     '--entrypoint', 'sh', image, '-c', script, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RecoveryError('the pinned AppWorld actor Python probe could not run locally') from exc
        if probe.returncode:
            raise RecoveryError('the pinned AppWorld actor image or its Python is unavailable locally')
        try:
            result = json.loads(probe.stdout.strip().splitlines()[-1])
            version = result['version']
            if not isinstance(version, str) or not re.fullmatch(r'3\.[0-9]+', version) or not result['python']:
                raise ValueError('invalid Python ABI probe')
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise RecoveryError('the pinned AppWorld actor Python ABI could not be determined locally') from exc
        _IMAGE_ABI_CACHE[image] = version
    expected = (Path(root).expanduser() / spec[0][:16] / version / 'PROVENANCE.json').resolve()
    inventory = {item['resolved_path'] for item in runtime['files']}
    if str(expected) not in inventory:
        raise RecoveryError(f'runtime inventory does not contain the actual AppWorld {version} overlay provenance: {expected}')
    provenance = read(expected)
    if provenance.get('install_sha256') != spec[0] or provenance.get('python') != version:
        raise RecoveryError('AppWorld overlay provenance does not match requirements and Python ABI')


def preflight_appworld_data():
    """Refuse absent inputs before importing an adapter that can prepare them."""
    from harnessgrad.paths import dataset_root
    root = Path(dataset_root('appworld'))
    missing = [str(path) for path in (root / 'pool.jsonl', root / '_src' / 'data-0.1.0.bundle')
               if not path.is_file()]
    if missing:
        raise RecoveryError('AppWorld input files are missing; configure/prepare data separately: '
                            + ', '.join(missing))
    return root


def dataset_environment():
    preflight_appworld_data()
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
    harness, manifest, source, manifest_sha, runtime = harness_inputs(identity['harness'], bindings)
    if identity['harness'] in bindings.get('harness_sources', {}):
        if source['submission_sha256'] != identity['harness_sha256'] or manifest_sha != identity['manifest_sha256']:
            raise RecoveryError('prepared harness source changed after identity review')
    validate_appworld_overlay(harness, manifest, runtime,
                              env.get('HG_APPWORLD_IMAGE'))
    if digest(runtime_commitment(runtime)) != identity['runtime_sha256']:
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
    # Fixed tracked policy, never an inherited or operator-overridable option.
    # Set before the driver interpreter starts, including its import-time readers.
    env[REVIEWED_ENVIRONMENT_ONLY] = '1'
    return env


def reviewed_harness_inputs(identity, bindings):
    inputs = harness_inputs(identity['harness'], bindings)
    _, _, source, manifest_sha, runtime = inputs
    if (source['submission_sha256'] != identity['harness_sha256']
            or manifest_sha != identity['manifest_sha256']
            or digest(runtime_commitment(runtime)) != identity['runtime_sha256']):
        raise RecoveryError('harness source/filesystem/runtime changed after identity review')
    return inputs


def command_for(identity, selected, receipt, bindings=None):
    budget = identity['budget']
    filesystem_flags = []
    if bindings is not None:
        path, _, _, _, runtime = reviewed_harness_inputs(identity, bindings)
        filesystem_flags = ['--expected-harness-filesystem-sha256', runtime['harness_filesystem']['sha256']]
    else:
        path = harness_source(identity['harness'], {})
    return [str(Path(sys.executable).resolve()), str((ROOT/'driver.py').resolve()), '--dataset', 'appworld',
            '--harness-path', str(path),
            '--expected-harness-sha256', identity['harness_sha256'], *filesystem_flags,
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
        command = command_for(expected, selected, receipt, bindings)
        _, _, _, _, reviewed_runtime = reviewed_harness_inputs(expected, bindings)
        plan = {'schema': 'harnessgrad.appworld-launch.v2', 'run_id': receipt.name,
                'harness': args.harness, 'pool': args.pool, 'selected': selected,
                'selection_sha256': digest(selection), 'measurement_identity': expected,
                'measurement_identity_sha256': digest(expected), 'command': command,
                'runtime_commitment': runtime_commitment(reviewed_runtime),
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
