"""Historical record decoding shared by resume and H0; no resume/driver dependency."""
from __future__ import annotations

import json
from pathlib import Path
from harnessgrad import candidate_runtime, dataset_runtime_identity, verification_context


def read_trace_bodies(directory: Path) -> tuple[dict, dict]:
    """Read back the trace bodies this platform wrote for one round.

    `_save_traces` writes `traces[task_id]` **verbatim** to `<task>.jsonl`, so the file is
    the value and the read is lossless. Needed when a measurement is shared: the method's
    trace channel is staged from the in-memory dict, not from the run directory, so a
    shared H0 without its traces would hand the methods an empty channel -- the same
    unfairness sharing exists to remove.
    """
    traces: dict = {}
    trials: dict = {}
    if not directory.is_dir():
        return traces, trials
    for path in sorted(directory.glob("*.jsonl")):
        name = path.name[: -len(".jsonl")]
        if ".trial" in name:
            task, _, index = name.partition(".trial")
            try:
                trials.setdefault(task, []).append((int(index), path.read_text()))
            except ValueError:
                continue
        else:
            traces[name] = path.read_text()
    return traces, {task: [body for _, body in sorted(parts)]
                    for task, parts in trials.items()}


def require_verification_context(recorded, current, task_ids=None):
    if recorded is None or current is None:
        raise ValueError("verification context is missing; old measurements cannot be reused")
    if task_ids is not None:
        if not task_ids:
            raise ValueError("verification context has no selected task set")
        recorded = verification_context.project(recorded, task_ids)
        current = verification_context.project(current, task_ids)
    errors = verification_context.problems(recorded, current)
    if errors:
        raise ValueError("; ".join(errors))


def base_from_point(point: dict) -> dict:
    """Rebuild the measurement dictionary from a curve point.

    A curve point keeps everything the rest of the round-0 path reads: the per-task
    scores, the invalid and harness-failed records, the runtime and protocol records,
    and the trial spread. It does not keep the trace maps -- those live in the run's
    `round0_traces/`, which `--h0-from` copies. This exists so a run measured **before**
    `h0.json` was written can still be shared instead of being measured again: the
    number is the same number.
    """
    identity = point.get("identity") or {}
    base = {
        **dataset_runtime_identity.fields(point.get("dataset_runtime_identity")),
        **verification_context.fields(point.get("verification_context")),
        **candidate_runtime.fields(point.get("harness_runtime_identity")),
        "per_task": point.get("per_task") or {},
        "invalid": point.get("invalid"),
        "harness_failed": point.get("harness_failed"),
        "model_gateway": point.get("model_gateway"),
        "harness_runtime": (point.get("env") or {}).get("harness_runtime", point.get("harness_runtime")),
        "evaluation_protocols": point.get("evaluation_protocols"),
        "identity_declared": identity.get("harness_identity"),
        "trials": point.get("n_trials"),
        "score_std": point.get("score_std"),
        "per_task_std": point.get("per_task_std"),
        "per_task_trials": point.get("per_task_trials"),
        "cached_tasks": point.get("cached_tasks"),
    }
    return base


def read_progress_results(run_dir: Path) -> dict[str, dict]:
    """Per-task results an interrupted eval run already wrote.

    `evaluate()` records one JSON per finished task under `progress/round-0/`. Those files
    are the only evidence that a task was measured, so a resume reads them rather than
    trusting a receipt count.
    """
    out: dict[str, dict] = {}
    # task_file preserves namespaced task IDs as nested paths.
    for path in sorted((run_dir / "progress" / "round-0").rglob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        task_id = record.get("task_id")
        if task_id and record.get("complete"):
            out[task_id] = record
    return out
