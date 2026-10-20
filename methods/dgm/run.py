#!/usr/bin/env python3
"""DGM's archive rule, as a HarnessGrad method.

Why this file exists
--------------------
DGM's contribution is not the edit -- a coding agent makes that -- it is **the
archive and how a parent is drawn from it**. From its own loop (`DGM_outer.py`):

    score_child_prop:  p(c) ∝ sigmoid(10*(score_c - 0.5)) * 1/(1 + children_c)

A commit's weight rises with its score and falls with how many children it has
already produced, so the search is pushed away from a single lucky ancestor. The
archive keeps every variant and names its `parent_commit`, which is what makes
"children" a computable quantity at all.

What is transplanted
--------------------
* The parent-selection distribution above (`DGM_outer.py:91-100`), with one task
  set in place of a domain list, so a candidate's score is that round's score.
* The child-count penalty `1/(1+children)` (`DGM_outer.py:70-72` defines the
  count; `:91-100` applies it).
* The retention gate `keep_better`: a variant enters the archive iff
  `score >= initial_score - noise_leeway` (`DGM_outer.py:178-185`).
* The shape of entry selection -- pick *which failure to target* before editing
  (`DGM_outer.py:111-148`), rather than editing blind.

What is not transplanted
------------------------
DGM's experiment scaffolding: Docker containers, git-commit-per-variant with
chained `model_patch.diff` replay, the SWE-bench/Polyglot harnesses, and the o1
diagnosis pipeline. The platform supplies all of it -- a task set, execution,
scoring and a record of every round -- so reimplementing it here would be
reimplementing the platform.

What is not reproduced, and two findings worth carrying
-------------------------------------------------------
1. **`--update_archive` defaults to `keep_all`, not `keep_better`**
   (`DGM_outer.py:232`). Our own read-only adapter states the opposite
   (`legacy/adapters/dgm.py:145-149`). With `keep_all` the archive is not a filter at
   all: every compiled variant enters, and the only thing that ever leaves the
   parent pool is a commit whose metadata will not load. Both are implemented
   here and the choice is recorded, because a run that silently picks one and
   claims the other is a false claim about someone else's method.
2. **`best` mode is inverted in the shipped code.** `sorted(candidates, key=score)`
   followed by `[:k]` takes the *ascending* sort, i.e. the worst k
   (`DGM_outer.py:101-106`). We do not port that branch.
3. **DGM's strongest input does not exist here.** Its improvement prompt carries
   the gold patch, the private test patch and the test log
   (`prompts/self_improvement_prompt.py:95-106`). The platform's traces exclude
   expected answers on purpose, so this port is **DGM minus its most informative
   signal**, and the copy of DGM we can run is materially weaker than the
   published one. Saying so is part of porting it honestly.

The `noise_leeway` default is 0.1, matching `DGM_outer.py:238`, and is configured
through `HG_DGM_NOISE_LEEWAY`. It sets the initial-score-minus-leeway floor only
under `keep_better`; the default `keep_all` bypasses it (`DGM_outer.py:174-188`).
No task-set-specific statistical calibration is implemented here.

Batch submission and rejection
-----------------------------
Upstream `DGM_outer.py:90-100` samples parents with replacement and `:111-148`
chooses an entry per parent. Here each child samples a parent and chooses a target,
then usable edits are generated sequentially and submitted via `editor.report_many`.
`HG_DGM_CHILDREN` defaults to 3, a port configuration choice differing from upstream
`--selfimprove_size` default 2 (`DGM_outer.py:224`); the remaining budget caps it.
There is no post-measurement score-based rejection or `incumbent_round` rollback.
Archive retention controls future parents; no-change edits and invalid parents
are still filtered before submission.
"""
from __future__ import annotations

import json
import importlib.util
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor                                                  # noqa: E402
import protocol                                                # noqa: E402


