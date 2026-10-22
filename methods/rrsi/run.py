#!/usr/bin/env python3
"""RRSI's per-round decision rule, as a HarnessGrad method.

Why this file exists
--------------------
RRSI's contribution is not the edit an LLM makes -- it is the **rule that decides
whether a candidate harness is allowed to replace the incumbent, and how many edits
the round is allowed to bundle**. From its own selection module
(`rrsi/selection.py:30-38`):

    Delta S = S' - S_t,   Delta C = (C' - C_t) / C_t
    c = [Delta C <= beta0 + beta1 Delta S]             if Delta S > delta
        [w_s Delta S - w_c Delta C + w_n nu(l') > 0]   otherwise
    admissible  iff  S' >= S* - delta  and  c
    H_{t+1} = argmax_{H' admissible} S',  or H_t if none is admissible

Around that rule sit five other decisions RRSI makes every round, and this file
ports all six: the annealed edit budget, the stall flag and its reserved
exploration slot, the prune set, the novelty term, and the pre-evaluation leakage
critic. What differs from RRSI is the loop around them: this port submits one
candidate per call, evaluated by the platform, whereas
RRSI owns its own loop (m candidates, k trials, its own evaluation).

Where each rule was read from
-----------------------------
* noise-adjusted floor   `rrsi/selection.py:107-111`
* two-branch cost rule   `rrsi/selection.py:81-94`
* novelty nu(l')         `rrsi/components.py:103-108`
* annealed budget b_t    `rrsi/schedule.py:43-50`
* stall flag sigma_t     `rrsi/history.py:189-193`
* exploration reserve    `rrsi/history.py:196-211`
* prune set B_t          `rrsi/history.py:143-163`
* relative cost Delta C  `rrsi/evaluate.py:131-135`
* component vocabulary   `rrsi/components.py:44-100`
* leakage critic         `rrsi/critic.py:105-154`
* calibration (partial)  `rrsi/calibrate.py:85-108`

What is transplanted
--------------------
**The decision rules, as pure functions, against this platform's curve points.**
`_edit_budget`, `_stall_flag`, `_exploration`, `_prune_set`, `_novelty`,
`_cost_rule`, `_judge`, `_classify_diff`/`_normalize`/`_has_evidence` and the two
critic layers are RRSI's algorithms with this platform's data in place of its
`EvalResult`/`History` objects. The critic is real: a deterministic denylist runs
first, a model review second, and a rejection sends the objections back to the
proposer for a bounded number of repair rounds (`rrsi/critic.py:113-154`).

**The state the rules need is reconstructed from the platform's own record.**
RRSI keeps `history.jsonl` (one record per edit: component, accepted, Delta S,
Delta C). This platform keeps one curve point per round, so a component touched by
round *i*, the score series, and Delta S = S(i) - S(i-1) are recovered from the
points themselves, where `S` is the diagnostic score of that point -- which field
holds it is decided by `score_kind`, see `_train_score` below. The method also declares `touched_components`
in `method_reported.rrsi_rule`, which is the one piece of state the points cannot
otherwise carry, and which a later round reads back out of them.

What is *not* transplanted
--------------------------
RRSI's scaffolding: its analyst/digester/proposer role prompts, its git worktrees,
`frontier.json`, its harbor domain adapters, and its `k` trials per task. None of
that is the method; the platform already supplies a task set, execution, scoring
and a per-round record. Reimplementing it here would be reimplementing the
platform.

What is *not reproduced*, and why -- stated plainly
---------------------------------------------------
The platform can measure several submitted steps, record `kept_round`, and expose
historical trees in `states/`. This port still drafts one candidate per call:

1. **The native `m`-candidate loop is not wired here.** `report_many` can carry a
   batch, but the method sees no measurements between its steps. Upstream drafts
   and screens m candidates from one incumbent, evaluates them, then selects
   (`rrsi/loop.py:290-307`, `rrsi/selection.py:124-137`). This port does not group
   such a pool and select it on a later call.
2. **Retention is recorded after measurement, without restoring a tree.** The
   latest historical candidate is re-adjudicated against its actual predecessor.
   An admissible verdict names that candidate's round; a rejection names its
   predecessor, following the singleton case of `rrsi/selection.py:124-137` and
   `rrsi/loop.py:327-340`. The step carries this as `incumbent_round`. A missing
   verdict carries no selection claim and explains why. This is a delayed receipt,
   not an immediate veto or a reconstructed native frontier: the newly submitted
   candidate is still built from the platform's current base, and the record does
   not claim to judge that unmeasured candidate. Restoring `states/` is possible
   on this platform but not implemented in this port.
3. **Delta IS calibrated now -- the channel arrived with per-trial rewards.** This
   paragraph used to say the opposite, and it was true when written: the platform
   recorded one score per task per round, so `calibrate.py`'s two estimators (R >= 2
   repeated evaluations of the base, or a bootstrap over per-trial rewards) had nothing
   to read. The record now carries `per_task_trials` and `score_std`, where `score_std`
   is the platform's own population standard deviation of the **run-level score across
   trials of one measurement** -- which is exactly `sd(S)` for a fixed harness, tasks and
   budget. Two independent measurements of the same harness have `sd(Delta S) = sqrt(2) *
   sd(S)`, so `delta = z * sqrt(2) * score_std` is `_delta_from_repeated_base`'s formula
   with the platform's number in place of the repeated runs it could not stage. Measured
   on this platform (5 tasks, 3 trials): `score_std = 0.094` -> **delta ~ 0.27**, which is
   the size of the re-measurement swing that fooled a control arm into "gaining" +0.200.
   When the point carries no `score_std` (one trial) the fallback is used and said so.
4. **A dropped candidate leaves this call with no live proposal.** In RRSI a
   critic rejection or an over-budget proposal is a `gate_failure`: the candidate is
   dropped while other screened variants can still be measured
   (`rrsi/loop.py:290-302`). This port drafted only one,
   so this file reports `changed: false` with the objection recorded -- the honest
   reading; `stop=False` lets the platform invoke it again within the run budget.

The one place the port is deliberately stricter than RRSI: RRSI substitutes
`Delta C = 0` whenever either side reports no token count (`evaluate.py:131-135`),
which lets a cost rule that never fired look like a cost rule that passed. This
file computes the same 0.0 but marks `cost_rule_active: false` and leaves
admissibility `null` instead. A rule fed a constant zero permits everything, and a
record must not present that as a verdict.

Which score the rule reads
--------------------------
**The diagnostic side, and never the exam.** The method is shown the traces and the
per-task scores of the side it studies (`INTERFACE.md` §2.3), and selecting on the
exam is exactly the leak the split exists to prevent. Since §2.6 a run covers one
side, so which *field* holds the diagnostic score depends on the record:
`score_kind == "training"` means `score` **is** the diagnostic (`train_score` is
absent by design on a single-side run -- it would be a copy of `per_task`), while a
record predating §2.6 carries both and there `train_score` is the diagnostic.
`protocol.studied_score` decides, from `score_kind` and never by trying `score`
first. `request["incumbent_score"]` is never fed to the rule or to the prompt. When
there is no usable signal the rule reports `S_star: null` / `rule_evaluated: false`
rather than falling back to the exam.

Tunables (environment, with defaults)
-------------------------------------
RRSI's paper instances hard-code per-domain values (`rrsi/config.py:56-85`), so
every number below is read from the environment and defaulted to the published
value only where the value is actually RRSI's:

  HG_RRSI_T              20      anneal horizon T (schedule.py)
  HG_RRSI_B_MIN          1       b_min (schedule.py)
  HG_RRSI_B_MAX          4       b_max (schedule.py)
  HG_RRSI_W              3       stall window w (history.py)
  HG_RRSI_M_DRAFT        1       reserved exploration slots; capped at this port's m=1
  HG_RRSI_DELTA          unset   calibrated noise band; unset = use the fallback
  HG_RRSI_DELTA_FALLBACK 0.0     delta used when no calibration exists (NOT RRSI's)
  HG_RRSI_DELTA_Z        2.0     z for delta = z * sd(null Delta S) (calibrate.py)
  HG_RRSI_BETA0          0.10    beta0 (config.py)
  HG_RRSI_BETA1          40.0    beta1 (config.py)
  HG_RRSI_W_S            100.0   w_s (config.py)
  HG_RRSI_W_C            15.0    w_c (config.py)
  HG_RRSI_W_N            0.5     w_n (config.py)
  HG_RRSI_N_PRUNE        4       n_prune (config.py)
  HG_RRSI_REPAIR_ROUNDS  5       critic -> proposer repair attempts (config.py)
  HG_RRSI_CRITIC_ATTEMPTS 3      critic JSON retries (critic.py:131)

The honest summary: **RRSI's per-round decision rules are RRSI's, the loop around
them is the platform's.** A run of this method is evidence about those rules, not a
reproduction of RRSI's published results.
"""
from __future__ import annotations

