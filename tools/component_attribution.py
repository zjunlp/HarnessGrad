#!/usr/bin/env python3
"""Which harness components methods edit -- and what a round's gain looks like by component.

**Why this tool exists.** `harnessgrad/components.py` names ten components and
`harnessgrad/loops/mode_a.py` records `editable_surface_components` on every curve point
it writes, but no shipped harness declared the map until 2026-11-02. So a capability
existed with no data: in the archive up to that date **zero** curve points named a
component. A missing measurement and a measured zero look identical in a table, and this
tool is what keeps them apart.

It answers two different questions, and says which is which everywhere:

1. **Which components do methods edit?** Counts of attributed rounds per component and
   per method. This is a count of *edits*, not of gains: a round that touched `control`
   and lost 3 pp still counts as a round that touched `control`.
2. **What does a round's gain look like by component set?** For each attributed round,
   `gain_pp = (score - the previous round's score) * 100`, grouped by the set of components
   that round names (and by single component), with the number of rounds in each group so
   a reader can see how thin the evidence is. A mean over `n = 1` is one round's move, not
   a trend.

It also asks the corpus's own version of "should evolution be restricted to prompts and
skills?": the **narrow-vs-broad** split. `narrow` is a round that names only prompt-like
components (`context`, or a `prompt`/`skill`/`skills` value when a record carries one);
`broad` is a round that names anything else. Mean, median and `n` for each side.

**What is never done.**
* **An unattributed point is never a component.** A point counts as attributed only when
  `editable_surface_components.components` names at least one component. A point with no
  field, a point whose field names none (`[]`: a path moved and no component was named), and
  a point whose field cannot be read are all counted as `unattributed`, with the cases
  broken apart in `No data` (`field_absent`, `declaration_unreadable`, `no_component_named`,
  `no_path_moved`). On the 2026-11-02 archive that is 1008 analysed points -- 1049 including
  the other `runs/` roots under the same archive, and 54 more in the runs it labels
  discarded, which are counted rather than silently dropped.
* **Two denominators, because one of them is a denominator of nothing.** `points.total` is
  every curve point read; `points.with_field` is the subset that carries
  `editable_surface_components`, i.e. the points on which an attribution was *attempted*. A
  ratio about attribution belongs over `with_field`; today's archive has 1008 total and 310
  with the field, so "0 of 1008" and "0 of 310" are the same measurement read two ways and
  only the second is about attribution. `points.in_discarded_runs` is printed too, so a
  reader can audit that the denominator is a subset of the archive.
* **A state names what the record shows, not what caused it.** `no_component_named` replaced
  the earlier `outside_declared_map`: `components.attribute` writes
  `{"components": [], "unattributed": touched}` both for a harness that declared no map and
  for one whose map misses the moved path, so the record cannot say "outside". Later
  declarations must not be retroactively attributed to historical runs.
* **An unknown value is `—`, never `0`.** A round with no previous point, or where either
  score is not a number, has no gain; it is excluded from means and medians and counted in
  `unknown`. A component with no attributed round gets no row rather than a fabricated 0.
* **A corrupt curve is not partially read.** Dropping one line would silently move every
  later round's "previous round", so the whole run is skipped with its reason.
* **Nothing in the archive is written.** Discovery only reads; `--json`/`--md` write where
  they are told to.

**`--min-rounds` filters studies, not points.** A *study* is one run. A run with fewer than
N **attributed rounds** is skipped (listed in `No data`) and contributes nothing to any
table. N = 0 (the default) keeps every run, including runs whose points are all
unattributed -- those are the evidence for the unattributed count.

The markdown and JSON are reproducible: no timestamps, every list sorted, every float
rounded to six decimals, so the same archive produces byte-identical output (`--json`
twice is a quality test).

Usage:
    python3 tools/component_attribution.py --runs <root>[,<root>...] [--json FILE] [--md FILE] [--min-rounds N]

Exit status: 0 after a successful read, including when nothing is attributed (the current
state of the archive); 2 for a bad invocation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harnessgrad.components import COMPONENTS, UNATTRIBUTED  # noqa: E402

SCHEMA = "harnessgrad.component-attribution.v1"

#: Printed where the record does not provide a value. Never `0`.
DASH = "—"

#: The curve-point key this tool reads. It is the `components.attribute()` output.
FIELD = "editable_surface_components"

#: The closed vocabulary, taken from its one definition rather than repeated here.
COMPONENT_ORDER = tuple(COMPONENTS)

#: Components a round may name and still count as "prompt-like" under the narrow side of
#: the split. The ten-name vocabulary has no `prompt` or `skill` component, so today
#: `narrow` means `context` alone; the extra names are accepted because the question this
#: split asks is phrased in those terms and a future record may use them. Anything else --
#: including an unknown name -- puts the round on the broad side.
NARROW_COMPONENTS = frozenset({"context", "prompt", "skill", "skills"})

#: A directory is treated as a run when it holds at least one of these.
RUN_MARKERS = ("curve.jsonl", "run_meta.json", "events.jsonl")

#: A subtree the archive itself labels discarded. Its runs are listed in `No data` and are
#: not read as evidence. Passing such a directory as a root still reads it: an explicit
#: request is not an accident.
DISCARDED_DIRS = frozenset({"runs_discarded", "discarded", "_discarded"})

#: How deep below a root a run directory is looked for. The archive nests runs one level
#: (`runs/<run>/`) plus `runs/runs_discarded/<run>/`; 6 is generous and stops a symlink
#: cycle from recursing forever.
MAX_DISCOVERY_DEPTH = 6

UNKNOWN_METHOD = "unknown"

#: The four states of a point that is not attributed, kept apart because they support
#: different conclusions. See `_components_of`.
STATE_ATTRIBUTED = "attributed"
STATE_FIELD_ABSENT = "field_absent"
STATE_UNREADABLE = "declaration_unreadable"
#: Named for what the record shows -- a path moved and no component was named -- and not for
#: a cause the record cannot establish. See `_components_of`.
STATE_NO_COMPONENT = "no_component_named"
STATE_NO_PATH = "no_path_moved"

#: Display order of the unattributed states: from "the record never attempted an attribution"
#: to "the record says nothing moved".
UNATTRIBUTED_STATES = (
    STATE_FIELD_ABSENT,
    STATE_UNREADABLE,
    STATE_NO_COMPONENT,
    STATE_NO_PATH,
)

#: The narrowest run-id templates that name a method. The first shape is
#: `tools/results_matrix.py`'s, extended to the four-digit `HHMMZ` clock the later
#: `rsi10-*T1715Z-*` runs use (its six-digit form covers the earlier ones). Deliberately
#: duplicated rather than imported: this tool's method column is a label on attribution
#: counts, and it must not change because a presentation tool's resolution was edited.
#: Only an exact known prefix is read; anything else is `unknown`, because a wrong method
#: on a count is worse than an honest `unknown`.
_APPWORLD_ID = re.compile(
    r"^appworld-(?:dev|study|exam|repair2?|probe)-([A-Za-z0-9_]+)(?:-|$)")
_RSI10_ID = re.compile(
    r"^rsi10-(?:[a-z]+-)?\d{8}T\d{4,6}Z-([A-Za-z0-9_]+)-(?:train|exam|ckpt)(?:-|$)")

#: Values an `identity.method` may carry to say it names no method.
_NO_METHOD = frozenset({"", "n/a", "na", "none", "unknown"})


# ------------------------------------------------------------------------ small helpers

def _string(value: Any) -> str | None:
    """A non-empty string, or None. `True` is not a string and not a name."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _numeric(value: Any) -> float | None:
    """A score, or None. `True`/`False` are not scores and strings are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _round6(value: float | None) -> float | None:
    """Stable six-decimal rounding, with `-0.0` normalised to `0.0`.

    Two runs over the same archive must produce the same bytes; Python's float
    formatting is already deterministic, but `-0.0` in a mean (`-0.0`) reads as a
    different value from `0.0` and is worth folding away.
    """
    if value is None:
        return None
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def _median(values: list[float]) -> float | None:
    """The median, or None for no values. An even count averages the two middle values."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _stats(gains: list[float], rounds: int, runs: int) -> dict:
    """One group's gain summary. `n` counts rounds with a gain, never runs or points.

    `unknown` is the number of attributed rounds in the group that carry no gain (the
    round has no predecessor, or a score on one side is missing). Keeping it visible is
    what stops `mean` over two of five rounds from being read as the group's gain.
    """
    gains = [gain for gain in gains if gain is not None]
    return {
        "rounds": rounds,
        "runs": runs,
        "n": len(gains),
        "unknown": rounds - len(gains),
        "mean_pp": _round6(sum(gains) / len(gains)) if gains else None,
        "median_pp": _round6(_median(gains)),
    }


