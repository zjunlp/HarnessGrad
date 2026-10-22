"""Staged selection and immutable receipts; no model, scorer or runtime calls.

Evidence is supplied by the trusted orchestrator. Structural validation cannot
authenticate a gate/review or prove a runtime cleaned up. Unknown facts remain
explicit and cannot pass the expansion gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

from eval.run.metrics import diagnostic_task_set, pilot_task_set
from eval.admission import lifecycle_receipt
from eval.policy import unobserved_diagnostic

SCHEMA = "harnessgrad.diagnostic-receipt.v2"
HASH_NAMES = ("source", "split", "manifest", "model", "harness")
#: The local endpoints a staged receipt may bind. **One entry, deliberately.** This
#: listing is what a receipt's `endpoint` is checked against, so an entry here is a
#: claim that the endpoint exists: leaving `8001` in after its server was retired on
#: 2026-10-14 would let a receipt attest to a binding nothing could serve. `8002` is
#: the one that was kept because `eval/unix_model_relay.py` routes the agent's model
#: there, so it is the endpoint a containerised harness actually reaches. Adding a
#: second server again means adding it back here at the same time.
ENDPOINTS = ("http://127.0.0.1:8002/v1",)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def receipt_digest(receipt: dict | list) -> str:
    """Bind a separate review to the exact receipt, including all failed attempts."""
    body = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def eligible_digest(eligible: list[str]) -> str:
    """Bind the entire eligible ID pool without exporting its task payloads."""
    _require(isinstance(eligible, list) and all(_text(t) for t in eligible),
             "eligible IDs must be strings")
    return receipt_digest(sorted(set(eligible)))


def validate_context(context: dict) -> None:
    """Required evidence fields, with null accepted for explicitly unknown facts."""
    _require(isinstance(context, dict), "context must be an object")
    _require(_text(context.get("dataset")) and context.get("side") in ("train", "eval"),
             "context must bind dataset and side")
    metric = context.get("metric")
    _require(isinstance(metric, dict) and _text(metric.get("name"))
             and "scorer_sha256" in metric
             and (metric["scorer_sha256"] is None or _sha(metric["scorer_sha256"])),
             "metric requires a name and scorer_sha256 (or null)")
    hashes = context.get("hashes")
    _require(isinstance(hashes, dict) and all(
        key in hashes and (hashes[key] is None or _sha(hashes[key])) for key in HASH_NAMES),
        "source/split/manifest/model/harness hashes must be SHA256 or null")
    _require("endpoint" in context and (context["endpoint"] is None
             or context["endpoint"] in ENDPOINTS), "endpoint must bind one local GPU or be null")
    limits = context.get("limits")
    _require(isinstance(limits, dict) and all(
        key in limits and (limits[key] is None or (
            isinstance(limits[key], dict) and bool(limits[key]) and all(
                _text(k) and type(v) in (int, float) and math.isfinite(v) and v >= 0
                for k, v in limits[key].items()))) for key in ("resource", "budget")),
        "limits require resource and budget objects with nonnegative finite values (or null)")
    notes = context.get("comparability_notes")
    _require(isinstance(notes, list) and bool(notes) and all(_text(n) for n in notes),
             "comparability_notes must explicitly describe comparison limits")
    validate_lifecycle(context.get("gates"), "gates")


def validate_lifecycle(value: dict, name: str) -> None:
    _require(isinstance(value, dict) and value.get("status") in ("passed", "failed", "unknown")
             and "receipt_sha256" in value
             and (value["receipt_sha256"] is None or _sha(value["receipt_sha256"]))
             and _text(value.get("detail")), f"{name} requires status, receipt_sha256 and detail")
    _require(value["status"] != "passed" or _sha(value["receipt_sha256"]),
             f"{name} passed requires a receipt hash")


def staged_task_set(eligible: list[str], stage: str, *, trials: int = 1,
                    jobs: int = 1, rounds: int = 0) -> list[str]:
    _require(isinstance(eligible, list) and all(_text(t) for t in eligible),
             "eligible IDs must be strings")
    _require(stage in ("diagnostic", "pilot_10pct"), "unknown staged policy")
    _require(type(trials) is int and trials == 1 and type(jobs) is int and jobs == 1,
             "staged diagnostics and temporary pilots require trials=1, jobs=1")
    _require(type(rounds) is int and rounds == 0,
             "staged diagnostics and temporary pilots measure H0 only (rounds=0)")
    return diagnostic_task_set(eligible) if stage == "diagnostic" else pilot_task_set(eligible)


def measurement_summary(selected: list[str], outcomes: list[dict]) -> dict:
    """Partition one-trial outcomes. Missing records are unmeasured, never zero.

    Infrastructure/timeout/provider/scorer failures must be supplied as invalid
    or unmeasured. A measured zero requires a successful measurement contract.
    """
    _require(isinstance(selected, list) and all(_text(t) for t in selected)
             and len(set(selected)) == len(selected), "selected task IDs must be unique strings")
    _require(isinstance(outcomes, list), "outcomes must be a list")
    rows = {}
    for row in outcomes:
        _require(isinstance(row, dict) and row.get("task_id") in selected,
                 "outcome must name a selected task")
        tid = row["task_id"]
        _require(tid not in rows and type(row.get("trial")) is int and row["trial"] == 1,
                 "duplicate outcome or trial outside the one-trial plan")
        status = row.get("status")
        _require(status in ("measured", "invalid", "unmeasured"), "unknown outcome status")
        _require("score" in row and "reason" in row, "outcome requires explicit score and reason")
        if status == "measured":
            score = row["score"]
            _require(type(score) in (int, float) and math.isfinite(score)
                     and row["reason"] is None,
                     "measured outcome needs a finite score; metric-specific range is scorer-owned")
        else:
            _require(row["score"] is None and _text(row["reason"]),
                     "invalid/unmeasured outcomes require score=null and a reason")
        rows[tid] = copy.deepcopy(row)
    ordered = [rows.get(t, dict(task_id=t, trial=1, status="unmeasured", score=None,
                               reason="no outcome recorded")) for t in selected]
    measured = [r["score"] for r in ordered if r["status"] == "measured"]
    counts = {s: sum(r["status"] == s for r in ordered)
              for s in ("measured", "invalid", "unmeasured")}
    return {"outcomes": ordered, "score": sum(measured) / len(measured) if measured else None,
            "coverage": {"selected_tasks": len(selected), "expected_trials": len(selected),
                         **{f"{s}_{unit}": n for s, n in counts.items()
                            for unit in ("tasks", "trials")}}}


def scope_digest(*, stage: str, eligible_sha256: str, selected: list[str], context: dict) -> str:
    """Bind selection, provenance, metric, endpoint and limits before execution."""
    return receipt_digest(dict(stage=stage, eligible_task_ids_sha256=eligible_sha256,
                               selected_task_ids=selected, context=context))


def _scope_binding(receipt: dict) -> dict:
    return dict(run_id=receipt["run_id"], manifest_sha256=receipt["context"]["hashes"]["manifest"],
                scope_sha256=scope_digest(stage=receipt["stage"],
                                         eligible_sha256=receipt["eligible_task_ids_sha256"],
                                         selected=receipt["selected_task_ids"], context=receipt["context"]))


def _require_publication(receipt: dict, summary: dict) -> None:
    _require(receipt["context"]["gates"]["status"] == "passed" and all(
        receipt[k]["status"] == "passed" for k in ("cleanup", "snapshot")),
        "gates, cleanup and snapshot must pass before measured score publication")
    lifecycle_receipt.require_publication(
        receipt.get("lifecycle"), **_scope_binding(receipt),
        outcomes_sha256=receipt_digest(summary["outcomes"]),
        cleanup_sha256=receipt["cleanup"]["receipt_sha256"],
        snapshot_sha256=receipt["snapshot"]["receipt_sha256"])


def build_receipt(*, run_id: str, stage: str, eligible: list[str], selected: list[str],
                  context: dict, outcomes: list[dict], cleanup: dict, snapshot: dict,
                  lifecycle: dict | None = None, enforcement: dict | None = None) -> dict:
    """Create a receipt from observed evidence, including incomplete runs."""
    _require(_text(run_id), "run_id is required")
    _require(selected == staged_task_set(eligible, stage),
             "selected tasks must match the deterministic plan before execution")
    validate_context(context)
    validate_lifecycle(cleanup, "cleanup")
    validate_lifecycle(snapshot, "snapshot")
    if enforcement is not None:
        unobserved_diagnostic.validate_record(enforcement, run_id=run_id, selected=selected, stage=stage)
        # Execution permission is not a measurement contract, even if a scorer ran.
        outcomes = [dict(row, status="unmeasured", score=None,
                         reason=unobserved_diagnostic.REASON)
                    if row.get("status") == "measured" else row for row in outcomes]
    summary = measurement_summary(selected, outcomes)
    summary["coverage"]["eligible_tasks"] = len(set(eligible))
    if lifecycle is not None:
        lifecycle_receipt.validate_lifecycle_receipt(lifecycle)
    receipt = dict(schema=SCHEMA, run_id=run_id, stage=stage, trials=1, jobs=1,
                   eligible_task_ids_sha256=eligible_digest(eligible),
                   selected_task_ids=list(selected), context=copy.deepcopy(context),
                   cleanup=copy.deepcopy(cleanup), snapshot=copy.deepcopy(snapshot),
                   lifecycle=copy.deepcopy(lifecycle), **summary)
    if enforcement is not None:
        receipt["enforcement"] = copy.deepcopy(enforcement)
    if lifecycle is not None:
        lifecycle_receipt.require_scope(lifecycle, **_scope_binding(receipt))
    if summary["coverage"]["measured_trials"]:
        if (lifecycle is None or not lifecycle["publication_ready"]
                or context["gates"]["status"] != "passed"
                or cleanup["status"] != "passed" or snapshot["status"] != "passed"):
            # Scores are provisional until lifecycle completion. Persist uncertainty,
            # never a measured zero, including individual task/trial score fields.
            withheld = [dict(row, status="unmeasured", score=None,
                             reason="score withheld: cleanup and snapshot lifecycle publication gate pending")
                        if row["status"] == "measured" else row for row in summary["outcomes"]]
            receipt.update(measurement_summary(selected, withheld))
            receipt["coverage"]["eligible_tasks"] = len(set(eligible))
        else:
            _require_publication(receipt, summary)
    return receipt


def validate_receipt(receipt: dict) -> None:
    _require(isinstance(receipt, dict) and receipt.get("schema") == SCHEMA, "unsupported receipt schema")
    _require(_text(receipt.get("run_id")), "receipt run_id is required")
    _require(_sha(receipt.get("eligible_task_ids_sha256")), "eligible ID pool hash is required")
    staged_task_set([], receipt.get("stage"), trials=receipt.get("trials"), jobs=receipt.get("jobs"))
    validate_context(receipt.get("context"))
    if "enforcement" in receipt:
        unobserved_diagnostic.validate_record(
            receipt["enforcement"], run_id=receipt["run_id"],
            selected=receipt.get("selected_task_ids"), stage=receipt.get("stage"))
        _require(receipt.get("score") is None and not any(
            r.get("status") == "measured" for r in receipt.get("outcomes", [])),
            "unobserved diagnostic cannot publish measured scores")
    for name in ("cleanup", "snapshot"):
        validate_lifecycle(receipt.get(name), name)
    _require("lifecycle" in receipt, "receipt requires explicit lifecycle or null")
    if receipt["lifecycle"] is not None:
        lifecycle_receipt.validate_lifecycle_receipt(receipt["lifecycle"])
    summary = measurement_summary(receipt.get("selected_task_ids"), receipt.get("outcomes"))
    if receipt["lifecycle"] is not None:
        lifecycle_receipt.require_scope(receipt["lifecycle"], **_scope_binding(receipt))
    _require(summary["outcomes"] == receipt["outcomes"], "receipt must include every selected outcome")
    coverage = receipt.get("coverage")
    _require(isinstance(coverage, dict), "receipt coverage must be an object")
    eligible = coverage.get("eligible_tasks")
    _require(type(eligible) is int and eligible > 0, "receipt eligible_tasks must be positive")
    expected = 1 if receipt["stage"] == "diagnostic" else min(30, (eligible + 9) // 10)
    _require(len(receipt["selected_task_ids"]) == expected, "selected count differs from staged policy")
    _require(all(type(coverage.get(k)) is int and coverage[k] == v
                 for k, v in summary["coverage"].items()), "inconsistent task/trial coverage")
    _require("score" in receipt and receipt["score"] == summary["score"]
             and (receipt.get("score") is None or type(receipt["score"]) in (int, float)),
             "receipt score differs from measured outcomes")
    if summary["coverage"]["measured_trials"]:
        _require_publication(receipt, summary)


def evaluation_outcomes(result: dict, selected: list[str]) -> list[dict]:
    """Adapt a one-trial runner result without assigning zeros to failures."""
    _require(type(result.get("trials")) is int and result["trials"] == 1,
             "staged receipt requires one-trial evaluation evidence")
    rows = []
    for tid in selected:
        verdict = (result.get("verdicts") or {}).get(tid) or {}
        failure = (result.get("invalid") or {}).get(tid)
        if any((result.get("tampered") or {}).values()):
            status, score, reason = "invalid", None, "platform changed during evaluation"
        elif failure is not None:
            status, score, reason = "invalid", None, json.dumps(failure, sort_keys=True)
        elif verdict.get("unmeasured"):
            status, score, reason = "unmeasured", None, str(verdict["unmeasured"])
        elif tid in (result.get("not_run") or []) or tid in (result.get("harness_failed") or {}):
            status, score, reason = "invalid", None, "harness did not run or reported a runtime failure"
        elif tid in (result.get("cached_tasks") or []):
            status, score, reason = "unmeasured", None, "cached result; no fresh diagnostic trial"
        elif tid in (result.get("harness_output") or {}) and (
                type(result["harness_output"][tid].get("exit_code")) is not int
                or result["harness_output"][tid]["exit_code"] != 0):
            # run_one records timeouts as exit -1. A trace with usage can keep
            # harness_failed empty, and the general runner can retain an artifact
            # score after a crash. Neither proves completed staged execution.
            exit_code = result["harness_output"][tid].get("exit_code")
            status = "invalid" if type(exit_code) is int else "unmeasured"
            score, reason = None, f"staged execution did not complete with integer exit 0: {exit_code!r}"
        elif tid in (result.get("per_task") or {}):
            status, score, reason = "measured", result["per_task"][tid], None
        else:
            status, score, reason = "unmeasured", None, "no outcome recorded"
        rows.append(dict(task_id=tid, trial=1, status=status, score=score, reason=reason))
    return rows


def require_review(bundle: dict, *, context: dict, eligible: list[str], run_id: str | None = None) -> None:
    """Expansion requires a distinct, hash-bound review of a complete diagnostic."""
    _require(isinstance(bundle, dict), "diagnostic review must be an object")
    receipt = bundle.get("receipt")
    validate_receipt(receipt)
    _require("enforcement" not in receipt,
             "unobserved diagnostic cannot authorize expansion")
    _require(receipt["stage"] == "diagnostic", "expansion requires a diagnostic receipt")
    _require(receipt["coverage"]["eligible_tasks"] == len(set(eligible))
             and receipt["eligible_task_ids_sha256"] == eligible_digest(eligible)
             and receipt["selected_task_ids"] == diagnostic_task_set(eligible),
             "diagnostic does not match the current eligible pool")
    validate_context(context)
    for key in ("dataset", "side", "metric", "hashes", "endpoint", "limits", "gates"):
        _require(receipt["context"][key] == context[key], f"diagnostic context changed: {key}")
    _require(all(_sha(context["hashes"][k]) for k in HASH_NAMES)
             and _sha(context["metric"]["scorer_sha256"]) and context["endpoint"] in ENDPOINTS
             and all(context["limits"][k] is not None for k in ("resource", "budget")),
             "unknown provenance, endpoint or limits cannot authorize expansion")
    _require(context["gates"]["status"] == "passed" and all(
        receipt[k]["status"] == "passed" for k in ("cleanup", "snapshot")),
        "gates, cleanup and snapshot must have passing evidence")
    _require(receipt["coverage"]["measured_trials"] == 1
             and receipt["coverage"]["invalid_trials"] == 0
             and receipt["coverage"]["unmeasured_trials"] == 0,
             "expansion requires a valid measured diagnostic")
    review = bundle.get("review")
    _require(isinstance(review, dict) and review.get("approved") is True
             and _text(review.get("reviewer")) and _text(review.get("reviewed_at"))
             and review.get("receipt_sha256") == receipt_digest(receipt),
             "separate approved review must bind the exact diagnostic receipt")
    _require(run_id is None or run_id != receipt["run_id"], "expansion requires a fresh run ID")


def write_receipt(path: Path, receipt: dict) -> None:
    """Exclusive creation preserves old and incomplete evidence."""
    validate_receipt(receipt)
    body = json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(body)


def reserve_run_directory(root: Path, run_id: str) -> Path:
    """A fresh staged ID is a single directory component, never a resume target."""
    _require(_text(run_id) and run_id not in (".", "..")
             and Path(run_id).name == run_id, "run_id must be a single directory name")
    path = Path(root) / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path