def _load_component(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).with_name(filename))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load DGM component {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Kept method-local: DGM's archive and staged schedule are not a shared algorithm.
DGM_STAGED = _load_component("staged_eval.py", "harnessgrad_dgm_staged_eval")

#: DGM's absolute `--eval_noise` default (`DGM_outer.py:238`), configurable here.
#: Applied only to `keep_better`; `keep_all` ignores this score floor.
NOISE_LEEWAY = float(os.environ.get("HG_DGM_NOISE_LEEWAY", "0.1"))

#: `keep_better` or `keep_all`. Default matches the shipped CLI default
#: (`DGM_outer.py:232`), not the adapter's description of it.
RETAIN = os.environ.get("HG_DGM_RETAIN", "keep_all")

#: The logistic's steepness and centre (`DGM_outer.py:91-100`).
STEEP, CENTRE = 10.0, 0.5

DGM_SYSTEM = """You are improving an agent harness, acting as DGM's self-modification step.

You will be shown one harness and the record of running it on a set of tasks, plus a
specific failure the archive has chosen to target this round.

Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>"}],
   "hypothesis": "<one sentence: what you changed and why>"}

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- Generalise: a change that only helps the task you were shown is not an improvement.
  Never hardcode a task's literal value.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
"""


def _score(point: dict) -> float:
    """The number the archive ranks on.

    Never the exam: ranking parents on `score` when `score` is the exam is the leak the
    split exists to prevent (`INTERFACE.md` §2.3). Which field holds the diagnostic
    score is decided by `score_kind` (§2.6) and written once in
    `protocol.studied_score` -- this used to try `train_score` and fall back to `score`,
    which gave the right number for the wrong reason and would read the exam on any
    point where `score` is the exam.
    """
    value = protocol.studied_score(point)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def _studied_score(point: dict) -> float | None:
    """Return only the score visible to the method, rejecting NaN/inf."""
    value = protocol.studied_score(point)
    return (float(value) if isinstance(value, (int, float)) and math.isfinite(value)
            else None)


def _lineage(point: dict) -> tuple:
    reported = point.get("method_reported") or {}
    refs = reported.get("dgm_parents")
    if refs is None:
        ref = reported.get("dgm_parent")
        refs = [] if ref is None else [ref]
    return tuple(refs) if isinstance(refs, list) else (refs,)


def _validated_history(history: list[dict]) -> tuple[list[dict], list[str]]:
    """Filter malformed history before it can affect archive weights.

    The source archive is keyed by unique commits.  A duplicate round, a future
    parent reference, or a non-finite studied score is rejected as an invalid
    archive record rather than collapsing a weight dictionary or sampling NaN.
    """
    valid, reasons, seen = [], [], set()
    for point in history:
        rid = point.get("round")
        if rid in seen:
            reasons.append(f"duplicate node id {rid!r}")
            continue
        score = _studied_score(point)
        if score is None:
            reasons.append(f"node {rid!r} has no finite studied score")
            continue
        refs = _lineage(point)
        if any(ref not in seen for ref in refs):
            reasons.append(f"node {rid!r} has an inconsistent parent reference")
            continue
        seen.add(rid)
        valid.append(point)
    return valid, reasons


def _sigmoid(x: float) -> float:
    # Clamped: `10*(score-0.5)` overflows for |x| beyond ~700, and a score is not
    # bounded to [0,1] in principle, so the guard is not cosmetic.
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + pow(2.718281828459045, -x))


def _initial_score(history: list[dict]) -> float:
    for point in history:
        refs = _lineage(point)
        if point.get("is_root") is True or point.get("role") == "root" or (
                point.get("round") == 0 and not refs):
            return _studied_score(point)
    return None


def _archive(history: list[dict]) -> list[dict]:
    """What may be a parent, under the chosen retention rule.

    `keep_all` keeps every round with a usable number. `keep_better` keeps only
    those within `NOISE_LEEWAY` of the *initial* score -- DGM's gate compares
    against the original, never against the best archive member
    (`DGM_outer.py:178-185`), so an underperforming child inside the leeway stays
    in the pool forever.
    """
    usable, _ = _validated_history(history)
    if RETAIN == "keep_all":
        return usable
    initial = _initial_score(usable)
    if initial is None:
        return usable
    floor = initial - NOISE_LEEWAY
    return [p for p in usable if _studied_score(p) is not None and _score(p) >= floor]


def _eligible_archive(history: list[dict]) -> tuple[list[dict], list[str]]:
    archive = _archive(history)
    eligible, reasons = [], []
    for point in archive:
        reported = point.get("method_reported") or {}
        if point.get("measured_by_platform") is False:
            reasons.append(f"round {point.get('round')} is not platform-measured")
        elif reported.get("rejected") or reported.get("platform_safety_ineligible"):
            reasons.append(f"round {point.get('round')} is platform-safety-ineligible")
        elif reported.get("compiled") is False:
            reasons.append(f"round {point.get('round')} did not compile")
        else:
            eligible.append(point)
    return eligible, reasons