def _component_order(names: Iterable[str]) -> list[str]:
    """Known components in the vocabulary's order, then any recorded extra alphabetically."""
    index = {name: position for position, name in enumerate(COMPONENT_ORDER)}
    return sorted(set(names), key=lambda name: (index.get(name, len(index)), name))


def _method_order(method: str) -> tuple[int, str]:
    """Alphabetical, with `unknown` last: it is an absence, not a peer of the methods."""
    return (1 if method == UNKNOWN_METHOD else 0, method)


# ------------------------------------------------------------------------------- reading

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
    """Every curve point, or the reason the file cannot be trusted as a curve.

    A corrupt line fails the **whole run** rather than being skipped: a gain is a
    difference against the previous round, so a dropped round silently changes the
    numerator and denominator of every later round in that run.
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


def _components_of(point: dict) -> tuple[tuple[str, ...], str]:
    """`(components, state)` for one curve point. `unattributed` is never a component.

    The states are the four ways a point can fail to attribute, plus success:

    * `field_absent` -- the point carries no `editable_surface_components`, so the record
      never attempted an attribution: a point written before the field existed, or an H0
      point, which no round produced (the field is written per round, together with the
      touched paths it names);
    * `declaration_unreadable` -- the field is there but no component list can be read
      from it;
    * `no_component_named` -- the list is empty (or names only `unattributed`) and the
      point's `unattributed` paths say a file moved. **The record does not say whether the
      harness declared a map**: `components.attribute` returns the same
      `{"components": [], "unattributed": touched}` both for a harness with no map and for
      one whose map does not name the moved path. So the name states only what is recorded
      -- a path moved and no component was named -- and a reader who needs the distinction
      reads it from the harness, not from this state. Measured on the 2026-11-02 archive,
      every one of these points came from a harness that declared no map at the time, and
      every moved path is inside the map `base_harness/loop` declares *today*;
    * `no_path_moved` -- no component and no moved path, i.e. the record says nothing
      moved this round;
    * `attributed` -- at least one component is named.

    `unattributed` is stripped out first and never returned as a component, even if a
    record wrongly put it inside `components`.
    """
    if FIELD not in point:
        return (), STATE_FIELD_ABSENT
    raw = point.get(FIELD)
    if not isinstance(raw, dict):
        return (), STATE_UNREADABLE
    listed = raw.get("components")
    if not isinstance(listed, list):
        return (), STATE_UNREADABLE
    names = sorted({
        name.strip() for name in listed
        if isinstance(name, str) and name.strip() and name.strip() != UNATTRIBUTED
    })
    if names:
        return tuple(names), STATE_ATTRIBUTED
    if listed:
        # A list with entries but no readable component name is a broken declaration,
        # not an edit outside a declared map.
        return (), STATE_UNREADABLE
    moved = raw.get("unattributed")
    if isinstance(moved, list) and any(_string(path) for path in moved):
        return (), STATE_NO_COMPONENT
    return (), STATE_NO_PATH


def _round_of(point: dict, position: int) -> int:
    """The point's round number, or its position when the record does not number it."""
    value = point.get("round")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return position


