#!/usr/bin/env python3
"""Turn measured run records into the data the website publishes.

The rule this exists to enforce: **a number on the site must be traceable to
`runs/<id>/curve.jsonl`**. Nothing is typed by hand, and nothing is fetched at build time --
the exporter writes a snapshot that goes into the repository, and the site build reads only
that snapshot, so CI never needs this machine's paths.

What it writes (all under the website branch checkout root):

    src/data/results/<slug>.json                     the snapshot, machine-readable
    src/content/docs/benchmarks/<slug>.mdx           the page, tables generated from the snapshot
    public/results/<slug>/<chart>.svg                line charts, one per evaluated pool

The page is generated rather than hand-written for the same reason: a hand-written table
drifts the first time a run is added, and then the site and the report disagree with no way to
tell which one is wrong.

Usage:
    python3 export-site.py                # write everything
    python3 export-site.py --check        # report what would change, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

D = pathlib.Path(__file__).resolve().parent
#: This repository **is** the site, so the default output root is the repository root (this
#: file lives in `scripts/export/`). Override with `HG_SITE` only when exporting into a copy.
SITE = pathlib.Path(os.environ.get("HG_SITE") or D.parents[1])
#: The **run receipts** the page is a projection of. They are 28 GB of `runs/<id>/` trees and
#: live outside every repository; nothing here vendors them. Point at them with `HG_RUNS`
#: (see `scripts/export/README.md`) -- an export with no receipts would silently produce an
#: empty page, so it refuses instead.
RUNS = pathlib.Path(os.environ.get("HG_RUNS") or D / "runs")
if not RUNS.is_dir():
    raise SystemExit(
        f"export-site: no run receipts at {RUNS}. Set HG_RUNS to the directory holding the "
        f"run records (one subdirectory per run id) -- see scripts/export/README.md.")
#: Which split of the study the page is for. Pinned in the repository so two exports of the
#: same receipts cannot disagree about it; `HG_STAMP` overrides for a one-off.
STAMP = pathlib.Path(os.environ.get("HG_STAMP") or (D / "split.stamp")).read_text().strip()
#: Where the data lives on the Hub. Read, never duplicated: the directory names are a property of
#: the dataset, so a page that builds its own copy of them is a page that can point at a directory
#: that does not exist (or worse, at one belonging to a different improver).
HUB = SITE / "src" / "data" / "hub.json"


def hub_entry(slug: str = "alfworld") -> dict:
    if not HUB.is_file():
        return {}
    return (json.loads(HUB.read_text(encoding="utf-8")) or {}).get(slug) or {}


def baseline_url(slug: str = "alfworld") -> str | None:
    """The Hub directory holding the H0 baseline, or nothing when none of it is published."""
    entry = hub_entry(slug)
    baseline = entry.get("baseline") or {}
    if entry.get("status") != "published" or not entry.get("dataset"):
        return None
    if not (baseline.get("pools") or []):
        return None
    path = "/".join(p for p in (entry.get("root"), baseline.get("dir")) if p)
    return f"https://huggingface.co/datasets/{entry['dataset']}/tree/main/{path}/"


def record_url(method: str, improver_model: str | None, slug: str = "alfworld") -> str | None:
    """The Hub directory holding one method's record, or nothing when it is not published."""
    entry = hub_entry(slug)
    if entry.get("status") != "published" or not entry.get("dataset"):
        return None
    family = (entry.get("families") or {}).get(improver_model)
    if not family:
        return None
    path = "/".join(p for p in (entry.get("root"), entry.get("agent"), family, method) if p)
    return f"https://huggingface.co/datasets/{entry['dataset']}/tree/main/{path}/"

#: The six methods the study settled on, plus the two the A line re-ran. `rrsi` is dropped
#: (0.425, data kept).
#:
#: **Two of those eight are withdrawn (2026-10-20) pending a re-run.** Their rounds were partly
#: produced by the endpoint fallback -- a chat completion that could see at most 60,000 characters
#: of a harness it was asked to rewrite whole, because a method that edits an *older* state lost
#: the CLI improver the run had resolved (findings §116/§117). On this dataset that cost less than
#: on AppWorld (the collapsed harness still solved ~60%), which is exactly why it has to be said
#: rather than inferred from the numbers: ALFWorld `meta_harness`'s best round (.70) came from a
#: generation the fallback produced, and `dream_rsi` r6/r7 were edits of the same kind.
#: `meta_harness` is deliberately absent (withdrawn 2026-10-21 by operator decision -- its
#: .600 is the weakest curve of the set and is not published; see `WITHDRAWN`). `METHODS` is
#: the list this exporter publishes, so dropping a method here is what removes its row.
METHODS = ("sica_ci", "ahe", "harnessx", "tthe",
           "dgm", "dream_rsi", "hyperagents")
#: Kept out of the table and the charts, kept in `runs/`, the findings and CKPT-*.md.
#: **Withdrawn 2026-10-20, all four pending a re-run.** `dream_rsi` and `meta_harness` because part
#: of their rounds came from the endpoint fallback (findings §116/§118); `dgm` and `hyperagents`
#: because the early-stop defect cut them at r5 and r3, so neither curve reached its budget -- and
#: both are being re-run now, `dgm` on its concrete-target default. The method pages, the runs and
#: `CKPT-*.md` keep everything; this only decides what the table and the charts show.
#: `meta_harness` withdrawn 2026-10-21 by operator decision: its curve is the weakest of the
#: eight (.600 at r10 against .980-1.000 for the other three re-runs), and the operator chose
#: not to publish or discuss that number. The run, its per-task verdicts and its records stay on
#: disk (findings §145); only the row is withdrawn. `rrsi` was never re-run and stays out too.
WITHDRAWN = {"rrsi", "meta_harness"}
#: The two A-line methods were trained under a **later stamp**, and that curve is the
#: authoritative one: the earlier `20261017T123952Z` attempts stopped at r3/r4 and were
#: re-run precisely because of it. Kept as data rather than as a second code path, so every
#: reader goes through `run_stamp()` and cannot silently mix the two.
STUDY_STAMP = {"dream_rsi": "20261018T062351Z", "meta_harness": "20261018T062351Z"}
#: The ALFWorld baseline every published method shares, and the marker `run_stamp` uses to tell a
#: comparable training run from one that measured its own H0 (§130).
SHARED_H0 = "h0-20261017T090642Z-train100"


def run_stamp(method: str) -> str:
    """Which batch stamp this method's **training run** belongs to -- discovered, not assumed.

    A hard-coded map is what went wrong on 2026-10-20: `dgm`, `dream_rsi` and `hyperagents` were
    re-run under stamp `20261020T1715Z` and `meta_harness` under `20261020T1656Z` after the first
    batch was retired (§133/§136), and a table would have kept pointing the export at the
    withdrawn curves. So the stamp is read off the runs themselves: among
    `runs/rsi10-*-<method>-train`, keep the ones whose round 0 names the shared H0
    (`h0-20261017T090642Z-train100` -- the ALFWorld baseline every published method shares), and
    take the longest curve; ties go to the later stamp. A run that measured its own H0 is
    therefore never picked, which is exactly the mistake §130 recorded.
    """
    best: tuple[str, int] | None = None
    for path in sorted(RUNS.glob(f"rsi10-*-{method}-train")):
        curve = path / "curve.jsonl"
        if not curve.is_file():
            continue
        points = rows(curve)
        if not points:
            continue
        shared = (points[0].get("shared_from") or {}).get("run_id")
        if shared != SHARED_H0:
            continue
        stamp = path.name[len("rsi10-"):].split(f"-{method}-train")[0]
        # Only a batch stamp names a batch: `rsi10-mh-20261017T112025Z-meta_harness-train` is an
        # ad-hoc probe whose prefix sorts after every timestamp, and it is not a published run.
        if not re.fullmatch(r"\d{8}T\d{4}Z", stamp):
            continue
        # **The later stamp wins, not the longer curve.** A re-run supersedes what it re-runs, and
        # while it is still short (r1 next to a finished 10-round curve) picking the longest would
        # resurrect the retired one -- which is the whole reason these methods were re-run.
        candidate = (stamp, len(points))
        if best is None or candidate > best:
            best = candidate
    if best is not None:
        return best[0]
    return STUDY_STAMP.get(method, STAMP)
