"""The evidence pack: one reviewer concern, the runs behind it, and the receipt for each number.

## Why this module exists

A rebuttal answers a review, and a reviewer reading the answer asks a different question
from the one the answer states: *which run produced this number, what did it change, and
what else did you try that did not work?* On disk the platform already keeps most of that
-- `curve.jsonl`, `run_meta.json`, `diffs/round-N.patch`, `state_after_round<N>/` -- but
nothing joins a *concern* to the *runs* that address it, and nothing says which attempts
existed and were not reported.

The gap is not cosmetic. A follow-up experiment that only exports the run it likes is
indistinguishable, from the outside, from one that ran eleven attempts and reported the
best. The reviewer's request names exactly that: record the attempts, record how the
reported result was selected, and let the baseline comparison be read beside the search
budget and the variation across runs.

## What this module is not

It does not decide which experiment answers a concern. That is the author's judgement, and
a platform that guessed it would be writing the rebuttal. This module checks only the
self-consistency of manifest claims. It does not read receipts or verify scores, statuses,
rounds, diffs, costs, or ledger completeness against external evidence.

## The two levels

* `check_manifest` -- does this document hold together? Cross-references, statuses and the
  rule that a result must be a measurement. Problems are refusals; notes are facts worth
  printing that do not invalidate the pack (an older pack recorded fewer fields).
* `summary` -- what the pack contains, in the counts a reviewer scans first.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

SCHEMA = "harnessgrad.evidence.v1"

#: What an attempt is allowed to be. `measured` is the only status a result may cite;
#: the other four exist so that a reader can see the whole search, not the surviving run.
STATUSES = ("measured", "failed", "stopped", "invalid", "unmeasured")

#: Statuses that say something went wrong, and therefore owe a `reason`.
_MUST_EXPLAIN = ("failed", "stopped", "invalid", "unmeasured")

#: Fields every attempt carries. `rounds` is the states this attempt measured; a stopped
#: attempt may name the rounds it reached before it stopped.
_ATTEMPT_REQUIRED = ("run_id", "purpose", "status")

_TOP_REQUIRED = ("schema", "created_utc", "submission", "requests", "attempts",
                 "selection", "budget")


class EvidenceError(ValueError):
    """The document cannot be read at all -- not a finding about its content."""


def finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def load(path: str | Path) -> dict:
    """Read a manifest. A malformed file is an error, not a validation problem."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceError(f"no evidence manifest at {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise EvidenceError(f"{path} is not a JSON object")
    return document


def write(document: dict, path: str | Path) -> Path:
    """Write a manifest deterministically, so two packs that say the same thing hash alike.

    The pack is meant to be attached to a submission and diffed against a later one; a
    writer that reordered keys from run to run would put noise in that diff.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def _attempts(document: dict) -> dict[str, dict]:
    return {a.get("run_id"): a for a in document.get("attempts") or [] if isinstance(a, dict)}


def _rounds(attempt: dict | None) -> set[int]:
    if not attempt:
        return set()
    return {r for r in attempt.get("rounds") or [] if type(r) is int}


def _shape_problems(document: dict) -> list[str]:
    """Check types before looking up cross-references in untrusted manifests."""
    errors = []

    def field(obj, key, kind, where, nullable=False):
        if key in obj and not (nullable and obj[key] is None) and not isinstance(obj[key], kind):
            errors.append(f"{where}.{key} has an invalid type")

    for key, kind in (("attempts", list), ("requests", list), ("submission", dict),
                      ("selection", dict), ("budget", dict)):
        field(document, key, kind, "manifest")
    for group in ("attempts", "requests"):
        entries = document.get(group, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                errors.append(f"{group} entry is not an object")
                continue
            if group == "attempts":
                for key in ("run_id", "purpose", "status", "reason"):
                    field(entry, key, str, group, nullable=key == "reason")
                field(entry, "rounds", list, group)
                if isinstance(entry.get("rounds"), list) and any(
                    type(r) is not int or r < 0 for r in entry["rounds"]
                ):
                    errors.append("attempts.rounds must contain nonnegative integers")
            else:
                for key in ("id", "concern", "status", "reason"):
                    field(entry, key, str, group, nullable=key == "reason")
                field(entry, "runs", list, group)
                if isinstance(entry.get("runs"), list) and any(
                    not isinstance(r, str) or not r for r in entry["runs"]
                ):
                    errors.append("requests.runs must contain nonempty run IDs")
                for key in ("result", "vs_baseline"):
                    field(entry, key, dict, group, nullable=True)
                result = entry.get("result")
                if isinstance(result, dict):
                    field(result, "run_id", str, "result")
                    field(result, "metric", str, "result")
                baseline = entry.get("vs_baseline")
                if isinstance(baseline, dict):
                    field(baseline, "baseline_run", str, "vs_baseline")
                    field(baseline, "diff", dict, "vs_baseline", nullable=True)
    for key in ("submission", "selection"):
        obj = document.get(key)
        if not isinstance(obj, dict):
            continue
        if key == "submission":
            field(obj, "run_id", str, key)
        else:
            field(obj, "rule", str, key)
            field(obj, "reported", dict, key, nullable=True)
            field(obj, "rejected", list, key)
            if isinstance(obj.get("reported"), dict):
                field(obj["reported"], "run_id", str, "selection.reported")
            if isinstance(obj.get("rejected"), list):
                for rejected in obj["rejected"]:
                    if not isinstance(rejected, dict):
                        errors.append("selection.rejected entry is not an object")
                    else:
                        field(rejected, "run_id", str, "selection.rejected")
                        field(rejected, "why", str, "selection.rejected")
    if isinstance(document.get("budget"), dict):
        field(document["budget"], "source", str, "budget")
    return errors


def check_manifest(document: dict) -> tuple[list[str], list[str]]:
    """Problems (the pack is not usable) and notes (true, but not a defect)."""
    problems: list[str] = []
    notes: list[str] = []

    if not isinstance(document, dict):
        return ["manifest is not a JSON object"], notes
    for field in _TOP_REQUIRED:
        if field not in document:
            problems.append(f"missing top-level field: {field}")
    if document.get("schema") != SCHEMA:
        problems.append(f"schema is {document.get('schema')!r}, expected {SCHEMA!r}")
    problems.extend(_shape_problems(document))
    if problems:
        return problems, notes

    attempts = _attempts(document)
    if len(attempts) != len(document.get("attempts") or []):
        problems.append("two attempts share a run_id; a run id names exactly one attempt")

    for attempt in document.get("attempts") or []:
        if not isinstance(attempt, dict):
            problems.append("an attempt is not an object")
            continue
        name = attempt.get("run_id", "<unnamed>")
        for field in _ATTEMPT_REQUIRED:
            if not attempt.get(field):
                problems.append(f"attempt {name}: missing {field}")
        status = attempt.get("status")
        if status not in STATUSES:
            problems.append(f"attempt {name}: status {status!r} is not one of {', '.join(STATUSES)}")
        if status in _MUST_EXPLAIN and not attempt.get("reason"):
            problems.append(
                f"attempt {name}: a {status} attempt has to say why in `reason`; "
                "an unexplained gap is the one thing a reader cannot check")
        if status == "measured" and not _rounds(attempt):
            problems.append(f"attempt {name}: a measured attempt has to name the rounds it measured")

    submission = document.get("submission") or {}
    reported_run = submission.get("run_id")
    if not reported_run:
        problems.append("submission has no run_id")
    if type(submission.get("round")) is not int or submission["round"] < 0:
        problems.append("submission has no valid round")
    if reported_run and reported_run not in attempts:
        problems.append(f"submission run {reported_run} is not in the attempt ledger")
    elif reported_run:
        if attempts[reported_run].get("status") != "measured":
            problems.append(
                f"submission run {reported_run} has status "
                f"{attempts[reported_run].get('status')!r}; the submitted number has to be a measurement")
        elif isinstance(submission.get("round"), int) and submission["round"] not in _rounds(attempts[reported_run]):
            problems.append(
                f"submission round {submission['round']} was not measured by {reported_run}")

    seen: set[str] = set()
    for request in document.get("requests") or []:
        if not isinstance(request, dict):
            problems.append("a request is not an object")
            continue
        rid = request.get("id")
        if not rid:
            problems.append("a request has no id")
            continue
        if rid in seen:
            problems.append(f"request {rid} appears twice")
        seen.add(rid)
        if not request.get("concern"):
            problems.append(f"request {rid}: missing the author's statement of the concern")
        cited = list(request.get("runs") or [])
        for run_id in cited:
            if run_id not in attempts:
                problems.append(f"request {rid} cites run {run_id}, which is not in the attempt ledger")
        status = request.get("status")
        if status not in (None, "measured", "unmeasured"):
            problems.append(f"request {rid}: unknown status {status!r}")
        if status == "unmeasured":
            if not request.get("reason"):
                problems.append(
                    f"request {rid}: an unmeasured request has to say why -- that the evidence "
                    "does not exist yet is the honest answer, not a silent omission")
            if request.get("result"):
                problems.append(f"request {rid}: an unmeasured request cannot carry a result")
        else:
            if not cited:
                problems.append(f"request {rid}: names no run and does not declare itself unmeasured")
            result = request.get("result") or {}
            if not result:
                problems.append(f"request {rid}: missing the result this request reports")
            else:
                if not result.get("metric"):
                    problems.append(f"request {rid}: result has no metric")
                value = result.get("value")
                if not finite_number(value):
                    problems.append(f"request {rid}: result has no finite numeric value")
                run_id = result.get("run_id")
                attempt = attempts.get(run_id)
                if attempt is None:
                    problems.append(f"request {rid}: result cites run {run_id}, which is not in the ledger")
                elif attempt.get("status") != "measured":
                    problems.append(
                        f"request {rid}: result cites a {attempt.get('status')} attempt; "
                        "a failed, stopped, invalid or unmeasured attempt is not a result")
                else:
                    round_number = result.get("round")
                    if type(round_number) is not int or round_number < 0:
                        problems.append(f"request {rid}: result has no round; a score belongs to a state")
                    elif round_number not in _rounds(attempt):
                        problems.append(
                            f"request {rid}: result round {round_number} was not measured by {run_id}")
                    if run_id not in cited:
                        problems.append(f"request {rid}: its result run {run_id} is not among its cited runs")

        baseline = request.get("vs_baseline")
        if baseline:
            base_run = baseline.get("baseline_run")
            if base_run not in cited:
                problems.append(f"request {rid}: baseline run {base_run} is not among its cited runs")
            if base_run not in attempts:
                problems.append(f"request {rid}: baseline run {base_run} is not in the ledger")
            elif attempts[base_run].get("status") != "measured":
                problems.append(
                    f"request {rid}: baseline run {base_run} is not a measurement, so the "
                    "comparison has nothing to stand on")
            if not (baseline.get("diff") or {}).get("changed"):
                notes.append(
                    f"request {rid}: the baseline diff is empty; a follow-up that changed "
                    "nothing is allowed to say so, but the reader will ask")

    selection = document.get("selection") or {}
    reported = selection.get("reported") or {}
    if not reported:
        problems.append("selection has no reported result")
    else:
        if type(reported.get("round")) is not int or reported["round"] < 0:
            problems.append("selection has no valid reported round")
        if (reported.get("run_id"), reported.get("round")) != (submission.get("run_id"), submission.get("round")):
            problems.append("selection reported result does not match submission")
    if reported:
        run_id = reported.get("run_id")
        attempt = attempts.get(run_id)
        if attempt is None:
            problems.append(f"selection cites run {run_id}, which is not in the ledger")
        elif attempt.get("status") != "measured":
            problems.append(f"selection cites a {attempt.get('status')} attempt as the reported result")
        elif isinstance(reported.get("round"), int) and reported["round"] not in _rounds(attempt):
            problems.append(
                f"selection round {reported['round']} was not measured by {run_id}")
    if not selection.get("rule"):
        problems.append(
            "selection has no rule: the sentence that says how the reported result was chosen "
            "is the difference between a search and a cherry-pick")
    for rejected in selection.get("rejected") or []:
        if reported and rejected.get("run_id") == reported.get("run_id"):
            problems.append(f"selection rejects reported run {rejected.get('run_id')}")
        if rejected.get("run_id") not in attempts:
            problems.append(f"selection rejects run {rejected.get('run_id')}, which is not in the ledger")
        if not rejected.get("why"):
            problems.append(
                f"selection rejects run {rejected.get('run_id', '<unnamed>')} without saying why")

    budget = document.get("budget") or {}
    if not budget.get("source"):
        problems.append(
            "budget has no `source`: the pack has to say where the search cost came from "
            "(recorded cost fields, a provider invoice, or a stated estimate) instead of "
            "presenting a number with no provenance")
    if type(budget.get("rounds")) is not int or budget["rounds"] < 0:
        problems.append("budget has no round count; the budget is part of the result")

    return problems, notes


def summary(document: dict) -> dict:
    """The counts a reviewer scans before reading anything else."""
    attempts = [a for a in document.get("attempts") or [] if isinstance(a, dict)]
    by_status: dict[str, int] = {status: 0 for status in STATUSES}
    for attempt in attempts:
        status = attempt.get("status")
        if status in by_status:
            by_status[status] += 1
    requests = [r for r in document.get("requests") or [] if isinstance(r, dict)]
    answered = [r for r in requests if (r.get("result") or {}).get("run_id")]
    return {
        "schema": document.get("schema"),
        "requests": len(requests),
        "answered": len(answered),
        "unmeasured": sum(1 for r in requests if r.get("status") == "unmeasured"),
        "attempts": len(attempts),
        "by_status": by_status,
        "rounds": (document.get("budget") or {}).get("rounds"),
    }


def referenced_runs(document: dict) -> set[str]:
    """Every run id the pack mentions, so an exporter can check the bundle carries them."""
    runs = set(_attempts(document))
    for request in document.get("requests") or []:
        if isinstance(request, dict):
            runs.update(request.get("runs") or [])
            result = request.get("result") or {}
            if result.get("run_id"):
                runs.add(result["run_id"])
            baseline = request.get("vs_baseline") or {}
            if baseline.get("baseline_run"):
                runs.add(baseline["baseline_run"])
    selection = document.get("selection") or {}
    for key in ("reported",):
        entry = selection.get(key) or {}
        if entry.get("run_id"):
            runs.add(entry["run_id"])
    for rejected in selection.get("rejected") or []:
        if isinstance(rejected, dict) and rejected.get("run_id"):
            runs.add(rejected["run_id"])
    return runs


def iter_problems(document: dict) -> Iterable[str]:
    problems, _ = check_manifest(document)
    return problems
