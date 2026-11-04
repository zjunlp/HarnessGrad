#!/usr/bin/env python3
"""Resume reviewed AppWorld H0 attempts without trusting completed flags.

Dry-run is the default. v1 statuses remain audit-only: task-ID agreement is not
measurement identity. v2 recovery reconstructs coverage from every unique
attempt, retains process failure independently, and stops for ambiguous evidence.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.appworld_campaign_state import (POOLS, SELECTION_SCHEMA, RecoveryError,
    read, recover, selection_identity, legacy_audit)
from tools.launch_appworld_harness_baselines import current_identity, check_identity, write


def validate_selection(selection, harnesses, pools):
    if selection.get('schema') != SELECTION_SCHEMA:
        raise RecoveryError('legacy selection is audit-only; reviewed v2 identities are required')
    if len(harnesses) != len(set(harnesses)) or len(pools) != len(set(pools)):
        raise RecoveryError('harnesses/pools must be unique')
    if set(selection.get('pools', {})) != set(POOLS):
        raise RecoveryError('selection must keep all four official pools separate')
    from data.datasets.appworld import split_ids, BUNDLE
    from eval.run.metrics import pilot_task_set
    released = split_ids(BUNDLE)
    caps = {'train': 9, 'dev': 6, 'test_normal': 17, 'test_challenge': 30}
    for pool, info in selection['pools'].items():
        if info.get('eligible') != len(released[pool]) or info.get('selected') != pilot_task_set(released[pool], cap=caps[pool]):
            raise RecoveryError(f'{pool}: selection differs from frozen native cohort')
    for h in harnesses:
        for pool in pools:
            selection_identity(selection, h, pool)


def run_launcher(command):
    return subprocess.run(command, cwd=ROOT)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selection', type=Path, required=True)
    p.add_argument('--bindings', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--harness', action='append', required=True)
    p.add_argument('--pools', nargs='+', choices=POOLS)
    p.add_argument('--resume-status', type=Path)
    p.add_argument('--jobs', type=int, default=1)
    p.add_argument('--review-note', required=True)
    p.add_argument('--operator-authorization', required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        if args.jobs < 1 or not args.review_note.strip() or not args.operator_authorization.strip():
            raise RecoveryError('positive jobs and explicit review/authorization are required')
        selection = read(args.selection)
        if selection.get('schema') != SELECTION_SCHEMA:
            if args.execute:
                raise RecoveryError('legacy selection is audit-only; reviewed v2 identities are required')
            audit = legacy_audit(selection, read(args.resume_status) if args.resume_status else {})
            print(json.dumps(audit, indent=2)); return 0
        pools = args.pools or list(POOLS)
        validate_selection(selection, args.harness, pools)
        bindings = read(args.bindings)
        for h in args.harness:
            for pool in pools:
                expected = selection_identity(selection, h, pool)
                if expected['budget']['jobs'] != args.jobs:
                    raise RecoveryError('jobs differ from reviewed execution identity')
                check_identity(expected, current_identity(expected, bindings))
        state = recover(selection, read(args.resume_status) if args.resume_status else None)
        state['actions'] = [{'harness': h, 'pool': pool,
                             'task_ids': state['coverage'][f'{h}:{pool}']['remaining_task_ids']}
                            for h in args.harness for pool in pools
                            if state['coverage'][f'{h}:{pool}']['remaining_task_ids']]
        if not args.execute:
            print(json.dumps(state, ensure_ascii=False, indent=2)); return 0
        if not state['execution_allowed']:
            raise RecoveryError('recovery requires review; inspect dry-run issues/coverage')
        args.output = args.output.resolve()
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output/'selection.json', selection)
        state.update(review_note=args.review_note, operator_authorization=args.operator_authorization)
        write(args.output/'status.json', state)
        for action in list(state['actions']):
            receipt = args.output / f'appworld-{action["harness"]}-{action["pool"]}-{uuid.uuid4().hex}'
            attempt = {'harness': action['harness'], 'pool': action['pool'], 'receipt': str(receipt)}
            # Persist the attempt pointer before the child starts. A crash before
            # exit/profile/curve still leaves a discoverable attempt directory.
            state['attempts'].append(attempt)
            state['current'] = attempt
            write(args.output/'status.json', state)
            command = [sys.executable, str(ROOT/'tools/launch_appworld_harness_baselines.py'),
                       '--selection', str(args.output/'selection.json'), '--bindings', str(args.bindings.resolve()),
                       '--harness', action['harness'], '--pool', action['pool'],
                       '--task-ids', ','.join(action['task_ids']), '--receipt-dir', str(receipt), '--execute']
            result = run_launcher(command)
            state = recover(selection, state)
            state.update(review_note=args.review_note, operator_authorization=args.operator_authorization)
            write(args.output/'status.json', state)
            if result.returncode or not state['execution_allowed']:
                return 2
        return 0
    except (RecoveryError, OSError, ValueError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