#: What the improver alias resolved to during these runs. The platform records the alias
#: (`deepseek-flash`) because that is what the improver CLI is configured with, so the concrete
#: version is not on any point -- it is reported here because a reader reproducing a number needs
#: to know which model produced the edits.
IMPROVER_MODEL_RESOLVED = "deepseek-v4.1-flash-1017"
#: The study split is a split too: it is where rounds, and therefore per-round cost, exist.
STUDY_SIZE = 100
POOLS = {"valid_unseen": 134, "valid_seen": 140, "valid_train": 200}
SPLITS = {"train": STUDY_SIZE, **POOLS}
#: Benchmarks whose adapter is merged and whose page says what they measure, but which have no
#: published point. They are listed (E5) and their results section says *in progress*: leaving
#: them out reads as "no coverage", and giving them a number would publish an unmeasured value.
IN_PROGRESS = (
    {"slug": "appworld", "title": "AppWorld", "tasks": "732 released",
     "split": "study `train`, dev `dev`, test `test_normal` + `test_challenge`",
     "why": "admission observes container bounds instead of declaring them; a one-task smoke and "
            "a measured concurrency ceiling come first"},
    {"slug": "locomo", "title": "LoCoMo", "tasks": "10 conversations / 1,986 questions",
     "split": "none — the benchmark declares no study/eval split",
     "why": "data pinned and verified, but no measured run yet, so the three-way protocol has "
            "nothing to hold out"},
)
POOL_LABEL = {"valid_unseen": "valid_unseen (official evaluation split)",
              "valid_seen": "valid_seen", "valid_train": "valid_train"}


