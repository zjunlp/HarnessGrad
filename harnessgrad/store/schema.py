"""What a run's record **is** -- declared once, so a reader can check it.

## Why this module exists

A curve point was a dict assembled at three call sites, and it had grown to **38 possible
fields of which 3 were present on every point** (measured 2026-10-05, `runs/ds-dgm`). No
file said which fields a point must have, so a field missing because the code path did not
set it, missing because the point predates the feature, and present-but-`null` were three
different facts with one spelling: nothing.

That is not a style problem. It is how a non-functional candidate came to be read as a
`+0.100` gain (`docs/experiment_noise.md`): the point that "gained" 0.100 carried no
`no_loadable_change` field, exactly like a point recorded before that field existed, and
nothing on disk could tell a reader which one they had.

And a run directory said nothing about **which platform produced it**. Measured the same
day: 82 run directories, none carrying the content hash of the code that decided what its
numbers meant. One experiment was invalidated mid-flight by an edit to `eval/` and is
indistinguishable on disk from the valid ones.

## The two levels

* `check_point` -- is this dict a curve point? Field presence, types, and the handful of
  invariants a point cannot violate and still be a measurement.
* `certify` -- is this *run directory* a measurement? It reads `curve.jsonl` and
  `run_meta.json` and answers the question a reader actually asks before quoting a number.

A field added at a known time is listed in `ADDED_LATER` with the date. Its absence is a
**note**, never a problem: an old point is not a broken point, and a validator that fails
every run recorded before today is a validator nobody will use.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Field:
    """One declared field: what it is called, and why it has to be there."""

    name: str
    why: str
    #: `False` for a field that is genuinely optional (present only when it applies).
    #: `**` fields in `_curve_point` are the optional ones; the required ones are the
    #: facts without which the point cannot be read at all.
    required: bool = True
    #: Accepted python types. `None` is always accepted for a required field that may be
    #: explicitly unknown -- the platform distinguishes "absent" from "null" on purpose.
    kinds: tuple[type, ...] = (object,)


#: A curve point. The required set is deliberately small: the facts without which the
#: number has no meaning. Everything else is a report, and adding a report must never
#: require touching this list -- only a field a reader cannot do without belongs here.
CURVE_POINT: tuple[Field, ...] = (
    Field("run_id", "which run this point belongs to; a point is exported on its own",
          kinds=(str,)),
    Field("round", "which state of the run this is; round 0 is the unmodified harness",
          kinds=(int,)),
    Field("identity",
          "what ran: the harness content hash, both models, and the improver. Without it "
          "the point cannot be compared with any other point", kinds=(dict,)),
    Field("score", "the platform's verdict on the artifact, and the headline number",
          kinds=(int, float)),
    Field("score_kind", "`training` or `exam` -- a training score is inflated by "
                        "construction and must never be quoted as a result",
          kinds=(str,)),
    Field("side", "which half of the split this point measured", kinds=(str,)),
    Field("per_task", "the score of every task that was scored", kinds=(dict,)),
    Field("n_scored", "how many tasks were scored; `score` is the mean over these",
          kinds=(int,)),
    Field("env", "the environment the measurement was taken in: kind, image digest, "
                 "placement. Two points from different environments are different "
                 "quantities", kinds=(dict,)),
    Field("sampling", "which tasks were selected and on what basis", kinds=(dict,)),
    Field("cost", "what it cost; a method's acceptance rule may be cost-aware, and a "
                  "rule fed a constant zero is not a weak rule but no rule",
          kinds=(dict,)),
    Field("timestamp_utc", "when; a run is read months later", kinds=(str,)),
    Field("measured_by_platform",
          "the platform scored this, not the method. `False` or absent means the number "
          "is a claim rather than a measurement", kinds=(bool,)),
    # Optional: present only when it applies, and its absence is meaningful.
    Field("method_reported", "what the method claimed; kept beside what was measured "
                             "because the gap between them is itself a finding",
          required=False, kinds=(dict,)),
    Field("edit_kind", "the method's own word for what kind of change this was; recorded, "
                       "never interpreted", required=False),
    Field("harness_failed", "per task, why the harness itself failed, when it did",
          required=False, kinds=(dict,)),
    Field("invalid", "per task, why the task could not be measured", required=False,
          kinds=(dict,)),
)

#: Fields added at a known time. Absence is a note, not a problem: the point predates the
#: field. This is the list that makes an old record readable rather than suspect.
ADDED_LATER: dict[str, str] = {
    "scorer_isolation_class": "2026-10-15: executed scorer class; missing means unknown",
    "scorer_isolation_comparable": "2026-10-15: false for unknown topology; true only "
                                   "establishes the isolation-class prerequisite",
    "per_task_scorer_isolation": "2026-10-15: class next to each task's score",
    "test_rate": "2026-10-05 (stage 0): the check's own test tally, as a number",
    "test_rate_n_tasks": "2026-10-05 (stage 0): how many tasks reported a tally",
    "per_task_test_rate": "2026-10-05 (stage 0)",
    "tests_passed": "2026-10-05 (stage 0)",
    "tests_total": "2026-10-05 (stage 0)",
    "no_loadable_change": "2026-10-05: every changed file is outside the entrypoint's "
                          "import closure, so the score is not evidence about the edit",
    "startup_failure": "2026-10-05: the harness never started on any task",
    "per_task_trials": "2026-10-04 (`--trials > 1`)",
    "score_std": "2026-10-04 (`--trials > 1`)",
    "n_trials": "2026-10-04 (`--trials > 1`)",
    "kept_round": "2026-10-05: which state the method decided to keep",
    "step_index": "2026-10-05: which step of its submission this point is",
    "steps_in_submission": "2026-10-05: how many states one call bought",
}

#: Fields whose *presence* is a fact and whose absence would be a problem, but which no
#: point before stage 1 could carry because the platform did not record them.
RUN_META: tuple[Field, ...] = (
    Field("run_id", "which run this record is for", kinds=(str,)),
    Field("platform",
          "the content hash of the code that decided what these numbers mean. Without it "
          "a curve cannot be compared with another curve, and an invalidated run cannot "
          "be told from a valid one", kinds=(dict,)),
    Field("agent_model", "the model that solved the tasks", kinds=(str,)),
    Field("agent_backend", "how it was reached (`openai`, `codex`, `mock`)", kinds=(str,)),
    Field("harness", "the base harness this run started from", kinds=(str,)),
    Field("dataset", "the task set", kinds=(str,)),
    # Optional: a control run (`--improver none`) measures one state and never enters the
    # method loop, so nothing writes this. The number of points in `curve.jsonl` is the
    # authority anyway; this field is a convenience.
    Field("rounds_measured", "how many states were measured", required=False, kinds=(int,)),
    Field("workspace",
          "where the harness's git history lives; the checkpoints are in it, so a reader "
          "that guesses the path loses them", kinds=(str,)),
)

#: `run_meta` fields added at a known time, same rule as `ADDED_LATER`.
RUN_META_ADDED_LATER: dict[str, str] = {
    "platform": "2026-10-05 (stage 1)",
    "improver": "2026-10-05 (stage 1): the improver the run resolved, not the one a "
                "reader assumes from the method's name",
    "platform_content_sha_started": "2026-10-20: the tree the curve *began* on. Every later "
                                    "write moves `platform_content_sha` to the tree that is "
                                    "running now, so a resumed run would otherwise read as one "
                                    "platform; `platform-seams/` in the run directory carries "
                                    "the review that allowed the crossing",
}


def _has(point: dict, field: Field) -> bool:
    if field.name not in point:
        return False
    if point[field.name] is None:
        # A required field may be explicitly null when the platform looked and found
        # nothing -- `test_rate: null` is a fact. But a required *identity* field cannot.
        return field.name not in ("identity", "score", "run_id", "round")
    return True


def check_point(point: dict, *, index: int = 0) -> tuple[list[str], list[str]]:
    """`(problems, notes)` for one curve point.

    A problem means the point cannot be read as a measurement. A note means the point is
    missing something a later platform recorded -- old, not broken.
    """
    problems: list[str] = []
    notes: list[str] = []
    where = f"point {index}"
    if not isinstance(point, dict):
        return [f"{where}: not an object"], []
    for field in CURVE_POINT:
        if not _has(point, field):
            if field.required:
                problems.append(f"{where}: no `{field.name}` -- {field.why}")
            continue
        value = point[field.name]
        if value is None or field.kinds == (object,):
            continue
        if not isinstance(value, field.kinds):
            want = "/".join(k.__name__ for k in field.kinds)
            problems.append(
                f"{where}: `{field.name}` is {type(value).__name__}, expected {want}")
    for name, when in ADDED_LATER.items():
        if name not in point:
            notes.append(f"{where}: no `{name}` (added {when})")
    ident = point.get("identity")
    if isinstance(ident, dict) and not ident.get("harness_sha"):
        problems.append(f"{where}: `identity` names no `harness_sha` -- which harness "
                        f"this score belongs to is not recorded")
    if point.get("measured_by_platform") is not True:
        problems.append(
            f"{where}: `measured_by_platform` is not true -- this number is a claim by "
            f"the method, not a measurement by the platform")
    return problems, notes


def check_run_meta(meta: dict) -> tuple[list[str], list[str]]:
    """`(problems, notes)` for a `run_meta.json`."""
    problems: list[str] = []
    notes: list[str] = []
    if not isinstance(meta, dict):
        return ["run_meta: not an object"], []
    for field in RUN_META:
        if field.name not in meta or meta[field.name] is None:
            if field.required:
                problems.append(f"run_meta: no `{field.name}` -- {field.why}")
            else:
                notes.append(f"run_meta: no `{field.name}` (optional)")
    for name, when in RUN_META_ADDED_LATER.items():
        if name not in meta:
            notes.append(f"run_meta: no `{name}` (added {when})")
    return problems, notes


def read_curve(run_dir: Path) -> tuple[list[dict], list[str]]:
    """Every curve point, plus a problem for each line that is not one."""
    path = Path(run_dir) / "curve.jsonl"
    if not path.is_file():
        return [], [f"no {path.name}: nothing was measured"]
    points: list[dict] = []
    problems: list[str] = []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            points.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problems.append(f"curve.jsonl line {n} is not JSON: {exc}")
    if not points:
        problems.append("curve.jsonl holds no points")
    return points, problems


def certify(run_dir: Path) -> dict:
    """Is this run directory a measurement? The question a reader asks before quoting.

    `valid` -- every point can be read, and every one was measured by the platform.
    `certified` -- additionally, the record names the platform that produced it and the
    run did not touch the platform while it ran. A run that predates those fields is
    `valid` and **not** `certified`, which is the honest reading: nothing on disk lets a
    reader compare it with a run made by different code.
    """
    run_dir = Path(run_dir)
    points, problems = read_curve(run_dir)
    notes: list[str] = []
    for i, point in enumerate(points):
        p, n = check_point(point, index=i)
        problems.extend(p)
        notes.extend(n)

    meta_path = run_dir / "run_meta.json"
    meta: dict = {}
    if not meta_path.is_file():
        problems.append("no run_meta.json: the run's own account of itself is missing")
    else:
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"run_meta.json is not JSON: {exc}")
        else:
            p, n = check_run_meta(meta)
            notes.extend(n)
            # `platform` and the newer fields are certification requirements, not
            # validity ones -- see the docstring. Kept out of `problems` on purpose.
            notes.extend(x for x in p if "platform" in x or "improver" in x)
            problems.extend(x for x in p if "platform" not in x and "improver" not in x)

    tampered: list[str] = []
    for point in points:
        t = point.get("tampered")
        if isinstance(t, dict):
            tampered.extend(f"{k}: {', '.join(v)}" for k, v in t.items() if v)
        for step in ("startup_failure",):
            if point.get(step):
                problems.append(f"round {point.get('round')}: {step} -- "
                                f"{str(point[step])[:160]}")

    platform = meta.get("platform") if isinstance(meta.get("platform"), dict) else {}
    certified = bool(points) and not problems and not tampered and bool(platform.get("content_sha"))
    if not certified and not problems and not tampered:
        notes.append("not certified: the record does not name the platform that produced "
                     "it (`run_meta.platform.content_sha`), so its numbers cannot be "
                     "compared with a run made by different code")

    return {
        "run_id": meta.get("run_id") or run_dir.name,
        "points": len(points),
        "rounds": sorted({p.get("round") for p in points if isinstance(p.get("round"), int)}),
        "valid": bool(points) and not problems,
        "certified": certified,
        "problems": problems,
        "notes": notes,
        "tampered": tampered,
        "platform_content_sha": platform.get("content_sha"),
        "final_score": points[-1].get("score") if points else None,
    }


def _iter_field_names(fields: Iterable[Field]) -> list[str]:
    return [f.name for f in fields]
