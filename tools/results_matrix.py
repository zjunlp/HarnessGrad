#!/usr/bin/env python3
"""Export a run archive as one result matrix: method x dataset -> score.

Why this exists
---------------
The archive already contains the numbers. Each run directory has a `run_meta.json`
(which dataset, harness, method entrypoint, agent model) and a `curve.jsonl` (one
line per measured round: score, per-task scores, invalid count, call count). The
console can draw those curves one run at a time.

What is missing is the *table*: "method x dataset -> score", the form in which the
result is actually read, quoted and compared. Without an export, a reader who wants
to know how `dgm` did on `appworld` has to click through 784 runs and remember what
they saw. That is how a measured result becomes an invisible one.

What a row is
-------------
One row is **one run**, summarised from its own curve -- not one hand-picked number:

* `r0` -- the score of the run's earliest scored point (round 0 in this archive);
* `last` -- the score of its highest-numbered scored point;
* `best` / `best_round` -- the highest score over its scored points, and the round
  that first reached it;
* `delta_pp` -- `(best - r0) * 100`, in percentage points, and `delta` -- the same
  difference as a raw fraction. `last_delta_pp` / `last_delta` are the final-round
  change (`last - r0`), which *can* be negative when a method ends below its start:
  `best - r0` cannot, because `best` is a maximum over the same points that contain
  `r0`;
* `rounds` -- how many scored points the run has;
* `invalid` -- the sum of `n_invalid` over those points;
* `calls` -- the sum of `cost.harness_model_calls` over those points;
* `n_tasks` -- the number of tasks in the last point's `per_task`.

A single scored point has no trend, so its deltas are `null` (printed as `—`), never
`0.0`: a run with one point is an eval measurement, not a flat curve.

What is never done
------------------
* **A missing measurement is never a zero.** A run whose `curve.jsonl` is absent,
  whose curve has no numeric score, or whose record says it is still in flight is
  listed in `skipped` with the reason, not printed as `0.000` in the matrix.
* **Nothing is guessed silently.** `method` comes from the curve point's
  `identity.method`, else the run's recorded `method_entrypoint`, else the run it
  evaluates (`evaluated_from`), else the platform's own run-id template, else it is
  `unknown`; the row says which with `method_source`.
* **Nothing in the run archive is written.** Discovery only reads.

The JSON is reproducible: no timestamps, deterministic ordering (group, then dataset,
then harness, then run id). The markdown carries no timestamp either, so it is
diffable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "harnessgrad.results-matrix.v1"

#: Printed where the record does not define or record a value. Never `0`.
DASH = "—"

#: A directory is treated as a run when it holds at least one of these.
RUN_MARKERS = ("curve.jsonl", "run_meta.json", "events.jsonl")

#: Values a record uses to say a run is still being produced.
RUNNING_WORDS = frozenset({"running", "in_progress", "in-flight", "started", "pending"})

#: The platform's own run-id templates, for runs whose record does not name a method
#: (checkpoint evals, exams and H0 references made before `method_entrypoint` was
#: written into `run_meta.json`). Deliberately narrow: a wrong method on a row is
#: worse than `unknown`, so only these exact shapes are read.
_APPWORLD_ID = re.compile(
    r"^appworld-(?:dev|study|exam|repair2?|probe)-([A-Za-z0-9_]+)(?:-|$)")
_RSI10_ID = re.compile(
    r"^rsi10-(?:[a-z]+-)?\d{8}T\d{6}Z-([A-Za-z0-9_]+)-(?:train|exam|ckpt)(?:-|$)")

_METHODS_SEGMENT = "methods"


# --------------------------------------------------------------------------- reading

def _read_json(path: Path) -> tuple[dict, str | None]:
    """`(value, problem)`. A corrupt record is reported, never silently empty."""
    if not path.is_file():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"{path.name} does not parse: {exc}"
    if not isinstance(value, dict):
        return {}, f"{path.name} is not a JSON object"
    return value, None


def _read_curve(path: Path) -> tuple[list[dict], dict | None]:
    """Every curve point, or the reason the file cannot be trusted as one.

    A corrupt line fails the **whole run** rather than being skipped: dropping it
    would leave a curve that looks complete while missing a round, and a reader
    cannot tell that from a run whose round really is absent.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [], {"reason": "corrupt_curve", "detail": f"curve.jsonl unreadable: {exc}"}
    points: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            return [], {"reason": "corrupt_curve",
                        "detail": f"curve.jsonl line {number} is not JSON: {exc}"}
        if not isinstance(value, dict):
            return [], {"reason": "corrupt_curve",
                        "detail": f"curve.jsonl line {number} is not a JSON object"}
        points.append(value)
    if not points:
        return [], {"reason": "empty_curve", "detail": "curve.jsonl holds no point"}
    return points, None


