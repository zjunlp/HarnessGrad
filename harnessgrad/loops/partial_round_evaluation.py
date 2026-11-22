"""Execute the missing samples of an interrupted single-trial train round.

This adapter does not restore a method submission or publish a training point.
The training loop must admit those separately before calling it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from eval.run.runner import evaluate
from harnessgrad.orchestration.interrupted_measurement import InterruptedMeasurementRefused
from harnessgrad.orchestration.partial_round_resume import resume_missing_samples


def evaluate_missing_samples(
    *, run_dir: Path, round_no: int, work: Path, tasks: list[dict], scorable: dict,
    harness_sha: str, run_id: str, run_seed: str, jobs: int, sandbox: bool,
    setups: dict, verifiers: dict, envs: dict, dataset_runtime_identity: dict | None,
    verification_context: dict, harness_runtime_identity: dict | None,
    admit_execution: Callable[[dict], None], evaluator: Callable = evaluate,
) -> dict:
    """Run only missing tasks using the original round and persist ordinary evidence.

    The caller must verify the full model route, seed, protocol, live-driver
    exclusion and method submission in ``admit_execution``. It must not start
    this adapter merely because a historical model name happens to match.
    """
    root = Path(run_dir).resolve(strict=True)
    if root.name != run_id or not isinstance(run_seed, str) or not run_seed:
        raise InterruptedMeasurementRefused('run identity or seed missing')
    if os.environ.get('HG_TASK_VERDICT_JOURNAL') != '1':
        raise InterruptedMeasurementRefused('task verdict journal is required for resumption')
    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
        raise InterruptedMeasurementRefused('invalid worker count')
    try:
        meta = json.loads((root / 'run_meta.json').read_text())
    except (OSError, ValueError) as exc:
        raise InterruptedMeasurementRefused('missing run metadata') from exc
    if (verification_context != meta.get('verification_context') or
            dataset_runtime_identity != meta.get('dataset_runtime_identity')):
        raise InterruptedMeasurementRefused('selected verification or dataset runtime changed')
    by_id = {}
    for task in tasks:
        tid = task.get('task_id')
        if not isinstance(tid, str) or not tid or tid in by_id:
            raise InterruptedMeasurementRefused('invalid or duplicate selected task')
        by_id[tid] = task
    fresh = {}

    def admit(inspection):
        if inspection['run_id'] != run_id:
            raise InterruptedMeasurementRefused('saved run identity changed')
        expected = set(inspection['completed']) | set(inspection['missing_task_ids'])
        if set(by_id) != expected:
            raise InterruptedMeasurementRefused('selected task set differs from interrupted round')
        admit_execution(inspection)

    def execute(ids):
        if not set(ids) <= set(by_id):
            raise InterruptedMeasurementRefused('missing tasks are not in the admitted selection')
        selected = [task for task in tasks if task['task_id'] in set(ids)]
        result = evaluator(work, selected, scorable, {}, harness_sha=harness_sha,
            jobs=jobs, trials=1, progress_dir=root, sandbox=sandbox, setups=setups,
            verifiers=verifiers, envs=envs, run_id=run_id, run_seed=run_seed,
            recordings_root=root / 'recordings', round_no=round_no,
            dataset_runtime_identity=dataset_runtime_identity,
            verification_context=verification_context,
            harness_runtime_identity=harness_runtime_identity)
        if not isinstance(result, dict) or result.get('tampered'):
            raise InterruptedMeasurementRefused('partial evaluator failed integrity checks')
        fresh.update(result)

    result = resume_missing_samples(root, round_no, expected_candidate_tree=harness_sha,
        admit_execution=admit, evaluate_missing=execute)
    result['fresh_evaluation'] = fresh if result['requested_task_ids'] else None
    result['status'] = ('samples_complete_not_published' if result['status'] == 'complete_not_published'
                        else 'samples_incomplete')
    return result
