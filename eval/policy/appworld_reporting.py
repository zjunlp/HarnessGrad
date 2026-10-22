"""AppWorld TGC/SGC from selected public metadata and trusted verifier receipts.

No adapter import, dataset files, gold, model or runtime execution is needed.
The pinned release has three task variants per scenario. SGC is the conjunction
of their TGC successes within the same trial, never a conjunction of task means.
"""
from __future__ import annotations

import hashlib
import json
import re

TGC_METRIC = "appworld.task_goal_completion"
SGC_METRIC = "appworld.scenario_goal_completion"
SPLIT_LABELS = {"train": "Train", "dev": "Dev",
                "test_normal": "Test-N", "test_challenge": "Test-C"}
VARIANTS = (1, 2, 3)


def make_manifest(rows: list[dict]) -> list[dict]:
    """Project selected rows onto public reporting facts; discard all other fields."""
    manifest, seen, scenarios = [], set(), {}
    for row in rows:
        tid = row["task_id"]
        split = row.get("source_split", row.get("split"))
        if not isinstance(tid, str) or tid in seen:
            raise ValueError("task IDs must be unique strings")
        scenario, sep, suffix = tid.rpartition("_")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", scenario)
                or not sep or suffix not in {"1", "2", "3"}):
            raise ValueError(f"invalid AppWorld task ID: {tid!r}")
        if split not in SPLIT_LABELS:
            raise ValueError(f"unknown AppWorld source split: {split!r}")
        if scenario in scenarios and scenarios[scenario] != split:
            raise ValueError(f"scenario {scenario!r} crosses source splits")
        scenarios[scenario] = split
        seen.add(tid)
        manifest.append({"task_id": tid, "scenario_id": scenario,
                         "variant": int(suffix), "source_split": split,
                         "split_label": SPLIT_LABELS[split]})
    return sorted(manifest, key=lambda row: row["task_id"])


def receipts_from_verdicts(verdicts: list[dict], *, trials: int = 1) -> list[dict]:
    """Pure adapter from saved round verdict dictionaries to report receipts.

    Scores/means and generic ``passed`` flags cannot substitute for TGC evidence.
    Older verdicts without evidence remain unmeasured for this report.
    Explicit one-based ``trial`` identities on verdicts or evidence are retained.
    Legacy complete anonymous histories retain their ordered positions unless marked
    unaligned. The saved-verdict writer now marks all anonymous trials unaligned;
    their report slots provide TGC coverage only, never original identities for SGC.
    No file access, execution or mutation of the supplied dictionaries occurs here.
    """
    if type(trials) is not int or trials < 1:
        raise ValueError("trials must be a positive integer")
    receipts = []
    seen = set()
    for entry in verdicts:
        tid = entry["task_id"]
        if tid in seen:
            raise ValueError(f"duplicate saved verdict: {tid}")
        seen.add(tid)
        saved_trials = entry.get("trials")
        if saved_trials is None:
            saved_trials = [entry]
        if not isinstance(saved_trials, list) or not saved_trials:
            raise ValueError(f"invalid saved trials: {tid}")
        if len(saved_trials) > trials:
            raise ValueError(f"saved trials exceed requested coverage: {tid}")
        identities, used = [], set()
        for verdict in saved_trials:
            if not isinstance(verdict, dict):
                raise ValueError(f"invalid saved trial verdict: {tid}")
            evidence = verdict.get("evidence")
            if evidence is None:
                evidence = {}
            if not isinstance(evidence, dict):
                raise ValueError(f"invalid saved evidence: {tid}")
            declared = [source["trial"] for source in (verdict, evidence) if "trial" in source]
            for trial in declared:
                if type(trial) is not int or not 1 <= trial <= trials:
                    raise ValueError(f"trial outside requested coverage: {trial!r}")
            if len(declared) == 2 and declared[0] != declared[1]:
                raise ValueError(f"evidence trial mismatch: {tid}")
            identity = declared[0] if declared else None
            if identity is not None:
                if identity in used:
                    raise ValueError(f"duplicate task/trial receipt: {tid}, {identity}")
                used.add(identity)
            identities.append(identity)
        ordered = len(saved_trials) == trials and not used
        slots = iter(trial for trial in range(1, trials + 1) if trial not in used)
        for identity, verdict in zip(identities, saved_trials):
            trial = identity if identity is not None else next(slots)
            receipts.append({"task_id": tid, "trial": trial,
                             "evidence": dict(verdict.get("evidence") or {}),
                             "trial_aligned": (identity is not None or ordered)
                             and entry.get("trial_aligned", True) is True
                             and verdict.get("trial_aligned", True) is True,
                             "invalid": bool(entry.get("invalid") or verdict.get("invalid")),
                             "unmeasured": bool(entry.get("unmeasured") or verdict.get("unmeasured"))})
    return receipts


