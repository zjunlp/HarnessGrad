"""Two scoring policies the platform records, and why they are recorded rather than enforced.

Both come from Anthropic's automated-alignment-researcher work
(`alignment.anthropic.com/2026/automated-alignment-researchers/`), which states them as
evaluator-side rules:

* **The score is a geometric mean.** *"the overall score is the geometric mean of the scored
  benchmarks, so the lowest one binds and all must be lifted"*. An arithmetic mean lets a
  candidate buy a large gain on one task with a small loss on another; the geometric mean
  refuses that trade, which is the property a task set is *for*.
* **A capability floor can disqualify a candidate whatever its score.** *"a pass/fail check
  that disqualifies a method, whatever its score, if the trained model's 95% confidence
  interval on any capability benchmark falls entirely below the base model's"*.

**Neither is enforced here, and that is the design.** This platform has no acceptance rule
(`docs/framework_design.md` §1: the platform records the rule, it does not own it), and a
method cannot be told "do not keep what you already handed over". So both are computed and
written on the curve point, next to the mean:

    "score_geomean": 0.0,                     # the lowest task binds
    "capability_floor": {
        "baseline": "H0",                     # the point everything is compared against
        "violations": [{"task_id": …, "candidate": 0.0, "baseline": 1.0, "gap": -1.0,
                        "ci_entirely_below": true}],
        "checked": 4, "z": 1.96,
    }

A method whose acceptance rule wants Anthropic's gate has everything it needs on the point;
a method that ignores it is not silently overruled. The same split as the noise band: the
number is the platform's, the decision is the method's.

**The measured caveat about the geometric mean.** On a task set whose per-task scores are
binary, a geometric mean is 0 whenever any task fails -- which is every interesting round of
a harness-evolution run. That is not a defect of the formula, it is what "the lowest one
binds" means at the extreme; it becomes informative when task scores are continuous (a
`reward_file` check, or a set large enough that the score is a rate). It is recorded rather
than adopted as *the* score for that reason, and because changing `score` would make every
curve recorded before today incomparable with every curve after it (`INTERFACE.md` §3 is a
frozen contract).

Pure on purpose: dicts in, dicts out. No docker, no network, no filesystem.
"""
from __future__ import annotations

import math

#: The floor a zero is clamped to before the log, so one failed task gives a very small
#: positive number rather than an absolute zero. The number is deliberately tiny: it keeps
#: "the lowest binds" while leaving the result usable as a *number* (a zero cannot be
#: compared with another zero).
ZERO_FLOOR = 1e-6


def geomean(scores: list[float], *, floor: float = ZERO_FLOOR) -> float | None:
    """The geometric mean of per-task scores, with zeros clamped to `floor`.

    `None` for an empty list -- no tasks measured is not a score of zero, the same
    distinction `invalid` draws everywhere else (`INTERFACE.md` §2.5.7).
    """
    values = [float(s) for s in scores if isinstance(s, (int, float))]
    if not values:
        return None
    if any(v < 0 for v in values):
        # A negative per-task score is not something this platform produces, and a log of it
        # is a complex number that would raise deep inside `math`. Refused loudly.
        raise ValueError(f"a per-task score is negative, which the geometric mean cannot "
                         f"take: {min(values)}")
    logs = [math.log(max(v, floor)) for v in values]
    return math.exp(sum(logs) / len(logs))


def studied_per_task(point: dict) -> dict:
    """Per-task scores on the side a method was allowed to study.

    **The platform's copy of a rule that also exists in `methods/protocol.py`.** A method
    runs inside a sandbox that hides the platform, so it cannot import this; the platform
    cannot import a method either. Two copies of one rule is a cost, and it is the same cost
    `methods/apply.py` and `eval/run/candidate.py` already pay for the same reason -- with the
    same mitigation: a test asserts the two agree (`tests/quality/test_scoring_policies.py`).

    `score_kind` decides, and never a fallback that reaches the exam: `training` means the
    method read exactly these tasks' traces, so `per_task` *is* the diagnostic side; anything
    else (`exam`, or a record predating the field) means `train_score`'s side if it is there,
    and otherwise nothing when a split exists.
    """
    if point.get("score_kind") == "training":
        return dict(point.get("per_task") or {})
    if point.get("train_per_task"):
        return dict(point["train_per_task"])
    if not point.get("split"):
        return dict(point.get("per_task") or {})
    return {}