def _ordered_points(points: list[dict]) -> list[tuple[int, dict]]:
    """Points in round order, file order breaking a tie in round numbers.

    Gains are differences against the *previous round*, so the order has to be the
    record's own round numbering, not whatever order a resumed writer appended in.
    """
    indexed = list(enumerate(points))
    return sorted(indexed, key=lambda item: (_round_of(item[1], item[0]), item[0]))


def _observations(points: list[dict], run_id: str, method: str) -> tuple[list[dict], Counter]:
    """The run's attributed rounds, with their gain, and a count of every point state.

    A gain is `(this score - the previous point's score) * 100`. The previous point is the
    immediately preceding one in round order, attributed or not: a round's move is the
    move of that round, and an unattributed round between two attributed ones still
    happened. When the previous point has no score, the gain is unknown rather than
    "against the last round we could measure", which would silently skip a round.
    """
    observations: list[dict] = []
    states: Counter = Counter()
    previous_score: float | None = None
    previous_round: int | None = None
    for position, point in _ordered_points(points):
        score = _numeric(point.get("score"))
        components, state = _components_of(point)
        states[state] += 1
        round_no = _round_of(point, position)
        gain = (None if score is None or previous_score is None
                else (score - previous_score) * 100.0)
        if components:
            observations.append({
                "run_id": run_id,
                "method": method,
                "round": round_no,
                "components": list(components),
                "score": _round6(score),
                "previous_round": previous_round,
                "previous_score": _round6(previous_score),
                "gain_pp": _round6(gain),
            })
        previous_score = score
        previous_round = round_no
    return observations, states


def _entrypoint_method(entry: Any) -> str | None:
    """The method directory named by a `method_entrypoint` path."""
    entry = _string(entry)
    if not entry:
        return None
    parts = entry.replace("\\", "/").split("/")
    if "methods" in parts:
        index = parts.index("methods") + 1
        if index < len(parts) and parts[index]:
            return parts[index]
    return None


def _run_id_method(run_id: str) -> str | None:
    match = _APPWORLD_ID.match(run_id) or _RSI10_ID.match(run_id)
    return match.group(1) if match else None


def _resolve_method(run_id: str, meta: dict, points: list[dict]) -> tuple[str, str]:
    """`(method, where it came from)`, most direct record first.

    1. a curve point's `identity.method` -- the measurement names its own method;
    2. `run_meta.method_entrypoint` -- the run names its own method;
    3. the platform's narrow run-id templates -- last resort, for runs that predate
       `method_entrypoint`;
    4. `unknown`.

    The source travels into the JSON and the skipped table, so a reader can tell a
    measured label from a reconstructed one.
    """
    for point in points:
        identity = point.get("identity")
        if isinstance(identity, dict):
            method = _string(identity.get("method"))
            if method and method.lower() not in _NO_METHOD:
                return method, "identity"
    method = _entrypoint_method(meta.get("method_entrypoint"))
    if method:
        return method, "entrypoint"
    method = _run_id_method(run_id)
    if method:
        return method, "run_id"
    return UNKNOWN_METHOD, "none"


# ----------------------------------------------------------------------------- discovery

def _is_run_dir(path: Path) -> bool:
    return path.is_dir() and any((path / marker).is_file() for marker in RUN_MARKERS)


