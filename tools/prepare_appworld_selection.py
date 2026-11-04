#!/usr/bin/env python3
"""Prepare a reviewable AppWorld H0 selection without executing any task.

Model/runtime provenance and per-harness budgets must already be supplied. The
result records local identities and four separate official pools; creating it
does not approve a campaign or attest the model bytes currently in GPU memory.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.run.metrics import pilot_task_set
from eval.run.repeated_trials import immutable_json
from harnessgrad.paths import main_checkout
from tools.appworld_campaign_state import (
    IDENTITY_SCHEMA,
    POLICY,
    POOLS,
    SELECTION_SCHEMA,
    RecoveryError,
    digest,
    read,
    validate_identity,
)
from tools.launch_appworld_harness_baselines import (
    current_identity,
    ensure_frozen,
    preflight_appworld_data,
)


def prepare(bindings, budgets, harnesses):
    if (not isinstance(harnesses, list) or not harnesses or len(set(harnesses)) != len(harnesses)
            or any(not isinstance(harness, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', harness)
                   for harness in harnesses)):
        raise RecoveryError('supply unique harness names')
    if set(budgets) != set(harnesses):
        raise RecoveryError('budgets must name exactly the selected harnesses')
    if not isinstance(bindings.get('model'), dict) or not isinstance(bindings.get('harness_runtimes'), dict):
        raise RecoveryError('bindings need explicit model and harness_runtimes')
    if set(harnesses) - set(bindings['harness_runtimes']):
        raise RecoveryError('missing reviewed runtime inventory for a selected harness')
    preflight_appworld_data()
    from data.datasets.appworld import BUNDLE, split_ids
    released = split_ids(BUNDLE)
    if set(released) != set(POOLS):
        raise RecoveryError('AppWorld release does not contain exactly four official pools')
    caps = {'train': 9, 'dev': 6, 'test_normal': 17, 'test_challenge': 30}
    pools = {pool: {'eligible': len(released[pool]),
                    'selected': pilot_task_set(released[pool], cap=caps[pool])} for pool in POOLS}
    if any(not item['selected'] for item in pools.values()):
        raise RecoveryError('an official pool has no selectable tasks')
    identities = {}
    for harness in harnesses:
        for pool in POOLS:
            seed = {'schema': IDENTITY_SCHEMA, 'dataset': 'appworld', 'harness': harness,
                    'pool': pool, 'side': 'train' if pool == 'train' else 'eval',
                    'model': copy.deepcopy(bindings['model']),
                    'budget': copy.deepcopy(budgets[harness]), 'policy': POLICY}
            identity = current_identity(seed, bindings)
            validate_identity(identity)
            identities[f'{harness}:{pool}'] = identity
    for field in ('source_sha256', 'scorer_sha256', 'platform_commit'):
        if len({identity[field] for identity in identities.values()}) != 1:
            raise RecoveryError(f'{field} changed while the selection was prepared')
    for pool in POOLS:
        if len({identities[f'{h}:{pool}']['pool_ids_sha256'] for h in harnesses}) != 1:
            raise RecoveryError(f'{pool} roster changed while the selection was prepared')
    for harness in harnesses:
        for field in ('harness_sha256', 'manifest_sha256', 'runtime_sha256'):
            if len({identities[f'{harness}:{pool}'][field] for pool in POOLS}) != 1:
                raise RecoveryError(f'{harness} {field} changed while the selection was prepared')
    return {'schema': SELECTION_SCHEMA, 'pools': pools, 'identities': identities,
            'prepared_for_review': True,
            'model_provenance': 'declared bindings; not live GPU weight or tokenizer attestation'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', required=True, type=Path,
                        help='reviewed model provenance and runtime file inventories')
    parser.add_argument('--budgets', required=True, type=Path,
                        help='explicit v2 budget object keyed by harness')
    parser.add_argument('--harness', action='append', required=True)
    parser.add_argument('--output', required=True, type=Path,
                        help='new selection file outside the platform checkout')
    args = parser.parse_args(argv)
    try:
        output = args.output.expanduser().resolve()
        if (output.is_relative_to(ROOT.resolve())
                or output.is_relative_to(main_checkout(ROOT).resolve())):
            raise RecoveryError('selection output must be outside the frozen platform checkout')
        if output.exists() or output.is_symlink():
            raise RecoveryError('selection output already exists')
        # A clean commit is part of the identity; otherwise platform_commit
        # would identify code other than the code that prepared this selection.
        ensure_frozen()
        selection = prepare(read(args.bindings), read(args.budgets), args.harness)
        immutable_json(output, selection, reuse=False)
        print(json.dumps({'output': str(output), 'selection_sha256': digest(selection),
                          'pairs': len(selection['identities']),
                          'pools': {key: {'eligible': value['eligible'],
                                         'selected': len(value['selected'])}
                                    for key, value in selection['pools'].items()},
                          'execution_authorized': False}))
        return 0
    except (RecoveryError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
