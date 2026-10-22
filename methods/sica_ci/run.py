#!/usr/bin/env python3
"""SICA's base-selection port and its recorded incumbent.

Upstream `sica/runner.py:118-135` takes the highest mean's confidence lower bound,
then returns the newest iteration whose mean is at least that bound. This port now
does exactly that: `_select_base` ranks by mean, takes that iteration's `ci_lower`
as the bound, and accepts `mean >= bound`. An earlier version ranked by `ci_lower`
and used `>`, which demanded strictly more evidence than SICA does -- a conservative
divergence, corrected rather than merely recorded.

The chosen historical round is recorded as a step's `incumbent_round` when the
comparison has an available CI. Without that evidence, the record explains why no
kept round is specified. The platform owns measurement and stopping; the local
`_worth_continuing` result is diagnostic only.

Selection guides the editor prompt, but the supplied harness tree is still the
platform's base: this port does not restore a chosen historical source snapshot.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor                                              # noqa: E402


# ------------------------------------------------------------ SICA 的规则 ---

def _ci_lower(point: dict) -> float:
    """A curve point's confidence lower bound, as SICA reads `ci_lower`.

    The platform records `score_ci95` as [lo, hi]; lo is the quantity SICA's rule
    compares against. Points written before the field existed fall back to the score
    itself, which makes them harmless: an interval of zero width never clears a real
    bound by accident.
    """
    ci = point.get("score_ci95")
    if isinstance(ci, list) and len(ci) == 2 and isinstance(ci[0], (int, float)):
        return float(ci[0])
    return float(point.get("score") or 0.0)


def _score(point: dict) -> float:
    """The point's mean score -- the quantity upstream ranks iterations by."""
    return float(point.get("score") or 0.0)


def _select_base(history: list[dict]) -> tuple[dict, str]:
    """SICA's rule: the newest round whose mean clears **the best mean's** lower bound.

    Upstream `sica/runner.py:118-135`, in its own order:

        best_idx = stats["target_score"].idxmax()      # the highest MEAN
        best_lower_bound = best_stats["ci_lower"]      # that iteration's lower bound
        for i in range(current_iteration, -1, -1):     # newest first
            if stats.loc[i, "target_score"] >= best_lower_bound:
                return i

    **Two details here are easy to get wrong, and both were wrong once.** The bound is
    the lower bound of the *highest-mean* iteration, not the highest lower bound in the
    set: `max(history, key=_ci_lower)` returns a different iteration as soon as the
    intervals have different widths, and because a maximum over lower bounds is never
    smaller than one particular lower bound, that version demanded strictly more
    evidence than SICA does -- a conservative divergence, but not SICA's rule. And the
    comparison is `>=`: the best iteration always clears its own bound, so a strict `>`
    can report "nothing clears" for a set SICA resolves.

    Returns (chosen_round, why). The reasoning string names both rounds, so a reader
    can see which branch fired instead of reconstructing it from the curve.
    """
    if not history:
        return {}, "no history yet"

    best = max(history, key=_score)
    bound = _ci_lower(best)
    # `>=`, because SICA's own best iteration qualifies by construction; a NaN bound
    # would make this empty, and an empty set must fall back rather than crash.
    clearing = [p for p in history if _score(p) >= bound]
    if not clearing:
        return best, (f"no round clears the best mean's lower bound {bound:.3f} "
                      f"(round {best.get('round')} holds the best mean)")

    # "Newest whose mean clears the bound" -- SICA's tie-break, and the reason its
    # selection prefers recent work among statistical equals: an older iteration is a
    # longer walk from whatever the current state is.
    newest = max(clearing, key=lambda p: p.get("round", -1))
    return newest, (f"round {newest.get('round')} has mean {_score(newest):.3f} >= lower "
                    f"bound {bound:.3f} of the best mean "
                    f"(round {best.get('round')}, {_score(best):.3f})")


def _worth_continuing(history: list[dict]) -> tuple[bool, str]:
    """Whether another round can still buy anything statistically.

    Two stops, both from SICA's comparison:

    * **A bound that nothing clears.** Every round is inside the noise of the best
      one, so a further edit is a coin flip dressed as progress.
    * **A bound that keeps being cleared by the incumbent alone.** The current round
      *is* the best; there is nothing to build past it yet.
    """
    if len(history) < 2:
        return True, "too little history to judge"

    incumbent = history[-1]
    incumbent_score = float(incumbent.get("score") or 0.0)
    best_other = max(history[:-1], key=_ci_lower)
    bound = _ci_lower(best_other)

    if incumbent_score > bound:
        return True, (f"incumbent {incumbent_score:.3f} clears the previous best lower "
                      f"bound {bound:.3f}")
    return False, (f"incumbent {incumbent_score:.3f} does not clear the previous best "
                   f"lower bound {bound:.3f}; another round would be noise")


# ----------------------------------------------------------------- 主流程 ---