def _event_records(path: Path, *, tail_bytes: int | None = None) -> list[dict]:
    """Parsed event records, from the head (default) or the tail of the file."""
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            if tail_bytes is None:
                data = handle.read(65536)
            else:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - tail_bytes))
                data = handle.read()
    except OSError:
        return []
    out: list[dict] = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            # A tail read can start mid-line; a head read can meet garbage. Either
            # way one bad line is not the whole log.
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _header_n_tasks(run_dir: Path) -> int | None:
    """How many tasks this run set out to measure, from its header event.

    The header is the only run-level record of the selected task count that is
    written before the first point exists.
    """
    for record in _event_records(run_dir / "events.jsonl"):
        if record.get("kind") == "header":
            count = record.get("n_tasks")
            if isinstance(count, int) and not isinstance(count, bool):
                return count
    return None


def _refusal_note(run_dir: Path) -> str | None:
    """The driver's own sentence for why it wrote no point, when it said so.

    "None of the tasks produced a scoreable result" is an *unmeasured* run, not a
    missing one, and the archive records the difference in the event log. Reading it
    is what lets the skipped table say which it was.
    """
    for record in reversed(_event_records(run_dir / "events.jsonl", tail_bytes=8192)):
        message = record.get("message") or ""
        if "refusing to write a point" in message:
            return str(message)
    return None


def _inflight_round(run_dir: Path, n_tasks: int | None) -> dict | None:
    """A round that started but left no curve point, and how far it got.

    `progress/round-<N>/<task>.json` is written the moment one task finishes, so
    fewer files than the header's `n_tasks` means the round had not finished when
    the process stopped. That is the run record's own "in flight" signal; it cannot
    distinguish "still running" from "killed mid-round", so the detail says both.
    """
    if n_tasks is None:
        return None
    progress = run_dir / "progress"
    if not progress.is_dir():
        return None
    for child in sorted(progress.iterdir()):
        match = re.fullmatch(r"round-(\d+)", child.name)
        if not match or not child.is_dir():
            continue
        number = int(match.group(1))
        files = sorted(p for p in child.iterdir() if p.suffix == ".json")
        if files and len(files) < n_tasks:
            return {"round": number, "tasks": len(files), "n_tasks": n_tasks}
    return None


# ------------------------------------------------------------------- method naming

def _entrypoint_method(meta: dict) -> str | None:
    """The method directory named by the run's `method_entrypoint` path."""
    entry = meta.get("method_entrypoint")
    if not isinstance(entry, str) or not entry:
        return None
    parts = entry.replace("\\", "/").split("/")
    if _METHODS_SEGMENT in parts:
        index = parts.index(_METHODS_SEGMENT) + 1
        if index < len(parts) and parts[index]:
            return parts[index]
    return None


def _evaluated_from(meta: dict, points: list[dict]) -> str | None:
    """The run this one measures, if the record says.

    `run_meta.json` carries the join from the first write; a curve point carries it
    after the round finished, so either can answer.
    """
    sources: list[Any] = [meta.get("evaluated_from")]
    if points:
        sources.append((points[-1] or {}).get("evaluated_from"))
    for source in sources:
        if isinstance(source, dict):
            run_id = source.get("run_id")
        elif isinstance(source, str):
            run_id = source
        else:
            run_id = None
        if isinstance(run_id, str) and run_id:
            return run_id
    return None


def _method_from_id(run_id: str) -> str | None:
    match = _APPWORLD_ID.match(run_id) or _RSI10_ID.match(run_id)
    return match.group(1) if match else None


def _point_method(points: list[dict]) -> str | None:
    """The method a curve point recorded for itself, if any point did.

    Study runs write `identity.method` on their points. It is the most direct record
    there is -- the point is the measurement -- so it is read first when present.
    """
    for point in points:
        identity = point.get("identity")
        if isinstance(identity, dict):
            method = identity.get("method")
            if isinstance(method, str) and method:
                return method
    return None