def _child_counts(history: list[dict]) -> dict[int, int]:
    """How many later rounds declared each round as their parent.

    The platform's mode A is a straight chain, so this count would be a constant
    1 for every round but the last and the penalty would be inert. It is not
    inert because *this method declares its own parent* in
    `method_reported.dgm_parent`, and `method_reported` round-trips into the next
    round's history -- so the count measures the lineage this method actually
    built, including the rounds where it chose to branch backwards. That is also
    why the declaration has to be recorded even when the round changes nothing.
    """
    counts: dict[int, int] = {}
    for point in history:
        declared = (point.get("method_reported") or {}).get("dgm_parent")
        if isinstance(declared, int):
            counts[declared] = counts.get(declared, 0) + 1
    return counts


def select_parent(history: list[dict], round_index: int) -> tuple[dict, dict]:
    """The rule. Returns (chosen point, a report of the whole table).

    Sampled rather than argmax'd, because that is what `random.choices(...,
    weights=...)` does (`DGM_outer.py:91-100`) -- taking the argmax would silently
    turn a diversification rule into a greedy one, which is the difference the
    child-count term exists to make.
    """
    archive, safety_exclusions = _eligible_archive(history)
    if not archive:
        return {}, {"archive": [], "rule": "empty", "reason": "no eligible parent",
                    "platform_safety_exclusions": safety_exclusions}

    children = _child_counts(history)
    weights = {}
    for point in archive:
        round_no = point.get("round", 0)
        weights[round_no] = (_sigmoid(STEEP * (_score(point) - CENTRE))
                             * 1.0 / (1.0 + children.get(round_no, 0)))
    total = sum(weights.values())
    if total <= 0:
        # Every candidate is at the logistic's floor. Uniform is what DGM falls
        # back to when the weights do not normalize (`DGM_outer.py:107-109`).
        weights = {r: 1.0 for r in weights}
        total = float(len(weights))

    rng = random.Random(round_index * 7919)          # recorded seed; see INTERFACE.md
    pick = rng.random() * total
    chosen, running = archive[-1], 0.0
    for point in archive:
        running += weights[point.get("round", 0)]
        if pick <= running:
            chosen = point
            break

    best = max(archive, key=_score)
    report = {
        "rule": "DGM score_child_prop: sigmoid(10*(s-0.5)) / (1+children)",
        "retain": RETAIN,
        "noise_leeway": NOISE_LEEWAY,
        "archive": [p.get("round") for p in archive],
        "child_counts": {str(k): v for k, v in sorted(children.items())},
        "weights": {str(r): round(w, 6) for r, w in sorted(weights.items())},
        "selected_parent": chosen.get("round"),
        "best_round": best.get("round"),
        "chose_something_other_than_best": chosen.get("round") != best.get("round"),
        "seed": round_index * 7919,
        "platform_safety_exclusions": safety_exclusions,
        "selection": {
            "selected_parent": chosen.get("round"),
            "selected_parent_role": "task_agent_state",
            "deployment_status": "pending_trusted_runtime",
            "deployed_node": None,
        },
    }
    return chosen, report


def choose_target(parent: dict) -> tuple[str, str]:
    """Which failure this round targets -- the shape of `DGM_outer.py:111-148`.

    DGM chooses between four concrete entries: `solve_empty_patches` (>=10% of
    tasks produced no patch), `solve_stochasticity`, `solve_contextlength`, and
    otherwise a uniformly random unresolved task. The last three are read out of
    SWE-bench's logs and **have no counterpart in a trace that carries no
    expected answer** -- so what is ported is the shape (decide the target before
    editing, from the parent's per-task record) and not DGM's specific entries.

    The record read here is the **train** side. The platform withholds the eval
    side's per-task breakdown from the channel on purpose: a method that can see
    which exam questions are failing can iterate against those specific tasks. Its
    aggregate score is still reported, because a method with no reward signal at
    all is a random walk, not a fairer experiment.
    """
    per_task = protocol.studied_per_task(parent)
    if not per_task:
        return "no per-task record on the parent; edit from the trace alone", "unspecified"
    failing = [tid for tid, s in sorted(per_task.items()) if not s]
    if not failing:
        return "the parent passes every task it is shown", "none"
    share = len(failing) / max(len(per_task), 1)
    if share >= 0.5:
        return (f"{len(failing)}/{len(per_task)} tasks fail -- this is the broad "
                f"failure mode, not a corner case"), "broad-failure"
    return (f"target the {len(failing)} failing task(s): "
            f"{', '.join(failing[:6])}"), "narrow-failure"


def build_prompt(base: Path, req: dict, parent: dict, target: str,
                 report: dict) -> str:
    return (
        f"DGM round {req.get('round_index', 1)}. "
        f"The archive chose round {parent.get('round')} as this round's parent "
        f"(score {_score(parent):.3f} on the tasks you can see; "
        f"the best archive member is round {report.get('best_round')}).\n\n"
        f"## What to target\n{target}"
    )