def build_report(manifest: list[dict], receipts: list[dict], *, trials: int = 1) -> dict:
    """Report fractions and coverage; incomplete scenario trials never become zero.

    ``manifest`` must name the selected tasks and their declared release splits.
    ``receipts`` are task/trial cells with trusted ``evidence`` dictionaries.
    Duplicate, unselected, out-of-range or contradictory receipts are refused.
    Absent evidence or an explicit unmeasured result stays ``unmeasured``;
    explicit failures or malformed measurement evidence are ``invalid``.
    """
    if type(trials) is not int or trials < 1:
        raise ValueError("trials must be a positive integer")
    manifest = make_manifest(manifest)
    metadata = {row["task_id"]: row for row in manifest}
    cells = {}
    for receipt in receipts:
        tid, trial = receipt["task_id"], receipt.get("trial", 1)
        if tid not in metadata:
            raise ValueError(f"unselected task receipt: {tid}")
        if type(trial) is not int or not 1 <= trial <= trials:
            raise ValueError(f"trial outside requested coverage: {trial!r}")
        if (tid, trial) in cells:
            raise ValueError(f"duplicate task/trial receipt: {tid}, {trial}")
        evidence = receipt.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise ValueError(f"invalid evidence: {tid}, {trial}")
        if evidence.get("task_id") not in (None, tid):
            raise ValueError(f"evidence task ID mismatch: {tid}")
        if evidence.get("source_split") not in (None, metadata[tid]["source_split"]):
            raise ValueError(f"evidence source split mismatch: {tid}")
        if "trial" in evidence and (type(evidence["trial"]) is not int
                                    or evidence["trial"] != trial):
            raise ValueError(f"evidence trial mismatch: {tid}, {trial}")
        measured = (not receipt.get("invalid") and not receipt.get("unmeasured")
                    and evidence.get("measured") is True
                    and evidence.get("metric") == TGC_METRIC
                    and type(evidence.get("success")) is bool)
        if measured:
            status = "measured"
        elif receipt.get("invalid"):
            status = "invalid"
        elif receipt.get("unmeasured") or not evidence or evidence.get("measured") is False:
            status = "unmeasured"
        else:
            status = "invalid"
        cells[tid, trial] = {"trial": trial,
                             "status": status,
                             "trial_aligned": receipt.get("trial_aligned", True) is True,
                             "success": evidence["success"] if measured else None}

    per_task, groups = {}, {}
    for row in manifest:
        tid = row["task_id"]
        task_trials = [cells.get((tid, trial), {"trial": trial, "status": "missing",
                                               "success": None})
                       for trial in range(1, trials + 1)]
        successes = [c["success"] for c in task_trials if c["status"] == "measured"]
        per_task[tid] = {**row, "TGC": _fraction(successes), "trials": task_trials}
        groups.setdefault(row["scenario_id"], []).append(tid)

    per_scenario = {}
    for scenario, ids in sorted(groups.items()):
        variants = sorted(metadata[tid]["variant"] for tid in ids)
        scenario_trials = []
        for trial in range(1, trials + 1):
            task_cells = [per_task[tid]["trials"][trial - 1] for tid in ids]
            status = ("incomplete_selection" if variants != list(VARIANTS) else
                      "unmeasured" if any(c["status"] != "measured" for c in task_cells) else
                      "unaligned_trials" if any(not c["trial_aligned"] for c in task_cells) else
                      "measured")
            scenario_trials.append({"trial": trial, "status": status,
                                    "success": all(c["success"] for c in task_cells)
                                    if status == "measured" else None})
        successes = [c["success"] for c in scenario_trials if c["status"] == "measured"]
        per_scenario[scenario] = {"source_split": metadata[ids[0]]["source_split"],
                                  "selected_variants": variants,
                                  "expected_variants": list(VARIANTS),
                                  "SGC": _fraction(successes), "trials": scenario_trials}

    def summary(tasks: dict, scenarios: dict) -> dict:
        task_cells = [c for task in tasks.values() for c in task["trials"]]
        scenario_cells = [c for scenario in scenarios.values() for c in scenario["trials"]]
        measured = [c["success"] for c in task_cells if c["status"] == "measured"]
        measured_scenarios = [c["success"] for c in scenario_cells if c["status"] == "measured"]
        invalid = sum(c["status"] == "invalid" for c in task_cells)
        missing = sum(c["status"] == "missing" for c in task_cells)
        unmeasured = sum(c["status"] == "unmeasured" for c in task_cells)
        return {"TGC": _fraction(measured), "SGC": _fraction(measured_scenarios),
                "coverage": {"selected_tasks": len(tasks), "trials": trials,
                             "expected_task_trials": len(task_cells),
                             "measured_task_trials": len(measured),
                             "invalid_task_trials": invalid, "missing_task_trials": missing,
                             "recorded_unmeasured_task_trials": unmeasured,
                             # Retain the aggregate non-measured coverage counter.
                             "unmeasured_task_trials": invalid + missing + unmeasured,
                             "selected_scenarios": len(scenarios),
                             "expected_scenario_trials": len(scenario_cells),
                             "measured_scenario_trials": len(measured_scenarios),
                             "unmeasured_scenario_trials": len(scenario_cells) - len(measured_scenarios)}}

    by_split = {}
    for split, label in SPLIT_LABELS.items():
        tasks = {tid: task for tid, task in per_task.items() if task["source_split"] == split}
        if tasks:
            scenarios = {sid: s for sid, s in per_scenario.items() if s["source_split"] == split}
            by_split[split] = {"label": label, **summary(tasks, scenarios)}
    manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {"dataset": "appworld", "schema_version": 2,
            "metrics": {"TGC": {"identity": TGC_METRIC, "scale": "fraction",
                                "definition": "mean of measured TestTracker.success task/trial cells"},
                        "SGC": {"identity": SGC_METRIC, "scale": "fraction",
                                "definition": "mean of same-trial success conjunctions over all three variants"}},
            "manifest_sha256": manifest_sha, "source_split_provenance": "declared",
            "comparability": "Selected manifest only; incomplete cells excluded with coverage. "
                             "This report does not certify a full official split or run validity.",
            **summary(per_task, per_scenario), "by_split": by_split,
            "per_task": per_task, "per_scenario": per_scenario}


def _fraction(successes: list[bool]) -> float | None:
    return sum(successes) / len(successes) if successes else None