def resolve_method(run_id: str, meta: dict, points: list[dict],
                   index: dict[str, Path], *, depth: int = 0) -> tuple[str, str]:
    """`(method, where it came from)`, in the order the record can be trusted.

    1. a curve point's `identity.method` -- the measurement names its own method;
    2. `method_entrypoint` -- the run names its own method;
    3. `evaluated_from` -- an eval/checkpoint run names the study run it measured;
    4. the platform's run-id template -- last resort, for early runs that predate
       `method_entrypoint`;
    5. `unknown`.

    Step 3 is a join, not an inference: the method that produced the state is the
    one recorded on the run that produced it. It stops at `depth` so a cycle (never
    seen, but cheap to survive) cannot recurse forever.
    """
    own = _point_method(points)
    if own:
        return own, "identity"
    own = _entrypoint_method(meta)
    if own:
        return own, "entrypoint"
    parent = _evaluated_from(meta, points) if depth < 4 else None
    if parent and parent in index and parent != run_id:
        parent_meta, _ = _read_json(index[parent] / "run_meta.json")
        method, _source = resolve_method(parent, parent_meta, [], index, depth=depth + 1)
        if method != "unknown":
            return method, "evaluated_from"
    guessed = _method_from_id(run_id)
    if guessed:
        return guessed, "run_id"
    return "unknown", "none"


# ------------------------------------------------------------------ row construction

def _numeric(value: Any) -> float | None:
    """A score, or None. `True`/`False` are not scores and strings are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _round_of(point: dict, position: int) -> int:
    value = point.get("round")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return position


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sum_field(points: list[dict], getter) -> int | None:
    """Sum a per-point count, or None when no point recorded it.

    None (printed `—`) is the honest answer for a record that predates the field;
    0 would read as "it happened zero times", which is a different claim.
    """
    seen = False
    total = 0
    for point in points:
        value = getter(point)
        if isinstance(value, int) and not isinstance(value, bool):
            seen = True
            total += value
    return total if seen else None


def _harness_calls(point: dict) -> int | None:
    cost = point.get("cost")
    if isinstance(cost, dict):
        return cost.get("harness_model_calls")
    return None


def _build_row(run_id: str, meta: dict, points: list[dict],
               method: str, method_source: str) -> dict:
    """Summarise one run from its own scored points."""
    scored = [(p, _round_of(p, i)) for i, p in enumerate(points)
              if _numeric(p.get("score")) is not None]
    scored.sort(key=lambda item: item[1])

    first_point, _ = scored[0]
    last_point, _ = scored[-1]
    # Earliest round wins a tie, so `best_round` is stable rather than "whichever
    # max() happened to return".
    best_point, best_round = max(
        scored, key=lambda item: (_numeric(item[0].get("score")), -item[1]))

    r0 = _numeric(first_point.get("score"))
    last = _numeric(last_point.get("score"))
    best = _numeric(best_point.get("score"))
    assert r0 is not None and last is not None and best is not None

    identity = last_point.get("identity") if isinstance(last_point.get("identity"), dict) else {}
    sampling = last_point.get("sampling") if isinstance(last_point.get("sampling"), dict) else {}
    per_task = last_point.get("per_task") if isinstance(last_point.get("per_task"), dict) else {}

    n_tasks = len(per_task) or None
    if n_tasks is None:
        scored_count = last_point.get("n_scored")
        if isinstance(scored_count, int) and not isinstance(scored_count, bool):
            n_tasks = scored_count

    # A trend needs two points. One measured point is an eval, not a flat curve;
    # `null` keeps that distinction instead of printing a zero that looks measured.
    has_trend = len(scored) >= 2
    delta = (best - r0) if has_trend else None
    last_delta = (last - r0) if has_trend else None

    return {
        "run_id": run_id,
        "method": method,
        "method_source": method_source,
        "harness": _string(identity.get("harness_name")) or _string(meta.get("harness")),
        "dataset": _string(meta.get("dataset")) or _string(sampling.get("dataset")),
        "pool": _string(sampling.get("selected_within")),
        "side": _string(last_point.get("side")),
        "agent_model": _string(identity.get("agent_model")) or _string(meta.get("agent_model")),
        "r0": r0,
        "last": last,
        "best": best,
        "best_round": best_round,
        "delta_pp": round(delta * 100.0, 6) if delta is not None else None,
        "delta": delta,
        "last_delta_pp": round(last_delta * 100.0, 6) if last_delta is not None else None,
        "last_delta": last_delta,
        "rounds": len(scored),
        "invalid": _sum_field([p for p, _ in scored], lambda p: p.get("n_invalid")),
        "calls": _sum_field([p for p, _ in scored], _harness_calls),
        "n_tasks": n_tasks,
    }


def _skip(run_id: str, meta: dict, points: list[dict], method: str,
          method_source: str, reason: str, detail: str) -> dict:
    """One run that cannot be a row, with the record's reason for it."""
    tail = points[-1] if points else {}
    identity = tail.get("identity") if isinstance(tail.get("identity"), dict) else {}
    sampling = tail.get("sampling") if isinstance(tail.get("sampling"), dict) else {}
    return {
        "run_id": run_id,
        "method": method,
        "method_source": method_source,
        "harness": _string(identity.get("harness_name")) or _string(meta.get("harness")),
        "dataset": _string(meta.get("dataset")) or _string(sampling.get("dataset")),
        "side": _string(tail.get("side")),
        "pool": _string(sampling.get("selected_within")),
        "reason": reason,
        "detail": detail,
    }