import difflib
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Called through the module, never `from editor import ask`: the tests monkeypatch
# `editor.ask`, and a method that bound the function at import time could not be
# tested without a network call -- the stub is the only way to check what it asked.
import editor                                                  # noqa: E402
import protocol                                                # noqa: E402


# ------------------------------------------------- RRSI 的组件词表与信号 ---

#: The fixed component vocabulary K (`rrsi/components.py:44-45`) and its structural
#: subset K_str (`:46`). A tag outside K corrupts T_t, U_t and the novelty term.
K = ["prompt", "control_flow", "config", "output_plumbing", "context_mgmt",
     "client_tool", "skill", "memory", "subagent"]
K_STR = ["client_tool", "skill", "memory", "subagent"]

#: Signals shared by every domain (`rrsi/components.py:49-55`), transplanted
#: verbatim. They are what turns a declared tag into a verified one.
GENERIC_SIGNALS = [
    ("memory",      [r"\bMemory\(", r"\.remember\(", r"\.recall\(", r"_STATE_DIR"]),
    ("skill",       [r"skills/", r"SkillRegistry", r"skill_use", r"skill_catalog",
                     r"upload_skills"]),
    ("client_tool", [r"ToolRegistry", r"register_tool", r"tool_spec", r"CLIENT_TOOLS"]),
    ("subagent",    [r"\bsubcall\(", r"sub_agent", r"subagent"]),
]

#: A changed line that is only a string literal or a comment is a model-facing text
#: edit, i.e. `prompt` whatever words the prose contains (`components.py:58`, used
#: by `text_only` at `components.py:61-69`).
_STRING_LINE = re.compile(r'^[+-]\s*(?:[frb]?["\']|""")')

#: The critic's deterministic layer: RRSI's generic credential denylist
#: (`rrsi/critic.py:50-52`) plus this platform's own patterns, which are the
#: `critic_patterns` the HarnessGrad domain adapter hands RRSI
#: (`legacy/adapters/harnessgrad_domain/adapter.py:421-431`). A hit here is a hard
#: rejection before any model is asked (`rrsi/critic.py:105-119`).
CRITIC_PATTERNS = [
    (r"AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{20,}|api_key\s*=\s*[\"\'][^\"\']{8,}",
     "credential in diff"),
    (r"\bscorable\b|\bSCORABLE\b", "the answer key referenced in the diff"),
    (r"\beval/runner\.py\b|\beval/metrics\.py\b|\beval/integrity\.py\b",
     "the platform's scorer in the diff"),
    (r"\bdriver\.py\b", "the platform's driver in the diff"),
    (r"\beval/", "a platform evals path in the diff"),
    (r"answer\s*==\s*['\"]|['\"]\s*==\s*answer", "answer comparison hardcoded"),
    (r"\bHG_METHOD_", "the method's own budget referenced in the diff"),
]


# --------------------------------------------------------------- 可调参数 ---

class RRSIConfig(NamedTuple):
    """RRSI's hyperparameters, read from the environment.

    Field names and published defaults follow `rrsi/config.py:56-85`; the two that
    are deliberately *not* RRSI's (`delta_fallback`, and `m_draft` capped by this port)
    are documented on the field and in the module docstring.
    """
    T: int = 20
    b_min: int = 1
    b_max: int = 4
    w: int = 3
    m_draft: int = 1
    delta: float | None = None              # None -> delta_fallback
    delta_fallback: float = 0.0             # not RRSI's: it has a calibration step
    delta_z: float = 2.0
    beta0: float = 0.10
    beta1: float = 40.0
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5
    n_prune: int = 4
    repair_rounds: int = 5
    critic_attempts: int = 3

    @classmethod
    def from_env(cls, env: dict | None = None) -> "RRSIConfig":
        src = os.environ if env is None else env

        def f(name: str, default: float) -> float:
            try:
                return float(src[name])
            except (KeyError, TypeError, ValueError):
                return default

        def i(name: str, default: int) -> int:
            try:
                return int(float(src[name]))
            except (KeyError, TypeError, ValueError):
                return default

        raw_delta = src.get("HG_RRSI_DELTA")
        delta: float | None
        try:
            delta = float(raw_delta) if raw_delta not in (None, "") else None
        except (TypeError, ValueError):
            delta = None
        return cls(
            T=i("HG_RRSI_T", 20),
            b_min=i("HG_RRSI_B_MIN", 1),
            b_max=i("HG_RRSI_B_MAX", 4),
            w=i("HG_RRSI_W", 3),
            m_draft=i("HG_RRSI_M_DRAFT", 1),
            delta=delta,
            delta_fallback=f("HG_RRSI_DELTA_FALLBACK", 0.0),
            delta_z=f("HG_RRSI_DELTA_Z", 2.0),
            beta0=f("HG_RRSI_BETA0", 0.10),
            beta1=f("HG_RRSI_BETA1", 40.0),
            w_s=f("HG_RRSI_W_S", 100.0),
            w_c=f("HG_RRSI_W_C", 15.0),
            w_n=f("HG_RRSI_W_N", 0.5),
            n_prune=i("HG_RRSI_N_PRUNE", 4),
            repair_rounds=i("HG_RRSI_REPAIR_ROUNDS", 5),
            critic_attempts=i("HG_RRSI_CRITIC_ATTEMPTS", 3),
        )


# ------------------------------------------------------- RRSI 的纯函数 ---