def _walk(directory: Path, *, depth: int = 0, discarded: bool = False):
    """Yield `(run_dir, discarded)` for every run at or below `directory`.

    A run directory stops the walk: its `state_after_round*`/`round0_traces` children are
    part of it, not runs of their own. A grouping directory is descended. A subtree named
    like the archive's discarded pile is tagged, not silently dropped -- the reader sees
    those runs in `No data` with the reason.
    """
    if _is_run_dir(directory):
        yield directory, discarded
        return
    if depth >= MAX_DISCOVERY_DEPTH:
        return
    try:
        children = sorted((child for child in directory.iterdir() if child.is_dir()),
                          key=lambda child: child.name)
    except OSError:
        return
    for child in children:
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        inside = discarded or child.name in DISCARDED_DIRS
        yield from _walk(child, depth=depth + 1, discarded=inside)


def _discover(roots: list[str]) -> list[tuple[Path, bool]]:
    """Every run directory under the roots, deduplicated by resolved path.

    Root order is preserved (it only matters for which copy of a duplicated run id wins),
    and discovery inside a root is name-sorted, so the same invocation sees the same
    order.
    """
    found: list[tuple[Path, bool]] = []
    seen: set[Path] = set()
    for raw in roots:
        root = Path(raw)
        if not root.is_dir():
            continue
        for run_dir, discarded in _walk(root):
            key = run_dir.resolve()
            if key in seen:
                continue
            seen.add(key)
            found.append((run_dir, discarded))
    return found


# -------------------------------------------------------------------------- study loading

def _skip(run_id: str, method: str, method_source: str, reason: str, detail: str, *,
          points: int | None = None, attributed_rounds: int | None = None) -> dict:
    """One run that contributes nothing, with the record's own reason for it."""
    return {
        "run_id": run_id,
        "method": method,
        "method_source": method_source,
        "reason": reason,
        "detail": detail,
        "points": points,
        "attributed_rounds": attributed_rounds,
    }


def _load_run(run_dir: Path, *, discarded: bool, min_rounds: int) -> tuple[str, dict]:
    """`("study", study)` or `("skipped", entry)` for one run directory."""
    meta, meta_problem = _read_json(run_dir / "run_meta.json")
    run_id = _string(meta.get("run_id")) or run_dir.name

    if discarded:
        method, source = _resolve_method(run_id, meta, [])
        # The curve is **counted, never read as evidence**: the denominator in the header
        # is "curve points in included studies", and a reader has to be able to see that it
        # is a subset. Before this, a discarded run's points vanished without a number
        # beside them, so "0 of 1008" could not be audited against the archive's own size.
        ignored, problem = _read_curve(run_dir / "curve.jsonl")
        return "skipped", _skip(
            run_id, method, source, "discarded",
            "under a directory the archive labels as discarded; not read as evidence",
            points=None if problem else len(ignored))

    curve_path = run_dir / "curve.jsonl"
    if not curve_path.is_file():
        method, source = _resolve_method(run_id, meta, [])
        detail = "no curve.jsonl, so no round was recorded"
        if meta_problem:
            detail = f"{detail}; {meta_problem}"
        return "skipped", _skip(run_id, method, source, "no_curve", detail)

    points, problem = _read_curve(curve_path)
    if problem:
        method, source = _resolve_method(run_id, meta, [])
        detail = problem["detail"]
        if meta_problem:
            detail = f"{detail}; {meta_problem}"
        known_empty = problem["reason"] == "empty_curve"
        return "skipped", _skip(run_id, method, source, problem["reason"], detail,
                                points=0 if known_empty else None,
                                attributed_rounds=0 if known_empty else None)

    run_id = (_string(meta.get("run_id"))
              or _string(points[0].get("run_id"))
              or run_dir.name)
    method, method_source = _resolve_method(run_id, meta, points)
    observations, states = _observations(points, run_id, method)
    attributed_rounds = len(observations)
    unattributed = {state: count for state, count in states.items()
                    if state != STATE_ATTRIBUTED}

    if min_rounds and attributed_rounds < min_rounds:
        return "skipped", _skip(
            run_id, method, method_source, "below_min_rounds",
            f"{attributed_rounds} attributed round(s) from {len(points)} curve point(s); "
            f"--min-rounds is {min_rounds}",
            points=len(points), attributed_rounds=attributed_rounds)

    return "study", {
        "run_id": run_id,
        "method": method,
        "method_source": method_source,
        "points": len(points),
        "attributed_rounds": attributed_rounds,
        "observations": observations,
        "unattributed": unattributed,
    }


# --------------------------------------------------------------------------- aggregation