def _marked_running(meta: dict, points: list[dict]) -> bool:
    """True when the record itself says the run is still in flight."""
    for source in (meta.get("state"), meta.get("status")):
        if isinstance(source, str) and source.strip().lower() in RUNNING_WORDS:
            return True
    for point in points:
        for source in (point.get("state"), point.get("status")):
            if isinstance(source, str) and source.strip().lower() in RUNNING_WORDS:
                return True
    return False


def summarise(run_dir: Path, run_id: str, index: dict[str, Path],
              *, min_rounds: int = 0) -> tuple[str, dict]:
    """`("row", row)` or `("skipped", entry)` for one run directory."""
    meta, meta_problem = _read_json(run_dir / "run_meta.json")
    curve_path = run_dir / "curve.jsonl"

    points: list[dict] = []
    curve_problem: dict | None = None
    if curve_path.is_file():
        points, curve_problem = _read_curve(curve_path)
    method, method_source = resolve_method(run_id, meta, points, index)

    if curve_problem:
        detail = curve_problem["detail"]
        if meta_problem:
            detail = f"{detail}; {meta_problem}"
        return "skipped", _skip(run_id, meta, points, method, method_source,
                                curve_problem["reason"], detail)

    if not curve_path.is_file():
        # No curve: say *why* the archive has none when the record can.
        refusal = _refusal_note(run_dir)
        inflight = _inflight_round(run_dir, _header_n_tasks(run_dir))
        if _marked_running(meta, points):
            return "skipped", _skip(run_id, meta, points, method, method_source,
                                    "running", "the run record marks this run as in flight")
        if refusal:
            return "skipped", _skip(run_id, meta, points, method, method_source,
                                    "unmeasured", refusal)
        if inflight:
            return "skipped", _skip(
                run_id, meta, points, method, method_source, "running",
                f"round {inflight['round']}: {inflight['tasks']} of "
                f"{inflight['n_tasks']} task records and no curve point "
                f"(in flight, or interrupted before the round finished)")
        detail = "no curve.jsonl, so no measured point was recorded"
        if meta_problem:
            detail = f"{detail}; {meta_problem}"
        return "skipped", _skip(run_id, meta, points, method, method_source,
                                "no_curve", detail)

    if _marked_running(meta, points):
        return "skipped", _skip(run_id, meta, points, method, method_source,
                                "running", "the run record marks this run as in flight")

    scored = [p for p in points if _numeric(p.get("score")) is not None]
    if not scored:
        return "skipped", _skip(
            run_id, meta, points, method, method_source, "unmeasured",
            "curve points carry no numeric score (the run was measured but not scored)")

    row = _build_row(run_id, meta, points, method, method_source)

    if (min_rounds and row["side"] == "train" and row["rounds"] < min_rounds):
        return "skipped", _skip(
            run_id, meta, points, method, method_source, "below_min_rounds",
            f"training run has {row['rounds']} scored round(s); --min-rounds is {min_rounds}")
    return "row", row


# ------------------------------------------------------------------------ discovery

def _is_run_dir(path: Path) -> bool:
    return any((path / marker).is_file() for marker in RUN_MARKERS)