def _edit_budget(t: int, T: int, b_min: int, b_max: int) -> int:
    """b_t = ceil(b_min + (b_max-b_min) * 1/2 (1 + cos(pi t / T))).

    `rrsi/schedule.py:43-50`, transplanted with its floating-point guard intact:
    at t = T the cosine is exactly -1 and the expression is exactly b_min, but a
    `1.0000000002` from rounding would ceil to b_min + 1. Early rounds may bundle
    several coordinated edits; late rounds become sparse and attributable.
    """
    if T <= 0:
        return int(b_max)
    t = max(0, min(int(t), int(T)))
    v = b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T))
    return int(math.ceil(round(v, 9)))


def _budget_table(T: int, b_min: int, b_max: int) -> list[int]:
    """The whole schedule, for the record and for tests."""
    return [_edit_budget(t, T, b_min, b_max) for t in range(T)]


def _delta_from_repeated_base(scores: list[float], z: float = 2.0) -> float | None:
    """delta = z * stdev(scores) * sqrt(2) over >= 2 evaluations of the SAME harness.

    `rrsi/calibrate.py:89-92`, transplanted verbatim. **This file cannot feed it.**
    The platform stages one curve point per round, and those points are different
    harnesses, not repeated evaluations of the base -- feeding them here would
    compute a number that looks calibrated and is not. Kept as the reference
    formula so the fallback's provenance is readable, and so a future channel that
    does carry repeated base evaluations has the estimator ready.
    """
    if len(scores) < 2:
        return None
    return z * statistics.stdev(scores) * math.sqrt(2)


def _delta(history: list[dict], cfg: RRSIConfig) -> tuple[float, str]:
    """The noise band and where it came from.

    RRSI raises rather than guess when no band exists (`rrsi/loop.py:102-107`); this file
    has always fallen back explicitly and said so. What changed is that there is now
    something to calibrate **from**: a curve point carries `score_std` -- the platform's
    population sd of the run-level score across the trials of that one measurement -- so
    `delta = z * sqrt(2) * score_std` is `_delta_from_repeated_base`'s estimator fed with
    the platform's own number instead of repeated runs it could not stage.

    Measured (5 tasks, 3 trials, `noise-1`): `score_std = 0.094` -> delta ~ 0.266, which is
    the size of the swing that made a control arm look like it gained +0.200. That is the
    point of the band: a rule that accepts only what clears it cannot be fooled by
    re-measurement. With `n_trials = 1` the point carries no `score_std`, and the fallback
    is used with the reason in the record.
    """
    if cfg.delta is not None:
        return float(cfg.delta), "explicit: HG_RRSI_DELTA"
    incumbent = (history or [{}])[-1]
    std = incumbent.get("score_std")
    trials = incumbent.get("n_trials")
    if isinstance(std, (int, float)) and not isinstance(std, bool) and std > 0 \
            and isinstance(trials, int) and trials > 1:
        return cfg.delta_z * float(std) * math.sqrt(2), (
            f"calibrated: z={cfg.delta_z} * sqrt(2) * score_std={float(std):.4f} "
            f"(the incumbent's own {trials} trials; RRSI's estimator, the platform's "
            f"number)")
    return float(cfg.delta_fallback), (
        "fallback: HG_RRSI_DELTA unset and the incumbent point carries no score_std "
        "(n_trials must be > 1 for the platform to have a spread to report)")


def _train_score(point: dict) -> float | None:
    """The rule's `S`: the diagnostic side, which side that is decided by the record.

    `INTERFACE.md` §2.6 made a run cover exactly one side, so "the diagnostic side" is
    no longer a fixed field name:

    * a **train run's** point carries `score_kind == "training"`, and there `score` *is*
      the diagnostic score -- the method has read the traces of exactly the tasks it is
      scored on. `train_score` is absent on such a point by design (`driver.py`,
      `_curve_point`: with one task set it would be a copy of `per_task`).
    * a point that carries both sides -- curves recorded before §2.6 -- has `train_score`
      as the diagnostic and `score` as the exam.

    **Never `score` when it is the exam.** That is the leak the split exists to prevent
    (§2.3). The choice is made from `score_kind` rather than by trying one field and
    falling back on the other, because a fallback that reaches the exam is precisely the
    failure this function is here to avoid. `protocol.studied_score` holds the rule once
    for every method; it lives there because six methods had written it six ways.

    What reading the wrong one costs, measured on `loop` + `terminal_bench` + this
    method: `train_scores: [null, null, null]`, `S_star: null`, `rule_evaluated: false`
    -- the rule never fired and every round reported "no edit", which is also exactly
    what a crashed harness reports.
    """
    return protocol.studied_score(point)


def _stall_flag(trajectory: list, t: int, w: int, delta: float) -> int:
    """sigma_t = 1[S_t - S_{t-w} <= delta]; 0 while fewer than w rounds exist.

    `rrsi/history.py:189-193`. `trajectory` is the train-score series indexed by
    RRSI round number (baseline is index 0). A None entry means the platform
    recorded no train score for that round, and the flag is 0 rather than a
    subtraction over a missing number.
    """
    if t < w or t >= len(trajectory) or t - w < 0:
        return 0
    now, before = trajectory[t], trajectory[t - w]
    if now is None or before is None:
        return 0
    return int(now - before <= delta)


def _exploration(t: int, stall: int, tried: set, m_draft: int) -> dict:
    """E_t = (sigma_t, U_t, m_draft) plus the text handed to the proposer.

    `rrsi/history.py:196-211`. `untried` is ordered by K, not by the set, so the
    reserved-slot text is stable across rounds.
    """
    untried = [c for c in K if c not in tried]
    if stall and untried:
        text = (f"STALL: the incumbent has not moved by more than the noise band "
                f"over the last rounds (sigma_t = 1). {m_draft} candidate slot(s) "
                f"this round are RESERVED for exploratory edits on components the "
                f"run has never exercised: {untried}. A variant holding a reserved "
                f"slot must put at least one edit on one of those components.")
    elif untried:
        text = (f"Components not yet exercised in this run: {untried}. Not "
                f"mandatory this round (sigma_t = 0), but evidence about them is "
                f"still missing.")
    else:
        text = "Every component in K has been exercised at least once."
    return {"sigma": stall, "untried": untried, "m_draft": m_draft, "text": text}


def _relative_cost_change(C_cand: float | None,
                          C_inc: float | None) -> tuple[float, bool]:
    """Delta C = (C' - C_t) / C_t, and whether the rule could actually fire.

    `rrsi/evaluate.py:131-135` returns 0.0 when either side has no token count.
    This function returns the same 0.0 but also says whether it was computed from
    real tokens: the caller must not present a constant-zero input as a rule that
    fired (see the module docstring).
    """
    if not C_cand or not C_inc:
        return 0.0, False
    return (C_cand - C_inc) / C_inc, True


