"""Trusted external actor admission shared by execution and measurement reuse.

Only platform-owned stdlib providers are imported. Records contain summaries,
never command arguments, credentials, binding paths or installation inventories.
The existing Bun identity format remains readable and reusable.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

from harnessgrad.paths import main_checkout
from eval.containers import bun_runtime, openclaw_runtime

ROOT = Path(__file__).resolve().parents[2]
KINDS = frozenset({'bun', 'openclaw', 'codex', 'opencode', 'dsh', 'external'})


class RuntimeUnavailable(ValueError):
    pass


def _provider(relative):
    path = ROOT / 'base_harness' / relative
    spec = importlib.util.spec_from_file_location('_platform_external_' + path.parent.name.replace('-', '_'), path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _summary(kind, record, cacheable):
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':'),
                                       allow_nan=False).encode()).hexdigest()
    return {'kind': kind, 'schema': 1, 'installation_sha256': digest,
            'complete': bool(cacheable), 'cacheable': bool(cacheable)}


def identities(repo, env=None):
    """Re-admit current installations without running candidate code or building."""
    if repo is None:
        return {}
    repo = Path(repo).resolve()
    environment = dict(os.environ) if env is None else dict(env)
    try:
        path = repo / 'harness.json'
        if not path.exists():
            bun_runtime.admit(repo, environment)
            bun = bun_runtime.identity(repo)
            return {'bun': bun} if bun is not None else {}
        if not path.resolve(strict=True).is_relative_to(repo):
            raise ValueError('Harness manifest must belong to candidate')
        manifest = json.loads(path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError('Harness manifest must be an object')
        values = {}
        bun_runtime.admit(repo, environment)
        bun = bun_runtime.identity(repo)
        if bun is not None:
            values['bun'] = bun
        claw = openclaw_runtime.identity(repo, environment)
        if claw is not None:
            values['openclaw'] = claw
        for kind, key, name, relative in (
                ('codex', '_codex_runtime', 'codex', 'codex/codex_runtime.py'),
                ('opencode', '_opencode_runtime', 'opencode', 'opencode/opencode_runtime.py'),
                ('dsh', '_dsh_runtime', 'deepseek-harness', 'deepseek-harness/dsh_runtime.py')):
            if key in manifest:
                helper = _provider(relative)
                record = helper.resolve(repo, environment, forbidden=(ROOT, main_checkout(ROOT), repo))
                # Completeness belongs to the canonical provider contract.
                # Old receipts without the flag remain diagnostic only.
                complete = record.get('cacheable') is True
                values[kind] = _summary(kind, record, complete)
                if kind == 'opencode':
                    values[kind]['wire'] = helper.execution_wire(environment)
            elif manifest.get('name') == name and (manifest.get('runtime') or {}).get('kind') != 'python':
                values[kind] = {'kind': kind, 'complete': False, 'cacheable': False}
        if (not values and (manifest.get('runtime') or {}).get('kind') != 'python'
                and (manifest.get('name') in ('openclaw', 'opencode') or manifest.get('runtime_paths'))):
            values['external'] = {'kind': 'external', 'complete': False, 'cacheable': False}
        return values
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise RuntimeUnavailable(f'External actor runtime admission failed: {exc}') from exc


def cacheable(current):
    return all(value.get('cacheable', True) is True for value in current.values())


def stamp(result, current):
    """Attach admission only to an admitted execution, retaining diagnostics.

    Invalid results may contain useful peer receipts but are not proof that an
    aggregate executed with the expected runtime. Never promote them here.
    """
    if result.get('invalid'):
        return result
    if current:
        actual = result.get('harness_runtime') or {}
        if any(kind in actual and actual[kind] != identity for kind, identity in current.items()):
            raise RuntimeUnavailable('Executed external actor differs from admitted installation')
        result['harness_runtime'] = {**(result.get('harness_runtime') or {}), **current}
        services = result.setdefault('runtime_services', {})
        for kind, identity in current.items():
            services[kind] = {**(services.get(kind) or {}), 'identity': identity}
    return result


def require_current(repo, expected, env=None):
    current = identities(repo, env)
    if current != expected:
        raise RuntimeUnavailable('External actor runtime changed after admission')
    return current


def records(value):
    """Read result/curve/H0 forms without synthesizing missing provenance."""
    if not isinstance(value, dict):
        return []
    environment = value.get('env')
    entries = environment.get('harness_runtime') if isinstance(environment, dict) else None
    if entries is None:
        entries = value.get('harness_runtime')
    if isinstance(entries, dict):
        return [entries]
    return entries if isinstance(entries, list) else []

def require_records(expected, value, *, services=False, reusable=True):
    if reusable and not cacheable(expected):
        raise RuntimeUnavailable('External actor runtime is incomplete or noncacheable; fresh measurement required')
    entries = records(value)
    if services:
        receipts = value.get('runtime_services') or []
        if isinstance(receipts, dict):
            receipts = [receipts]
        if expected and (not receipts or len(receipts) != value.get('n_trials', len(receipts))):
            raise RuntimeUnavailable('External actor trial identity coverage is missing')
        if not isinstance(receipts, list) or any(not isinstance(item, dict) for item in receipts):
            raise RuntimeUnavailable('External actor trial identity receipts are invalid')
        if any(not isinstance(item.get(kind), dict) for item in receipts for kind in KINDS if kind in item):
            raise RuntimeUnavailable('External actor trial identity is invalid')
        entries = [{kind: item[kind].get('identity') for kind in KINDS if kind in item}
                   for item in receipts]
    if expected and not entries:
        raise RuntimeUnavailable('External actor runtime identity is missing from measurement')
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeUnavailable('External actor runtime identity record is invalid')
        actual = {key: item for key, item in entry.items() if key in KINDS}
        if actual != expected:
            raise RuntimeUnavailable('External actor runtime identity is missing or changed')


def admit_reuse(repo, value, *, services=False, env=None, reusable=True):
    current = identities(repo, env)
    require_records(current, value, services=services, reusable=reusable)
    return current


def fields(current):
    return {'harness_runtime': [current]} if current else {}


def admit_checkpoint(run_dir, point, *, fallback=None, env=None):
    """Re-admit retained source for each reused train checkpoint, including H0."""
    if point.get('unmeasured'):
        return {}
    from harnessgrad import candidate_runtime, snapshot
    run_dir = Path(run_dir)
    number = point.get('round')
    if type(number) is int and number >= 0 and (
            (run_dir / f'state_after_round{number}').is_dir() or snapshot.path_for(run_dir, number).is_file()):
        with candidate_runtime.checkpoint_source(run_dir, point) as source:
            return admit_reuse(source, point, env=env)
    if fallback is not None:
        return admit_reuse(fallback, point, env=env)
    # Preserve reading/resuming old pure-Python records. An external receipt
    # without retained source cannot prove which installation it originally used.
    if any(any(kind in entry for kind in KINDS) for entry in records(point)):
        raise RuntimeUnavailable('External actor checkpoint source is unavailable')
    return {}