def _aggregate(roots: list[str], min_rounds: int, discovered: int,
               duplicates: list[str], included: list[dict],
               skipped: list[dict]) -> dict:
    """The two answers the tool exists for, plus the split, plus what was left out."""
    total_points = sum(study["points"] for study in included)
    observations = [obs for study in included for obs in study["observations"]]
    attributed = len(observations)
    unattributed = total_points - attributed

    breakdown: Counter = Counter()
    for study in included:
        breakdown.update(study["unattributed"])
    # The unattributed states are the *only* points that are not attributed; a mismatch
    # would mean a point was counted twice or not at all.
    if sum(breakdown.values()) != unattributed:
        raise AssertionError(
            "attribution bookkeeping is inconsistent: "
            f"{sum(breakdown.values())} unattributed states for {unattributed} points")
    # **Two denominators, because one of them is a denominator of nothing.** A point with no
    # `editable_surface_components` was never a candidate for attribution -- the record does
    # not even say a path moved -- so "0 of 1008" invites the reading "attribution failed on
    # 1008 rounds" when for 698 of them nothing was attempted. `with_field` is the number a
    # ratio about attribution has to be over; `total` stays beside it so the reader can see
    # how much of the archive the field reaches.
    with_field = total_points - breakdown.get(STATE_FIELD_ABSENT, 0)
    # Curve points in runs the archive labels discarded are not evidence, but they are part
    # of the archive's size; reporting the count keeps the denominator auditable.
    discarded_points = sum(entry["points"] or 0 for entry in skipped
                           if entry["reason"] == "discarded")

    # -- question 1: which components do methods edit (attributed rounds) ---------------
    component_rounds: Counter = Counter()
    component_runs: defaultdict[str, set[str]] = defaultdict(set)
    component_methods: defaultdict[str, Counter] = defaultdict(Counter)
    by_method: dict[str, dict] = {}
    method_unattributed: Counter = Counter()
    method_unattributed_runs: defaultdict[str, set[str]] = defaultdict(set)

    for study in included:
        unattributed_here = sum(study["unattributed"].values())
        if unattributed_here:
            method_unattributed[study["method"]] += unattributed_here
            method_unattributed_runs[study["method"]].add(study["run_id"])
        if not study["observations"]:
            continue
        record = by_method.setdefault(study["method"], {
            "rounds": 0, "runs": set(), "components": Counter()})
        record["rounds"] += len(study["observations"])
        record["runs"].add(study["run_id"])
        for obs in study["observations"]:
            for component in obs["components"]:
                component_rounds[component] += 1
                component_runs[component].add(study["run_id"])
                component_methods[component][study["method"]] += 1
                record["components"][component] += 1

    by_component = [
        {
            "component": component,
            "rounds": component_rounds[component],
            "runs": len(component_runs[component]),
            "methods": {method: component_methods[component][method]
                        for method in sorted(component_methods[component],
                                             key=_method_order)},
        }
        for component in _component_order(component_rounds)
    ]
    by_method_rows = [
        {
            "method": method,
            "rounds": record["rounds"],
            "runs": len(record["runs"]),
            "unattributed_points": method_unattributed.get(method, 0),
            "components": {component: record["components"][component]
                           for component in _component_order(record["components"])},
        }
        for method, record in sorted(by_method.items(), key=lambda item: _method_order(item[0]))
    ]

    # -- question 2: a round's gain by component set, and by single component -----------
    set_records: dict[tuple[str, ...], dict] = {}
    single_records: dict[str, dict] = {}
    split_records = {"narrow": {"rounds": 0, "runs": set(), "gains": []},
                     "broad": {"rounds": 0, "runs": set(), "gains": []}}

    def add(record: dict, run_id: str, gain: float | None) -> None:
        record["rounds"] += 1
        record["runs"].add(run_id)
        if gain is not None:
            record["gains"].append(gain)

    for study in included:
        for obs in study["observations"]:
            components = tuple(obs["components"])
            gain = obs["gain_pp"]
            record = set_records.setdefault(
                components, {"rounds": 0, "runs": set(), "gains": []})
            add(record, study["run_id"], gain)
            for component in components:
                add(single_records.setdefault(
                    component, {"rounds": 0, "runs": set(), "gains": []}),
                    study["run_id"], gain)
            side = "narrow" if set(components) <= NARROW_COMPONENTS else "broad"
            add(split_records[side], study["run_id"], gain)

    gains_by_set = [
        {"components": list(key),
         **_stats(record["gains"], record["rounds"], len(record["runs"]))}
        for key, record in sorted(set_records.items(), key=lambda item: item[0])
    ]
    gains_by_single = []
    for component in _component_order(single_records):
        record = single_records[component]
        gains_by_single.append({
            "component": component,
            **_stats(record["gains"], record["rounds"], len(record["runs"])),
        })
    overall = _stats([obs["gain_pp"] for obs in observations if obs["gain_pp"] is not None],
                     attributed, len({obs["run_id"] for obs in observations}))
    split = {
        side: _stats(record["gains"], record["rounds"], len(record["runs"]))
        for side, record in split_records.items()
    }

    # -- the honest leftovers ------------------------------------------------------------
    unattributed_by_method = [
        {"method": method,
         "points": method_unattributed[method],
         "runs": len(method_unattributed_runs[method])}
        for method in sorted(method_unattributed, key=_method_order)
    ]

    return {
        "schema": SCHEMA,
        "generated_from": list(roots),
        "min_rounds": min_rounds,
        "definition": {
            "gain": "score - the previous round's score, times 100 (percentage points), "
                    "within one run; null when the round has no previous curve point or "
                    "either score is not a number",
            "n": "number of rounds with a computable gain (not runs, not curve points)",
            "attribution": "editable_surface_components.components names at least one "
                           "component; otherwise the point is unattributed",
            "with_field": "curve points that carry editable_surface_components, i.e. the "
                          "points on which an attribution was attempted. This is the "
                          "denominator of any attribution ratio; a point without the field "
                          "was never a candidate",
            "in_discarded_runs": "curve points in runs the archive labels discarded. Counted "
                                 "so the denominator is auditable, never read as evidence",
            "narrow_components": sorted(NARROW_COMPONENTS),
            "unattributed_states": list(UNATTRIBUTED_STATES),
        },
        "runs": {
            "discovered": discovered,
            "included": len(included),
            "skipped": len(skipped),
            "duplicate_run_ids": sorted(set(duplicates)),
        },
        "points": {
            "total": total_points,
            "attributed": attributed,
            "unattributed": unattributed,
            "with_field": with_field,
            "without_field": total_points - with_field,
            "in_discarded_runs": discarded_points,
            "breakdown": {state: breakdown.get(state, 0) for state in UNATTRIBUTED_STATES},
        },
        "edited": {
            "by_component": by_component,
            "by_method": by_method_rows,
        },
        "gains": {
            "overall": overall,
            "by_component_set": gains_by_set,
            "by_single_component": gains_by_single,
        },
        "split": split,
        "unattributed_by_method": unattributed_by_method,
        "observations": observations,
        "skipped": skipped,
    }