def discover(roots: list[str]) -> tuple[list[tuple[Path, str]], list[str]]:
    """Every run directory under the roots, sorted, plus the run ids seen twice.

    A root may be a directory of run directories or a single run directory. A
    directory that carries none of the run markers (a grouping directory such as
    `runs_discarded/`, or a stray file) is not a run and is not reported as one.
    """
    found: list[tuple[Path, str]] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw)
        candidates = [root] if _is_run_dir(root) else (
            sorted(p for p in root.iterdir() if p.is_dir() and _is_run_dir(p))
            if root.is_dir() else [])
        for run_dir in candidates:
            meta, _ = _read_json(run_dir / "run_meta.json")
            run_id = _string(meta.get("run_id")) or run_dir.name
            if run_id in seen:
                duplicates.append(run_id)
                continue
            seen.add(run_id)
            found.append((run_dir, run_id))
    return found, duplicates


def build_matrix(roots: list[str], *, dataset: str | None = None, side: str | None = None,
                 min_rounds: int = 0) -> tuple[dict, list[str]]:
    """The reproducible payload, plus any run ids that appeared under two roots."""
    discovered, duplicates = discover(roots)
    index = {run_id: run_dir for run_dir, run_id in discovered}

    rows: list[dict] = []
    skipped: list[dict] = []
    for run_dir, run_id in discovered:
        kind, entry = summarise(run_dir, run_id, index, min_rounds=min_rounds)
        if dataset is not None and entry.get("dataset") != dataset:
            continue
        if side is not None and entry.get("side") != side:
            continue
        (rows if kind == "row" else skipped).append(entry)

    rows.sort(key=lambda r: (r.get("method") or "unknown", r.get("dataset") or "",
                             r.get("harness") or "", r["run_id"]))
    skipped.sort(key=lambda r: (r["reason"], r["run_id"]))
    payload = {"schema": SCHEMA, "generated_from": list(roots), "rows": rows,
               "skipped": skipped}
    return payload, duplicates


# ------------------------------------------------------------------------- markdown

def _dash(value: Any) -> str:
    return DASH if value is None else str(value)


def _pct(value: float | None) -> str:
    return DASH if value is None else f"{value * 100.0:.1f}"


def _pp(value: float | None) -> str:
    return DASH if value is None else f"{value:+.1f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(cell) for cell in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(header)) + " |",
             "| " + " | ".join("-" * widths[i] for i in range(len(header))) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(cell.ljust(widths[i])
                                       for i, cell in enumerate(row)) + " |")
    return lines


_ROW_COLUMNS = ["run_id", "method", "harness", "dataset", "pool", "side", "agent_model",
                "r0", "last", "best", "best_round", "delta_pp", "last_delta_pp",
                "rounds", "invalid", "calls", "n_tasks"]
_SKIP_COLUMNS = ["run_id", "method", "harness", "dataset", "side", "reason", "detail"]


def _row_cells(row: dict) -> list[str]:
    return [
        row["run_id"], _dash(row.get("method")), _dash(row.get("harness")),
        _dash(row.get("dataset")), _dash(row.get("pool")), _dash(row.get("side")),
        _dash(row.get("agent_model")),
        _pct(row.get("r0")), _pct(row.get("last")), _pct(row.get("best")),
        _dash(row.get("best_round")), _pp(row.get("delta_pp")), _pp(row.get("last_delta_pp")),
        _dash(row.get("rounds")), _dash(row.get("invalid")), _dash(row.get("calls")),
        _dash(row.get("n_tasks")),
    ]


def _skip_cells(entry: dict) -> list[str]:
    return [entry["run_id"], _dash(entry.get("method")), _dash(entry.get("harness")),
            _dash(entry.get("dataset")), _dash(entry.get("side")),
            entry["reason"], entry["detail"]]


