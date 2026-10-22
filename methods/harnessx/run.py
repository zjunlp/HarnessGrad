#!/usr/bin/env python3
"""HarnessX's historical-best acceptance gate, as a HarnessGrad method.

Source root: /mnt/20t/xubuqiang/Study/methods_src/harnessx/.
The GAIA kernel in recipe/gaia_evolver/run.py:1174-1257 compares a measured
candidate with the historical best, adjusts for relative cost increases, keeps
the earliest best on ties, and restores best_cfg on regression (:813-820).
The port replays that kernel over platform history and, on rollback, copies the
complete staged best tree and declares incumbent_round via editor.report_many
(INTERFACE.md §4.1.1). No editor/model call is made on the rollback path; the
graded round's pre-registration is retained independently of the restored tree.

HG_HARNESSX_REGRESSION_TOLERANCE defaults to upstream's 0.03
(recipe/gaia_evolver/run.py:463-475), which absorbs typical eval stochastic noise
(~2-3 pp) so a useful code fix isn't discarded because an unrelated task flipped.
The historical-best baseline still only advances on strict improvements.
The passed-count noise guard defaults to upstream's 3 (:1184, :1222-1236).
Explicit tolerance 0.0 and count threshold 0 select a strict score-only gate.
HARNESSX_TOLERANCE remains a legacy fallback when the HG variable is absent.

The gate acts on the last measured round, before proposing another edit. It
excludes unmeasured or nonfinite scores rather than treating them as zero.
It cannot grade a newly proposed candidate before the platform measures it; if no
further method call is made at budget exhaustion, that last proposal is ungated.
A missing best snapshot still yields an incumbent_round selection record, but
cannot physically restore that tree. The method stops without another edit in
that case. An available restore continues even when the tree already equals the
base, so a no-op restore does not prematurely end the search.

The upstream shipping validator is a separate, partially unported mechanism:
harnessx/meta_harness/validate_workflow.py:857-869, :909-933 requires YAML
HarnessConfig canonicalization/structural diffs, processor hook contracts,
synthetic-task replay in the HarnessX runtime, and journal/memo evidence and
novelty checks. This method receives generic harness source trees and platform
measurements, not that engine's configuration/runtime or journal artifacts.
Selecting incumbent_round expresses version selection; it does not execute or
claim to pass those preflight checks.

Edits remain delegated to editor.ask. The pre-registration fields and four-lever
vocabulary follow harnessx/meta_harness/workspace/skills/journal/SKILL.md:40-51
and harnessx/meta_harness/journal.py:57. Cost is platform harness_tokens (falling
back to method_generation_tokens), not USD; its weight defaults to upstream's
0.0. Passed counts use n_passed, with score * task count as a legacy binary-score
fallback. These are platform adaptations, not a reproduction of upstream scores.

Environment variables:
    HG_HARNESSX_REGRESSION_TOLERANCE    0.03 (legacy: HARNESSX_TOLERANCE)
    HARNESSX_COST_WEIGHT               0.0
    HARNESSX_PASS_COUNT_NOISE_THRESHOLD 3 (0 disables the count guard)
    HARNESSX_COST_FIELD                harness_tokens
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor                                              # noqa: E402


#: The four levers HarnessX allows (`harnessx/meta_harness/journal.py:57`,
#: `workspace/skills/analyze/SKILL.md:119-124`). Recorded as declared, never used to
#: reject an edit: the platform records `edit_kind`, it does not police it.
LEVERS = ("configuration", "control", "action", "instruction")

#: What the record says the method's acceptance rule is. Kept next to the code that
#: implements it, because `editor.report` copies it into every trajectory.
ACCEPTANCE_RULE = (
    "HarnessX best-so-far gate (recipe/gaia_evolver/run.py:1210-1257): "
    "score = pass_rate - cost_weight*max(cost_delta_ratio,0); revert iff "
    "score < best_rate - tolerance (HG_HARNESSX_REGRESSION_TOLERANCE defaults to 0.03) "
    "and the passed-count delta meets the noise threshold (default 3). A "
    "noise-level breach is accepted without updating best. A rollback hands back "
    "the historical best tree and declares incumbent_round; missing snapshots "
    "record selection only and stop. Upstream shipping preflight is not executed."
)


# ------------------------------------------------------------- tunables ---

def _tunables() -> dict:
    """The gate's knobs, with the upstream defaults (run.py:463-498, :1184).

    Read per call, not at import time, so a test (or an operator) can change one
    without reloading the module. A malformed value falls back to the default rather
    than crashing the round: a method that dies on a typo'd env var reports nothing.
    """
    def _float(name: str, default: str) -> float:
        try:
            value = float(os.environ.get(name, default))
            return value if math.isfinite(value) else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _int(name: str, default: str) -> int:
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return int(default)

    # recipe/gaia_evolver/run.py:463-475 defaults to 0.03 to absorb typical
    # eval noise (~2-3 pp) without discarding a useful fix over a task flip.
    # The HG variable wins over the previous port's legacy variable.
    tolerance_var = ("HG_HARNESSX_REGRESSION_TOLERANCE"
                     if "HG_HARNESSX_REGRESSION_TOLERANCE" in os.environ
                     else "HARNESSX_TOLERANCE")
    return {
        "tolerance": max(0.0, _float(tolerance_var, "0.03")),
        "cost_weight": _float("HARNESSX_COST_WEIGHT", "0.0"),
        # Upstream run.py:1184, :1222-1236 exempts passed-count changes below 3.
        "pass_count_noise_threshold": _int("HARNESSX_PASS_COUNT_NOISE_THRESHOLD", "3"),
        "cost_field": os.environ.get("HARNESSX_COST_FIELD", "harness_tokens"),
    }


# ------------------------------------------------------- the gate kernel ---

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _passed(point: dict) -> int | None:
    """HarnessX's `round_passed`: how many of the scored tasks the round passed.

    HarnessX counts tasks whose binary pass is `1.0`. The platform withholds the
    eval side's per-task breakdown from the method channel -- a method that can see
    which exam questions fail can iterate against those specific tasks -- so the
    count is reconstructed from the aggregate instead: `score * n`, where `n` is how
    many tasks the score averages over. On the 0.0/1.0 scores this platform produces
    that is exact, and it is exact for the same reason HarnessX's own pass is binary.

    Returns `None` when the point does not say how many tasks were scored, or when
    the platform produced no score at all, so the caller can tell "zero passes" from
    "no count available". Those are different states and the noise guard must not
    read the second as the first.
    """
    if not point.get("measured_by_platform", True):
        return None
    # `n_passed` is the platform's own count and is exact for any scoring scheme.
    exact = point.get("n_passed")
    if isinstance(exact, int):
        return exact
    # Fallback for a point written before the field existed. Reconstructing the
    # count from the mean is exact only for 0/1 scores; a fractionally-graded
    # dataset would round, which is why the field exists rather than the fallback.
    task_ids = (point.get("sampling") or {}).get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        return None
    score = _num(point.get("score"))
    if score is None:
        return None
    return int(round(score * len(task_ids)))


def _round_cost(point: dict, cost_field: str) -> float:
    """The round's cost in the field the platform actually records.

    HarnessX reads USD. A HarnessGrad point records no USD, so the closest analogue
    is `cost.harness_tokens` -- the *harness's* own per-round spend, not the method's
    -- and the record names the field. `cost_weight` defaults to 0.0 in both
    published recipes, so this only matters when someone turns the term on.
    """
    cost = point.get("cost")
    value = cost.get(cost_field) if isinstance(cost, dict) else None
    if value is None and isinstance(cost, dict):
        value = cost.get("method_generation_tokens")
    return _num(value, 0.0)


def _gate_decision(point: dict, best: dict | None, *, tolerance: float,
                   cost_weight: float, pass_count_noise_threshold: int,
                   cost_field: str) -> dict:
    """One call of `_score_and_gate` -- the transplanted kernel.

    `best` is `{rate, cost, round, passed}` or `None`. Returns the gate record plus
    the updated `best` and, on a revert, the round to restore. The two-condition rule
    is the source's (`recipe/gaia_evolver/run.py:1222-1236`), including the default
    positive count threshold. Setting that threshold to 0 disables the guard and
    applies the score comparison even if passed counts are unavailable.
    """
    rate = _num(point.get("score"))
    cost = _round_cost(point, cost_field)
    passed = _passed(point)
    record: dict = {
        "graded_round": point.get("round"),
        "round_rate": rate,
        "round_cost": cost,
        "round_passed": passed,
    }

    if best is None:
        new_best = {"rate": rate, "cost": cost,
                    "round": point.get("round"), "passed": passed}
        record.update(
            decision="ACCEPTED",
            reason="first round — no prior to compare against",
            best_round=None, best_rate=None, best_passed=None,
            count_delta=None, cost_delta_ratio=0.0, adjusted_score=rate,
            updated_best_round=new_best["round"], reverted_to_round=None,
            noise_level=False, pass_count_available=passed is not None,
        )
        return {**record, "best": new_best, "revert_to": None}

    best_rate = _num(best.get("rate"))
    best_cost = _num(best.get("cost"))
    # The source's guard: a zero baseline cost means no ratio (run.py:1219), not a
    # division by the 1e-3 floor.
    cost_delta_ratio = ((cost - best_cost) / max(best_cost, 1e-3)
                        if best_cost else 0.0)
    adjusted = rate - cost_weight * max(cost_delta_ratio, 0.0)
    best_passed = best.get("passed")
    count_delta = (None if (passed is None or best_passed is None)
                   else abs(passed - best_passed))
    record.update(
        best_round=best.get("round"), best_rate=best_rate, best_passed=best_passed,
        count_delta=count_delta, cost_delta_ratio=cost_delta_ratio,
        adjusted_score=adjusted, pass_count_available=count_delta is not None,
    )

    if adjusted < best_rate - tolerance:
        # Upstream's optional count guard distinguishes signal from noise; a
        # disabled guard must not turn unavailable counts into an exemption.
        if pass_count_noise_threshold > 0 and (
                count_delta is None or count_delta < pass_count_noise_threshold):
            why = (f"score {adjusted:.3f} < R{best.get('round')} {best_rate:.3f} - "
                   f"tolerance {tolerance:.3f} but "
                   + ("no passed-task count is available"
                      if count_delta is None
                      else f"|Δpassed|={count_delta} < threshold "
                           f"{pass_count_noise_threshold}")
                   + " — noise-level, accepted without updating best")
            record.update(decision="ACCEPTED", reason=why, noise_level=True,
                          updated_best_round=best.get("round"),
                          reverted_to_round=None)
            return {**record, "best": best, "revert_to": None}
        why = (f"reverted: candidate {point.get('round')} score {adjusted:.3f} vs "
               f"best round {best.get('round')} {best_rate:.3f}, "
               f"delta {adjusted - best_rate:+.3f}; "
               f"drop {best_rate - adjusted:.3f} > tolerance {tolerance:.3f} "
               f"(|Δpassed|={count_delta}, "
               f"noise threshold={pass_count_noise_threshold})")
        record.update(decision="REVERTED", reason=why, noise_level=False,
                      updated_best_round=best.get("round"),
                      reverted_to_round=best.get("round"))
        return {**record, "best": best, "revert_to": best.get("round")}

    new_best = ({"rate": rate, "cost": cost,
                 "round": point.get("round"), "passed": passed}
                if adjusted > best_rate else best)
    record.update(
        decision="ACCEPTED",
        reason=(f"score {adjusted:.3f} >= R{best.get('round')} {best_rate:.3f} - "
                f"tolerance {tolerance:.3f}"),
        noise_level=False, updated_best_round=new_best.get("round"),
        reverted_to_round=None,
    )
    return {**record, "best": new_best, "revert_to": None}


def _replay_best(points: list[dict], **tun) -> tuple[dict | None, list[dict]]:
    """Rebuild HarnessX's `best_so_far` by replaying the gate over the curve.

    The gate is path-dependent once `cost_weight > 0` (a round's adjusted score is
    measured against the best's cost), so `max(history, key=score)` is *not* the same
    quantity. Replaying the kernel is the faithful reconstruction, and it is cheap.
    """
    best: dict | None = None
    steps: list[dict] = []
    for point in points:
        record = _gate_decision(point, best, **tun)
        best = record["best"]
        steps.append({
            "round": record["graded_round"],
            "decision": record["decision"],
            "adjusted_score": record["adjusted_score"],
            "best_round": record["best_round"],
            "updated_best_round": record["updated_best_round"],
        })
    return best, steps


def _grade(history: list[dict], tun: dict) -> dict:
    """The gate record for the round just measured, plus the replayed best.

    At mode A round `r` the staged `history` is the whole curve so far, so
    `history[-1]` is the round the platform just measured and `history[:-1]` is what
    HarnessX's `best_so_far` had seen when it graded it.
    """
    tun = {k: tun[k] for k in ("tolerance", "cost_weight",
                               "pass_count_noise_threshold", "cost_field")}
    # The upstream kernel receives measured pass rates (run.py:802-811).
    # Platform history can also contain unmeasured candidates; those are not
    # ordinary zero scores and cannot become the best or trigger a rollback.
    def measured(point: dict) -> bool:
        return bool(point.get("measured_by_platform", True)) and math.isfinite(
            _num(point.get("score"), float("nan")))

    scored = [point for point in history if measured(point)]
    latest_measured = bool(history) and measured(history[-1])
    if len(scored) < 2 or not latest_measured:
        best, steps = _replay_best(scored, **tun)
        best = best or {}
        return {
            "decision": "NO_GATE",
            "reason": (
                "no finite platform measurement for the latest candidate; gate skipped"
                if history and not latest_measured else
                "no measured candidate to grade yet — this is the baseline; "
                "HarnessX's first round only establishes best"),
            "tunables": dict(tun),
            "graded_round": None, "round_rate": None, "round_passed": None,
            "round_cost": None, "best_round": best.get("round"),
            "best_rate": best.get("rate"),
            "best_passed": best.get("passed"), "count_delta": None,
            "cost_delta_ratio": 0.0,
            "adjusted_score": None, "updated_best_round": best.get("round"),
            "reverted_to_round": None, "noise_level": False,
            "pass_count_available": False, "history_steps": steps,
        }

    best, steps = _replay_best(scored[:-1], **tun)
    record = _gate_decision(scored[-1], best, **tun)
    out = {k: v for k, v in record.items() if k not in ("best", "revert_to")}
    out["tunables"] = dict(tun)
    out["history_steps"] = steps
    return out


# ------------------------------------------------- previous state + revert ---

def _state_dir(base: Path, round_no) -> str | None:
    """Where the platform staged a previous round's harness.

    `states/index.json` is read rather than the directory guessed, because a state
    that could not be materialized is deliberately absent from the index -- a method
    that guessed the path would find an empty directory and report a failure the
    platform caused (`harnessgrad/channel.py:435-444`).
    """
    index = editor.channel(base) / "states" / "index.json"
    if not index.exists():
        return None
    try:
        data = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return (data.get(f"round-{round_no}") or {}).get("harness_dir")


def _revert_directive(base: Path, best_round, state_dir: str | None) -> dict:
    """The best-tree restore target, accompanying the incumbent_round selection."""
    relative = f"{editor.CHANNEL}/states/round-{best_round}"
    available = bool(state_dir) and Path(state_dir).is_dir()
    return {
        "action": "revert",
        "base_round": best_round,
        "base_state": relative,
        "base_harness_dir": str(state_dir) if available else None,
        "state_available": available,
        "text": (f"build on the historically best state at `{relative}`, because the "
                 f"last round's candidate was reverted by HarnessX's gate"),
    }


def _tree_digest(root: Path) -> dict[str, str]:
    """Content hash of every harness file, ignoring git and the method channel."""
    out: dict[str, str] = {}
    root = Path(root)
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if ".git" in rel.parts or editor.CHANNEL in rel.parts:
            continue
        try:
            out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def _same_tree(a: Path, b: Path) -> bool:
    """Whether two harness directories hold byte-identical files.

    Needed on the revert path: if the state the gate chose for us *is* the incumbent
    (possible when an earlier revert already restored it), reporting `changed: true`
    would mint a checkpoint that differs from its parent by nothing -- the exact
    thing `protocol.emit`'s docstring warns makes a control curve move.
    """
    return _tree_digest(a) == _tree_digest(b)


# ------------------------------------------------------ the editor facade ---

HARNESSX_SYSTEM = """You are the meta-agent of HarnessX, whose acceptance gate \
decides whether the round you are about to propose survives.

You will be shown the harness's source and the record of running it on a set of \
tasks. Your changes should flip failing tasks without \
breaking passing ones.

Before the platform measures your proposal you must pre-register the hypothesis, \
in HarnessX's schema. Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>"}],
   "hypothesis": "<one sentence: what you changed and why>",
   "hypothesis_id": "h_<short slug>",
   "levers": ["configuration" | "control" | "action" | "instruction"],
   "predicted_affected": ["<task id>", "..."],
   "rollback_trigger": "<observable signal that means revert this next round>",
   "expected_global_gain": "<cluster-level upside, not a single-task anecdote>",
   "regression_risk": "<what could break outside predicted_affected>",
   "cost_shift": "<expected token/cost movement>",
   "lever_argument": "<why this lever and not the adjacent one>"}