def _cost_rule(delta_S: float, delta_C: float, nov: int, delta: float,
               cfg: RRSIConfig) -> tuple[bool, str, str]:
    """c of Algorithm 2, line 5 (`rrsi/selection.py:81-94`).

    Two branches, and the novelty term lives in exactly one of them:
    a real gain must justify its tokens (`Delta C <= beta0 + beta1 Delta S`);
    a change inside the noise band is judged by the shaped score, where `nu` can
    break a tie that the gain and cost alone could not.
    """
    if delta_S > delta:
        budget = cfg.beta0 + cfg.beta1 * delta_S
        ok = delta_C <= budget
        return ok, "gain_above_band", (
            f"gain {delta_S:+.4f} > delta {delta:.4f}; cost change "
            f"{delta_C:+.3f} {'<=' if ok else '>'} budget {budget:.3f} "
            f"(beta0 {cfg.beta0} + beta1 {cfg.beta1} * dS)")
    shaped = cfg.w_s * delta_S - cfg.w_c * delta_C + cfg.w_n * nov
    ok = shaped > 0
    return ok, "within_band", (
        f"gain {delta_S:+.4f} within delta {delta:.4f}; shaped "
        f"{cfg.w_s}*dS - {cfg.w_c}*dC + {cfg.w_n}*nu = {shaped:+.4f} "
        f"{'>' if ok else '<='} 0 (nu={nov})")


def _novelty(components: list[str], incumbent_counts: dict) -> int:
    """nu(l'): structural components the candidate touches that the incumbent has
    never had an accepted edit on (`rrsi/components.py:103-108`).

    `set()` first: two edits on the same untouched component count once, exactly
    as in RRSI.
    """
    return sum(1 for c in set(components)
               if c in K_STR and incumbent_counts.get(c, 0) == 0)


def _judge(S_cand: float, C_cand: float | None, S_inc: float,
           C_inc: float | None, S_star: float, delta: float,
           components: list[str], counts: dict,
           cfg: RRSIConfig) -> dict:
    """`rrsi/selection.py:97-121`, one candidate.

    Order is RRSI's and it matters: the floor is checked first, and a candidate
    below it is rejected without the cost rule being consulted at all. When the
    floor *does* pass but the platform reported no token usage, the cost rule is
    left unevaluated (`admissible: null`, `cost_rule_active: false`) instead of
    being handed a substituted 0.0.
    """
    floor = S_star - delta
    delta_S = S_cand - S_inc
    delta_C, cost_available = _relative_cost_change(C_cand, C_inc)
    nov = _novelty(components, counts)
    out = {
        "S": S_cand, "C": C_cand, "delta_S": delta_S, "delta_C": delta_C,
        "novelty": nov, "S_star": S_star, "delta": delta, "floor": floor,
        "components": list(components), "cost_inputs_available": cost_available,
        "cost_rule_active": False, "branch": None, "admissible": None,
        "stage": "floor", "reason": "",
    }
    if S_cand < floor:
        out["admissible"] = False
        out["stage"] = "floor"
        out["reason"] = (f"below noise-adjusted floor: S' {S_cand:.4f} < "
                         f"S* {S_star:.4f} - delta {delta:.4f}")
        return out
    if not cost_available:
        out["stage"] = "cost_skipped"
        out["reason"] = (
            "floor cleared; the cost rule did NOT fire because the platform "
            "reported no token usage for the incumbent and/or the candidate "
            "(cost_rule_active=false). RRSI itself substitutes Delta C = 0 "
            "(rrsi/evaluate.py:131-135), which would permit everything; this "
            "record does not present that as a verdict.")
        return out
    ok, branch, why = _cost_rule(delta_S, delta_C, nov, delta, cfg)
    out["cost_rule_active"] = True
    out["branch"] = branch
    out["admissible"] = bool(ok)
    out["stage"] = "admissible" if ok else "cost"
    out["reason"] = ("admissible: " if ok else "cost rule failed: ") + why
    return out


# ------------------------------------------- 从曲线点重建 RRSI 的历史 ---

def _point_components(point: dict) -> list[str]:
    """Which components round `point` touched.

    This platform's curve points do not carry RRSI's per-edit records, so this
    method writes the tags it computed into `method_reported.rrsi_rule` and reads
    them back next round. Points produced by another method carry none, and an
    untagged history is empty rather than guessed -- an invented `T_t` would make
    the novelty term and the reserve slot both meaningless.
    """
    rule = (point.get("method_reported") or {}).get("rrsi_rule") or {}
    comps = rule.get("touched_components")
    if not isinstance(comps, list):
        return []
    return [str(c) for c in comps if str(c) in K]


def _edit_history(history: list[dict]) -> tuple[set, dict, dict, dict]:
    """Reconstruct (T_t, incumbent counts, components-by-round, Delta S-by-round).

    Two facts mode A imposes and this reconstruction states rather than hides:

    * **Every measured round is an accepted edit.** Mode A accepts unconditionally,
      so a component touched by any round counts as machinery in the incumbent.
      RRSI's `T_t` only contains *measured* edits and its accepted counts only
      *accepted* ones (`rrsi/history.py:117-141`); here they coincide, which makes
      both an upper bound relative to an RRSI run that rejected candidates.
    * **Delta S is the paired train-score difference.** Round i built on round i-1,
      so `S_i - S_{i-1}` on the train side is the same quantity RRSI records as
      `ev.S - incumbent.S`.
    """
    tried: set = set()
    counts = {c: 0 for c in K}
    comps_by_round: dict = {}
    ds_by_round: dict = {}
    prev: float | None = None
    for point in history:
        r = point.get("round", 0)
        comps = _point_components(point)
        comps_by_round[r] = comps
        for c in comps:
            tried.add(c)
            counts[c] += 1
        score = _train_score(point)
        if score is not None:
            if prev is not None:
                ds_by_round[r] = score - prev
            prev = score
    return tried, counts, comps_by_round, ds_by_round


def _prune_set(t: int, n_prune: int, tried: set, comps_by_round: dict,
               ds_by_round: dict, counts: dict) -> list[dict]:
    """B_t = {l in T_t : g_t(l) <= 0} with
    g_t(l) = max{Delta S_i : l_i = l, t - t_i <= n_prune} (`rrsi/history.py:143-163`).

    RRSI initialises `g` to -inf for tried components with no measurement inside
    the window and keeps those in the prune set (`recent_best_gain: null`); that
    behaviour is kept, because "tried, recently unmeasured" is exactly the case a
    proposer should revisit. `accepted_edits_in_incumbent` is RRSI's list of edit
    records; here it is the count, because the platform's points do not carry the
    individual records.
    """
    g: dict = {c: -math.inf for c in tried}
    for r, comps in comps_by_round.items():
        if r not in ds_by_round or t - r > n_prune:
            continue
        for c in comps:
            if c in g:
                g[c] = max(g[c], float(ds_by_round[r]))
    out = []
    for c in sorted(g):
        if g[c] <= 0:
            out.append({
                "component": c,
                "recent_best_gain": (None if g[c] == -math.inf
                                     else round(g[c], 5)),
                "accepted_edits_in_incumbent": counts.get(c, 0),
            })
    return out