def main() -> int:
    """**A B-shaped submission: one call, `k` children from sampled parents.**

    DGM's rule is an archive-wide search, not a per-round step: parent weights are
    `sigmoid(10*(score-0.5)) / (1+children)` and it draws children from sampled parents.
    Our port was written one-candidate-per-round to fit mode A, which meant the rule ran
    exactly one draw per round and the archive could never do its job -- the child-count
    term needs siblings, and it never had any.

    Now the submission is the algorithm: sample `HG_DGM_CHILDREN` parents (default 3, from
    the same distribution), produce one candidate per parent, and hand the batch over in
    one call. Every one of them is measured; the platform's budget caps how many states a
    run pays for, and the archive's `keep_better` gate lives in the history each round.
    """
    import os

    req = editor.read_request()
    editor.require_api(req)
    base = Path(req["base_harness"])
    history = editor.load_history(base)
    round_index = int(req.get("round_index", 1))
    children = max(1, int(os.environ.get("HG_DGM_CHILDREN", "3")))
    # The budget knows how many states this run still buys; never submit more than that,
    # because the platform truncates and a truncated batch is a batch the method planned
    # wrong.
    remaining = int(req.get("epochs_remaining") or req.get("epochs_total") or children)
    children = max(1, min(children, remaining))

    steps: list[dict] = []
    reports: list[dict] = []
    errors: list[BaseException] = []
    for child in range(children):
        parent, report = select_parent(history, round_index + child)
        if not parent:
            reports.append({**report, "child": child + 1,
                            "children_this_call": children,
                            "why": "no eligible parent; no edit submitted"})
            continue
        target, target_kind = choose_target(parent)
        report["target"] = target
        report["target_kind"] = target_kind
        report["child"] = child + 1
        report["children_this_call"] = children
        report["parent_round"] = parent.get("round")
        report["staged_eval"] = DGM_STAGED.DGMStagedScheduler().budget_plan(
            [], f"r{parent.get('round')}"
        )

        parent_dir = _state_dir(base, parent.get("round"))
        if not parent_dir or not Path(parent_dir).is_dir():
            reports.append({**report, "why": "the selected state is not staged"})
            continue

        dest = editor.candidate_path(req, f"child{child + 1}")
        try:
            reply = editor.ask(build_prompt(Path(parent_dir), req, parent, target, report),
                               system=DGM_SYSTEM, base=base, parent=Path(parent_dir))
        except SystemExit:
            raise
        except Exception as exc:                                       # noqa: BLE001
            # A provider error is not "this parent had nothing to say": it is the call not
            # running at all. Timestamps kept in `errors` so that a call where *every* draw
            # errored is reported through `editor.fail` (which writes `step.error` and
            # `stop: false`), which is what the record needs to distinguish an outage from a
            # method with nothing to propose.
            errors.append(exc)
            reports.append({**report, "why": f"{type(exc).__name__}: {exc}"[:160]})
            continue

        edits = [] if "no_change" in reply else reply.get("files")
        changed, files, note = editor.apply_edits(Path(parent_dir), dest, edits)
        if "no_change" in reply:
            hypothesis = str(reply["no_change"])[:200]
        elif not changed:
            hypothesis = f"unusable proposal: {note}"
        else:
            hypothesis = str(reply.get("hypothesis", ""))[:200]
        reports.append({**report, "changed": bool(changed), "why": hypothesis})
        steps.append({
            "harness_dir": dest, "changed": changed, "files": files,
            "label": ((f"[DGM parent r{parent.get('round')}] " + hypothesis[:60])
                      if changed else f"DGM: no edit (parent r{parent.get('round')})"),
            "edit_kind": "dgm_rule", "hypothesis": hypothesis,
            "method_reported": {
                "dgm": report, "dgm_parent": parent.get("round"),
                "target": target, "child": child + 1,
                "artifact": {
                    "artifact_id": f"dgm-r{round_index}-child{child + 1}",
                    "parent_refs": [f"round-{parent.get('round')}"],
                    "immutable": True, "patch_kind": "tree",
                },
                "role_refs": {
                    "diagnosis_agent": {
                        "declared_upstream_role": "diagnosis",
                        "declared_upstream_model": "o1-2024-12-17",
                        "status": "not_invoked",
                        "artifact_ref": f"diagnosis-r{round_index}-child{child + 1}",
                    },
                    "editor_agent": {
                        "declared_upstream_role": "task_and_harness_edit",
                        "declared_upstream_model": "claude-3-5-sonnet-20241022",
                        "actual_execution": {
                            "invoked": True,
                            "method_model": editor.spent().get("model"),
                        },
                        "artifact_ref": f"dgm-r{round_index}-child{child + 1}",
                    },
                },
                "selection": {
                    "selected_parent": parent.get("round"),
                    "selected_parent_ref": f"round-{parent.get('round')}",
                },
                "deployment": {
                    "deployed_node": None,
                    "status": "pending_trusted_runtime",
                    "reason": "method can nominate a child; trusted runtime deploys it",
                },
                "fidelity_deviation": {
                    "diagnostic_gold_input": "withheld_by_platform",
                    "diagnostic_private_test_patch": "withheld_by_platform",
                },
                "valid_parent": True,
                "evaluation_success": True,
            },
        })

    # **Only the children that produced an edit are submitted.** A step that changed
    # nothing is a measurement of a state the platform has already measured, and a batch of
    # them would spend the whole budget saying "no change" k times. The draw as a whole is
    # still recorded (`method_reported.dgm`), so a reader can see how many parents were
    # sampled and why each one came back empty.
    measured = [step for step in steps if step.get("changed")]
    if not measured:
        # **Every draw came back empty.** Two different facts, and the record must not
        # merge them: a provider error is the call not running, while `no_change` is the
        # model declining. The lineage goes into `method_reported` on *every* path -- the
        # child-count term reads `dgm_parent` from there, and dropping it on the empty path
        # would reset a method's own memory exactly when it is drawing again.
        parents = [r.get("parent_round") for r in reports]
        carried = {"dgm": reports, "dgm_parent": parents[0] if parents else None,
                   "dgm_parents": parents,
                   "children_this_call": children,
                   "selection": {"selected_parent_refs": [f"round-{p}" for p in parents if p is not None]},
                   "deployment": {"deployed_node": None, "status": "pending_trusted_runtime"},
                   "role_refs": {"diagnosis_agent": {"declared_upstream_model": "o1-2024-12-17",
                                                        "status": "not_invoked"},
                                 "editor_agent": {"declared_upstream_model": "claude-3-5-sonnet-20241022",
                                                   "status": "platform_editor",
                                                   "actual_method_model": editor.spent().get("model")}}}
        if errors:
            return editor.fail(req, method="dgm", exc=errors[0], base=base,
                               state=carried)
        reasons = []
        for report in reports:
            why = str(report.get("why") or "")
            if why and why not in reasons:
                reasons.append(why)
        return editor.report(
            req, method="dgm", harness_dir=base, changed=False,
            hypothesis=(f"{len(steps)} child(ren) drew no usable edit this call: "
                        + " | ".join(reasons))[:200],
            edit_kind="dgm_rule", method_reported=carried,
            acceptance_rule=("DGM's archive is maintained by keep_better/keep_all inside "
                             "the method; the platform's measurement decides the score"),
            stop=False, label="DGM: no child produced")

    steps = measured
    return editor.report_many(
        req, method="dgm", steps=steps,
        acceptance_rule=("DGM's archive is maintained by keep_better/keep_all inside "
                         "the method; the platform's measurement decides the score"),
        # **A finished batch ends the batch, not the run.** The platform's budget counts
        # measured states (`mode_a.py`: "measured states, not method calls"), and
        # `children = max(1, min(children, remaining))` above exists so one batch tiles
        # the remaining budget. `stop` here therefore says only "this call has nothing
        # more to add"; the macro iteration continues until the budget or a real
        # convergence rule ends it. Measured 2026-10-20: `stop=True` on this path ended the
        # run at its first complete batch (AppWorld r3 of 10; the archive was never reused,
        # every child's `dgm_parent` was r0). The no-child case returns above with
        # `stop: false` for the same reason.
        stop=False,
        note=f"DGM: {len(steps)} child(ren) submitted in one call",
        extra={"children_this_call": len(steps),
               "children_requested": children,
               "partial_batch": bool(errors or len(measured) < children)},
    )


def _state_dir(base: Path, round_no) -> str | None:
    """Where the platform staged a previous round's harness.

    `states/index.json` is read rather than the directory guessed, because a state that
    could not be materialized is deliberately absent from the index -- a method that guessed
    the path would find an empty directory and report a failure the platform caused.
    """
    index = editor.channel(base) / "states" / "index.json"
    if not index.exists():
        return None
    try:
        data = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get(f"round-{round_no}") or {}).get("harness_dir")


if __name__ == "__main__":
    raise SystemExit(main())