The four levers are HarnessX's and they mean what they mean there:
- configuration: kwargs on an existing component, when its tuning is off;
- control: a new mechanical hook around the loop (guard, parse, sanitise);
- action: a new tool, when the agent has no way to take a class of action;
- instruction: a prompt/template edit, when capability and control are fine but the \
agent does not know when or in what order to use them.
Every candidate owes the "why this lever and not the adjacent one" argument.

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- `predicted_affected` lists the task ids you claim will flip F->T (or, for a \
preservative change, the passing ids you claim to protect). Over-predicting is \
graded against you.
- If the evidence supports no change, reply {"no_change": "<reason>"}.
"""


def _preregistration(reply: dict) -> dict:
    """HarnessX's journal frontmatter, as declared by the model before measurement.

    Recorded verbatim plus one derived flag. `levers_valid` is derived because
    HarnessX's `append_entry` refuses unknown levers (`journal.py:155-157`); here the
    platform records rather than polices, so an invalid declaration is reported, not
    silently dropped.
    """
    levers = reply.get("levers")
    if not isinstance(levers, list):
        levers = []
    levers = [str(lever) for lever in levers]
    return {
        "hypothesis_id": reply.get("hypothesis_id"),
        "levers": levers,
        "levers_valid": bool(levers) and all(lever in LEVERS for lever in levers),
        "predicted_affected": reply.get("predicted_affected"),
        "rollback_trigger": reply.get("rollback_trigger"),
        "expected_global_gain": reply.get("expected_global_gain"),
        "regression_risk": reply.get("regression_risk"),
        "cost_shift": reply.get("cost_shift"),
        "lever_argument": reply.get("lever_argument"),
        "source": "reply",
    }


def _previous_preregistration(history: list[dict]) -> dict | None:
    """The graded round's own prediction, read back from the curve point.

    `_curve_point` copies the method's `method_reported` onto the point
    (`driver.py:850-851`), which is what makes it readable next round -- the platform
    records the prediction beside the measurement, and grading is a separate act.
    """
    if not history:
        return None
    reported = history[-1].get("method_reported")
    if isinstance(reported, dict):
        value = reported.get("harnessx_preregistration")
        if isinstance(value, dict):
            return {**value, "source": "previous_round_record"}
    return None


def build_prompt(base: Path, request: dict, gate: dict, revert_directive: dict | None,
                 prereg: dict | None) -> str:
    """The edit is made in the light of the gate's verdict, not independently of it."""
    lines = [
        f"HarnessX evolve round {request.get('round_index', 1)}. The harness scored "
        f"{request.get('incumbent_score')} on the task set.",
        "",
        "## HarnessX's gate on the round just measured",
        f"- decision: {gate.get('decision')}",
        f"- {gate.get('reason')}",
    ]
    if gate.get("best_round") is not None:
        lines.append(f"- historical best: round {gate.get('best_round')} at "
                     f"{gate.get('best_rate')} (compared against the best, never the "
                     f"last accepted round)")
    if revert_directive:
        lines.append(f"- {revert_directive.get('text')}")
    if prereg:
        lines.append(f"- the round's pre-registered hypothesis was "
                     f"`{prereg.get('hypothesis_id')}` on levers "
                     f"{prereg.get('levers')}")
    return "\n".join(lines)