def _harness_tokens(point: dict) -> float | None:
    """The platform's own count of what the harness spent, in tokens.

    `cost.harness_tokens` is None until a task reports usage, and
    `harness_tokens_reported_by_tasks` says how many did -- both are read, because
    "zero tokens" and "no token accounting" are different facts and the cost rule
    must not read the first when the second is true.
    """
    cost = point.get("cost") or {}
    reported = cost.get("harness_tokens_reported_by_tasks") or 0
    tokens = cost.get("harness_tokens")
    if not reported or tokens is None:
        return None
    return float(tokens)


def _adjudicate_incumbent(history: list[dict], cfg: RRSIConfig,
                          delta: float) -> dict:
    """What RRSI's rule would have said about the round mode A already accepted.

    This is the re-adjudication the module docstring describes: the incumbent
    (`history[-1]`) is judged as the candidate it was, against the state it replaced
    and against the running max *at that time*. Mode A already advanced past it, so
    the verdict is a record, never a veto.
    """
    if len(history) < 2:
        return {"stage": "not_evaluated", "admissible": None,
                "cost_rule_active": False,
                "reason": "no previous round to judge; the incumbent is the baseline"}
    prev, inc = history[-2], history[-1]
    S_inc, S_cand = _train_score(prev), _train_score(inc)
    if S_inc is None or S_cand is None:
        return {"stage": "no_train_signal", "admissible": None,
                "cost_rule_active": False,
                "reason": ("the run declares no train side, so S is unavailable; the "
                           "rule is not evaluated and the eval score is not used "
                           "as a substitute")}
    prior = [s for s in (_train_score(p) for p in history[:-1]) if s is not None]
    S_star_then = max(prior) if prior else S_inc
    counts_before = _edit_history(history[:-1])[1]
    decision = _judge(S_cand, _harness_tokens(inc), S_inc, _harness_tokens(prev),
                      S_star_then, delta, _point_components(inc), counts_before, cfg)
    decision["re_adjudicated"] = True
    decision["note"] = ("mode A accepted this candidate unconditionally; this is "
                        "what rrsi/selection.py:97-121 would have decided")
    return decision


def _retention(history: list[dict], adjudication: dict) -> dict:
    """Translate the existing singleton verdict into a historical round receipt.

    Upstream `rrsi/selection.py:133-137` selects an admissible candidate, or none;
    `rrsi/loop.py:327-340` then keeps H_t when none qualify. Here the measured H'
    is history[-1], and its actual H_t is history[-2]. Do not invent an argmax over
    unrelated historical candidates or claim a verdict when inputs are missing.
    """
    admissible = adjudication.get("admissible")
    if admissible is None:
        return {"incumbent_round": None, "reason": adjudication["reason"]}
    chosen = history[-1] if admissible else history[-2]
    round_no = chosen.get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        return {"incumbent_round": None,
                "reason": "the selected historical state has no valid round number"}
    return {"incumbent_round": round_no,
            "candidate_round": history[-1].get("round"),
            "prior_incumbent_round": history[-2].get("round"),
            "reason": (("retain the admissible historical candidate: " if admissible
                        else "no admissible candidate; retain its prior incumbent: ")
                       + adjudication["reason"]),
            "scope": "delayed singleton re-adjudication; no tree restored"}


# --------------------------------------------- 组件归类(critic 的第一层) ---

def _text_only(diff: str) -> bool:
    """Every changed line is a string literal or a comment (`components.py:61-69`)."""
    changed = [line for line in diff.splitlines()
               if (line.startswith("+") or line.startswith("-"))
               and not line.startswith("+++") and not line.startswith("---")
               and line[1:].strip()]
    if not changed:
        return False
    return all(_STRING_LINE.match(line) or line[1:].lstrip().startswith("#")
               for line in changed)


def _classify_diff(diff: str) -> str:
    """First matching component among the generic signals; default `prompt`
    (`rrsi/components.py:72-79`)."""
    if _text_only(diff):
        return "prompt"
    for component, patterns in GENERIC_SIGNALS:
        if any(re.search(p, diff) for p in patterns):
            return component
    return "prompt"


def _has_evidence(component: str, diff: str) -> bool:
    """Does the diff carry a signal of `component`? (`components.py:82-92`)

    A declared tag is kept only when the diff proves it: a proposer can name a
    skill/memory/tool/subagent edit without shipping one -- or relabel a text edit
    as an untried component to satisfy a reserved exploration slot -- and an
    unverified tag corrupts T_t, U_t and the novelty term.
    """
    for comp, patterns in GENERIC_SIGNALS:
        if comp == component and any(re.search(p, diff) for p in patterns):
            return True
    return False


def _normalize(declared: str | None, diff: str) -> str:
    """A verified declaration, or the component recovered from the diff
    (`rrsi/components.py:95-100`)."""
    d = (declared or "").strip().lower()
    if d in K and _has_evidence(d, diff):
        return d
    return _classify_diff(diff)


# ------------------------------------------------------- critic 的两层 ---

def _critic_precheck(diff: str) -> list[str]:
    """Deterministic layer: RRSI's generic patterns plus this domain's
    (`rrsi/critic.py:105-110`). A hit is a hard rejection, before any model call."""
    hits = []
    for pattern, why in CRITIC_PATTERNS:
        if re.search(pattern, diff):
            hits.append(why)
    return hits


def _critic_review(diff: str, summary: str, components: list[str],
                   attempts: int, base: Path | None = None) -> dict:
    """The pre-evaluation leakage screen (`rrsi/critic.py:113-154`).

    Deterministic first; then a model reads **intent and content, not style**, and
    rejects task-specialization, degeneracy, grader gaming, undeclared bundling,
    runtime memory/skill leakage, or unbounded work. The reply is retried while it
    cannot be reduced to a `verdict`, and an unparseable critic rejects (the safe
    direction) rather than accepting by default.

    Only the diff is shown, so a claim that something is "undefined" or "would
    crash" is out of scope here -- RRSI says so explicitly and this port keeps
    that boundary.
    """
    hard = _critic_precheck(diff)
    if hard:
        return {"verdict": "reject", "reasons": [f"precheck: {h}" for h in hard],
                "risk_notes": [], "model_used": False}
    if not diff.strip():
        return {"verdict": "reject",
                "reasons": [
                    "empty diff: after the proposer/repair phase the candidate was "
                    "byte-identical to the incumbent, so there was nothing for the "
                    "critic to review"],
                "risk_notes": [], "model_used": False}
    payload = (
        f"CANDIDATE SUMMARY: {summary}\n"
        f"TARGETS: the train (diagnostic) tasks this harness is evolved against\n\n"
        f"=== DECLARED EDITS (independent changes in this candidate) ===\n"
        f"{components}\n\n"
        f"=== DIFF ===\n{diff[:120_000]}"
    )
    expect = ("Return JSON only with `verdict` set to `accept` or `reject`, plus "
              "`reasons` and `risk_notes` arrays; do not return prose.")

    def accept(out: dict) -> str:
        if isinstance(out, dict) and out.get("verdict") in ("accept", "reject"):
            return ""
        return ("the critic reply was readable JSON but carried no usable `verdict`: "
                + str(out)[:240])

    # The critic is a second model call and needs the same two-step treatment as the
    # proposer. It used to send the **identical payload** three times; a deterministic
    # formatting failure was therefore three identical failures and a good candidate
    # was dropped for a transport shape rather than for leakage. Feed the failure back
    # through the method's own prompt, exactly as `propose_and_apply` does.
    #
    # **`endpoint_only=True`, and the reason is a measurement.** This call must come back
    # as `{"verdict": ...}`, and a CLI improver (codex) cannot answer that: it answers by
    # editing a tree and printing a paragraph. Left on the default dispatch, the critic was
    # sent to codex, codex edited `.gitignore` and put the verdict JSON in its prose, and
    # `accept` rejected every candidate in all three rounds -- an arm that measured nothing
    # while the curve blamed the proposer. The critic is a review, not an edit: it belongs
    # on the chat endpoint whatever the run resolved for the editing layer.
    out, problems = editor.ask_for_json(
        lambda failure: payload + editor.repair_block(failure, expect=expect),
        system=CRITIC_SYSTEM, base=base, attempts=attempts, endpoint_only=True,
        accept=accept, what="a critic verdict")
    if problems:
        return {"verdict": "reject", "model_used": True,
                "reasons": [f"critic output unusable after {attempts} attempts: "
                            + problems],
                "risk_notes": []}
    out.setdefault("risk_notes", [])
    out["model_used"] = True
    return out