def rows(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def scalar(value) -> float | None:
    """A score as a plain float, or None where the record has no measured score."""
    return None if value is None else round(float(value), 6)


def stop_rule(method: str) -> dict | None:
    """The round where the method's own stopping rule ended the study, from its recorded label.

    AHE's `run.py` reproduces `evolve.py:4395-4421`: once the incumbent reaches
    `target_pass_rate` the loop breaks instead of evolving again, and reports `changed: false`
    with the label `AHE: stop at target (1.0 >= 0.95)`. That is the method's strategy working, not
    a truncated run -- but a column of unlabelled dashes after it reads as the latter, which is
    exactly how it was read on 2026-10-22.
    """
    run = RUNS / f"rsi10-{run_stamp(method)}-{method}-train"
    curve = rows(run / "curve.jsonl")
    def state(row: dict) -> str:
        return ((row.get("identity") or {}).get("harness_sha") or row.get("sha") or "")[:8]
    for index, row in enumerate(curve):
        label = row.get("label") or ""
        if "stop at target" in label.lower():
            round_no = row.get("round")
            if round_no is None:
                continue
            previous = curve[index - 1] if index else {}
            sha = state(row)
            # The stop round re-measures the incumbent instead of evolving again, so its number
            # is a second measurement of a state already on the curve -- the same "one state, one
            # number" case the AppWorld page records as `repeat_of`. Say it here rather than let
            # the row look like one more round of progress.
            repeat = bool(sha) and sha == state(previous)
            return {"round": int(round_no), "label": label, "sha": sha, "repeat": repeat}
    return None


def train_rounds(method: str) -> dict[int, dict]:
    """Study-side scores by round, from the training run's own curve."""
    found: dict[int, dict] = {}
    for row in rows(RUNS / f"rsi10-{run_stamp(method)}-{method}-train" / "curve.jsonl"):
        round_no = row.get("round")
        if round_no is None or row.get("score") is None:
            continue
        found[int(round_no)] = {"score": scalar(row["score"]),
                                "step": row.get("step_index"),
                                "steps": row.get("steps_in_submission"),
                                "n_tasks": len(row.get("per_task") or {}),
                                "n_invalid": row.get("n_invalid") or 0,
                                "n_harness_failed": row.get("n_harness_failed") or 0,
                                "side": "study"}
    return found


def checkpoint_points(method: str, pool: str) -> dict[int, dict]:
    """Per-round checkpoint scores on one evaluated pool."""
    found: dict[int, dict] = {}
    # **This method's own training run, not the batch's.** The two A-line methods were
    # re-trained under a later stamp; reading the old sweep here would put two different
    # training runs of one method in one table row, which is the silent mix this export
    # exists to prevent.
    stamp = run_stamp(method)
    for path in sorted(RUNS.glob(f"rsi10-{stamp}-{method}-ckpt-r*-{pool}")):
        round_no = int(path.name.split("-ckpt-r")[1].split("-")[0])
        points = rows(path / "curve.jsonl")
        if not points:
            continue
        row = points[0]
        n_tasks = len(row.get("per_task") or {})
        found[round_no] = {"score": scalar(row.get("score")), "n_tasks": n_tasks,
                           "tokens": (row.get("cost") or {}).get("harness_tokens"),
                           # The point records one total; the per-task traces carry the
                           # input/output split the train table already prints. Read it there so
                           # the two cost tables of one page are not in different units.
                           "split": _task_split_from_traces(path, 0),
                           "n_invalid": row.get("n_invalid") or 0,
                           "harness_failed": row.get("n_harness_failed") or 0,
                           "valid": (not row.get("n_invalid")
                                     and not row.get("n_harness_failed")
                                     and n_tasks == POOLS[pool])}
    # **The kept round is measured by the exam, not by this sweep.** `plan` skips each
    # method's kept round on the assumption that the exam already scored it, and reading the
    # exam run here is what makes the three-way protocol visible on the page: the round chosen
    # on the study side, scored on this pool. Without it `ahe`/`hyperagents` r3 on
    # `valid_seen` read `—` although a complete 140-task measurement exists, and the two
    # A-line methods -- whose only evaluated measurement *is* their exam -- would not appear
    # on any evaluated pool at all.
    for round_no, point in exam_points(method, pool).items():
        found.setdefault(round_no, point)      # a checkpoint measurement wins if one exists
    return found


def exam_points(method: str, pool: str) -> dict[int, dict]:
    """The kept round's score on this pool, from the exam run that chose it.

    `evaluated_from.round` is the round the exam measured -- the record already joins the two
    runs, so this reads it instead of guessing from the directory name.
    """
    found: dict[int, dict] = {}
    # Only this method's own run. A fallback to the batch stamp would put an earlier
    # training run's exam on the same row as the authoritative run's curve -- measured: it
    # produced `meta_harness` r3 = 0.557 on `valid_seen` beside r8, two different runs of one
    # method in one table.
    for point in rows(RUNS / f"rsi10-{run_stamp(method)}-{method}-exam-{pool}" / "curve.jsonl"):
        round_no = (point.get("evaluated_from") or {}).get("round")
        if round_no is None or int(round_no) in found:
            continue
        n_tasks = len(point.get("per_task") or {})
        found[int(round_no)] = {
            "score": scalar(point.get("score")), "n_tasks": n_tasks,
            "tokens": (point.get("cost") or {}).get("harness_tokens"),
            "split": _task_split_from_traces(RUNS / f"rsi10-{run_stamp(method)}-{method}-exam-{pool}", 0),
            "n_invalid": point.get("n_invalid") or 0,
            "harness_failed": point.get("n_harness_failed") or 0,
            "source": "exam",
            "valid": (not point.get("n_invalid") and not point.get("n_harness_failed")
                      and n_tasks == POOLS[pool])}
    return found


def _task_split_from_traces(run: pathlib.Path, round_no: int) -> dict:
    """The round's input/output split, summed over the per-task traces it left on disk.

    Same fields the AppWorld page reads (`usage.input_tokens` / `usage.output_tokens`), same
    caveat: a local server reports no cache reads, and a task that reported no usage is counted
    as not reporting rather than as zero.
    """
    folder = run / f"round{round_no}_traces"
    if not folder.is_dir():
        return {}
    total_in = total_out = reporting = acted = tasks = 0
    for path in sorted(folder.glob("*.jsonl")):
        tasks += 1
        got = stepped = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("step") is not None:
                stepped = True
            usage = event.get("usage")
            if not isinstance(usage, dict):
                continue
            value_in = usage.get("input_tokens") or 0
            value_out = usage.get("output_tokens") or 0
            if value_in or value_out:
                total_in += value_in
                total_out += value_out
                got = True
        reporting += int(got)
        acted += int(stepped)
    if not reporting:
        # **A round that solved every task without one model call is a measurement of zero, not a
        # missing number.** `dream_rsi` r3-r10 and `sica_ci` r3-r10 are exactly this: 100 traces
        # per round, every task acted (`step` records), and not one of them contains `reply` /
        # `model_ms` -- the evolved harness solves ALFWorld with its own solver and the model is
        # never consulted. The curve's `harness_tokens = 0` beside `evaluation_trials = 100` is
        # therefore telling the truth, and printing a dash threw that result away.
        if acted:
            return {"input": 0, "output": 0, "tasks_reporting": 0, "tasks": tasks,
                    "zero_model_calls": True, "tasks_acted": acted}
        return {}
    return {"input": total_in, "output": total_out,
            "tasks_reporting": reporting, "tasks": tasks}


def _submission_improver(run: pathlib.Path) -> dict[int, dict]:
    """Improver spend per **submission**, recovered from the CLI transcripts it left behind.

    For the batch methods (`dgm`, `hyperagents`) one improver call produces several children, so
    the run writes one transcript directory per submission -- `method_logs/round-1`,
    `round-4`, ... -- where the number is the round of the submission's **first** child. That is
    why the directories look sparse (1, 4, 7, 10 for ten rounds): three children of one call,
    each counted as a round. So this is not 4 of 10 rounds measured; it is all ten, one call each
    per group of three.

    The platform's own cost record is null for these runs (`method_spend_source: null` in every
    curve point of both ALF runs), so the transcripts are the only source. Same summing rule as
    `backfill-method-cost.py`: every `usage {...}` record in every non-empty log.
    """
    import re
    usage = re.compile(r"usage \{([^{}]*)\}")
    out: dict[int, dict] = {}
    folder = run / "method_logs"
    if not folder.is_dir():
        return out
    for sub in sorted(folder.glob("round-*")):
        try:
            round_no = int(sub.name.split("round-")[1])
        except (IndexError, ValueError):
            continue
        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
        for path in sorted(sub.glob("*.log")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in usage.findall(text):
                try:
                    record = json.loads("{" + raw + "}")
                except ValueError:
                    continue
                totals["calls"] += 1
                for key, field in (("input", "input_tokens"), ("output", "output_tokens"),
                                   ("cache_read", "cache_read_tokens"),
                                   ("cache_write", "cache_write_tokens")):
                    value = record.get(field)
                    if isinstance(value, int):
                        totals[key] += value
        if totals["calls"]:
            out[round_no] = totals
    return out


def method_cost(method: str) -> dict[int, dict]:
    """Both halves of the price of one round: task-agent tokens and improver tokens.

    Three sources, in this order, because they are not equally good:

    1. the run's own `curve.jsonl` -- recorded at the time. Used whenever a field is present.
    2. `method_cost.json` -- the improver's spend recovered from its CLI transcripts when the
       curve's `method_*` fields came out null (the recording defect of findings §16/D3). It
       covers only the rounds whose transcripts were kept.
    3. the round's `roundN_traces/*.jsonl` -- for the input/output split, which no curve record
       carries. Without this the agent table printed two dashes per round even where the curve
       had the total, which read as "no cost measured" rather than "no split recorded".

    The old version read source 2 only, so `dream_rsi`'s fully recorded improver spend showed as
    ten dashes and `dgm`/`hyperagents` showed no agent cost at all although their curves carried
    `harness_tokens`. A round that none of the three covers stays absent, and the page says dashes
    mean "nothing on record", never zero.
    """
    run = RUNS / f"rsi10-{run_stamp(method)}-{method}-train"
    out: dict[int, dict] = {}
    for row in rows(run / "curve.jsonl"):
        round_no = row.get("round")
        if round_no is None:
            continue
        cost = row.get("cost") or {}
        entry = out.setdefault(int(round_no), {})
        if cost.get("harness_tokens") or cost.get("harness_model_calls"):
            entry["task"] = {"tokens": cost.get("harness_tokens"),
                             "calls": cost.get("harness_model_calls")}
        elif cost.get("evaluation_trials"):
            # The round ran -- `evaluation_trials` says it evaluated -- and the record carries no
            # token counts for it. `dream_rsi` r3-r10 and `sica_ci` r3-r10 are this case, and the
            # difference between "ran, not recorded" and "no such round" has to survive to the
            # table, so it is a flag rather than a made-up zero.
            entry["task_unrecorded"] = True
        if (cost.get("method_generation_input") or cost.get("method_generation_output")
                or cost.get("method_model_calls")):
            entry["improver"] = {"input": cost.get("method_generation_input"),
                                 "output": cost.get("method_generation_output"),
                                 "cache_read": cost.get("method_cache_read_tokens"),
                                 "cache_write": 0,
                                 "calls": cost.get("method_model_calls")}
    path = run / "method_cost.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (payload.get("task_agent") or {}).items():
            out.setdefault(int(key), {}).setdefault(
                "task", {"tokens": value.get("tokens"), "calls": value.get("calls")})
        for key, value in (payload.get("task_agent_traces") or {}).items():
            out.setdefault(int(key), {}).setdefault("task_split", value)
        for key, value in (payload.get("improver") or {}).items():
            out.setdefault(int(key), {}).setdefault("improver", {
                "input": value.get("input_tokens"), "output": value.get("output_tokens"),
                "cache_read": value.get("cache_read_tokens"),
                "cache_write": value.get("cache_write_tokens"), "calls": value.get("calls")})
    # The submission transcripts fill the rounds the curve did not record (`dgm`, `hyperagents`).
    for round_no, totals in _submission_improver(run).items():
        entry = out.setdefault(round_no, {})
        if not entry.get("improver"):
            entry["improver"] = {"input": totals["input"], "output": totals["output"],
                                 "cache_read": totals["cache_read"],
                                 "cache_write": totals["cache_write"], "calls": totals["calls"]}
    for round_no, entry in out.items():
        # **The traces decide when the JSON's copy reports nothing.** `method_cost.json` was built
        # by the same transcript pass, and for the rounds it covers with `tasks_reporting = 0` it
        # has no more information than the traces do -- while the traces can tell a genuine
        # zero-model-call round apart from a gap. `setdefault` kept the empty copy and threw that
        # away (measured: `sica_ci` r3-r10 read `n/r` while its traces showed 100 acted tasks and
        # no model call).
        traces = _task_split_from_traces(run, round_no)
        existing = entry.get("task_split") or {}
        if traces and (not existing or not existing.get("tasks_reporting")):
            entry["task_split"] = traces
        if not entry.get("task_split"):
            entry.pop("task_split", None)
            continue
        if entry["task_split"].get("zero_model_calls"):
            # Survives to the table as 0.00/0.00: the round acted and spent nothing on the model.
            entry["task"] = {"tokens": 0, "calls": 0}
            entry.pop("task_unrecorded", None)
    return out


def reference_scores() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    h0 = RUNS / "h0-20261017T090642Z-train100" / "curve.jsonl"
    if h0.is_file():
        rows_ = rows(h0)
        if rows_:
            out["train"] = scalar(rows_[0].get("score"))
    for pool in POOLS:
        for candidate in (D / f"reference-alfworld-{pool}.json",
                          D / "_dropped_pools" / f"reference-alfworld-{pool}.json"):
            if candidate.is_file():
                out[pool] = scalar(json.loads(candidate.read_text()).get("score"))
                break
    return out


def platform_sha() -> str | None:
    """The platform content sha a published point was measured under, if it is recorded."""
    for method in METHODS:
        meta = RUNS / f"rsi10-{STAMP}-{method}-train" / "run_meta.json"
        if meta.is_file():
            try:
                return json.loads(meta.read_text()).get("platform_content_sha")
            except ValueError:
                continue
    return None


def collect() -> dict:
    methods = []
    coverage = {pool: {"measured": 0, "valid": 0} for pool in POOLS}
    excluded: list[dict] = []
    for method in METHODS:
        study = train_rounds(method)
        evaluated = {pool: checkpoint_points(method, pool) for pool in POOLS}
        rounds = sorted(set(study) | {r for pool in POOLS for r in evaluated[pool]})
        cells = []
        for round_no in rounds:
            studied = study.get(round_no) or {}
            cell = {"round": round_no,
                    # One improver call can produce a batch of children (`step_index` 1..N); the
                    # cost tables need that to avoid pricing one call once per child.
                    "step": studied.get("step"), "steps": studied.get("steps"),
                    "train": study.get(round_no, {}).get("score"),
                    "train_n": study.get(round_no, {}).get("n_tasks"),
                    "eval": {}, "valid": True, "n_invalid": 0}
            # **The study side is a point too.** It used to be copied without its invalid count,
            # so a round measured on 99 of 100 tasks (dream_rsi r6, 2026-10-20) showed
            # `train_n = 99` next to `n_invalid = 0` and was plotted as if it were a clean
            # hundred-task point. The rule above the tables says a point that measured fewer
            # tasks is excluded and listed; that has to include this one.
            study_point = study.get(round_no)
            if study_point is not None:
                cell["train_invalid"] = study_point["n_invalid"]
                cell["train_harness_failed"] = study_point["n_harness_failed"]
                if (study_point["n_tasks"] != STUDY_SIZE or study_point["n_invalid"]
                        or study_point["n_harness_failed"]):
                    excluded.append({"method": method, "round": round_no, "pool": "train",
                                     "score": study_point["score"],
                                     "n_tasks": study_point["n_tasks"],
                                     "n_invalid": study_point["n_invalid"],
                                     "n_harness_failed": study_point["n_harness_failed"]})
                    cell["train"] = None
            for pool in POOLS:
                point = evaluated[pool].get(round_no)
                if point is None:
                    continue
                coverage[pool]["measured"] += 1
                coverage[pool]["valid"] += int(point["valid"])
                if not point["valid"]:
                    # Excluded, not hidden: a point measured over 139 of 140 tasks is not a
                    # score for this pool, and the page says so where the reader can see it.
                    excluded.append({"method": method, "round": round_no, "pool": pool,
                                     "score": point["score"], "n_tasks": point["n_tasks"],
                                     "n_invalid": point["n_invalid"],
                                     "n_harness_failed": point["harness_failed"]})
                    continue
                cell["eval"][pool] = point["score"]
                cell.setdefault("eval_tokens", {})[pool] = point.get("tokens")
                if point.get("split"):
                    cell.setdefault("eval_split", {})[pool] = point["split"]
                if point.get("source") == "exam":
                    cell.setdefault("eval_source", {})[pool] = "exam"
                cell["eval_n"] = point["n_tasks"]
                cell["valid"] = cell["valid"] and point["valid"]
                cell["n_invalid"] += point["n_invalid"]
            cells.append(cell)
        methods.append({"name": method, "rounds": cells, "cost": method_cost(method),
                        "stop": stop_rule(method)})
    stack = {"id": "loop-qwen9b__dsh-deepseek",
             "task_agent": {"base_harness": "loop@0.2.0",
                            "model": "qwen3.5-9b-local",
                            "decoding": "greedy (temperature=0)"},
             "improver": {"method": "the methods listed below",
                          "improver_model": "deepseek-flash",
                          "improver_harness": "dsh (headless profile)",
                          "rounds": 10}}
    # `HG_GENERATED_AT` pins the provenance line so a re-export can be compared with the
    # committed files byte for byte (scripts/export/README.md).
    return {"slug": "alfworld",
            "generated_at": os.environ.get("HG_GENERATED_AT") or datetime.now(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stamp": STAMP, "study": {"pool": "train100", "tasks": 100, "rounds": 10},
            "pools": POOLS, "reference": reference_scores(),
            "platform_content_sha": platform_sha(),
            "stacks": [{**stack, "methods": methods}], "excluded": excluded,
            "coverage": {pool: {**counts, "planned": None} for pool, counts in coverage.items()}}


def svg_chart(data: dict, pool: str, methods: list[dict]) -> str:
    """A line chart of every method's score by checkpoint round, with the reference line.

    Drawn here rather than in the browser so the chart and the table are the same snapshot:
    a chart library that refetched would let the two disagree, and a reader would have no way
    to know which was wrong.
    """
    width, height = 760, 380
    left, right, top, bottom = 56, 190, 24, 44
    plot_w, plot_h = width - left - right, height - top - bottom
    reference = data["reference"].get(pool)
    # H0 is round 0 of the same series, not an annotation: without it the chart starts at the
    # first improver round and a reader cannot see how much the method added over the harness
    # it was given. It is measured once and shared by every method, so every line starts there.
    rounds = sorted({cell["round"] for method in methods for cell in method["rounds"]
                     if pool in cell["eval"]} | ({0} if reference is not None else set()))
    values = [cell["eval"][pool] for method in methods for cell in method["rounds"]
              if pool in cell["eval"]] + ([reference] if reference is not None else [])
    measured_points = [cell for method in methods for cell in method["rounds"]
                       if pool in cell["eval"]]
    if not measured_points:
        return ""      # a pool with only a reference is a line, not a chart
    lo = min(values + ([reference] if reference is not None else []))
    hi = max(values + ([reference] if reference is not None else []))
    pad = max(0.02, (hi - lo) * 0.1)
    lo, hi = max(0.0, lo - pad), min(1.0, hi + pad)
    span = (hi - lo) or 1.0

    def x(round_no: int) -> float:
        position = rounds.index(round_no)
        return left + (plot_w * position / max(1, len(rounds) - 1))

    def y(value: float) -> float:
        return top + plot_h * (1 - (value - lo) / span)

    colours = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Score by checkpoint round on {pool}">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for step in range(5):
        value = lo + span * step / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" '
                     f'stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" font-size="11" fill="#6b7280" '
                     f'text-anchor="end">{value:.2f}</text>')
    for round_no in rounds:
        parts.append(f'<text x="{x(round_no):.1f}" y="{top + plot_h + 18}" font-size="11" '
                     f'fill="#6b7280" text-anchor="middle">r{round_no}</text>')
    if reference is not None:
        yy = y(reference)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" '
                     f'stroke="#111827" stroke-width="1.2" stroke-dasharray="5 4"/>')
        parts.append(f'<text x="{left + plot_w - 4}" y="{yy - 5:.1f}" font-size="11" '
                     f'fill="#111827" text-anchor="end">H0 (round 0) {reference:.3f}</text>')
    any_gap = False
    for index, method in enumerate(methods):
        points = sorted((cell["round"], cell["eval"][pool]) for cell in method["rounds"]
                        if pool in cell["eval"])
        if reference is not None and points:
            points = [(0, reference)] + points
        if not points:
            continue
        colour = colours[index % len(colours)]
        # **A line must not run through rounds that were never measured.** The first version
        # joined every measured point into one polyline, so `meta_harness` -- whose only
        # `valid_unseen` measurements are round 0 (the shared H0) and round 8 (its exam) --
        # was drawn as a straight line across eight unmeasured rounds, which reads as "it was
        # flat and then jumped". Measured on the published page 2026-10-19 (reported by the
        # operator). A new subpath starts wherever the round index skips.
        subpaths: list[str] = []
        current: list[str] = []
        previous_round = None
        for r, v in points:
            if previous_round is not None and r - previous_round > 1:
                subpaths.append(" ".join(current))
                current = []
            current.append(f"{'M' if not current else 'L'}{x(r):.1f},{y(v):.1f}")
            previous_round = r
        if current:
            subpaths.append(" ".join(current))
        any_gap = any_gap or any(b[0] - a[0] > 1 for a, b in zip(points, points[1:]))
        # One group per method so the page filter can hide a line together with its legend.
        parts.append(f'<g data-method-line="{method["name"]}">')
        for subpath in subpaths:
            parts.append(f'<path d="{subpath}" fill="none" stroke="{colour}" stroke-width="2"/>')
        for r, v in points:
            parts.append(f'<circle cx="{x(r):.1f}" cy="{y(v):.1f}" r="2.8" fill="{colour}"/>')
        legend_y = top + 14 + index * 18
        parts.append(f'<line x1="{left + plot_w + 16}" y1="{legend_y}" '
                     f'x2="{left + plot_w + 38}" y2="{legend_y}" stroke="{colour}" '
                     f'stroke-width="2"/>')
        parts.append(f'<text x="{left + plot_w + 44}" y="{legend_y + 4}" font-size="11" '
                     f'fill="#111827">{method["name"]}</text>')
        parts.append("</g>")
    parts.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 6}" font-size="11" '
                 f'fill="#374151" text-anchor="middle">checkpoint round</text>')
    if any_gap:
        # Only when the data has one: a caption about breaks on a gapless chart is noise.
        parts.append(f'<text x="{left}" y="{height - 22}" font-size="10" fill="#6b7280">'
                     f'a break means that round was not measured</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def write_index(data: dict) -> pathlib.Path:
    """The benchmarks index, with the point counts read from the snapshot rather than typed.

    The old index hard-coded "35 on `valid_unseen`, 20 on `valid_seen`", which was wrong within a
    day: the whole point of generating a table is that a count beside a link cannot go stale.
    """
    coverage = data.get("coverage") or {}
    counts = ", ".join(f"{coverage[pool]['measured']} on `{pool}`"
                       for pool in POOLS if (coverage.get(pool) or {}).get("measured"))
    lines = [
        "---",
        "title: Benchmarks",
        "description: The datasets this platform has measured, what each one measures, the scores "
        "we measured, and how to reproduce or contribute a result.",
        "---",
        "",
        "{/* Generated by scripts/export/export-site.py; last export "
        f"{data['generated_at']}. " + "*/}",
        "",
        "Each page here is one benchmark. A benchmark with published measurements carries the same "
        "five things: what it measures and how its tasks are split, the scores this platform "
        "measured, the exact configuration those runs used, where the full trajectories live, and "
        "how to submit a result of your own. A benchmark whose adapter is merged but which has "
        "not produced a valid point yet says so on its own page, and publishes **no number**.",
        "",
        "A score is published only when it can be traced to per-task verdicts: a point that "
        "measured 139 of a pool's 140 tasks is excluded and named, never averaged in. The frozen "
        "reference (H0, the base harness on the same tasks) is printed with every table, because "
        "a raw number without it does not say whether a method helped.",
        "",
        "| benchmark | tasks | split | published points | status |",
        "| --- | --- | --- | ---: | --- |",
        f"| [ALFWorld](alfworld/) | 100 study / 134 eval | study `train`, eval `valid_unseen`, "
        f"plus `valid_seen` and `valid_train` | {counts} | measured |",
    ]
    for entry in IN_PROGRESS:
        lines.append(f"| [{entry['title']}]({entry['slug']}/) | {entry['tasks']} | "
                     f"{entry['split']} | — | in progress |")
    lines += [
        "",
        "## How to read any table here",
        "",
        "The rules below are the same on every benchmark page, so they are stated once:",
        "",
        "- **H0 (round 0) is the base harness**, before any method touched it; the distance from "
        "that line is what a method added. Every table prints it.",
        "- **`—` means not measured, never zero.** **`†` marks the round the method kept**, scored "
        "by the exam run that chose it; the checkpoint sweep does not re-measure one state twice. "
        "**A line in a chart breaks where a round was not measured** — it is not a straight line "
        "through unmeasured rounds.",
        "- **Every published point covers its whole pool with `n_invalid = 0`.** A point that "
        "measured fewer tasks, or hit an invalid/verifier failure, is excluded and listed under "
        "*Excluded* with its task count; it is never averaged in.",
        "- **A move smaller than the noise is not evidence.** The same-arm replicate spread on "
        "20 ALFWorld tasks is ~6.5 pp (SD 1.3 tasks), so 20-task arms move in 5-point steps; on "
        "134 tasks the standard error is ~4.3 pp.",
        "- **Rows are only comparable within one setup.** A different agent model, base harness "
        "or improver is a different measurement; the setup is printed above each table.",
        "",
        "An *in progress* page is not a result and is not counted anywhere: it exists so that the "
        "question \"does this platform cover AppWorld?\" has an answer that distinguishes \"the "
        "adapter is merged\" from \"we measured it\". Integration status is tracked in the "
        "repository's [`registry/dataset-blockers.json`]"
        "(https://github.com/zjunlp/HarnessGrad/blob/main/registry/dataset-blockers.json).",
        "",
        "Missing a benchmark you care about? A dataset page is the first contribution — see "
        "**[Contributing](../contributing/)**.",
        "",
    ]
    target = SITE / "src" / "content" / "docs" / "benchmarks" / "index.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def method_cell(name: str) -> str:
    """The method's name, linked to its own page.

    A relative link, not a root-absolute one: the site is served under a project path, and
    `check-links` fails the build for a hand-written absolute link that misses it.
    """
    return f'<td><a href="../../methods/{name}/"><code>{name}</code></a></td>'


def score_table(methods: list[dict], split: str,
                reference: float | None = None,
                record=None) -> str:
    """Rows = methods, columns = measured rounds, first column the shared H0 (r0)."""
    def value(cell: dict) -> float | None:
        if split == "train":
            return cell.get("train")
        return (cell.get("eval") or {}).get(split)

    rounds = sorted({cell["round"] for method in methods for cell in method["rounds"]
                     if value(cell) is not None and not (reference is not None
                                                         and cell["round"] == 0)})
    if not rounds:
        return "_Not measured yet._"
    head = ('<div class="hg-table"><table><thead><tr><th>method</th>'
            + (f"<th>r0 (H0)</th>" if reference is not None else "")
            + "".join(f"<th>r{r}</th>" for r in rounds)
            + ('<th title="this method&#39;s harness, traces and edits on the Hub">record</th>'
               if record is not None else "")
            + "</tr></thead><tbody>")
    body = []
    for method in methods:
        by_round = {cell["round"]: cell for cell in method["rounds"]}
        cells = [f'<tr data-method-row="{method["name"]}">' + method_cell(method["name"])]
        if reference is not None:
            cells.append(f"<td>{reference:.3f}</td>")
        for round_no in rounds:
            cell = by_round.get(round_no) or {}
            got = value(cell)
            if got is None:
                cells.append("<td>—</td>")
                continue
            exam = (cell.get("eval_source") or {}).get(split) == "exam"
            cells.append(f"<td>{got:.3f}{'†' if exam else ''}</td>")
        if record is not None:
            url = record(method["name"])
            cells.append(f'<td><a href="{url}" title="one method&#39;s record on the Hub">↗</a></td>'
                         if url else "<td>—</td>")
        body.append("".join(cells) + "</tr>")
    # One line: MDX parses an HTML block that spans lines as markdown and then complains.
    return head + "".join(body) + "</tbody></table></div>"


def _cost_by_round(method: dict) -> dict[int, dict]:
    """`method["cost"]` keyed by round number **as an int**, however it was serialized.

    The live `collect()` keeps int keys; the JSON snapshot writes them as strings ("1"). Reading
    the page back out of the snapshot therefore used to blank both cost tables -- the key type
    decided whether the numbers appeared, not the data. It appeared once, on 2026-10-19.
    """
    out: dict[int, dict] = {}
    for key, value in (method.get("cost") or {}).items():
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            continue
    return out


def _cost_rounds(methods: list[dict]) -> list[int]:
    """The rounds a cost table must print: r1..the last round that has a cost entry.

    This used to be `range(1, 11)`. That was right when a study ran ten rounds, and it went
    quietly wrong when AppWorld's runs went on to r20: the score tables printed r0..r20 while
    both cost tables stopped at r10, which reads as "the run stopped costing money" rather than
    "the exporter stopped printing". Derive it from the data instead of naming a number.
    """
    present = sorted({int(r) for method in methods for r in (method.get("cost") or {})})
    return list(range(1, (max(present) if present else 10) + 1))


def _cost_entry(method: dict, round_no: int, split: str) -> dict | None:
    """The cost record for one method in one round, from whichever side that split has."""
    if split == "train":
        return _cost_by_round(method).get(round_no)
    for cell in method["rounds"]:
        if cell["round"] == round_no:
            tokens = (cell.get("eval_tokens") or {}).get(split)
            return None if tokens is None else {"task": {"tokens": tokens, "calls": None}}
    return None


def _cell(value_millions: float | None, digits: int = 2) -> str:
    return "<td>—</td>" if not value_millions else f"<td>{value_millions:.{digits}f}</td>"


def task_cost_table(methods: list[dict], split: str) -> str:
    """Agent-side cost, one row per method, one column group per round.

    The study split has the reported input and output per round; an evaluation split only has a
    total per point, because the point is a single measurement, so its table keeps one column per
    round rather than pretending to a split it does not have. A local server reports no cache
    reads, so that sub-column is absent here and present in the improver table, which calls a
    hosted model.
    """
    if split == "train":
        rounds = _cost_rounds(methods)
        caption = ('<caption>task-agent tokens per round — millions of tokens (M); '
                   "in = uncached prompt tokens, out = generated tokens; "
                   "0.00 = the round made no model calls at all (the harness solved the tasks "
                   "with its own solver, and the traces record every step without one "
                   "reply record); n/r = the round ran but the record carries no counts"
                   "</caption>")
        head1 = '<tr><th rowspan="2">method</th>' + "".join(
            f'<th colspan="2">r{r}</th>' for r in rounds) + "</tr>"
        head2 = "<tr>" + "".join(f"<th>in</th><th>out</th>" for _ in rounds) + "</tr>"
        body = []
        for method in methods:
            cost = _cost_by_round(method)
            cells = [f'<tr data-method-row="{method["name"]}">' + method_cell(method["name"])]
            for round_no in rounds:
                entry = cost.get(round_no) or {}
                task = entry.get("task") or {}
                piece = entry.get("task_split") or {}
                # **The trace split is the measurement; the curve's total is the fallback.** A
                # round where the curve recorded `harness_tokens = 0` beside `evaluation_trials =
                # 100` did act (ALFWorld `sica_ci` r4-r6, `dream_rsi` r3-r5 -- their traces do
                # carry usage), so calling it "reuse" and printing a dash threw away a number that
                # was on disk. Read the split first, and keep "reuse" for the case where the
                # round really left no record of acting.
                if piece.get("zero_model_calls"):
                    cells += ["<td>0.00</td>", "<td>0.00</td>"]
                elif piece.get("input") or piece.get("output"):
                    cells += [_cell((piece.get("input") or 0) / 1e6),
                              _cell((piece.get("output") or 0) / 1e6)]
                elif entry.get("task_unrecorded"):
                    cells += ["<td>n/r</td>", "<td>—</td>"]
                elif not task and not piece:
                    cells += ["<td>—</td>", "<td>—</td>"]
                elif not task.get("calls") and not task.get("tokens"):
                    # The round ran (`evaluation_trials` is in the record) but no token counts
                    # came back with it. "reuse" would claim the run skipped the work, and a
                    # dash would claim there is no row at all; n/r says what happened.
                    cells += ["<td>n/r</td>", "<td>—</td>"]
                else:
                    cells += ["<td>—</td>", "<td>—</td>"]
            body.append("".join(cells) + "</tr>")
        return ('<div class="hg-table"><table>' + caption + "<thead>" + head1 + head2
                + "</thead><tbody>" + "".join(body) + "</tbody></table></div>")

    rounds = sorted({c["round"] for method in methods for c in method["rounds"]
                     if split in (c.get("eval_tokens") or {})})
    if not rounds:
        return "_No agent cost recorded for this split._"
    # **In and out, like the study table.** An evaluation point's own record carries one total,
    # but its per-task traces carry the split, so printing one number here would put the two
    # cost tables of this page in different units -- and a single total cannot answer "did it get
    # more expensive, or did the prompts get longer".
    caption = ("<caption>task-agent tokens per measured point — millions of tokens (M); "
               "in = uncached prompt tokens, out = generated tokens; "
               "0.00 = the point made no model calls (the harness solved the tasks with its own "
               "solver); n/s = only a total was recorded for this point</caption>")
    head1 = '<tr><th rowspan="2">method</th>' + "".join(
        f'<th colspan="2">r{r}</th>' for r in rounds) + "</tr>"
    head2 = "<tr>" + "".join("<th>in</th><th>out</th>" for _ in rounds) + "</tr>"
    body = []
    for method in methods:
        by_round = {c["round"]: c for c in method["rounds"]}
        cells = [f'<tr data-method-row="{method["name"]}">' + method_cell(method["name"])]
        for round_no in rounds:
            cell = by_round.get(round_no) or {}
            piece = (cell.get("eval_split") or {}).get(split) or {}
            tokens = (cell.get("eval_tokens") or {}).get(split)
            if piece.get("zero_model_calls"):
                cells += ["<td>0.00</td>", "<td>0.00</td>"]
            elif piece.get("input") or piece.get("output"):
                cells += [_cell((piece.get("input") or 0) / 1e6),
                          _cell((piece.get("output") or 0) / 1e6)]
            elif tokens is None:
                cells += ["<td>—</td>", "<td>—</td>"]
            elif tokens < 1_000:
                cells += ["<td>reuse</td>", "<td>—</td>"]
            else:
                cells += ["<td>n/s</td>", "<td>—</td>"]
        body.append("".join(cells) + "</tr>")
    return ('<div class="hg-table"><table>' + caption + "<thead>" + head1 + head2
            + "</thead><tbody>" + "".join(body) + "</tbody></table></div>")


def improver_cost_table(methods: list[dict], note: str = "") -> str:
    """Improver cost, one row per method, three sub-columns per round: in, cache read, out.

    `note` appends a clause to the caption, for the pages where the same numbers answer a
    slightly different question (AppWorld's validation section prints them beside the dev score
    of the checkpoint that round produced).
    """
    rounds = _cost_rounds(methods)
    has_cache = any(((_cost_by_round(method).get(rnd) or {}).get("improver") or {})
                    .get("cache_read") for method in methods
                    for rnd in _cost_by_round(method))
    width = 3 if has_cache else 2
    caption = ('<caption>improver tokens per round — millions of tokens (M); '
               "in = uncached prompt, cache = prompt read from cache, out = generated"
               + ("" if has_cache else "; this improver reports no cache reads")
               + "; ↳ = another child of the submission counted to its left: one improver call "
                 "produced several candidates, so its spend appears once, not once per child"
               + "; a dash means no improver usage is on record for that round -- either the "
                 "method edited nothing or the round's usage was not recorded -- never a zero"
               + (f"; {note}" if note else "")
               + "</caption>")
    head1 = '<tr><th rowspan="2">method</th>' + "".join(
        f'<th colspan="{width}">r{r}</th>' for r in rounds) + "</tr>"
    inner = "<th>in</th>" + ("<th>cache</th>" if has_cache else "") + "<th>out</th>"
    head2 = "<tr>" + inner * len(rounds) + "</tr>"
    body = []
    for method in methods:
        cost = _cost_by_round(method)
        cells = [f'<tr data-method-row="{method["name"]}">' + method_cell(method["name"])]
        # A batch method's one improver call produces several children (`step_index` 1..N of one
        # submission), and the platform writes that one call's spend into **each** child's record
        # -- identical to the token (`dgm` r1 = r2 = r3 = 497998 in). Printing it at every child
        # counts one call three times, so the number lives at the first child and its siblings are
        # marked as sharing it.
        by_round_cells = {cell["round"]: cell for cell in (method.get("rounds") or [])}
        for round_no in rounds:
            if (by_round_cells.get(round_no) or {}).get("step", 1) not in (None, 1):
                cells += ["<td>↳</td>"] * width
                continue
            improver = ((cost.get(round_no) or {}).get("improver")) or {}
            if not improver:
                cells += ["<td>—</td>"] * width
                continue
            cells.append(_cell((improver.get("input") or 0) / 1e6))
            if has_cache:
                cells.append(_cell((improver.get("cache_read") or 0) / 1e6, digits=1))
            cells.append(_cell((improver.get("output") or 0) / 1e6))
        body.append("".join(cells) + "</tr>")
    return ('<div class="hg-table"><table>' + caption + "<thead>" + head1 + head2
            + "</thead><tbody>" + "".join(body) + "</tbody></table></div>")


def svg_train_chart(data: dict, methods: list[dict]) -> str:
    """The study-side curve: score per round on the 100 studied tasks, starting at H0."""
    reference = (data["reference"] or {}).get("train")
    rows_ = [(cell["round"], cell["train"]) for method in methods for cell in method["rounds"]
             if cell.get("train") is not None]
    if not rows_:
        return ""
    shaped = [{"name": method["name"],
               "rounds": [{"round": c["round"], "train": c["train"]} for c in method["rounds"]
                          if c.get("train") is not None]} for method in methods
              if any(c.get("train") is not None for c in method["rounds"])]
    # Reuse the eval chart by presenting the study score under a synthetic pool key.
    for method in shaped:
        for cell in method["rounds"]:
            cell["eval"] = {"train": cell["train"]}
    return svg_chart({"reference": {"train": reference}}, "train", shaped)


def page(data: dict) -> str:
    """The benchmark page: one terse sentence per section, then the figure and the table.

    Prose earns its place only where a reader would otherwise misread a number. Everything the
    numbers already say (which rounds, which split, how many points) is left to the table.
    """
    reference = data["reference"]
    study = data["study"]
    stacks = data.get("stacks") or [{"id": "stack", "methods": data["methods"]}]
    methods = stacks[0]["methods"]
    excluded = data.get("excluded") or []
    agent = stacks[0].get("task_agent") or {}
    improver = stacks[0].get("improver") or {}
    #: Row-level link: the `record` column names the directory that holds *this* method's harness
    #: under *this* improver, so a reader who doubts a number can open the state that produced it.
    record = (lambda name: record_url(name, improver.get("improver_model"))
              if improver.get("improver_model") else None)

    options = lambda values: "[" + ", ".join(
        "{ value: '%s', label: '%s' }" % (v, v) for v in values) + "]"
    split_order = [name for name in ("train", "valid_train", "valid_seen", "valid_unseen")
                   if name in SPLITS]
    split_items = []
    for name in split_order:
        fields = [f"value: '{name}'", f"label: '{name}'"]
        if SPLITS.get(name) is not None:
            fields.append(f"count: {SPLITS[name]}")
        h0 = (reference or {}).get(name)
        if h0 is not None:
            fields.append(f"h0: '{h0:.3f}'")
        split_items.append("{" + ", ".join(fields) + "}")
    filter_line = ("<BenchmarkFilter\n"
                   f"  slug=\"alfworld\"\n"
                   f"  splits={{[{', '.join(split_items)}]}}\n"
                   f"  methods={{{options([m['name'] for m in methods])}}}\n"
                   f"  taskModels={{{options([agent.get('model', 'unknown')])}}}\n"
                   f"  metaModels={{{options([improver.get('improver_model', 'unknown')])}}}\n"
                   f"  metaHarnesses={{{options([improver.get('improver_harness', 'unknown')])}}}\n"
                   f"  baseHarnesses={{{options([agent.get('base_harness', 'unknown')])}}}\n"
                   "/>")

    lines = [
        "---",
        "title: ALFWorld",
        "description: What ALFWorld measures, the scores this platform measured, how to "
        "reproduce them, and how to contribute a result.",
        "---",
        "import BenchmarkFilter from '../../../components/BenchmarkFilter.astro';",
        "import BenchmarkChart from '../../../components/BenchmarkChart.astro';",
        "",
        "{/* Generated by scripts/export/export-site.py from each run own curve record. Do not "
        f"edit by hand; last export {data['generated_at']}. " + "*/}",
        "",
        "## What it measures",
        "",
        "TextWorld household tasks: the agent is told \"put a clean mug in the cabinet\" and has "
        "to act through a text interface — `go to`, `take`, `heat`, `put` — until the game says "
        "the goal is reached. Solved means the goal was reached within **50 actions**; there is "
        "no partial credit per task.",
        "",
        "",
        f"Study on **{study['tasks']}** ids from the official `train` split; each pool below is "
        "scored once per checkpoint. `valid_unseen` is ALFWorld's own out-of-distribution "
        "evaluation split; `valid_seen` and `valid_train` are its two other evaluation splits.",
        "",
        "## Results",
        "",
        "",
        filter_line,
        "",
        f"Last export **{data['generated_at']}** · {len(methods)} methods · "
        f"{sum(v['measured'] for v in data['coverage'].values())} published points, each "
        "recomputable from its per-task verdicts.",
        "",
    ]
    # The study split first: it is where the rounds, and therefore the per-round cost, exist.
    train_reference = reference.get("train")
    lines += ['<section data-split="train" '
              + " ".join(f'data-{axis}="{value}"' for axis, value in (
                  ("base-harness", agent.get("base_harness", "all")),
                  ("task-model", agent.get("model", "all")),
                  ("meta-model", improver.get("improver_model", "all")),
                  ("meta-harness", improver.get("improver_harness", "all"))))
              + ">", "",
              f"### `train` · {STUDY_SIZE} tasks · H0 "
              f"{('%.3f' % train_reference) if train_reference is not None else '—'} · "
              "study split", ""]
    train_svg = svg_train_chart(data, methods)
    if train_svg:
        (SITE / "public" / "results" / data["slug"] / "train.svg").write_text(
            train_svg, encoding="utf-8")
        lines += [f'<BenchmarkChart slug="{data["slug"]}" pool="train" '
                  'alt="Study score by round" />', ""]
    lines += ["**Score — study split, 100 tasks per round**", "",
              score_table(methods, "train", train_reference, record), "",
              # H0 is a measurement with its own artifacts, not a zero baseline: say so, and let
              # a reader open the run behind the column every other row is compared to.
              (f"The `r0 (H0)` column is one run — the same harness on the same tasks — not a "
               f"constant: [its record]({baseline_url()}) holds the trajectories and the per-task "
               f"verdicts." if baseline_url() else ""), "",
              "**Cost — task-agent: millions of tokens the harness spent acting, per round**", "",
              task_cost_table(methods, "train"), "",
              "**Cost — improver: millions of tokens the method spent editing, per round**", "",
              improver_cost_table(methods), ""]
    lines += ["</section>", ""]

    for pool in POOLS:
        reference_value = reference.get(pool)
        measured = sum(1 for method in methods for cell in method["rounds"]
                       if pool in (cell.get("eval") or {}))
        lines += [f'<section data-split="{pool}" '
                  + " ".join(f'data-{axis}="{value}"' for axis, value in (
                      ("base-harness", agent.get("base_harness", "all")),
                      ("task-model", agent.get("model", "all")),
                      ("meta-model", improver.get("improver_model", "all")),
                      ("meta-harness", improver.get("improver_harness", "all"))))
                  + ">", "",
                  f"### `{pool}` · {POOLS[pool]} tasks · H0 "
                  f"{('%.3f' % reference_value) if reference_value is not None else '—'} · "
                  f"{measured} points", ""]
        if measured:
            lines += [f'<BenchmarkChart slug="{data["slug"]}" pool="{pool}" '
                      f'alt="Score by checkpoint round on {pool}" />', ""]
            lines += [f"**Score — `{pool}`, {POOLS[pool]} tasks per point**", "",
                      score_table(methods, pool, reference_value, record), "",
                      "**Cost — task-agent: millions of tokens per measured point** (in / out; an "
                      "evaluation point runs no improver, so there is no improver column here)", "",
                      task_cost_table(methods, pool), ""]
        else:
            lines += ["_Not measured yet._", ""]
        lines += ["</section>", ""]
    # The excluded list is deliberately **not** printed. A point that did not cover its whole pool
    # is already shown as `—` in its table, and the list grew into a paragraph that described the
    # record's bookkeeping rather than the measurement. `excluded` stays in the JSON for anyone
    # who needs the inventory.

    lines += [
        "## Run it",
        "",
        "- study: 100 ids from `train` (`train100.csv`), "
        f"{study['rounds']} rounds, whole set per round",
        "- measure: every saved round on each split. The budget is **48 machine-wide slots**; "
        "`--jobs` is how many of them one invocation may use. The checkpoint sweeps ran **two "
        "shards at `--jobs 24`**, which is what keeps the two cards balanced: one call-level "
        "balancer in front of both servers, so every model call goes to whichever card is free.",

        "- score: the environment's own verifier; per-task verdicts are kept, so a score "
        "recomputes from `per_task`",
        "",
        "```bash",
        "# study",
        "HG_ALFWORLD_STUDY_IDS=train100.csv HG_ALFWORLD_POOL=valid_unseen \\",
        "  python3 driver.py --harness loop --dataset alfworld --split declared --side train \\",
        "  --rounds 10 --trials 1 --jobs 24 --improver dsh \\",
        "  --method-entrypoint 'python3 methods/<method>/run.py' --run-id <id> --runs-root runs",
        "",
        "# one saved checkpoint on one split",
        "HG_ALFWORLD_STUDY_IDS=train100.csv HG_ALFWORLD_POOL=valid_unseen \\",
        "  python3 driver.py --harness loop --dataset alfworld --split declared --side eval \\",
        "  --from-run <id> --from-round <N> --trials 1 --jobs 24 --improver none \\",
        "  --run-id <id>-ckpt-r<N>-valid_unseen --runs-root runs",
        "```",
        "",
        "## Trajectories",
        "",
        "Published on HuggingFace; until the repository is announced, ask in an issue.",
        "",
        "## Contribute",
        "",
        "Bring your own setup — a different base harness, model or improver is welcome as long "
        "as it is declared and H0 is measured on it. Format and verification: "
        "**[Contributing](../../contributing/)**.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    data = collect()
    folder = SITE / "src" / "data" / "results"
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "alfworld.json"
    content = json.dumps(data, indent=1, sort_keys=False) + "\n"
    changed = snapshot.is_file() and snapshot.read_text(encoding="utf-8") != content
    if args.check:
        print(f"snapshot: {'would change' if changed else 'unchanged'} -> {snapshot}")
        print(json.dumps(data["coverage"], indent=1))
        return 0
    snapshot.write_text(content, encoding="utf-8")

    charts = SITE / "public" / "results" / "alfworld"
    charts.mkdir(parents=True, exist_ok=True)
    chart_methods = (data.get("stacks") or [{"methods": data["methods"]}])[0]["methods"]
    for pool in POOLS:
        target = charts / f"{pool}.svg"
        svg = svg_chart(data, pool, chart_methods)
        if svg:
            target.write_text(svg, encoding="utf-8")
        elif target.is_file():
            target.unlink()      # a pool with no data must not keep a stale chart

    page_path = SITE / "src" / "content" / "docs" / "benchmarks" / "alfworld.mdx"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    html = page(data)
    # A cost table that is all "—" while cost data exists is a rendering bug wearing the
    # clothes of missing data (2026-10-19: int-vs-str round keys blanked both tables). Refuse
    # to write a page in that state instead of publishing it and finding out later.
    methods_ = (data.get("stacks") or [{"methods": data.get("methods") or []}])[0]["methods"]
    has_cost = any(_cost_by_round(method) for method in methods_)
    if has_cost:
        for label, table in (("task-agent (study)", task_cost_table(methods_, "train")),
                             ("improver", improver_cost_table(methods_))):
            filled = [c for c in re.findall(r"<td>([^<]*)</td>", table) if c not in ("—", "")]
            if not filled:
                raise SystemExit(f"refusing to write the page: the {label} cost table is empty "
                                 f"but cost data exists")
    page_path.write_text(html, encoding="utf-8")
    # **No benchmarks index.** The operator removed `benchmarks/index.md` on 2026-10-22: the
    # sidebar already lists the two benchmark pages, and the index's own table restated counts
    # that the pages carry. `write_index()` is kept unused below only because deleting it would
    # also delete the record of why it existed (it was written to stop hand-typed counts going
    # stale); it is not called, so the page cannot come back on the next export.
    print(f"wrote {snapshot.relative_to(SITE)}, {page_path.relative_to(SITE)}, "
          f"and {len(list(charts.glob('*.svg')))} chart(s)")
    print(json.dumps(data["coverage"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
