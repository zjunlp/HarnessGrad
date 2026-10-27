#!/usr/bin/env python3
"""Aggregate complete platform trial archives with an explicit task/trial manifest.

--expected-cells is a JSON list of {task_id, run_id, trial_id} objects. The
report refuses missing, duplicate or unexpected cells and infrastructure errors.
Inputs must retain the standard run layout: trial archives, final task progress
and ordinary task indexes. Raw verifier evidence or an archive without final
progress cannot establish whether the platform accepted a trial.
It reruns the pinned official batch scorer so cost/path eligibility and ratios
are not approximated by averaging per-task statistics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from bootstrap import bootstrap
from scoring import METRIC, REVISION, aggregate
from dynamic_scoring import MODES, metric as dynamic_metric, aggregate as dynamic_aggregate


def cell(row):
    return tuple(row[name] for name in ('task_id', 'run_id', 'trial_id'))


def load_trial(path):
    """Read a trial and its final disposition without reclassifying failures."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    archive = json.loads(raw)
    if not isinstance(archive, dict) or archive.get('schema') != 'harnessgrad.trial-evidence.v1':
        raise ValueError('complete platform trial archive required; raw verifier evidence has no platform validity')
    binding = archive['binding']
    task_path = Path(binding['task_id'])
    if task_path.is_absolute() or '..' in task_path.parts:
        raise ValueError('invalid archive task identity')
    digest = lambda value: hashlib.sha256(str(value).encode()).hexdigest()
    round_path = f"round-{binding['round']}"
    harness = digest(binding['harness_sha'])[:16]
    relative = (Path('trial-evidence') / round_path / harness / digest(binding['task_id'])
                / (digest(binding['trial_id']) + '.json'))
    if path.parts[-len(relative.parts):] != relative.parts:
        raise ValueError('trial archive must retain its standard run directory layout')
    root = path.parents[len(relative.parts) - 1]
    progress_raw = (root / 'progress' / round_path / (binding['task_id'] + '.json')).read_bytes()
    progress = json.loads(progress_raw)
    index = json.loads((root / 'trial-evidence/ordinary' / round_path / harness
                        / 'tasks' / (digest(binding['task_id']) + '.json')).read_text())
    if (index.get('schema') != 'harnessgrad.trial-evidence-index.v1'
            or index.get('progress_sha256') != hashlib.sha256(progress_raw).hexdigest()
            or index.get('results') != progress.get('trial_evidence')
            or index.get('configuration') != archive.get('configuration')
            or any(index.get(key) != binding[key] for key in ('run_id', 'round', 'harness_sha'))):
        raise ValueError('final task progress differs from its archived ordinary task index')
    return {'archive': archive, 'progress': progress,
            'reference': {'path': relative.as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()}}


def archive_evidence(trial):
    if (not isinstance(trial, dict) or not isinstance(trial.get('archive'), dict)
            or not isinstance(trial.get('progress'), dict)
            or not isinstance(trial.get('reference'), dict)):
        raise ValueError('complete platform trial archive and final progress required; use load_trial(path)')
    archive, progress = trial['archive'], trial['progress']
    if (not isinstance(archive, dict)
            or archive.get('schema') != 'harnessgrad.trial-evidence.v1'
            or not isinstance(archive.get('result'), dict)
            or not isinstance(archive.get('binding'), dict)
            or not isinstance(archive.get('outcome'), dict)):
        raise ValueError('complete platform trial archive required; raw verifier evidence has no platform validity')
    result, binding, outcome = (archive[key] for key in ('result', 'binding', 'outcome'))
    verdict = result.get('verdict') or {}
    if (progress.get('complete') is not True or progress.get('invalid')
            or result.get('invalid') or result.get('exception') or verdict.get('unmeasured')
            or outcome.get('status') not in ('returned', 'measured')):
        raise ValueError('only measured, platform-valid CostBench trials are accepted')
    rows, scores = progress.get('trial_evidence'), progress.get('trial_scores')
    if (not isinstance(rows, list) or not isinstance(scores, list)
            or type(progress.get('n_trials')) is not int
            or progress['n_trials'] != len(rows) or len(scores) != len(rows)
            or not rows):
        # Older ordinary archives record returned attempts before the runner may
        # discard a provider failure. A shorter final score list cannot identify
        # which returned trials it accepted, so do not guess a mapping.
        raise ValueError('final progress does not account for every archived trial; discarded trials cannot be inferred')
    if (progress.get('task_id') != binding['task_id'] or progress.get('round') != binding['round']
            or len({row['trial_id'] for row in rows}) != len(rows)):
        raise ValueError('final progress and trial archive identity mismatch')
    matching = [(i, row) for i, row in enumerate(rows) if row.get('trial_id') == binding['trial_id']]
    if len(matching) != 1:
        raise ValueError('trial archive is not uniquely referenced by final progress')
    position, row = matching[0]
    if (row.get('trial_evidence') != trial['reference']
            or {key: value for key, value in row.items() if key != 'trial_evidence'} != outcome):
        raise ValueError('trial archive hash or outcome differs from final progress')
    evidence = verdict.get('evidence')
    if not isinstance(evidence, dict) or evidence.get('measured') is not True:
        raise ValueError('only measured, verified CostBench trials are accepted')
    evidence_binding = evidence.get('binding') or {}
    if (any(key not in binding or key not in evidence_binding
            or binding[key] != evidence_binding[key] for key in ('task_id', 'run_id', 'trial_id', 'seed'))
            or any(outcome.get(key) != binding[key] for key in ('task_id', 'trial_id', 'seed'))
            or result.get('task_id') != binding['task_id']):
        raise ValueError('platform trial archive and scorer evidence binding mismatch')
    # A valid zero and a scored artifact left by a nonzero actor exit remain
    # measured. The platform validity decision, not actor exit status, gates use.
    score = verdict.get('score')
    if type(score) not in (int, float) or score not in (0, 1):
        raise ValueError('platform CostBench verdict has no measured binary score')
    if evidence.get('official', {}).get('reward') != score:
        raise ValueError('platform verdict and official CostBench reward disagree')
    if scores[position] != score:
        raise ValueError('final progress and trial verdict scores disagree')
    return evidence


def collect(trials, expected, *, include_splits=False, mode=None):
    selected_metric = METRIC if mode is None else dynamic_metric(mode)
    expected_ids = [cell(row) for row in expected]
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError('expected-cell manifest is empty or contains duplicates')
    found = {}
    for trial in trials:
        evidence = archive_evidence(trial)
        if (evidence.get('measured') is not True or evidence.get('metric') != selected_metric
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
        if mode is not None and (evidence['official'].get('mode') != mode
                or not key[0].startswith(f'costbench_dynamic/{mode}/{split}/')):
            raise ValueError('dynamic evidence mode or task identity mismatch')
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
    parser.add_argument('--mode', choices=MODES,
                        help='Explicitly select one dynamic mode; omit for static evidence')
    parser.add_argument('archives', nargs='+', type=Path,
                        help='Trial archives in their run layout, alongside final progress and ordinary task indexes')
    args = parser.parse_args()
    queries, splits = collect([load_trial(p) for p in args.archives],
                             json.loads(args.expected_cells.read_text()), include_splits=True,
                             mode=args.mode)
    bootstrap(args.bundle, args.runtime)
    result = (aggregate(queries, splits=splits) if args.mode is None else
              dynamic_aggregate(queries, mode=args.mode, splits=splits))
    result.update(complete=True, revision=REVISION)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