# --------------------------------------------------------- 提示词 ---

#: RRSI's proposer is constrained, not merely informed, by b_t, the reserved slot
#: and B_t (`rrsi/loop.py:454-537`). The same directives are put here, in the
#: method's own framing, so what the model is asked for is the candidate RRSI's
#: rule would have drafted.
RRSI_SYSTEM = """You are the proposal step of RRSI, a harness-evolution method, \
running under a platform that owns the schedule: one candidate per round, measured \
by the platform, accepted unconditionally.

RRSI's per-round directives are computed for you from the TRAIN-side curve only. \
You MUST honour the edit budget and a reserved exploration slot when one is given.

Tag every file you change with the harness component it modifies, from this fixed \
vocabulary: prompt, control_flow, config, output_plumbing, context_mgmt, \
client_tool, skill, memory, subagent. A tag is kept only when the diff proves it; \
otherwise the component is recovered from the diff, so declare honestly.

Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>",
              "component": "<one of the tags above>"}],
   "hypothesis": "<one sentence: what you changed and why>"}

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- Do not hard-code task names, ids, expected answers, or anything that only makes
  sense for one task. The harness is measured on tasks it is evolved against, and
  task-specialization is rejected before evaluation.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
"""


#: The six rejection categories of `rrsi/critic.py:54-102`, kept in the same order
#: and with the same litmus test. Deliberately silent about runtime correctness:
#: RRSI's smoke/compile layer handles that, and the critic sees only the diff.
CRITIC_SYSTEM = """You are a strict reviewer of harness (agent scaffold) code \
changes in an evolution loop. The harness is evolved against the very tasks it is \
measured on, so anti-overfitting review is critical. The change was made by another \
model in response to failure evidence. Catch changes that would be cheating, \
degenerate, or self-destructive.

REJECT if ANY of:

1. LEAKAGE / TASK-SPECIALIZATION: the diff hard-codes knowledge of specific tasks:
   task names or ids, entity/company/case names, task-specific file names, expected
   outputs or answers, magic constants that only make sense for one task, per-task
   templates, or branching on task-identifying features. Litmus test: would this
   change still make sense, and still help, on an unfamiliar task of the same kind?
2. DEGENERATE: effectively a no-op (dead code, unused flag, comment-only) while
   claiming a mechanism, or it deletes/disables an existing safety mechanism
   (context compression, output truncation, error handling, termination guards)
   without a working replacement.
3. GRADER GAMING: it tries to read, detect, reconstruct or influence the verifier /
   judge / rubric at runtime, hard-codes a threshold or reference value that could
   only have come from a grader, or games the output format for the grader rather
   than improving task performance.
4. UNDECLARED BUNDLING: the declared edit list is in the payload. Reject only if the
   diff contains independent changes not covered by ANY declared edit, or one
   declared edit secretly bundles several independent mechanisms.
5. RUNTIME MEMORY / SKILL LEAKAGE: if an edit adds memory/state or a skill
   mechanism, inspect what it PERSISTS or INJECTS at runtime. Entity-free general
   procedures are fine. Reject if a code path can persist or inject task-specific
   runtime data across trials (captured file contents, tool outputs, computed
   answers, task/entity/file names, per-task solutions keyed by task identity).
6. UNBOUNDED WORK: an added check, retry or "keep improving" loop with no give-up
   path, or anything implying finishing should wait for a confirmation that may
   never come.

Otherwise ACCEPT. Review intent and content, not style. Runtime correctness is NOT
your job: you only see the diff, so never speculate that something is undefined or
would crash.

Return STRICT JSON:
{"verdict": "accept" | "reject", "reasons": ["..."], "risk_notes": ["..."]}"""


def build_prompt(base: Path, req: dict, rule: dict) -> str:
    """Everything the contract allows, ordered by RRSI's own round structure.

    Note what is absent: `request["incumbent_score"]` is the **eval** score, and it
    is never put in this prompt. The rule and the prompt read the train side only
    (`_train_score`), because selecting on the exam is the leak the split exists to
    prevent.
    """
    lines = [
        f"Round {req.get('round_index', 1)} (RRSI round index t={rule['t']}).",
        "",
        "RRSI's directives for this round (train side only):",
        f"- annealed edit budget b_t = {rule['budget']}: at most "
        f"{rule['budget']} independent edit(s) in this one candidate.",
        f"- noise band delta = {rule['delta']:.4f} ({rule['delta_source']}).",
        f"- running max train S* = {rule['S_star']}; a candidate must clear "
        f"S* - delta = {rule['next_floor']} to pass the floor.",
    ]
    if rule["reserved"]:
        lines.append(f"- RESERVED EXPLORATION SLOT (sigma_t=1): at least one edit "
                     f"must be on a component the run has never exercised: "
                     f"{rule['untried']}.")
    elif rule["untried"]:
        lines.append(f"- components not yet exercised (not mandatory this round): "
                     f"{rule['untried']}.")
    if rule["prune_set"]:
        prune = ", ".join(
            f"{p['component']} (recent best gain "
            f"{p['recent_best_gain']}, {p['accepted_edits_in_incumbent']} accepted "
            f"edit(s) in the incumbent)" for p in rule["prune_set"])
        lines.append(f"- prune set B_t (non-positive recent yield; RRSI would "
                     f"consider removing this machinery): {prune}.")
    adjudication = rule.get("incumbent_adjudication") or {}
    if adjudication.get("reason"):
        lines.append(f"- the round that produced the current harness: "
                     f"{adjudication['reason']}.")
    lines += [
    ]
    return "\n".join(lines)


def _repair_prompt(prompt: str, objections: list[str], attempt: int,
                   total: int) -> str:
    """A rejection goes back to the proposer, not into the void
    (`rrsi/critic.py:38-40`, `rrsi/loop.py` repair rounds)."""
    return (f"{prompt}\n\n## RRSI's screen rejected the previous proposal "
            f"(repair {attempt}/{total})\n"
            + "\n".join(f"- {o}" for o in objections)
            + "\n\nPropose a corrected candidate that fixes every objection while "
              "keeping the same edit budget. If no repair is possible, reply "
              "{\"no_change\": \"<reason>\"}.")