def render_markdown(payload: dict, *, group_by: str = "method") -> str:
    """The human-readable matrix: a header that defines the row, then the tables."""
    rows, skipped = payload["rows"], payload["skipped"]
    roots = payload["generated_from"]
    groups = sorted({(row.get(group_by) or "unknown") for row in rows})

    lines = [
        "# HarnessGrad results matrix",
        "",
        "Every row is one run, read from its own `run_meta.json` and `curve.jsonl`. "
        "The score columns are percentages of the tasks that point scored; the JSON "
        "keeps the raw fraction.",
        "",
        "Roots: " + (", ".join(f"`{root}`" for root in roots) or "`(none)`"),
        f"Grouped by: {group_by}. Rows: {len(rows)} included, {len(skipped)} skipped.",
        "",
        "## How a row was computed",
        "",
        "- `r0` — score of the run's earliest scored curve point (round 0 in this archive).",
        "- `last` — score of its highest-numbered scored point.",
        "- `best` / `best_round` — highest score over its scored points, and the round that "
        "first reached it (a tie keeps the earlier round).",
        "- `delta_pp` = `(best - r0) * 100`, in **percentage points**; the JSON's `delta` is "
        "the same difference as a raw fraction. `best` is a maximum over the points that "
        "contain `r0`, so this delta is never negative.",
        "- `last_delta_pp` = `(last - r0) * 100` — the final-round change, which **can** be "
        "negative when a method ends below where it started.",
        "- `rounds` — number of scored curve points; `invalid` — sum of `n_invalid` over "
        "them; `calls` — sum of `cost.harness_model_calls` over them; `n_tasks` — tasks in "
        "the last point's `per_task`.",
        f"- `{DASH}` means the run record does not define or does not record that value. "
        "It is **never** a zero. A single scored point (an eval measurement) has no trend, "
        "so its deltas are "
        f"`{DASH}`; a run with no scored point is not printed as `0.000` at all — it is in "
        "the skipped table with its reason.",
        "",
        "- `side=train` is a training curve: the method has read the traces of the tasks it "
        "is scored on, so its score is inflated by construction. A `side` of "
        f"`{DASH}` means the record does not say which side the run measured.",
        "- `method` comes from the curve point's `identity.method`, else the run's "
        "`method_entrypoint`, else the `evaluated_from` run, else the platform's run-id "
        "template, else `unknown`; `method_source` in the JSON says which. `pool` is the "
        "curve point's `sampling.selected_within`.",
        "",
    ]

    for group in groups:
        group_rows = [row for row in rows if (row.get(group_by) or "unknown") == group]
        lines.append(f"## {group_by}: {group} ({len(group_rows)} run(s))")
        lines.append("")
        lines.extend(_table(_ROW_COLUMNS, [_row_cells(row) for row in group_rows]))
        lines.append("")

    if not rows:
        lines.extend(["No run could be summarised. The skipped table below says why.", ""])

    lines.append(f"## Runs that could not be summarised ({len(skipped)} run(s), by reason)")
    lines.append("")
    if skipped:
        lines.extend(_table(_SKIP_COLUMNS, [_skip_cells(entry) for entry in skipped]))
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------------------ cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export run archives as one method x dataset result matrix.")
    parser.add_argument("--runs", required=True,
                        help="comma-separated directories of run directories "
                             "(a single run directory is accepted too)")
    parser.add_argument("--dataset", help="only rows measured on this dataset")
    parser.add_argument("--side", choices=("train", "eval"),
                        help="only rows measured on this side of the split")
    parser.add_argument("--md", dest="md_path", metavar="FILE",
                        help="write the markdown here instead of printing it")
    parser.add_argument("--json", dest="json_path", metavar="FILE",
                        help="write the reproducible JSON matrix here")
    parser.add_argument("--min-rounds", type=int, default=0, metavar="N",
                        help="drop training runs with fewer than N scored rounds "
                             "(eval-side single points are never filtered)")
    parser.add_argument("--group-by", choices=("method", "harness"), default="method",
                        help="group the rows by method (default) or harness")
    args = parser.parse_args(argv)

    if args.min_rounds < 0:
        parser.error("--min-rounds must be >= 0")
    roots = [part.strip() for part in args.runs.split(",") if part.strip()]
    if not roots:
        parser.error("--runs needs at least one directory")
    missing = [root for root in roots if not Path(root).is_dir()]
    if missing:
        parser.error("not a directory: " + ", ".join(missing))

    payload, duplicates = build_matrix(roots, dataset=args.dataset, side=args.side,
                                       min_rounds=args.min_rounds)
    for run_id in duplicates:
        print(f"warning: run {run_id} appears under more than one root; kept the first",
              file=sys.stderr)
    markdown = render_markdown(payload, group_by=args.group_by)

    if args.md_path:
        path = Path(args.md_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)
    else:
        print(markdown)

    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # No timestamp and no mtime: the same archive produces the same bytes.
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)

    print(f"{len(payload['rows'])} run(s) included, {len(payload['skipped'])} skipped",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