# ----------------------------------------------------------------- main ---

def _reported(gate: dict, prereg: dict | None, directive: dict | None,
              **extra) -> dict:
    """The one shape every exit path writes, so no path can forget the gate."""
    return {
        "harnessx_gate": gate,
        "harnessx_preregistration": prereg,
        "harnessx_revert_directive": directive,
        **extra,
    }


def main() -> int:
    request = editor.read_request()
    editor.require_api(request)

    base = Path(request["base_harness"])
    history = editor.load_history(base)
    tun = _tunables()
    gate = _grade(history, tun)
    graded_prereg = _previous_preregistration(history)

    # recipe/gaia_evolver/run.py:813-820 restores current_config to best_cfg.
    # Copy that round's full staged tree and record the chosen version using the
    # platform's incumbent_round contract (INTERFACE.md §4.1.1). report() has no
    # such argument; report_many() supports it even for a single step.
    if gate["decision"] == "REVERTED":
        # Upstream recipe/gaia_evolver/run.py:813-839 restores current_config,
        # then back-fills the graded round's existing journal declaration. It
        # does not erase the prediction or generate another one for the restore.
        # Legacy history can lack it: expose that absence without inventing one.
        if graded_prereg is None:
            graded_prereg = {
                **_preregistration({}), "source": "unavailable",
                "reason": (f"no pre-registration recorded for graded round "
                           f"{gate.get('graded_round')}"),
            }
        best_round = gate["reverted_to_round"]
        state_dir = _state_dir(base, best_round)
        directive = _revert_directive(base, best_round, state_dir)
        if not directive["state_available"]:
            return editor.report_many(
                request, method="harnessx", steps=[{
                    "harness_dir": str(base), "changed": False,
                    "incumbent_round": best_round,
                    "edit_kind": "harnessx_revert",
                    "method_reported": _reported(gate, graded_prereg, directive),
                    "label": f"HarnessX gate: select R{best_round} (state not staged)",
                }], stop=True,
                hypothesis=(f"HarnessX gate selected R{best_round} after rejecting "
                            f"round {gate.get('graded_round')}, but that state is "
                            f"not staged; physical restore unavailable, refusing "
                            f"to build on a base the gate rejected"),
                acceptance_rule=ACCEPTANCE_RULE,
            )

        dest = editor.candidate_path(request)
        editor.apply_edits(Path(directive["base_harness_dir"]), dest, [])
        same = _same_tree(Path(directive["base_harness_dir"]), base)
        return editor.report_many(
            request, method="harnessx", steps=[{
                "harness_dir": str(dest), "changed": not same,
                "incumbent_round": best_round,
                "edit_kind": "harnessx_revert",
                "method_reported": _reported(
                    gate, graded_prereg, directive,
                    candidate_equals_incumbent=same),
                "label": f"HarnessX gate: revert to R{best_round}",
            }], stop=False,
            hypothesis=(f"HarnessX gate reverted round {gate.get('graded_round')}: "
                        f"{gate.get('reason')}")[:300],
            acceptance_rule=ACCEPTANCE_RULE)

    # ---- the accept branch: spend the editor on a new candidate ---------------
    dest = editor.candidate_path(request)
    try:
        reply = editor.ask(
            build_prompt(base, request, gate, None, graded_prereg),
            system=HARNESSX_SYSTEM, base=base)
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        # `editor.fail` now carries the method's own record and continues the run.
        # Both matter here: the gate's arithmetic is what the next round grades
        # against, and a transient 500 is not a decision by this method.
        return editor.fail(
            request, method="harnessx", exc=exc, base=base,
            state={"harnessx_gate": gate,
                   "harnessx_preregistration": _preregistration({})})

    prereg = _preregistration(reply)

    if "no_change" in reply:
        editor.apply_edits(base, dest, [])
        return editor.report(
            request, method="harnessx", harness_dir=dest, changed=False,
            stop=False,
            hypothesis=str(reply["no_change"])[:200], edit_kind="harnessx_rule",
            method_reported=_reported(gate, prereg, None),
            acceptance_rule=ACCEPTANCE_RULE,
            label="HarnessX gate: accepted, no edit")

    changed, files, note = editor.apply_edits(base, dest, reply.get("files"))
    if not changed:
        return editor.report(
            request, method="harnessx", harness_dir=dest, changed=False,
            stop=False,
            hypothesis=f"unusable proposal: {note}", edit_kind="harnessx_rule",
            method_reported=_reported(gate, prereg, None, rejected=note),
            acceptance_rule=ACCEPTANCE_RULE,
            label="HarnessX gate: accepted, unusable proposal")

    lever_tag = "+".join(prereg["levers"]) if prereg["levers_valid"] else "harnessx_rule"
    return editor.report(
        request, method="harnessx", harness_dir=dest, changed=True, files=files,
        hypothesis=str(reply.get("hypothesis", ""))[:300],
        edit_kind=f"harnessx:{lever_tag}",
        method_reported=_reported(gate, prereg, None),
        acceptance_rule=ACCEPTANCE_RULE,
        extra={"preregistered_hypothesis": prereg.get("hypothesis_id")},
        label=(f"[HarnessX {lever_tag}] "
               + str(reply.get("hypothesis", ""))[:50]))


if __name__ == "__main__":
    raise SystemExit(main())