def build_report(roots: list[str], *, min_rounds: int = 0) -> dict:
    """Read every run under `roots` and return the reproducible payload."""
    discovered = _discover(roots)
    included: list[dict] = []
    skipped: list[dict] = []
    duplicates: list[str] = []
    seen_run_ids: set[str] = set()
    for run_dir, discarded in discovered:
        kind, entry = _load_run(run_dir, discarded=discarded, min_rounds=min_rounds)
        if kind == "skipped":
            skipped.append(entry)
            continue
        if entry["run_id"] in seen_run_ids:
            duplicates.append(entry["run_id"])
            continue
        seen_run_ids.add(entry["run_id"])
        included.append(entry)
    included.sort(key=lambda study: study["run_id"])
    skipped.sort(key=lambda entry: (entry["reason"], entry["run_id"]))
    return _aggregate(roots, min_rounds, len(discovered), duplicates, included, skipped)


# ------------------------------------------------------------------------------ markdown

def _dash(value: Any) -> str:
    return DASH if value is None else str(value)


def _pp(value: float | None) -> str:
    """A gain in percentage points, or the unknown marker -- never `0.0` for unknown."""
    return DASH if value is None else f"{value:+.2f}"


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


def _methods_cell(methods: dict[str, int]) -> str:
    return ", ".join(f"{method} {rounds}" for method, rounds in methods.items()) or DASH


def _components_cell(components: dict[str, int]) -> str:
    return ", ".join(f"{component} {rounds}" for component, rounds in components.items()) or DASH


def _set_label(components: list[str]) -> str:
    return " + ".join(components) if components else DASH


_GAIN_COLUMNS = ["rounds", "runs", "n", "unknown", "mean gain pp", "median gain pp"]


def _gain_cells(stats: dict) -> list[str]:
    return [str(stats["rounds"]), str(stats["runs"]), str(stats["n"]),
            str(stats["unknown"]), _pp(stats["mean_pp"]), _pp(stats["median_pp"])]


