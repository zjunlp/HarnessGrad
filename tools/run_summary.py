#!/usr/bin/env python3
"""One page per run: what a run directory measured, and what it does not say.

    python3 tools/run_summary.py runs/<run-id>                 # writes end-summary.md/.json into it
    python3 tools/run_summary.py runs/<run-id> --out /tmp/s    # ... or beside it
    python3 tools/run_summary.py --all runs/ --dry-run         # what would be written
    python3 tools/run_summary.py runs/<run-id> --json          # summary JSON on stdout

Why this exists
---------------
A run directory has the answer spread over at least five files: `curve.jsonl` (one point
per round), `run_meta.json` (identity), `diffs/round-N.json` (what the method changed),
`verdicts/` (per-task verdicts) and `state_after_roundN/` (the candidate harness). Reading
a run therefore meant opening all of them, and the cheap alternative -- reading only the
last line of the curve -- silently loses the r0 baseline, the invalid/unmeasured counts and
every "this run never recorded X" caveat. This tool writes the one page.

The rule it is built around
----------------------------
**A zero is a measurement; an unknown is not a zero.** `score: 0.0` on round 9 with
`n_passed: 0` is a real result and is printed as `0.000`. A run that never wrote
`n_invalid` gets `—` in the invalid column and a line under "what is missing" -- never a
fabricated `0`, which would read as "nothing was invalid". The same applies to gained/lost
counts, changed files, call/token totals and duration.

Field names are read from the files as they are actually written, not from an ideal schema:
the side is `config.run.side`, the rounds count is `config.run.rounds`, the improver is
`improver.name`, a training point's per-task map is usually `per_task` (an empty
`train_per_task` also appears), and a round's cost lives under `cost` / `cumulative`. Runs
that predate a field simply say so.

Read-only by contract: nothing here touches a run except to write `end-summary.md` and
`end-summary.json`, and an existing different summary is reported (with its hash) before it
is replaced rather than overwritten silently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "harnessgrad.run-summary.v1"

#: Printed wherever a value is unknown. Never `0`, never an empty cell: an empty cell reads
#: as "nothing to report", and `0` reads as a measured zero.
MISSING = "—"

#: The markdown page is meant to be read in one screen. Past this many rounds the table is
#: cut to its two ends and the rest is left in `end-summary.json`.
MAX_TABLE_ROWS = 24
TRUNCATED_HEAD = 8
TRUNCATED_TAIL = 12
MAX_MD_NOTES = 5

SUMMARY_MD = "end-summary.md"
SUMMARY_JSON = "end-summary.json"


# --------------------------------------------------------------------------- input


def _num(value):
    """A finite float, or None. `bool` is an int in Python and is not a score."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _int(value):
    """An int, or None. A float that is not integral (`1.5`) is not a count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def load_curve(run_dir: Path) -> tuple[list[dict], int]:
    """`(points, unparseable_line_count)`. Every good line survives a bad neighbour."""
    path = run_dir / "curve.jsonl"
    if not path.is_file():
        raise ValueError(f"no curve.jsonl in {run_dir}")
    points: list[dict] = []
    bad = 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            point = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(point, dict):
            bad += 1
            continue
        points.append(point)
    if not points:
        raise ValueError(f"curve.jsonl in {run_dir} has no readable points ({bad} line(s) skipped)")
    return points, bad


def load_meta(run_dir: Path) -> tuple[dict, str | None]:
    """`(meta, note)`: a missing or corrupt record is a fact to report, not a crash."""
    path = run_dir / "run_meta.json"
    if not path.is_file():
        return {}, "run_meta.json: absent, so identity comes from curve.jsonl and the directory name"
    try:
        meta = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return {}, f"run_meta.json: not valid JSON ({exc}), so identity falls back to curve.jsonl"
    if not isinstance(meta, dict):
        return {}, "run_meta.json: not a JSON object, so identity falls back to curve.jsonl"
    return meta, None


def _dig(mapping, *keys):
    """Walk nested dicts, returning None for any missing or non-dict step."""
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


# ----------------------------------------------------------------- per-point fields


def per_task_scores(point: dict) -> dict[str, float]:
    """The point's per-task map, whatever the run called it.

    `per_task` is what the current driver writes; an older point may carry only
    `train_per_task`, and `train_per_task` is frequently `{}` even when `per_task` is full.
    """
    for key in ("per_task", "train_per_task"):
        raw = point.get(key)
        if not isinstance(raw, dict) or not raw:
            continue
        scores = {}
        for task, value in raw.items():
            number = _num(value)
            if number is not None:
                scores[str(task)] = number
        if scores:
            return scores
    return {}


def _selected_tasks(point: dict):
    """How many tasks the point was supposed to score, or None when the run does not say."""
    ids = _dig(point, "sampling", "task_ids")
    if isinstance(ids, list) and ids:
        return len(ids)
    side = point.get("side")
    split = point.get("split")
    if isinstance(split, dict):
        for key in (("eval", "train") if side == "eval" else ("train", "eval")):
            tasks = split.get(key)
            if isinstance(tasks, list) and tasks:
                return len(tasks)
    return None


def _invalid(point: dict):
    """Invalid tasks counted from `n_invalid`, or from the `invalid` map when that is absent."""
    count = _int(point.get("n_invalid"))
    if count is not None:
        return count
    raw = point.get("invalid")
    if isinstance(raw, (dict, list)):
        return len(raw)
    return None


def _timeout(point: dict):
    """Verifier timeouts counted from `n_timeout`, or from the `timeouts` map."""
    count = _int(point.get("n_timeout"))
    if count is not None:
        return count
    raw = point.get("timeouts")
    if isinstance(raw, (dict, list)):
        return len(raw)
    return None


def _round_entry(point: dict, prev_score, notes: list[str]) -> tuple[dict, float | None]:
    score = _num(point.get("score"))
    delta = None if (score is None or prev_score is None) else score - prev_score

    task_scores = per_task_scores(point)
    n_scored = _int(point.get("n_scored"))
    if n_scored is None and task_scores:
        n_scored = len(task_scores)
    expected = _selected_tasks(point)
    unmeasured = None
    if expected is not None and n_scored is not None:
        if expected >= n_scored:
            unmeasured = expected - n_scored
        else:
            note = (f"unmeasured: round {point.get('round')} scored {n_scored} tasks against "
                    f"{expected} selected ones, so the gap is not reportable")
            if note not in notes:
                notes.append(note)

    entry = {
        "round": _int(point.get("round")),
        "score": score,
        "delta": delta,
        "invalid": _invalid(point),
        "unmeasured": unmeasured,
        "timeout": _timeout(point),
    }
    return entry, score


# --------------------------------------------------------------------- cost and time


def _sum_field(points: list[dict], group: str, field: str):
    """`(sum, rounds_reporting)`: `sum` is None when no round reports the field."""
    values = []
    for point in points:
        number = _num(_dig(point, group, field))
        if number is not None:
            values.append(number)
    if not values:
        return None, 0
    return sum(values), len(values)


def cost_totals(points: list[dict], side: str | None, notes: list[str]) -> dict:
    """Model calls and tokens, with the coverage of each total stated.

    `cost.harness_model_calls` and `cost.method_model_calls` are separate budgets, and on an
    exam run the method is not supposed to run at all -- so the total is exposed together
    with `cost_scope` instead of being presented as one number with no boundary.
    """
    harness_calls, hc_rounds = _sum_field(points, "cost", "harness_model_calls")
    method_calls, mc_rounds = _sum_field(points, "cost", "method_model_calls")
    harness_tokens, ht_rounds = _sum_field(points, "cost", "harness_tokens")
    method_tokens, mt_rounds = _sum_field(points, "cost", "method_generation_tokens")
    total_rounds = len(points)

    calls = None
    if harness_calls is not None or method_calls is not None:
        calls = (harness_calls or 0) + (method_calls or 0)
    if calls is not None and mc_rounds and mc_rounds < total_rounds:
        notes.append(f"cost: method call counts cover {mc_rounds} of {total_rounds} rounds, "
                     "so the calls total is a lower bound")
    if calls is not None and hc_rounds and hc_rounds < total_rounds:
        notes.append(f"cost: harness call counts cover {hc_rounds} of {total_rounds} rounds, "
                     "so the calls total is a lower bound")

    tokens = None
    if harness_tokens is not None or method_tokens is not None:
        tokens = (harness_tokens or 0.0) + (method_tokens or 0.0)
    if tokens is not None and mt_rounds and mt_rounds < total_rounds:
        notes.append(f"cost: method token counts cover {mt_rounds} of {total_rounds} rounds, "
                     "so the tokens total is a lower bound")
    if tokens is None:
        cumulative = _num(_dig(points[-1], "cumulative", "generation_tokens"))
        if cumulative is not None:
            tokens = cumulative
            notes.append("cost: tokens are the last recorded cumulative.generation_tokens; "
                         "no per-round token breakdown exists")

    scope = None
    if calls is not None or tokens is not None:
        has_harness = harness_calls is not None or harness_tokens is not None
        has_method = method_calls is not None or method_tokens is not None
        if has_harness and has_method:
            scope = "harness+method"
        elif has_harness:
            scope = "harness-only"
        else:
            scope = "method-only"
        if not has_method and side != "eval":
            notes.append("cost: method call/token counts are not recorded, so calls/tokens "
                         "totals cover the harness only")
        if harness_calls is None and harness_tokens is None:
            notes.append("cost: no harness call/token counts are recorded")

    if calls is None and tokens is None:
        notes.append("cost: curve.jsonl records no model-call or token counts")
    return {"calls": calls, "tokens": tokens, "cost_scope": scope}


def duration_seconds(points: list[dict], meta: dict, notes: list[str]):
    """`(seconds, source)`; the source is kept so a reader can tell a Sum from a clock."""
    cumulative = _num(_dig(points[-1], "cumulative", "wall_clock_s"))
    if cumulative is not None:
        return cumulative, "cumulative.wall_clock_s"
    recorded = _num(meta.get("wall_clock_s"))
    if recorded is not None:
        return recorded, "run_meta.wall_clock_s"
    per_round = []
    for point in points:
        number = _num(_dig(point, "cost", "wall_clock_s"))
        if number is None:
            per_round = []
            break
        per_round.append(number)
    if per_round:
        return sum(per_round), "sum of per-round cost.wall_clock_s"
    notes.append("duration: not recorded (no cumulative.wall_clock_s, run_meta.wall_clock_s "
                 "or complete per-round cost.wall_clock_s)")
    return None, None


# ------------------------------------------------------------------- changed files


def changed_files(run_dir: Path, per_round: list[dict], notes: list[str]) -> list[dict]:
    """Top 5 paths by churn (added+removed) across `diffs/round-*.json`."""
    diffs_dir = run_dir / "diffs"
    paths = sorted(diffs_dir.glob("round-*.json")) if diffs_dir.is_dir() else []
    if not paths:
        return []
    churn: dict[str, dict] = {}
    parsed_rounds = set()
    bad = 0
    missing_counts = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            bad += 1
            continue
        parsed_rounds.add(_int(data.get("round")))
        for entry in data["files"]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("path")
            if not isinstance(name, str) or not name:
                continue
            added = _int(entry.get("added"))
            removed = _int(entry.get("removed"))
            if added is None and removed is None:
                missing_counts += 1
            record = churn.setdefault(name, {"path": name, "added": 0, "removed": 0, "churn": 0})
            record["added"] += added or 0
            record["removed"] += removed or 0
            record["churn"] += (added or 0) + (removed or 0)
    if bad:
        notes.append(f"diffs: {bad} file(s) under diffs/ could not be parsed and were skipped")
    if missing_counts:
        notes.append(f"diffs: {missing_counts} changed-file record(s) carry no added/removed "
                     "counts, so their churn is a lower bound")
    curve_rounds = {entry["round"] for entry in per_round if entry["round"] is not None}
    # Round 0 is the baseline: it has no diff by construction, so only later gaps are a gap.
    absent = sorted(r for r in curve_rounds if r > 0 and r not in parsed_rounds)
    if absent:
        shown = ", ".join(str(r) for r in absent[:4]) + ("…" if len(absent) > 4 else "")
        notes.append(f"diffs: no round-*.json for {len(absent)} measured round(s) ({shown}), "
                     "so changed files cover only the rounds that recorded a diff")
    ranked = sorted(churn.values(), key=lambda item: (-item["churn"], item["path"]))
    return ranked[:5]


# ------------------------------------------------------------------------ summary


def summarise(run_dir: Path) -> dict:
    """Read one run directory and return the machine summary (also the source of the page)."""
    run_dir = Path(run_dir)
    points, bad_lines = load_curve(run_dir)
    meta, meta_note = load_meta(run_dir)
    notes: list[str] = []
    if bad_lines:
        notes.append(f"curve.jsonl: {bad_lines} line(s) could not be parsed as JSON objects "
                     "and were skipped")
    if meta_note:
        notes.append(meta_note)

    first, last = points[0], points[-1]
    run_cfg = _dig(meta, "config", "run") or {}
    identity = first.get("identity") if isinstance(first.get("identity"), dict) else {}

    run_id = meta.get("run_id") or first.get("run_id")
    if not run_id:
        run_id = run_dir.name
        notes.append("run_id: taken from the directory name; neither run_meta.json nor "
                     "curve.jsonl names it")

    dataset = meta.get("dataset") or _dig(first, "sampling", "dataset")
    side = run_cfg.get("side") or first.get("side")
    harness = meta.get("harness") or identity.get("harness_name")
    agent_model = meta.get("agent_model") or identity.get("agent_model")
    improver = _dig(meta, "improver", "name")
    improver_version = _dig(meta, "improver", "version")
    improver_model = _dig(meta, "improver", "model")
    if improver is None and meta:
        notes.append("improver: run_meta.json names none"
                     + (" (expected on an exam side: no improver runs there)"
                        if side == "eval" else ""))

    evaluated_from = meta.get("evaluated_from") or first.get("evaluated_from")
    if not isinstance(evaluated_from, dict):
        evaluated_from = None

    per_round: list[dict] = []
    previous = None
    for point in points:
        entry, previous = _round_entry(point, previous, notes)
        per_round.append(entry)

    scored = [(entry["round"], entry["score"]) for entry in per_round if entry["score"] is not None]
    best_round, best = (None, None)
    if scored:
        best_round, best = max(scored, key=lambda pair: pair[1])
    r0 = per_round[0]["score"]
    final = per_round[-1]["score"]

    delta_total = None
    if len(points) < 2:
        notes.append("delta_total: one curve point only, so there is no change to measure "
                     "(— rather than 0)")
    elif r0 is not None and final is not None:
        delta_total = final - r0

    if not scored:
        notes.append("score: curve.jsonl carries no numeric score, so score values are — (not 0)")
    elif r0 is None:
        notes.append("score: the first curve point carries no numeric score, so score.r0 is —")
    if per_round[0]["round"] not in (None, 0):
        notes.append(f"score.r0: the curve starts at round {per_round[0]['round']}, so "
                     "score.r0 is that point, not a round-0 baseline")

    first_tasks = per_task_scores(first)
    last_tasks = per_task_scores(last)
    has_per_task = bool(first_tasks or last_tasks)
    gained = lost = unchanged = None
    expected_tasks = _selected_tasks(last)
    if expected_tasks is None:
        expected_tasks = _selected_tasks(first)
    tasks = None
    if last_tasks:
        tasks = len(last_tasks)
    elif first_tasks:
        tasks = len(first_tasks)
    elif expected_tasks is not None:
        tasks = expected_tasks
    if not has_per_task:
        notes.append("per_task: absent from curve.jsonl, so gained/lost/unchanged are omitted "
                     "(never assumed 0)")
    elif not (first_tasks and last_tasks):
        notes.append("per_task: recorded on only one end of the curve, so gained/lost/"
                     "unchanged cannot be computed")
    elif len(points) < 2:
        notes.append("gained/lost/unchanged: one curve point only, so there is no before/after "
                     "to compare")
    else:
        common = set(first_tasks) & set(last_tasks)
        gained = sum(1 for task in common if last_tasks[task] > first_tasks[task])
        lost = sum(1 for task in common if last_tasks[task] < first_tasks[task])
        unchanged = sum(1 for task in common if last_tasks[task] == first_tasks[task])
        if len(common) != len(last_tasks) or len(common) != len(first_tasks):
            union = set(first_tasks) | set(last_tasks)
            notes.append(f"gained/lost/unchanged: cover {len(common)} of {len(union)} "
                         f"tasks; {len(union) - len(common)} appear on only one end")

    for field, label in (("invalid", "n_invalid/invalid"),
                         ("timeout", "n_timeout/timeouts"),
                         ("unmeasured", "sampling.task_ids/split")):
        reported = sum(1 for entry in per_round if entry[field] is not None)
        if reported == 0:
            notes.append(f"{field}: no {label} recorded, so the column is — (not 0)")
        elif reported < len(per_round):
            notes.append(f"{field}: recorded on {reported} of {len(per_round)} rounds; — on the "
                         "others means the run did not record it, not that it was 0")

    harness_sha_first = identity.get("harness_sha")
    last_identity = last.get("identity") if isinstance(last.get("identity"), dict) else {}
    harness_sha_last = last_identity.get("harness_sha")
    if not harness_sha_first and not harness_sha_last:
        notes.append("harness_sha: curve.jsonl identity records none, so first/last are —")

    cost = cost_totals(points, side, notes)
    duration, duration_source = duration_seconds(points, meta, notes)

    files = changed_files(run_dir, per_round, notes)
    if not files:
        diffs_dir = run_dir / "diffs"
        if not diffs_dir.is_dir():
            notes.append("diffs: no diffs/ directory, so changed files are —"
                         + (" (an exam run edits nothing)" if side == "eval"
                            else " (not 0 files)"))
        elif not list(diffs_dir.glob("round-*.json")):
            notes.append("diffs: diffs/ holds no round-*.json, so changed files are — (not 0)")
        else:
            notes.append("diffs: the recorded diffs contain no changed file paths, so "
                         "changed files are — (not 0)")

    summary = {
        "schema": SCHEMA,
        "run_id": run_id,
        "dataset": dataset,
        "side": side,
        "harness": harness,
        "improver": improver,
        "improver_version": improver_version,
        "improver_model": improver_model,
        "agent_model": agent_model,
        "evaluated_from": evaluated_from,
        "harness_sha_first": harness_sha_first,
        "harness_sha_last": harness_sha_last,
        "rounds": len(per_round),
        "score": {"r0": r0, "last": final, "best": best, "best_round": best_round},
        "delta_total": delta_total,
        "per_round": per_round,
        "per_task": has_per_task,
        "tasks": tasks,
        "gained": gained,
        "lost": lost,
        "unchanged": unchanged,
        "calls": cost["calls"],
        "tokens": cost["tokens"],
        "cost_scope": cost["cost_scope"],
        "duration_s": None if duration is None else round(duration, 3),
        "duration_source": duration_source,
        "changed_files": files,
        "notes": notes,
    }
    return summary


# ------------------------------------------------------------------------ markdown


def _fmt_score(value) -> str:
    return MISSING if value is None else f"{value:.3f}"


def _fmt_delta(value) -> str:
    if value is None:
        return MISSING
    if value == 0:
        return "0.000"
    return f"{value:+.3f}"


def _fmt_count(value) -> str:
    return MISSING if value is None else str(value)


def _fmt_round(value) -> str:
    return "?" if value is None else str(value)


def _fmt_human(count: float | None) -> str:
    if count is None:
        return MISSING
    if abs(count) >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if abs(count) >= 1_000:
        return f"{count / 1_000:.1f}k"
    return f"{count:.0f}" if float(count).is_integer() else f"{count:.1f}"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return MISSING
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m ({whole}s)"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{whole}s"


def _fmt_sha(sha: str | None) -> str:
    return MISSING if not sha else str(sha)[:7]


def _context_line(summary: dict) -> str:
    bits = [summary["dataset"] or MISSING, summary["side"] or MISSING,
            f"harness {summary['harness'] or MISSING}"]
    improver = summary["improver"]
    if improver:
        label = f"improver {improver}"
        if summary.get("improver_version"):
            label += f" {summary['improver_version']}"
        bits.append(label)
    else:
        bits.append(f"improver {MISSING}")
    bits.append(f"agent {summary['agent_model'] or MISSING}")
    return " · ".join(bits)


def _identity_line(summary: dict) -> str:
    first, last = summary["harness_sha_first"], summary["harness_sha_last"]
    sha = (f"harness {_fmt_sha(first)} → {_fmt_sha(last)}" if first != last
           else f"harness {_fmt_sha(first)}")
    bits = [sha, _fmt_duration(summary["duration_s"])]
    calls, tokens = summary["calls"], summary["tokens"]
    scope = summary.get("cost_scope")
    calls_label = _fmt_human(calls)
    tokens_label = _fmt_human(tokens)
    if scope and (calls is not None or tokens is not None):
        bits.append(f"calls {calls_label} ({scope}) · tokens {tokens_label} ({scope})")
    else:
        bits.append(f"calls {calls_label} · tokens {tokens_label}")
    return " · ".join(bits)


def _verdict_line(summary: dict) -> str:
    score = summary["score"]
    rounds = summary["rounds"]
    first_round = _fmt_round(summary["per_round"][0]["round"])
    last_round = _fmt_round(summary["per_round"][-1]["round"])
    tasks = summary["tasks"]
    task_bit = f", {tasks} tasks" if tasks is not None else ""
    if rounds < 2:
        side = summary["side"] or "unlabelled"
        heading = "exam point" if side == "eval" else f"{side} point"
        bits = [f"single {heading} r{first_round} {_fmt_score(score['last'])}{task_bit}"]
        source = summary.get("evaluated_from")
        if isinstance(source, dict) and source.get("run_id"):
            bits.append(f"replays {source['run_id']} @ round {_fmt_round(_int(source.get('round')))}")
        bits.append("no trend: one curve point, not one round")
        return ", ".join(bits)
    verdict = (f"r{first_round} {_fmt_score(score['r0'])} → r{last_round} "
               f"{_fmt_score(score['last'])} over {rounds} rounds{task_bit}")
    if summary["gained"] is not None:
        verdict += (f", {summary['gained']} gained / {summary['lost']} lost / "
                    f"{summary['unchanged']} unchanged")
    return verdict


def render_markdown(summary: dict) -> str:
    """The one page. At most ~40 lines, and every unknown stays a named absence."""
    lines = [f"# Run summary: {summary['run_id']}", _context_line(summary), _identity_line(summary), ""]
    lines.append(_verdict_line(summary))
    lines.append("")

    rows = summary["per_round"]
    shown, truncated = rows, False
    if len(rows) > MAX_TABLE_ROWS:
        shown = rows[:TRUNCATED_HEAD] + [None] + rows[-TRUNCATED_TAIL:]
        truncated = True
    lines.append("| round | score | Δ | invalid | unmeasured | timeout |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for entry in shown:
        if entry is None:
            lines.append(f"| … | … | … | … | … | … |")
            continue
        lines.append(f"| {_fmt_round(entry['round'])} | {_fmt_score(entry['score'])} | "
                     f"{_fmt_delta(entry['delta'])} | {_fmt_count(entry['invalid'])} | "
                     f"{_fmt_count(entry['unmeasured'])} | {_fmt_count(entry['timeout'])} |")
    lines.append("")

    lines.append("Top changed files by churn (added+removed):")
    if summary["changed_files"]:
        for item in summary["changed_files"]:
            lines.append(f"- `{item['path']}` +{item['added']} −{item['removed']} = {item['churn']}")
    else:
        lines.append(f"- {MISSING} (no diff record)")
    lines.append("")

    notes = summary["notes"]
    lines.append("## What is missing")
    if not notes and not truncated:
        lines.append("- nothing: every field this page reads is present")
    for note in notes[:MAX_MD_NOTES]:
        lines.append(f"- {note}")
    if len(notes) > MAX_MD_NOTES:
        lines.append(f"- … {len(notes) - MAX_MD_NOTES} more caveat(s) in end-summary.json")
    if truncated:
        lines.append(f"- per-round table shows {len(shown) - 1} of {len(rows)} rounds; "
                     "every row is in end-summary.json")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- output


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _line_count(text: str) -> int:
    return len(text.splitlines())


def write_summary(summary: dict, directory: Path, *, dry_run: bool, stream) -> None:
    """Write both files, saying what each write replaced.

    A summary is derived data, so replacing it is allowed -- but never silently: an existing
    different file is reported with the hash of what it used to be, which is enough to notice
    that somebody else's summary for a different curve just disappeared.
    """
    markdown = render_markdown(summary)
    payload = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    for name, text in ((SUMMARY_MD, markdown), (SUMMARY_JSON, payload)):
        path = Path(directory) / name
        old = None
        if path.exists():
            try:
                old = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                old = None
        if old is not None and old == text:
            print(f"unchanged {path} (byte-identical to the file on disk)", file=stream)
            continue
        if dry_run:
            if old is None:
                print(f"would write {path} ({_line_count(text)} lines)", file=stream)
            else:
                print(f"would replace {path} (was sha256 {_sha(old)}, {_line_count(old)} lines; "
                      f"now {_line_count(text)} lines)", file=stream)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if old is None:
            print(f"wrote {path} ({_line_count(text)} lines)", file=stream)
        else:
            print(f"replaced {path} (previous sha256 {_sha(old)}, {_line_count(old)} lines; "
                  f"now {_line_count(text)} lines)", file=stream)


def find_runs(root: Path) -> list[Path]:
    """Every directory under `root` that has a `curve.jsonl`, one level down at most.

    The archive layout is `root/<run-id>/curve.jsonl`, but a few roots group discarded runs
    one level deeper; those are found too without walking into a run's trace trees.
    """
    root = Path(root)
    if (root / "curve.jsonl").is_file():
        return [root]
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "curve.jsonl").is_file():
            found.append(child)
            continue
        for grandchild in sorted(child.iterdir()):
            if grandchild.is_dir() and (grandchild / "curve.jsonl").is_file():
                found.append(grandchild)
    return found


def _one_line(summary: dict, directory: Path) -> str:
    score = summary["score"]
    trend = (f"{_fmt_score(score['r0'])} → {_fmt_score(score['last'])}"
             if summary["rounds"] > 1 else f"{_fmt_score(score['last'])} (single point)")
    return (f"{summary['run_id']:<44s} rounds={summary['rounds']:<3d} {trend:<24s} "
            f"→ {directory / SUMMARY_MD}")


# ------------------------------------------------------------------------------- cli


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_summary.py",
        description="Write end-summary.md and end-summary.json for one run, or for every "
                    "run under a root.",
    )
    parser.add_argument("run_dir", nargs="?", help="a run directory containing curve.jsonl")
    parser.add_argument("--all", dest="runs_root", metavar="ROOT",
                        help="summarise every run under ROOT that has a curve.jsonl")
    parser.add_argument("--out", metavar="DIR",
                        help="write the summaries into DIR instead of the run directory "
                             "(with --all: DIR/<run-id>/)")
    parser.add_argument("--json", action="store_true",
                        help="print the summary JSON to stdout (write notices go to stderr)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be written and write nothing")
    args = parser.parse_args(argv)
    if bool(args.run_dir) == bool(args.runs_root):
        parser.error("give exactly one of <run-dir> or --all <root>")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    notice = sys.stderr if args.json else sys.stdout
    targets = find_runs(Path(args.runs_root)) if args.runs_root else [Path(args.run_dir)]
    if not targets:
        print(f"no run directory with a curve.jsonl under {args.runs_root}", file=sys.stderr)
        return 1

    summaries = []
    failures = 0
    for run_dir in targets:
        try:
            summary = summarise(run_dir)
        except (ValueError, OSError) as exc:
            print(f"skip {run_dir}: {exc}", file=sys.stderr)
            failures += 1
            continue
        out_dir = Path(args.out) / summary["run_id"] if args.out and args.runs_root \
            else Path(args.out) if args.out else run_dir
        write_summary(summary, out_dir, dry_run=args.dry_run, stream=notice)
        summaries.append(summary)
        if not args.json:
            print("  " + _one_line(summary, out_dir), file=sys.stdout)

    if args.json:
        payload = summaries[0] if (args.run_dir and summaries) else summaries
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
