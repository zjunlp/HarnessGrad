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
    # task_file stores namespaced IDs as subdirectories (e.g. ScienceWorld's
    # task-3-find-plant/41). Keep the complete relative ID when restoring evidence.
    for path in sorted(directory.rglob("*.jsonl")):
        name = path.relative_to(directory).as_posix()[: -len(".jsonl")]
        if ".trial" in path.stem:
            task, _, index = name.rpartition(".trial")
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
        "timeouts": point.get("timeouts"),
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


def reused_measurement(run_dir: Path, point: dict, history: list[dict]) -> dict:
    """Restore the actual evidence behind an admitted reused curve point.

    A copied point is an alias, not a fresh execution. Follow its recorded lineage
    so older aliases with incomplete verdict files do not erase native receipts.
    Admission of the current task/runtime identities remains the caller's job.
    """
    origin = point
    by_round = {entry["round"]: entry for entry in history}
    while origin.get("reused_from") is not None:
        number = origin["reused_from"]
        if type(number) is not int or number < 0 or number >= origin["round"] or number not in by_round:
            raise ValueError("invalid reused measurement lineage")
        earlier = by_round[number]
        if ((earlier.get("identity") or {}).get("harness_sha") !=
                (origin.get("identity") or {}).get("harness_sha")):
            raise ValueError("reused measurement source tree changed")
        ids = list(origin.get("per_task") or {})
        if any((earlier.get("per_task") or {}).get(tid) != origin["per_task"][tid] for tid in ids):
            raise ValueError("reused measurement scores changed")
        require_verification_context(earlier.get("verification_context"),
                                     origin.get("verification_context"), ids)
        origin = earlier
    number = origin["round"]
    result = base_from_point(point)
    wanted = set(result["per_task"])
    traces, trials = read_trace_bodies(run_dir / f"round{number}_traces")
    result["traces"] = {tid: body for tid, body in traces.items() if tid in wanted}
    result["trial_traces"] = {tid: bodies for tid, bodies in trials.items() if tid in wanted}
    result["verdicts"], result["trial_verdicts"], result["harness_output"] = {}, {}, {}
    for path in sorted((run_dir / "verdicts" / f"round-{number}").rglob("*.json")):
        record = json.loads(path.read_text())
        tid = record.get("task_id")
        if tid not in wanted:
            continue
        result["verdicts"][tid] = {k: v for k, v in record.items()
                                   if k not in ("task_id", "round", "harness", "trials", "trial_scores")}
        if record.get("harness"):
            result["harness_output"][tid] = record["harness"]
        if isinstance(record.get("trials"), list):
            scores = record.get("trial_scores") or []
            result["trial_verdicts"][tid] = [
                {**cell, **({"score": scores[i]} if i < len(scores) else {})}
                for i, cell in enumerate(record["trials"])]
    result["reused_evidence"] = {
        "source_round": number,
        "missing_trace_tasks": sorted(wanted - result["traces"].keys()),
        "missing_verdict_tasks": sorted(wanted - result["verdicts"].keys()),
    }
    return result


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