def floor_violations(candidate: dict, baseline: dict, *,
                     candidate_std: dict | None = None,
                     baseline_std: dict | None = None,
                     z: float = 1.96, tolerance: float = 0.0) -> dict:
    """Tasks where a candidate sits below the baseline, and whether it is below *significantly*.

    Anthropic's criterion is the strict one: the candidate is disqualified when its 95%
    confidence interval on a benchmark lies **entirely below** the base model's. That needs a
    spread, which this platform has only when a run used `--trials > 1`; with one sample the
    honest answer is a point comparison plus `ci_entirely_below: null` -- "we could not ask
    that question" rather than a `false` that reads like a passing grade.

    `tolerance` is subtracted from the baseline before comparing, so a caller can say "a
    tenth of a point below is not a violation" without editing the numbers. It is `0.0` by
    default because the policy this ports has no tolerance.
    """
    out = {"violations": [], "checked": 0, "z": z, "tolerance": tolerance,
           "ci_available": bool(candidate_std and baseline_std)}
    for task, base in sorted(baseline.items()):
        if task not in candidate:
            continue
        out["checked"] += 1
        got = float(candidate[task])
        base_value = float(base)
        gap = got - base_value
        if gap >= -tolerance:
            continue
        entry = {"task_id": task, "candidate": round(got, 4),
                 "baseline": round(base_value, 4), "gap": round(gap, 4)}
        # `z * se` with `se` from the *candidate's* spread across trials: the question is
        # whether this candidate is below a known baseline, not whether two noisy estimates
        # differ.
        std = (candidate_std or {}).get(task)
        if std is not None and z:
            upper = got + z * float(std)
            entry["candidate_ci_upper"] = round(upper, 4)
            entry["ci_entirely_below"] = bool(upper < base_value - tolerance)
        else:
            entry["ci_entirely_below"] = None
        out["violations"].append(entry)
    out["count"] = len(out["violations"])
    out["significant"] = sum(1 for v in out["violations"] if v["ci_entirely_below"] is True)
    return out


def improvement(candidate: dict, baseline: dict, *, alpha: float = 0.05) -> dict:
    """Whether a candidate is *better* than the baseline, paired task by task.

    **The mirror `floor_violations` does not have.** That function looks only down: it records
    the tasks where a candidate sits below the baseline, because Anthropic's criterion is a
    disqualification. Nothing in this platform asked the other question -- "did this edit buy
    more tasks than it cost" -- and the marginal interval cannot answer it either: `score_ci95`
    is computed over *tasks*, so it does not shrink when a run repeats them, and on 134 tasks
    at a mean near 0.5 it is about +/-0.09 wide, which is larger than the effect a harness edit
    produces. The operator's own archived runs show the size of that band: `refix-alfworld-train`
    scored 0.4146 with `score_ci95 [0.307, 0.522]` and `refix-alfworld-eval` scored 0.5577 with
    `[0.421, 0.694]` -- the same harness and model on two halves of one pool, 0.143 apart, with
    overlapping intervals.

    Paired data is available and unused: both points carry `per_task`, so the same task can be
    compared with itself before and after. The statistic is a **sign test over the discordant
    pairs** -- tasks the edit moved one way against tasks it moved the other -- which for binary
    per-task scores is exactly McNemar's exact test. It deliberately ignores magnitude, and
    that is the point: one task flipped 0 -> 1 and one task moved 0 -> 0.3 are one discordant
    pair each, which is what makes it usable when most tasks are binary. Magnitude is reported
    separately as `net_rate`, so a reader never has to choose between the two readings.

    `significant` means "the discordance is unlikely if the edit changed nothing". It is **not**
    the same as an improvement: read `significant and direction == "better"`. A significant
    regression is possible and is already covered by `floor_violations`.

    Recorded, not enforced, like everything else in this module: a method that wants this gate
    reads it off the point, and a method that ignores it is not overruled.
    """
    shared = sorted(set(candidate) & set(baseline))
    gained, lost = [], []
    for task in shared:
        got, base = float(candidate[task]), float(baseline[task])
        if got > base:
            gained.append(task)
        elif got < base:
            lost.append(task)
    n = len(gained) + len(lost)
    if n == 0:
        # "We could not ask that question" rather than a `false` that reads like a pass --
        # the same honesty `floor_violations` keeps with `ci_entirely_below: None`.
        p_value = 1.0
    else:
        k = min(len(gained), len(lost))
        tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
        p_value = min(1.0, 2.0 * tail)
    net = len(gained) - len(lost)
    direction = "better" if net > 0 else ("worse" if net < 0 else "unchanged")
    return {
        "checked": len(shared),
        "gained": len(gained),
        "lost": len(lost),
        "net": net,
        # The change in the mean, which the sign test deliberately does not use.
        "net_rate": round(
            (sum(float(candidate[t]) for t in shared)
             - sum(float(baseline[t]) for t in shared)) / len(shared), 6) if shared else None,
        "alpha": alpha,
        "p_value": round(p_value, 6),
        "significant": bool(p_value < alpha),
        "direction": direction,
        # Ids only: the per-task values are already in `per_task` on both points, and a task
        # set can be 8,792 long.
        "gained_ids": gained,
        "lost_ids": lost,
        "test": ("sign test over discordant pairs; McNemar's exact test when the per-task "
                 "scores are binary"),
    }


