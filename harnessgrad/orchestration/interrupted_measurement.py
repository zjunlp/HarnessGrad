"""Admit a completed interrupted measurement as an auditable read-only view.

This module does not publish a curve point or continue an optimization method.
Both task sources remain separate, including their trial identities and usage.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from eval.observe import task_verdicts
from eval.observe.tracing import harness_usage_totals
from eval.run.aggregation import score_of
from eval.run.outcomes import _harness_failure
from eval.verification.isolation import common_class, observed_class
from harnessgrad.core import snapshot
from harnessgrad.provenance import verification_context


class InterruptedMeasurementRefused(ValueError):
    """Saved material cannot establish a complete compatible measurement."""


def admit_execution_identity(original: Path, supplement: Path) -> dict:
    """Compare explicit nonsecret execution settings before reconciling samples.

    This checks recorded task execution identity only; it does not establish
    method state, live service identity, candidate provenance or authorization
    to publish a point.
    """
    left = _read_json(Path(original) / 'run_meta.json')
    right = _read_json(Path(supplement) / 'run_meta.json')
    fields = (('config', 'run', 'seed'), ('config', 'run', 'trials'),
              ('config', 'agent', 'backend'), ('config', 'agent', 'model'),
              ('config', 'agent', 'base_url'), ('config', 'sandbox'))
    def get(meta, path):
        value = meta
        for key in path:
            if not isinstance(value, dict) or key not in value:
                raise InterruptedMeasurementRefused('missing execution identity: ' + '.'.join(path))
            value = value[key]
        if value is None or value == '' or value == {}:
            raise InterruptedMeasurementRefused('missing execution identity: ' + '.'.join(path))
        return value
    for path in fields:
        if get(left, path) != get(right, path):
            raise InterruptedMeasurementRefused('execution identity changed: ' + '.'.join(path))
    for meta in (left, right):
        for field in ('model', 'backend'):
            top = meta.get('agent_' + field)
            if not isinstance(top, str) or not top or top != get(meta, ('config', 'agent', field)):
                raise InterruptedMeasurementRefused('inconsistent recorded agent ' + field)
    return {'status': 'recorded_execution_identity_matched_not_authorized',
            'checked_fields': ['.'.join(path) for path in fields]}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise InterruptedMeasurementRefused(f"cannot read {path}") from exc


def _records(run_dir: Path, round_no: int, *, allow_absent: bool = False):
    root = run_dir / "progress" / f"round-{round_no}"
    if allow_absent and not root.exists() and not root.is_symlink():
        return {}
    if not root.is_dir() or root.is_symlink():
        raise InterruptedMeasurementRefused("missing progress directory")
    result = {}
    for path in root.rglob("*.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise InterruptedMeasurementRefused("unsafe progress path")
        record = _read_json(path)
        tid = record.get("task_id")
        if not isinstance(tid, str) or not tid or tid in result:
            raise InterruptedMeasurementRefused("invalid or duplicate task id")
        if record.get("complete") is not True or record.get("round") != round_no:
            raise InterruptedMeasurementRefused("incomplete task record")
        trace_path = root / "traces" / f"{tid}.trial1.jsonl"
        trace = trace_path.resolve()
        if (trace != trace_path.absolute() or not trace.is_relative_to((root / "traces").resolve())
                or not trace.is_file()):
            raise InterruptedMeasurementRefused("missing or unsafe task trace")
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
            if not any(line.strip() for line in lines):
                raise ValueError("empty trace")
            for line in lines:
                if line.strip():
                    json.loads(line)
        except (OSError, ValueError, UnicodeError) as exc:
            raise InterruptedMeasurementRefused("unreadable task trace") from exc
        receipt = record.get("verdict_receipt")
        if not isinstance(receipt, dict):
            raise InterruptedMeasurementRefused("missing verdict receipt reference")
        try:
            journal, target = task_verdicts.load(run_dir, round_no, tid, receipt)
        except (OSError, ValueError, TypeError) as exc:
            raise InterruptedMeasurementRefused("invalid verdict receipt") from exc
        score = record.get("score")
        if (record.get("n_trials") != 1 or len(journal['trials']) != 1 or
                isinstance(score, bool) or not isinstance(score, (int, float)) or
                not math.isfinite(score) or score_of(journal['trials'][0]) != score):
            raise InterruptedMeasurementRefused("only single-trial scored records are supported")
        result[tid] = (record, path, trace, target)
    return result


def _require_task_binding(meta, context, tid, record):
    if verification_context.project(context, [tid]) != record.get("verification_context"):
        raise InterruptedMeasurementRefused("verification context changed")
    reference, actual = meta.get("dataset_runtime_identity") or {}, record.get("dataset_runtime_identity") or {}
    ref_tasks, actual_tasks = reference.get("tasks") or {}, actual.get("tasks") or {}
    if (tid not in ref_tasks or tid not in actual_tasks or
        reference.get("provider") != actual.get("provider") or ref_tasks[tid] != actual_tasks[tid]):
        raise InterruptedMeasurementRefused("dataset runtime changed")
    for digest in ref_tasks[tid].values():
        if (digest not in (reference.get("artifacts") or {}) or
            digest not in (actual.get("artifacts") or {}) or
            reference["artifacts"][digest] != actual["artifacts"][digest]):
            raise InterruptedMeasurementRefused("dataset runtime artifact changed")


def inspect_partial(original: Path, round_no: int) -> dict:
    """Inspect completed samples of an interrupted training round without execution.

    This does not itself authorize resumption: model route, seed, external
    harness runtime, method submission and the candidate's Git tree must be
    independently admitted before a caller executes any remaining tasks.
    """
    original = Path(original)
    meta = _read_json(original / 'run_meta.json')
    try:
        context = verification_context.validate(meta['verification_context'])
        candidate_path = original / 'snapshots' / f'round-{round_no}.json'
        candidate = snapshot.load(candidate_path)
        snapshot.verify(candidate_path, original / f'state_after_round{round_no}')
    except (KeyError, OSError, ValueError, snapshot.SnapshotInvalid) as exc:
        raise InterruptedMeasurementRefused('invalid interrupted round identity') from exc
    if (not context['tasks'] or not meta.get('agent_model') or
            candidate.get('run_id') != meta.get('run_id') or candidate.get('round') != round_no or
            not candidate.get('harness_sha')):
        raise InterruptedMeasurementRefused('missing interrupted round identity')
    completed = {}
    for tid, (record, progress, trace, verdict) in _records(original, round_no).items():
        if tid not in context['tasks']:
            raise InterruptedMeasurementRefused('progress contains an unauthorized task')
        _require_task_binding(meta, context, tid, record)
        completed[tid] = {
            'score': record['score'], 'progress': str(progress), 'trace': str(trace),
            'verdict_receipt': str(verdict),
            'progress_sha256': hashlib.sha256(progress.read_bytes()).hexdigest(),
            'trace_sha256': hashlib.sha256(trace.read_bytes()).hexdigest(),
            'verdict_sha256': hashlib.sha256(verdict.read_bytes()).hexdigest(),
            'trial_usage': record.get('trial_usage'), 'trial_env_usage': record.get('trial_env_usage'),
        }
    return {'schema': 'harnessgrad-interrupted-round-inspection/v1', 'run_id': meta['run_id'],
            'round': round_no, 'candidate_tree_sha': candidate['harness_sha'],
            'completed': completed, 'missing_task_ids': sorted(set(context['tasks']) - set(completed)),
            'status': 'inspected_not_execution_authorized'}


def admit(original: Path, supplement: Path, round_no: int) -> dict:
    """Validate complementary single-trial records and return their provenance.

    The caller must separately validate method submission state and decide how to
    resume it. Trial identities are not rewritten or presented as identical.
    """
    original, supplement = Path(original), Path(supplement)
    meta = _read_json(original / "run_meta.json")
    other = _read_json(supplement / "run_meta.json")
    context = verification_context.validate(meta["verification_context"])
    tasks = set(context["tasks"])
    if not tasks:
        raise InterruptedMeasurementRefused("empty task set")
    if not meta.get("agent_model") or meta.get("agent_model") != other.get("agent_model"):
        raise InterruptedMeasurementRefused("task model changed")
    if (not meta.get("dataset") or meta.get("dataset") != other.get("dataset") or
            not meta.get("split_sha") or meta.get("split_sha") != other.get("split_sha")):
        raise InterruptedMeasurementRefused("dataset or split changed")
    candidate = snapshot.load(original / "snapshots" / f"round-{round_no}.json")
    if candidate.get("run_id") != meta.get("run_id") or candidate.get("round") != round_no:
        raise InterruptedMeasurementRefused("candidate snapshot identity changed")
    try:
        snapshot.verify(original / "snapshots" / f"round-{round_no}.json",
                        original / f"state_after_round{round_no}")
    except (OSError, ValueError, snapshot.SnapshotInvalid) as exc:
        raise InterruptedMeasurementRefused("candidate checkpoint differs from snapshot") from exc
    point_path = supplement / "curve.jsonl"
    try:
        points = [json.loads(line) for line in point_path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise InterruptedMeasurementRefused("invalid supplement curve") from exc
    if len(points) != 1 or (points[0].get("identity") or {}).get("harness_sha") != candidate.get("harness_sha"):
        raise InterruptedMeasurementRefused("supplement candidate identity changed")
    sources = [("original", original, round_no), ("supplement", supplement, 0)]
    combined = {}
    for name, run_dir, index in sources:
        for tid, (record, progress, trace, verdict) in _records(
                run_dir, index, allow_absent=(name == 'original')).items():
            if tid not in tasks or tid in combined:
                raise InterruptedMeasurementRefused("task sets overlap or include an unauthorized task")
            _require_task_binding(meta, context, tid, record)
            combined[tid] = {
                "score": record["score"], "source": name, "run_id": run_dir.name,
                "round": index, "progress": str(progress), "trace": str(trace),
                "progress_sha256": hashlib.sha256(progress.read_bytes()).hexdigest(),
                "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                "verdict_receipt": str(verdict), "trial_usage": record.get("trial_usage"),
                "verdict_sha256": hashlib.sha256(verdict.read_bytes()).hexdigest(),
                "trial_env_usage": record.get("trial_env_usage"),
            }
    if set(combined) != tasks:
        raise InterruptedMeasurementRefused("task evidence is incomplete")
    supplemental = {tid: row['score'] for tid, row in combined.items() if row['source'] == 'supplement'}
    if (points[0].get('n_scored') != len(supplemental) or
            points[0].get('n_invalid', 0) != 0 or points[0].get('per_task') != supplemental):
        raise InterruptedMeasurementRefused("supplement curve does not match task evidence")
    return {"schema": "harnessgrad-interrupted-measurement-view/v1", "original_run": meta.get("run_id"),
            "supplement_run": other.get("run_id"), "round": round_no,
            "candidate_tree_sha": candidate.get("harness_sha"), "tasks": combined,
            "score": sum(float(x["score"]) for x in combined.values()) / len(combined),
            "status": "admitted_view_not_training_continuation",
            "limitations": ["Model route, seed and external harness runtime require independent verification.",
                "The snapshot's recorded Git tree identity requires independent verification.",
                "Distinct run and round trial identities are retained, not asserted equivalent."]}


def resume_inputs(view: dict, original: Path, supplement: Path) -> dict:
    """Rehydrate task evidence without writing history or starting an actor.

    Re-admit and compare the entire view before reading. Unknown usage remains
    unknown in the original progress record; no missing costs are synthesized.
    The returned payload is a building block, not a method-resume authorization.
    """
    if not isinstance(view, dict) or view.get('status') != 'admitted_view_not_training_continuation':
        raise InterruptedMeasurementRefused('invalid interrupted measurement view')
    current = admit(original, supplement, view['round'])
    if current != view:
        raise InterruptedMeasurementRefused('interrupted measurement evidence changed')
    runs = {'original': Path(original), 'supplement': Path(supplement)}
    records, traces, verdicts, outputs, provenance, failures = {}, {}, {}, {}, {}, {}
    for tid, item in current['tasks'].items():
        run = runs[item['source']]
        record = _read_json(Path(item['progress']))
        trace = Path(item['trace']).read_text(encoding='utf-8')
        journal, _ = task_verdicts.load(run, item['round'], tid, record['verdict_receipt'])
        if (hashlib.sha256(Path(item['progress']).read_bytes()).hexdigest() != item['progress_sha256'] or
                hashlib.sha256(trace.encode('utf-8')).hexdigest() != item['trace_sha256']):
            raise InterruptedMeasurementRefused('interrupted measurement changed during read')
        trial = journal['trials'][0]
        stderr = trial.get('stderr') or ''
        records[tid], traces[tid], verdicts[tid] = record, trace, trial.get('verdict') or {}
        outputs[tid] = {'exit_code': trial.get('exit_code'),
                        'stderr': stderr if len(stderr) <= 8000 else stderr[:2000] + '\n...\n' + stderr[-6000:]}
        failure = _harness_failure({**trial, 'trace': trace})
        if failure:
            failures[tid] = failure
        provenance[tid] = {'source': item['source'], 'run_id': item['run_id'], 'round': item['round']}
    return {'prior_records': records, 'traces': traces, 'verdicts': verdicts,
            'harness_output': outputs, 'harness_failed': failures, 'measurement_provenance': provenance,
            'status': 'rehydrated_not_training_continuation'}


def measurement_projection(view: dict, original: Path, supplement: Path) -> dict:
    """Project revalidated evidence and usage without publishing or authorizing a round.

    Trial-level reports retain their original run identity. Missing reports stay
    unknown, including when some other tasks did report usage.
    """
    execution_identity = admit_execution_identity(original, supplement)
    restored = resume_inputs(view, original, supplement)
    records = restored['prior_records']
    reports = {}
    for tid, record in records.items():
        usage = record.get('trial_usage')
        reports[tid] = usage if isinstance(usage, list) else [None]
    return {
        'schema': 'harnessgrad.interrupted-measurement-projection/v1',
        'round': view['round'],
        'candidate_tree_sha': view['candidate_tree_sha'],
        'recorded_execution_identity': execution_identity,
        'per_task': {tid: record['score'] for tid, record in records.items()},
        'per_task_trials': {tid: [record['score']] for tid, record in records.items()},
        'per_task_std': {tid: 0.0 for tid in records},
        'trials': 1,
        'traces': restored['traces'],
        'trial_traces': {tid: [trace] for tid, trace in restored['traces'].items()},
        'verdicts': restored['verdicts'],
        'trial_verdicts': {tid: [verdict] for tid, verdict in restored['verdicts'].items()},
        'harness_output': restored['harness_output'],
        'harness_failed': restored['harness_failed'],
        'per_task_scorer_isolation': {
            tid: record.get('scorer_isolation_class', 'unknown') for tid, record in records.items()},
        'per_task_env_usage': {tid: record.get('trial_env_usage') for tid, record in records.items()},
        'measurement_provenance': restored['measurement_provenance'],
        'harness_usage_by_task': reports,
        'tokens': harness_usage_totals(reports, trials=1),
        'status': 'projected_not_training_continuation',
    }


def recovered_evaluation(view: dict, original: Path, supplement: Path, *,
                         expected_candidate_tree: str, expected_context: dict) -> dict:
    """Build a complete result for a caller that separately admits method state.

    Only completed, single-trial task evidence is accepted here. This function
    does not claim that platform integrity was observed across the interrupted
    process, and does not publish a curve or authorize a new method invocation.
    """
    meta = _read_json(Path(original) / 'run_meta.json')
    if view.get('candidate_tree_sha') != expected_candidate_tree:
        raise InterruptedMeasurementRefused('candidate identity differs from resumed workspace')
    if meta.get('verification_context') != expected_context:
        raise InterruptedMeasurementRefused('verification context differs from resumed workspace')
    projection = measurement_projection(view, original, supplement)
    data = resume_inputs(view, original, supplement)
    for record in data['prior_records'].values():
        if record.get('process_outcome') != 'completed' or record.get('exit_code') != 0:
            raise InterruptedMeasurementRefused('non-clean task outcome requires separate reconciliation')
    classes = projection['per_task_scorer_isolation']
    for tid, verdict in data['verdicts'].items():
        if observed_class(verdict) != classes[tid]:
            raise InterruptedMeasurementRefused('task scorer isolation does not match verdict')
    isolation = common_class(classes.values())
    return {
        **projection,
        'dataset_runtime_identity': meta['dataset_runtime_identity'],
        'verification_context': expected_context,
        'scorer_isolation_class': isolation,
        'scorer_isolation_comparable': isolation != 'unknown',
        'score_std': 0.0,
        'invalid': {}, 'timeouts': {}, 'not_run': [], 'cached_tasks': [],
        'per_task_test_rate': {}, 'per_task_test_rate_trials': {},
        'test_rate': None, 'test_rate_n_tasks': 0,
        'tests_passed': None, 'tests_total': None,
        'env_usage': {'coverage_unknown': True, 'usage_complete': False},
        'integrity_status': 'unknown_interrupted_process',
        'status': 'recovered_evaluation_not_published',
    }


def make_recovery_loader(original: Path, supplement: Path, *, expected_round: int,
                         live_config: dict, expected_context: dict):
    """Build an explicit loader bound to a reviewed run and live task settings.

    The caller still owns method-journal admission, driver exclusion and
    experiment-specific platform/treatment review.
    """
    original = Path(original).resolve(strict=True)
    supplement = Path(supplement).resolve(strict=True)
    if (original == supplement or not isinstance(live_config, dict) or
            type(expected_round) is not int or expected_round < 1):
        raise InterruptedMeasurementRefused('invalid recovery source or live configuration')
    saved = _read_json(original / 'run_meta.json').get('config') or {}
    for section, field in (('run', 'seed'), ('run', 'trials'), ('agent', 'backend'),
                           ('agent', 'model'), ('agent', 'base_url')):
        old = (saved.get(section) or {}).get(field)
        new = (live_config.get(section) or {}).get(field)
        if old is None or new is None or old != new:
            raise InterruptedMeasurementRefused('live execution identity changed: ' + section + '.' + field)
    if not saved.get('sandbox') or saved.get('sandbox') != live_config.get('sandbox'):
        raise InterruptedMeasurementRefused('live execution identity changed: sandbox')
    def load(run_dir, round_no):
        if Path(run_dir).resolve(strict=True) != original or round_no != expected_round:
            raise InterruptedMeasurementRefused('recovery loader run or round changed')
        view = admit(original, supplement, round_no)
        return recovered_evaluation(view, original, supplement,
            expected_candidate_tree=view['candidate_tree_sha'], expected_context=expected_context)
    return load
