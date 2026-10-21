#!/usr/bin/env python3
"""Aggregate verified CostBench evidence with an explicit task/trial manifest.

--expected-cells is a JSON list of {task_id, run_id, trial_id} objects. The
report refuses missing, duplicate or unexpected cells and infrastructure errors.
It reruns the pinned official batch scorer so cost/path eligibility and ratios
are not approximated by averaging per-task statistics.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from bootstrap import bootstrap
from scoring import METRIC, REVISION, aggregate


def cell(row):
    return tuple(row[name] for name in ('task_id', 'run_id', 'trial_id'))


def collect(evidences, expected, *, include_splits=False):
    expected_ids = [cell(row) for row in expected]
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError('expected-cell manifest is empty or contains duplicates')
    found = {}
    for evidence in evidences:
        if (evidence.get('measured') is not True or evidence.get('metric') != METRIC
                or evidence.get('revision') != REVISION
                or evidence.get('scorer_input', {}).get('verified') is not True):
            raise ValueError('only measured, verified, pinned CostBench evidence is accepted')
        key = cell(evidence['binding'])
        if evidence['task_id'] != key[0] or evidence['scorer_input']['task_id'] != key[0]:
            raise ValueError('evidence task binding mismatch')
        if key in found:
            raise ValueError('duplicate task/trial evidence: ' + str(key))
        split = evidence['official']['split']
        if split not in ('train', 'test'):
            raise ValueError('invalid evidence train/test split')
        found[key] = (evidence['official']['query_result'], split)
    missing = set(expected_ids) - set(found)
    extra = set(found) - set(expected_ids)
    if missing or extra:
        raise ValueError(f'incomplete comparison: {len(missing)} missing, {len(extra)} unexpected cells')
    queries = [found[key][0] for key in expected_ids]
    splits = [found[key][1] for key in expected_ids]
    return (queries, splits) if include_splits else queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--expected-cells', type=Path, required=True)
    parser.add_argument('evidence', nargs='+', type=Path)
    args = parser.parse_args()
    queries, splits = collect([json.loads(p.read_text()) for p in args.evidence],
                             json.loads(args.expected_cells.read_text()), include_splits=True)
    bootstrap(args.bundle, args.runtime)
    result = aggregate(queries, splits=splits)
    result.update(complete=True, revision=REVISION)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