#: SICA's rule decides *whether* and *from where*; the edit itself is a separate
#: concern, and composing the two is what makes them separable at all. It is how
#: the platform can answer "is this method's advantage its rule or its editor?" --
#: run the same editor with and without the rule.
SICA_SYSTEM = """You are improving an agent harness, on behalf of a method whose \
selection rule has already decided that this round is worth spending on.

You will be shown the harness's source and the record of running it on a set of \
tasks.

Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>"}],
   "hypothesis": "<one sentence: what you changed and why>"}

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
"""


def build_prompt(base, request, chosen, why):
    """The edit is made *in the light of* the rule's choice, not independently of it."""
    return (
        f"Round {request.get('round_index', 1)}. The harness scored "
        f"{request.get('incumbent_score')} on the task set.\n\n"
        f"The selection rule chose round {chosen.get('round')} as the fairest base "
        f"to build on ({why}), so the change should be a step from there rather than "
        f"a restatement of the current state.\n\n"
    )


def _incumbent_fields(history: list[dict], chosen: dict) -> tuple[dict, str]:
    """Publish the existing choice only when its bound comes from an available CI."""
    # Upstream /mnt/20t/xubuqiang/Study/methods_src/sica/runner.py:118-135
    # returns the selected iteration index. Map that result to the platform's round
    # number, without changing this port's _select_base comparison or its fallback.
    # Upstream runner.py:107-114 defaults to 0 without statistics; here an absent CI
    # must instead leave incumbent_round unspecified, as required by this port's task.
    if not chosen:
        return {}, "no history; no kept round specified"

    bound_point = max(history, key=_ci_lower)
    ci = bound_point.get("score_ci95")
    if not (isinstance(ci, list) and len(ci) == 2
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in ci)
            and ci[0] <= ci[1]):
        return {}, "CI unavailable for the selection bound; no kept round specified"

    round_no = chosen.get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        return {}, "selected history point has no valid round; no kept round specified"
    return {"incumbent_round": round_no}, f"selected historical round {round_no}"


def _report(request, *, method, acceptance_rule, extra=None, stop=False, **step):
    """Use the single-step form of the editor API that supports incumbent_round.

    editor.report has no incumbent_round parameter; editor.report_many serializes
    it through _step_payload. Keep one measured candidate and continue the loop.
    """
    return editor.report_many(
        request, method=method, steps=[step], acceptance_rule=acceptance_rule,
        extra=extra, stop=stop, hypothesis=step.get("hypothesis", ""))


def main() -> int:
    request = editor.read_request()
    editor.require_api(request)

    base = Path(request["base_harness"])
    history = editor.load_history(base)

    chosen, why = _select_base(history)
    cont, cont_why = _worth_continuing(history)
    incumbent_fields, incumbent_reason = _incumbent_fields(history, chosen)

    rule_report = {
        "rule": "SICA: newest round whose mean clears the best confidence lower bound",
        "rounds_seen": [p.get("round") for p in history],
        "bounds": {str(p.get("round")): round(_ci_lower(p), 4) for p in history},
        "selected_base_round": chosen.get("round"),
        "selection_reason": why,
        "incumbent_reason": incumbent_reason,
        "continue": cont,
        "continue_reason": cont_why,
    }

    # _worth_continuing is an observation, not a gate: every admitted call still
    # asks for an edit, and no-change/unusable proposals keep stop=False.
    dest = editor.candidate_path(request)
    try:
        reply = editor.ask(build_prompt(base, request, chosen, why), system=SICA_SYSTEM, base=base)
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return editor.fail(request, method="sica_ci", exc=exc, base=base,
                           state={"sica_rule": rule_report})

    if "no_change" in reply:
        editor.apply_edits(base, dest, [])
        return _report(
            request, method="sica_ci", harness_dir=dest, changed=False,
            stop=False, **incumbent_fields,
            hypothesis=str(reply["no_change"])[:200], edit_kind="sica_rule",
            method_reported={"sica_rule": rule_report},
            acceptance_rule="SICA's rule admitted the round; the model found no edit",
            label=f"SICA rule: base round {chosen.get('round')}, no edit")

    changed, files, note = editor.apply_edits(base, dest, reply.get("files"))
    if not changed:
        return _report(
            request, method="sica_ci", harness_dir=dest, changed=False,
            stop=False, **incumbent_fields,
            hypothesis=f"unusable proposal: {note}", edit_kind="sica_rule",
            method_reported={"sica_rule": rule_report, "rejected": note},
            acceptance_rule="SICA's rule admitted the round; the proposal was unusable",
            label=f"SICA rule: base round {chosen.get('round')}, unusable proposal")

    return _report(
        request, method="sica_ci", harness_dir=dest, changed=True, files=files,
        **incumbent_fields,
        hypothesis=(f"[SICA base r{chosen.get('round')}] "
                    + str(reply.get("hypothesis", "")))[:300],
        edit_kind="sica_rule",
        method_reported={"sica_rule": rule_report},
        acceptance_rule="The port's confidence-lower-bound comparison selects a "
                        "historical incumbent when its CI is available; the platform "
                        "measures candidates and controls the stopping budget",
        extra={"selection_rule": rule_report["rule"]},
        label=f"[SICA base r{chosen.get('round')}] "
              + str(reply.get("hypothesis", ""))[:50])


if __name__ == "__main__":
    raise SystemExit(main())