# --------------------------------------------------------- 候选与筛查 ---

def _candidate_diff(base: Path, dest: Path, files: list[str],
                    limit: int = 120_000) -> str:
    """A unified diff of the written files, for the critic and the classifier.

    RRSI's critic and `classify_diff` read a diff, so one is reconstructed here
    rather than inferred from file names -- a rename or a one-line change carries
    evidence a path cannot.
    """
    chunks = []
    for rel in files:
        old_lines: list[str] = []
        new_lines: list[str] = []
        try:
            old_lines = (base / rel).read_text().splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            old_lines = []
        try:
            new_lines = (dest / rel).read_text().splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            new_lines = []
        chunks.append("".join(difflib.unified_diff(
            old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}")))
    return "\n".join(chunks)[:limit]


def _candidate_components(edits, files: list[str], diff: str) -> list[str]:
    """One normalized component per written file.

    Alignment is by declared path: `apply_edits` drops unusable entries, so the
    declaration list is filtered to the files that actually landed. An entry with
    no declaration is classified from the diff, exactly as RRSI recovers an invalid
    or missing tag (`rrsi/components.py:95-100`).
    """
    written = set(files)
    declared = []
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            rel = str(edit.get("path") or "").lstrip("/")
            if rel in written:
                declared.append(str(edit.get("component") or ""))
    components = [_normalize(d, diff) for d in declared]
    while len(components) < len(files):
        components.append(_classify_diff(diff))
    return components


def _gate(files: list[str], components: list[str], diff: str, rule: dict,
          cfg: RRSIConfig, summary: str,
          base: Path | None = None) -> tuple[list[str], dict]:
    """RRSI's pre-evaluation screen, in its order.

    Budget and reserved slot first (they are proposal-side constraints), then the
    deterministic denylist, then the model review -- and a deterministic hit skips
    the model call entirely, as in `rrsi/critic.py:116-121`.
    """
    objections: list[str] = []
    if len(files) > rule["budget"]:
        objections.append(f"edit budget: {len(files)} file(s) exceed b_t="
                          f"{rule['budget']} (the annealed L0 bound)")
    if rule["reserved"]:
        wanted = set(rule["untried"])
        if not (set(components) & wanted):
            objections.append(
                f"reserved exploration slot: no declared component is one of the "
                f"untried {sorted(wanted)} (sigma_t=1, RRSI reserves a slot)")
    if objections:
        return objections, {"verdict": "reject", "reasons": objections,
                            "risk_notes": [], "model_used": False}
    verdict = _critic_review(diff, summary, components, cfg.critic_attempts, base=base)
    if verdict.get("verdict") != "accept":
        objections += [f"critic: {r}" for r in verdict.get("reasons", [])]
    return objections, verdict


# ----------------------------------------------------------------- 主流程 ---

#: What the platform records as this method's acceptance rule. It says what was
#: transplanted and what this port still leaves to the platform.
_ACCEPTANCE_RULE = (
    "RRSI's rule: admissible iff S' >= S* - delta and the two-branch cost rule "
    "holds (Delta C <= beta0 + beta1 Delta S when the gain exceeds the band, "
    "otherwise w_s Delta S - w_c Delta C + w_n nu > 0). The last measured "
    "candidate is re-adjudicated against its actual predecessor, and "
    "incumbent_round records which of those historical rounds the rule retains. "
    "This delayed receipt neither judges the new unmeasured candidate nor restores "
    "a historical tree; the platform's measurement decides the score.")


def _report(req: dict, *, method_reported: dict, **kwargs) -> int:
    """Use the existing step serializer for retention, without changing editor.py.

    `report` has no incumbent_round argument; `report_many` supports it even for a
    singleton. Keep the same candidate, step metadata and stop behavior on every exit.
    """
    retention = method_reported["rrsi_rule"]["retention"]
    if retention["incumbent_round"] is None:
        return editor.report(req, method_reported=method_reported, **kwargs)
    step = {key: kwargs[key] for key in
            ("harness_dir", "changed", "files", "hypothesis", "edit_kind", "label")
            if key in kwargs}
    step.update(method_reported=method_reported,
                incumbent_round=retention["incumbent_round"])
    return editor.report_many(
        req, method=kwargs["method"], steps=[step],
        acceptance_rule=kwargs.get("acceptance_rule", ""),
        extra=kwargs.get("extra"), stop=kwargs.get("stop"),
        hypothesis=kwargs.get("hypothesis", ""))


def _fail(req: dict, *, exc: Exception | str, base: Path, rule: dict) -> int:
    """Preserve the already computed historical receipt on proposal failures."""
    why = exc if isinstance(exc, str) else f"{type(exc).__name__}: {str(exc)[:300]}"
    print(f"rrsi: {why}", file=sys.stderr)
    return _report(req, method="rrsi", harness_dir=base, changed=False, stop=False,
                   hypothesis=why, edit_kind="none", label="method error",
                   method_reported={"error": why, "rrsi_rule": rule},
                   acceptance_rule=_ACCEPTANCE_RULE)


def _rule_report(history: list[dict], t: int, cfg: RRSIConfig) -> dict:
    """Every number RRSI computes before drafting: S*, delta, b_t, sigma_t, U_t, B_t.

    Also the re-adjudication of the latest measured candidate and its retention
    receipt. This port does not see a new candidate's score before submitting it.
    """
    series = [_train_score(p) for p in history]
    scores = [s for s in series if s is not None]
    S_star = max(scores) if scores else None
    delta, delta_source = _delta(history, cfg)
    tried, counts, comps_by_round, ds_by_round = _edit_history(history)
    stall = _stall_flag(series, t, cfg.w, delta)
    # This port drafts one candidate (m=1), so at most one slot can be reserved; an
    # env value above 1 is capped rather than silently reserving slots that do not
    # exist.
    m_draft = min(max(cfg.m_draft, 0), 1)
    explore = _exploration(t, stall, tried, m_draft)
    reserved = bool(stall and explore["untried"] and m_draft >= 1)
    prune = _prune_set(t, cfg.n_prune, tried, comps_by_round, ds_by_round, counts)
    adjudication = _adjudicate_incumbent(history, cfg, delta)
    return {
        "rule": ("RRSI Algorithm 2: floor S' >= S* - delta, then the two-branch "
                 "L1 cost rule (w_s dS - w_c dC + w_n nu inside the band)"),
        "t": t,
        "rounds_seen": [p.get("round") for p in history],
        "train_scores": series,
        "score_source": ("the side this run studies, chosen by `score_kind` "
                          "(protocol.studied_score); never the exam when `score` "
                          "is the exam"),
        "S_star": S_star,
        "delta": delta,
        "delta_source": delta_source,
        "delta_z": cfg.delta_z,
        "next_floor": (None if S_star is None else S_star - delta),
        "budget": _edit_budget(t, cfg.T, cfg.b_min, cfg.b_max),
        "budget_table": _budget_table(cfg.T, cfg.b_min, cfg.b_max),
        "T": cfg.T, "b_min": cfg.b_min, "b_max": cfg.b_max,
        "stall": stall, "stall_window": cfg.w,
        "tried": sorted(tried),
        "untried": explore["untried"],
        "reserved": reserved,
        "exploration_text": explore["text"],
        "prune_set": prune,
        "incumbent_counts": counts,
        "incumbent_adjudication": adjudication,
        "retention": _retention(history, adjudication),
        # The one cost verdict this round can produce is the re-adjudication's.
        "cost_rule_active": bool(adjudication.get("cost_rule_active")),
        "rule_evaluated": S_star is not None,
        "not_expressible_in_mode_a": [
            "same-call selection after measuring a submitted batch: the method "
            "sees feedback only on its next invocation",
        ],
        "not_implemented_in_port": [
            "drafting and grouping m screened candidates from one incumbent",
            "restoring the selected historical tree from states/ before drafting",
            "upstream repeated-base calibration (the port adapts score_std instead)",
        ],
    }


def main() -> int:
    req = editor.read_request()
    editor.require_api(req)

    base = Path(req["base_harness"])
    history = editor.load_history(base)
    cfg = RRSIConfig.from_env()
    # RRSI's round index t is 0-based over its own trajectory, whose index 0 is the
    # baseline. Mode A's `round_index` starts at 1 and its `history` already holds
    # points 0..t, so t = round_index - 1 is the round being drafted.
    try:
        t = int(req.get("round_index", len(history))) - 1
    except (TypeError, ValueError):
        t = len(history) - 1
    rule = _rule_report(history, max(t, 0), cfg)

    dest = editor.candidate_path(req)
    try:
        # **The two steps.** Step 1 drafts an edit sequence; step 2 applies it and
        # checks the result loads, handing any failure back through the prompt. Before
        # this, a sequence that did not apply was indistinguishable from a method
        # deciding to change nothing -- measured: three rounds of "no edit" on
        # `build-pmars`, and a `UnparseableReply` round that the run reported as an
        # ordinary no-change.
        reply, dest, apply_problems = editor.propose_and_apply(
            base, req, RRSI_SYSTEM,
            lambda problems: build_prompt(base, req, rule)
                             + editor.repair_block(problems,
                                                   expect=editor.EDIT_EXPECT))
        if apply_problems:
            return _fail(
                req,
                exc=f"the edit sequence did not apply in 3 attempts: {apply_problems}",
                base=base, rule=rule)
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return _fail(req, exc=exc, base=base, rule=rule)

    if isinstance(reply, dict) and "no_change" in reply:
        editor.apply_edits(base, dest, [])
        return _report(
            req, method="rrsi", harness_dir=dest, changed=False,
            stop=False,
            hypothesis=str(reply["no_change"])[:200], edit_kind="none",
            method_reported={"rrsi_rule": rule},
            acceptance_rule=_ACCEPTANCE_RULE,
            # Not "RRSI rule: no edit". The rule produced a budget and a prompt; the
            # **model** answered `no_change`, and labelling that as the rule's decision
            # sends a reader looking for a bug in the arithmetic. Measured: it did --
            # `RRSI rule: no edit (b_t=4)` was read as the rule declining, while the
            # model had simply said the evidence did not support a change.
            label=f"model: no change (b_t={rule['budget']})")

    repairs = 0
    verdict: dict = {}
    components: list[str] = []
    files: list[str] = []
    diff = ""
    while True:
        edits = reply.get("files") if isinstance(reply, dict) else None
        changed, files, note = editor.apply_edits(base, dest, edits)
        if not changed:
            return _report(
                req, method="rrsi", harness_dir=dest, changed=False,
                stop=False,
                hypothesis=f"unusable proposal: {note}", edit_kind="rrsi_rule",
                method_reported={"rrsi_rule": rule, "rejected": note},
                acceptance_rule=_ACCEPTANCE_RULE,
                label=f"RRSI rule: unusable proposal (b_t={rule['budget']})")

        diff = _candidate_diff(base, dest, files)
        components = _candidate_components(edits, files, diff)
        summary = str(reply.get("hypothesis", ""))[:300]
        objections, verdict = _gate(files, components, diff, rule, cfg, summary, base)
        if not objections:
            break
        if repairs >= cfg.repair_rounds:
            rule = {**rule, "critic": verdict, "touched_components": [],
                    "repairs": repairs, "dropped": objections}
            return _report(
                req, method="rrsi", harness_dir=base, changed=False,
                stop=False,
                hypothesis=("RRSI's screen dropped the candidate: "
                            + "; ".join(objections))[:300],
                edit_kind="rrsi_rule",
                method_reported={"rrsi_rule": rule},
                acceptance_rule=_ACCEPTANCE_RULE,
                label=f"RRSI rule: candidate dropped (b_t={rule['budget']})")
        repairs += 1
        try:
            reply = editor.ask(
                _repair_prompt(build_prompt(base, req, rule), objections,
                               repairs, cfg.repair_rounds),
                system=RRSI_SYSTEM, base=base)
        except SystemExit:
            raise
        except Exception as exc:                               # noqa: BLE001
            return _fail(req, exc=exc, base=base, rule=rule)
        if isinstance(reply, dict) and "no_change" in reply:
            rule = {**rule, "critic": verdict, "touched_components": [],
                    "repairs": repairs, "dropped": objections}
            editor.apply_edits(base, dest, [])
            return _report(
                req, method="rrsi", harness_dir=dest, changed=False,
                stop=False,
                hypothesis=(f"repair {repairs} produced no candidate; dropped: "
                            + "; ".join(objections))[:300],
                # This one really is the method's own rule rejecting every candidate:
                # the model proposed, the critic refused, repairs were exhausted. The
                # label says so, because the difference between "I declined" and "my
                # reviewer rejected everything I drafted" is the difference between a
                # prompt problem and a rule problem.
                edit_kind="rrsi_rule",
                method_reported={"rrsi_rule": rule},
                acceptance_rule=_ACCEPTANCE_RULE,
                label=f"RRSI rule: dropped after {repairs} repair(s)")

    out_rule = {**rule, "touched_components": components, "repairs": repairs,
                "critic": verdict, "edit_budget_used": len(files)}
    hypothesis = str(reply.get("hypothesis", ""))[:300]
    return _report(
        req, method="rrsi", harness_dir=dest, changed=True, files=files,
        hypothesis=hypothesis,
        # RRSI tags each accepted edit with a component from K
        # (`rrsi/components.py:44-46`); the platform records one free-form string,
        # so the tags are joined rather than reduced to a single one.
        edit_kind=",".join(components) or "rrsi_rule",
        method_reported={"rrsi_rule": out_rule},
        acceptance_rule=_ACCEPTANCE_RULE,
        extra={"selection_rule": out_rule["rule"],
               "cost_rule_active": out_rule["cost_rule_active"]},
        label=(f"[RRSI b_t={out_rule['budget']} "
               f"{'stall' if out_rule['stall'] else 't' + str(out_rule['t'])}] "
               + hypothesis[:60]))


if __name__ == "__main__":
    raise SystemExit(main())