def render_markdown(payload: dict) -> str:
    """The human-readable report: the definition first, then the tables, then no data."""
    runs = payload["runs"]
    points = payload["points"]
    attributed = points["attributed"]
    min_rounds = payload["min_rounds"]
    roots = payload["generated_from"]

    lines: list[str] = [
        "# Component attribution across the run archive",
        "",
        "Roots: " + (", ".join(f"`{root}`" for root in roots) or "`(none)`"),
        "",
        f"Runs: {runs['discovered']} discovered, {runs['included']} included, "
        f"{runs['skipped']} skipped (all in [No data](#no-data)). "
        f"Curve points in included studies: {points['total']}, "
        f"attributed {attributed}, unattributed {points['unattributed']}.",
        "",
    ]
    if min_rounds:
        lines.append(
            f"`--min-rounds {min_rounds}`: a study (run) with fewer than {min_rounds} "
            "attributed round(s) is skipped and contributes nothing below.")
    else:
        lines.append("`--min-rounds 0`: no study was filtered; every readable run is included.")
    if runs["duplicate_run_ids"]:
        lines.append(
            f"{len(runs['duplicate_run_ids'])} run id(s) appeared under more than one path and "
            f"were read once: {', '.join(f'`{run_id}`' for run_id in runs['duplicate_run_ids'])}.")
    lines.extend([
        "",
        f"**The denominator that matters.** {points['with_field']} of those "
        f"{points['total']} curve point(s) carry `editable_surface_components`, so an "
        f"attribution was attempted on them. The other {points['without_field']} carry no "
        "field at all -- a record written before the field existed, or an H0 point, which no "
        "round produced -- so no component could have been named for them, whatever the "
        "harness declares.",
        "",
    ])
    if points["in_discarded_runs"]:
        lines.extend([
            f"{points['in_discarded_runs']} more curve point(s) sit in runs the archive labels "
            "discarded. They are **not** in the denominator above and are listed with their "
            "counts in [No data](#no-data).",
            "",
        ])

    if attributed == 0:
        lines.extend([
            f"**no attribution recorded**: 0 of {points['with_field']} curve point(s) that "
            f"carry the field name a component (0 of {points['total']} in total). Every "
            "attribution table below is empty. That is a property of the records, not a "
            "measurement that no component was edited -- the unattributed rounds are counted "
            "in [No data](#no-data).",
            "",
        ])
    else:
        lines.extend([
            f"{attributed} of {points['with_field']} curve point(s) that carry the field name "
            f"a component ({attributed} of {points['total']} in total). Every count below is a "
            "count of those attributed rounds; an unattributed round may have touched any "
            "component and is never counted as one.",
            "",
        ])

    lines.extend([
        "## How a gain is computed",
        "",
        "`gain_pp = (this round's score − the previous round's score) × 100`, in **percentage "
        "points**, computed **within one run only** -- never across runs and never against H0. "
        "The previous round is that run's immediately preceding curve point in round order "
        "(file order breaks a tie in round numbers). A round with no preceding curve point, or "
        "where either its own score or the preceding point's score is not a number, has **no "
        "gain** (`—`): it is excluded from every mean and median and counted in the group's "
        "`unknown` column. It is never a `0.0`.",
        "",
        "`n` is the number of **rounds** with a computable gain in a group -- **not** the number "
        "of runs and not the number of curve points. `rounds` is the number of attributed "
        "rounds in the group, so `rounds − n` is how many carry no gain; `runs` is how many "
        "studies contributed them. A mean over `n = 1` is one round's move, not a trend. Means "
        "and medians are in pp, two decimals here and six in the JSON.",
        "",
        "`—` means the record does not provide that value. It is never a zero.",
        "",
        "## What counts as attributed",
        "",
        "A curve point is **attributed** only when `editable_surface_components.components` "
        "names at least one component. A point whose field is absent, whose field names no "
        "component (`[]`), or whose field cannot be read is **unattributed**, and is never "
        "counted as a component. A point that carries no field was never a candidate, so it "
        "is not in the denominator of an attribution ratio -- `with_field` above is. "
        "[No data](#no-data) breaks the cases apart: \"no attribution was recorded\", \"a "
        "path moved and no component was named\" and \"nothing moved\" are different facts, "
        "and the middle one does **not** establish that the harness declared a map.",
        "",
        "Counts are attributed rounds, not all rounds. The vocabulary is the ten names in "
        "`harnessgrad.components.COMPONENTS`; a recorded name outside it is shown as recorded, "
        "never merged or dropped. A component with no row had no attributed round.",
        "",
        "## 1. Which components methods edit (attributed rounds)",
        "",
        "This is a count of edits, not of gains: a round that touched `control` and lost 3 pp "
        "still counts as a round that touched `control`. A round that names two components "
        "counts in both rows, so the `rounds` column does not sum to the attributed total.",
        "",
    ])
    component_rows = [
        [entry["component"], str(entry["rounds"]), str(entry["runs"]),
         _methods_cell(entry["methods"])]
        for entry in payload["edited"]["by_component"]
    ]
    lines.extend(_table(["component", "rounds", "runs", "methods (rounds)"], component_rows))
    if not component_rows:
        lines.append("")
        lines.append("_(empty -- no curve point names a component; see "
                     "[No data](#no-data) for what the points do record)_")
    lines.append("")

    lines.extend([
        "### Per method",
        "",
        "Only methods with at least one attributed round appear; a method whose rounds are all "
        "unattributed is in [No data](#no-data). `unattributed points` is how many of that "
        "method's points name no component, so it is never a component count.",
        "",
    ])
    method_rows = [
        [entry["method"], str(entry["rounds"]), str(entry["runs"]),
         _components_cell(entry["components"]), str(entry["unattributed_points"])]
        for entry in payload["edited"]["by_method"]
    ]
    lines.extend(_table(
        ["method", "rounds", "runs", "components (rounds)", "unattributed points"],
        method_rows))
    if not method_rows:
        lines.append("")
        lines.append("_(empty -- no attributed round)_")
    lines.append("")

    lines.extend([
        "## 2. A round's gain by component set",
        "",
        "One row per exact set of components an attributed round names. Groups are disjoint, so "
        "the `rounds` column sums to the attributed total.",
        "",
    ])
    set_rows = [
        [_set_label(entry["components"]), *_gain_cells(entry)]
        for entry in payload["gains"]["by_component_set"]
    ]
    lines.extend(_table(["component set", *_GAIN_COLUMNS], set_rows))
    if not set_rows:
        lines.append("")
        lines.append("_(empty -- no attributed round, so no set to group)_")
    lines.append("")

    lines.extend([
        "## 3. A round's gain by single component",
        "",
        "Components overlap: a round that names two components appears in both rows, so these "
        "rows do not sum. This is the question \"does an edit that includes `memory` move the "
        "score?\", which the disjoint set table cannot answer on its own.",
        "",
    ])
    single_rows = [
        [entry["component"], *_gain_cells(entry)]
        for entry in payload["gains"]["by_single_component"]
    ]
    lines.extend(_table(["component", *_GAIN_COLUMNS], single_rows))
    if not single_rows:
        lines.append("")
        lines.append("_(empty -- no attributed round, so no component to group)_")
    lines.append("")

    lines.extend([
        "## 4. Narrow (prompt/context) vs broad (anything else)",
        "",
        "`narrow` = every component the round names is prompt-like: `context`, or a "
        "`prompt`/`skill`/`skills` value when a record carries one (the ten-name vocabulary has "
        "no prompt or skill component, so today this is `context` alone). `broad` = at least one "
        "named component is not prompt-like, including an unknown name. The two classes "
        "partition attributed rounds, so `narrow + broad = all attributed`.",
        "",
    ])
    split = payload["split"]
    split_rows = [[side, *_gain_cells(split[side])] for side in ("narrow", "broad")]
    split_rows.append(["all attributed", *_gain_cells(payload["gains"]["overall"])])
    lines.extend(_table(["round", *_GAIN_COLUMNS], split_rows))
    lines.append("")

    lines.extend([
        "## No data",
        "",
        "### Skipped studies",
        "",
        "A study is one run. These runs contribute nothing to any table above.",
        "",
    ])
    skipped_rows = [
        [entry["run_id"], entry["method"], entry["reason"],
         _dash(entry["points"]), _dash(entry["attributed_rounds"]), entry["detail"]]
        for entry in payload["skipped"]
    ]
    lines.extend(_table(
        ["run", "method", "reason", "points", "attributed rounds", "why"], skipped_rows))
    if not skipped_rows:
        lines.append("")
        lines.append("(none)")
    lines.append("")

    lines.extend([
        "Skipped reasons: `discarded` -- under a directory the archive labels discarded, not "
        "read; `no_curve` -- no `curve.jsonl`; `empty_curve` -- `curve.jsonl` holds no point; "
        "`corrupt_curve` -- a line is not JSON, and the whole run is dropped because a dropped "
        "round would silently move every later round's previous-round gain; "
        "`below_min_rounds` -- fewer attributed rounds than `--min-rounds`.",
        "",
        "### Unattributed curve points",
        "",
        "These points are counted here and **never** as a component.",
        "",
    ])
    if points["unattributed"]:
        state_rows = [[state, str(points["breakdown"][state])]
                      for state in UNATTRIBUTED_STATES]
        lines.extend(_table(["state", "points"], state_rows))
        lines.extend([
            "",
            "`field_absent` -- the point carries no `editable_surface_components`, so no "
            "attribution was attempted: a record written before the field existed, or an H0 "
            "point, which no round produced; `declaration_unreadable` -- the field is there but "
            "no component list can be read from it; `no_component_named` -- no component is "
            "named and the point records at least one moved path. That is all the record says: "
            "it does **not** say whether the harness declared a map, because a harness with no "
            "map and a harness whose map misses the path record the same value; `no_path_moved` "
            "-- no component and no moved path, i.e. the record says nothing moved.",
            "",
            "By method:",
            "",
        ])
        by_method_rows = [
            [entry["method"], str(entry["points"]), str(entry["runs"])]
            for entry in payload["unattributed_by_method"]
        ]
        lines.extend(_table(["method", "points", "runs"], by_method_rows))
    else:
        lines.append("(none -- every curve point in the included studies is attributed)")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attribute harness evolution to components and describe each round's gain.")
    parser.add_argument("--runs", required=True,
                        help="comma-separated directories of run directories "
                             "(a single run directory is accepted too)")
    parser.add_argument("--json", dest="json_path", metavar="FILE",
                        help="write the reproducible JSON payload here")
    parser.add_argument("--md", dest="md_path", metavar="FILE",
                        help="write the markdown report here instead of printing it")
    parser.add_argument("--min-rounds", type=int, default=0, metavar="N",
                        help="skip studies (runs) with fewer than N attributed rounds; "
                             "0 keeps every run")
    args = parser.parse_args(argv)

    if args.min_rounds < 0:
        parser.error("--min-rounds must be >= 0")
    roots = [part.strip() for part in args.runs.split(",") if part.strip()]
    if not roots:
        parser.error("--runs needs at least one directory")
    missing = [root for root in roots if not Path(root).is_dir()]
    if missing:
        parser.error("not a directory: " + ", ".join(missing))

    payload = build_report(roots, min_rounds=args.min_rounds)
    markdown = render_markdown(payload)

    for run_id in payload["runs"]["duplicate_run_ids"]:
        print(f"warning: run {run_id} appears under more than one path; kept the first",
              file=sys.stderr)

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

    points = payload["points"]
    # The one line the task asks for: an archive with no attribution says so explicitly
    # instead of printing an empty table that could be read as a measured zero -- and it
    # says it over the denominator that attribution could have applied to.
    if points["attributed"] == 0:
        print(f"no attribution recorded: 0 of {points['with_field']} curve point(s) that "
              f"carry the field name a component ({points['total']} in total)",
              file=sys.stderr)
    print(f"{payload['runs']['included']} run(s) included, "
          f"{payload['runs']['skipped']} skipped; {points['attributed']} attributed round(s), "
          f"{points['unattributed']} unattributed ({points['with_field']} of "
          f"{points['total']} point(s) carry the field; "
          f"{points['in_discarded_runs']} in discarded runs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