#: Identity fields that must agree between a candidate and the reference it is compared
#: against. **`harness_sha` and `harness_identity.config` are deliberately absent**: they are
#: what the method edits, so requiring them to match would refuse every real experiment. The
#: fields here are the ones that must be held fixed for the comparison to be about the edit --
#: a different agent model, backend or harness family makes the two numbers answers to
#: different questions.
#: The *measurement condition*, kept apart from the identity because "absent" and
#: "different" are not the same answer here. Every frozen reference on disk predates these
#: fields, and a missing value must not read as a mismatch or the check would fire on every
#: comparison forever and teach readers to skip the list. They are still checked: a run at
#: `jobs=6` compared against a reference measured at `jobs=40` is a comparison the reader has
#: to know about, because contention is the one axis that is invisible in the score.
REFERENCE_MEASUREMENT = ("jobs", "trials")

REFERENCE_FIXED = ("agent_model", "agent_backend", "harness_name", "framework_version",
                   *REFERENCE_MEASUREMENT)


def reference_problems(reference: dict, current: dict, *,
                       reference_tasks=None, current_tasks=None) -> list[str]:
    """Why a reference point may not be comparable with the point being measured against it.

    A frozen reference is amortized over every future method, which is what makes cross-method
    comparison honest -- every method sits on one axis. The price is that nothing re-measures
    it, so it cannot notice that *it* has stopped describing the run in front of it. This is
    that check, and it is mechanical rather than advisory: the fields are already on both
    points.

    **The task set is the one that bites silently.** `improvement` pairs task by task over
    `set(candidate) & set(baseline)`, so a reference whose task ids differ does not fail -- it
    quietly shrinks the comparison and reports a p-value about the survivors. Measured: a run
    with `--split default` scored 81 tasks under `split_sha 919f17c8` while the archived
    reference scored 82 under `249d6aac`; one task moved sides and nothing would have said so.

    Returns named problems, empty when the reference holds.
    """
    ref_id = dict((reference or {}).get("identity") or {})
    cur_id = dict((current or {}).get("identity") or {})
    problems = []
    for field in REFERENCE_FIXED:
        was, now = ref_id.get(field), cur_id.get(field)
        if was == now:
            continue
        if field in REFERENCE_MEASUREMENT and was is None:
            problems.append(
                f"{field} is not recorded on the reference (it was measured "
                f"{now!r} here), so the two numbers' contention is not known to agree")
            continue
        problems.append(f"{field} changed: reference {was!r}, this run {now!r}")
    # **Defaulted from the points themselves.** The task set is the check that bites
    # silently, so leaving it to a caller to pass means the one check that must not be
    # forgotten is the one that is. Explicit arguments still win, because a point's studied
    # side is not always its `per_task` (`staged_per_task` exists for that).
    if reference_tasks is None:
        reference_tasks = (reference or {}).get("per_task") or {}
    if current_tasks is None:
        current_tasks = (current or {}).get("per_task") or {}
    if reference_tasks and current_tasks:
        ref, cur = set(reference_tasks), set(current_tasks)
        shared = ref & cur
        if ref != cur:
            problems.append(
                f"task set changed: {len(ref)} in the reference, {len(cur)} here, "
                f"{len(shared)} shared; {len(ref - cur)} dropped and {len(cur - ref)} new. "
                f"The paired test only sees the shared tasks")
        if not shared:
            problems.append("the reference and this run share no task at all")
    return problems
