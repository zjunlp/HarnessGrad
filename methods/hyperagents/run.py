#!/usr/bin/env python3
"""HyperAgents' parent-selection rule, as a HarnessGrad method.

Why this file exists
--------------------
HyperAgents keeps an archive and draws a parent from it. From its own code
(`utils/gl_utils.py`):

    mid = mean(top-3 scores)
    score_child_prop:  p(c) ∝ sigmoid(10*(score_c - mid)) * exp(-(children_c/8)^3)

The sigmoid is centred on the *mean of the top three*, not on a fixed 0.5 as DGM
centres it, and the child penalty is a **cube that only starts to bite past about
eight children** rather than DGM's `1/(1+children)`.

The finding this port exists to make checkable
----------------------------------------------
HyperAgents ships **two** selection functions that disagree, and the difference is
not the formula -- it is whether the formula is used at all:

* `utils/gl_utils.py:570-584` (`score_child_prop`, the CLI default,
  `generate_loop.py:1071`) really does sample proportional to
  `sigmoid(10*(s-mid)) * exp(-(c/8)^3)` via `random.choices(..., weights=...)`.
* `select_next_parent.py:49-57` (reached under `--edit_select_parent`) computes
  the same child counts, then ends in `random.choice(list(candidates.keys()))`.
  The counts and scores it just derived are **discarded**, and the line above is
  commented "keeping the search space open".

So the uniform branch is uniform because its weights are dead code, not because
the sigmoid cancels: a normalized sigmoid over candidates is *not* uniform, and a
first reading that says otherwise would turn "one of its two selectors ignores
its own statistics" into a much weaker "HyperAgents samples randomly by design".
This was checked against the source after the stronger claim failed a test, and
the claim that failed is recorded here rather than quietly dropped.

The method therefore records which setting ran and how far that setting's actual
probability vector is from uniform on this run, so the claim is checkable on real
data instead of asserted.

What is transplanted
--------------------
* Parent selection: `mid = mean(top-3)`, the sigmoid around it, the
  `exp(-(children/8)^3)` penalty (`gl_utils.py:570-584`).
* The **validity gate as a retention rule** (`generate_loop.py:706-714`): a node
  is a candidate for parenthood only if its run produced a score and the
  improvement mechanism compiled. Score never enters this gate.

What is not transplanted
------------------------
* **Staged-eval rescaling** (`utils/gl_utils.py:38-44`, `:527`): with no full eval run,
  HyperAgents multiplies both the agent and ensemble scores by the domain's
  staged fraction. This selector reads the platform's scalar studied score;
  `staged_eval.py:30-40` records stage metadata without rescaling, and main passes
  a staged fraction of 1.0.
* **`type="max"` over agent and ensemble scores** (`utils/gl_utils.py:51-57`):
  `archive.py:57-61` implements this helper, but the live selector below uses
  `_score`, not `ArchiveNode.score`, so the helper does not affect parent weights.
* **Multi-domain averaging** (`utils/gl_utils.py:523-530`): the live selector
  reads one `protocol.studied_score` per point, not separate per-domain results.
* Docker, the meta-agent that rewrites its whole repo, and generation-level patch
  replay (`generate_loop.py:502-504`). The edit half here is the shared editor.

Batch and archive limitations
------------------------------
One call can submit multiple children, drawn from the same pre-call history.
Upstream archives each evaluated generation before selecting its next parent
(`generate_loop.py:900-925,1006-1017`); siblings here do not see one another's
scores or update child counts between draws.

All historical round summaries are visible. Snapshot staging is controlled by
`HG_METHOD_HISTORY_ROUNDS=0` (all states by default); a positive N stages only
the newest N states (`harnessgrad/channel.py:395-397,427-449,565-569`;
`harnessgrad/config/areas.py:107-110`). A selected parent whose staged directory
is unavailable is skipped, not resampled. There is no fixed newest-12 cap.

`HG_HYPERAGENTS_SELECT` chooses a fixed local weight branch at import, rather
than running or evolving upstream `select_next_parent.py:49-57`. Unknown values
fall back to score_child_prop weights while retaining the requested setting in
the report; `latest` is not a live-selector branch.
"""
from __future__ import annotations

import math
import importlib.util
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
        raise ImportError(f"cannot load HyperAgents component {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


HYPER_STAGED = _load_component("staged_eval.py", "harnessgrad_hyperagents_staged_eval")
HYPER_ARCHIVE = _load_component("archive.py", "harnessgrad_hyperagents_archive")

#: The child penalty's scale and exponent (`gl_utils.py:570-584`).
CHILD_SCALE, CHILD_POWER = 8.0, 3.0
STEEP = 10.0

#: Which selection setting to actually build on. `score_child_prop` is
#: HyperAgents' CLI default (`generate_loop.py:1071`); `best` is its function
#: default (`gl_utils.py:511`) -- the program ships two different defaults, so the
#: one we use is named here and recorded on every point.
SETTING = os.environ.get("HG_HYPERAGENTS_SELECT", "score_child_prop")


def _meta_model(req: dict) -> str:
    domain = os.environ.get("HG_HYPERAGENTS_DOMAIN")
    if not domain:
        domain = req.get("domain")
    if not domain and isinstance(req.get("domains"), list) and len(req["domains"]) == 1:
        domain = req["domains"][0]
    return ("claude-3-5-sonnet-20241022" if domain == "polyglot"
            else "anthropic/claude-sonnet-4-5-20250929")

HYPER_SYSTEM = """You are improving an agent harness, as one step of a self-referential
search. The archive has chosen which previous state to build on.

Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>"}],
   "hypothesis": "<one sentence: what you changed and why>",
   "predicted_affected": ["<task ids you expect to change>"]}

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed, and
  neither may anything that selects or evaluates: those stay fixed by construction.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- The harness may be changed anywhere else -- there is no single editable file.
- `predicted_affected` is a *pre-registration*: write it before you are measured.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
"""


def _score(point: dict) -> float:
    """The score on the side the method is shown, never the exam (§2.3, §2.6).

    `protocol.studied_score` decides which field that is, from the record's
    `score_kind`. This used to try `train_score` and fall back to `score`, which
    happens to be right on a §2.6 train run (`train_score` is null there) but is the
    wrong reason for the right answer -- and on any point where `score` is the exam it
    would have read the exam.
    """
    value = protocol.studied_score(point)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def _sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _children(history: list[dict]) -> dict[int, int]:
    """Lineage as this method declared it, via `method_reported`.

    `method_reported` round-trips into the next round's history, so a method can
    keep its own record of which state each round was built on. On mode A's plain
    chain every count would be 1 and the penalty inert; it becomes informative
    exactly because this method may branch backwards to an earlier state.
    """
    counts: dict[int, int] = {}
    for point in history:
        declared = (point.get("method_reported") or {}).get("hyper_parent")
        if isinstance(declared, int):
            counts[declared] = counts.get(declared, 0) + 1
    return counts


def _valid(point: dict) -> bool:
    """The validity gate (`generate_loop.py:706-714`) -- never a score gate.

    HyperAgents never removes an archive entry for scoring badly. A node leaves
    the *candidate* set only when its run did not produce a usable result. Here
    that is: the round was measured, and the method's own edit was accepted as a
    change (a round that changed nothing is not a distinct state to build on).
    """
    if point.get("measured_by_platform") is False:
        return False
    reported = point.get("method_reported") or {}
    if reported.get("rejected") or reported.get("platform_safety_ineligible"):
        return False
    if point.get("valid_parent") is False or reported.get("valid_parent") is False:
        return False
    for key in ("task_reset", "meta_reset", "task_rollback", "meta_rollback"):
        state = reported.get(key)
        if isinstance(state, dict) and state.get("success") is False:
            return False
    value = protocol.studied_score(point)
    return isinstance(value, (int, float)) and math.isfinite(value)


def weights_for(candidates: list[dict], scores: dict, children: dict,
                mid: float, kind: str) -> dict:
    """One setting's raw (unnormalized) weights, exactly as the source computes them.

    Module-level so the rule can be tested without going through the sampler. A
    rule that can only be observed through one sampled pick is a rule nobody can
    check -- and checking this one is the whole reason the port reports a table
    rather than a single choice.
    """
    out = {}
    top = max(scores.values()) if scores else 0.0
    for point in candidates:
        r = point.get("round", 0)
        if kind == "best":
            out[r] = 1.0 if scores[r] == top else 0.0
        elif kind in {"uniform", "random"}:
            out[r] = 1.0
        elif kind == "score_prop":
            out[r] = _sigmoid(STEEP * (scores[r] - mid))
        else:                                       # score_child_prop
            out[r] = (_sigmoid(STEEP * (scores[r] - mid))
                      * math.exp(-((children.get(r, 0) / CHILD_SCALE) ** CHILD_POWER)))
    return out


def select_parent(history: list[dict], round_index: int) -> tuple[dict, dict]:
    """Returns (chosen point, a report comparing all three settings."""
    candidates = [p for p in history if _valid(p)]
    if not candidates:
        # Upstream falls back to the initial archive node, never the latest failed
        # generation. Keep the fallback explicit so callers can report it.
        candidates = [p for p in history if p.get("round") == 0 and _valid(p)]
        if not candidates:
            return {}, {"selector": SETTING, "candidates": [],
                        "reason": "no eligible parent; initial fallback unavailable",
                        "seed": round_index * 104729}

    scores = {p.get("round", 0): _score(p) for p in candidates}
    top3 = sorted(scores.values(), reverse=True)[:3]
    mid = sum(top3) / len(top3) if top3 else 0.0
    children = _children(history)

    table = {k: weights_for(candidates, scores, children, mid, k)
             for k in ("score_child_prop", "score_prop", "uniform", "random", "best")}
    chosen_setting = "random" if SETTING == "random" else SETTING
    chosen_weights = table.get(chosen_setting, table["score_child_prop"])
    total = sum(chosen_weights.values())

    # A degenerate weight vector (all zero, or a single candidate under `best`)
    # falls back to uniform, which is where HyperAgents lands anyway when the
    # weights do not normalize (`gl_utils.py:565-568`).
    if total <= 0:
        chosen_weights = {r: 1.0 for r in chosen_weights}
        total = float(len(chosen_weights))

    rng = random.Random(round_index * 104729)
    pick = rng.random() * total
    chosen, running = candidates[-1], 0.0
    for point in candidates:
        running += chosen_weights[point.get("round", 0)]
        if pick <= running:
            chosen = point
            break

    best = max(candidates, key=_score)
    # How far the setting that actually ran is from uniform, computed rather than
    # asserted. `score_prop` is NOT uniform -- a normalised sigmoid over
    # candidates keeps its ordering. The uniform branch is uniform only because
    # `select_next_parent.py` discards the weights it just computed, and an
    # earlier version of this file claimed the stronger, wrong thing.
    uniform = {r: 1.0 / len(candidates) for r in scores}
    chosen_total = sum(chosen_weights.values()) or 1.0
    max_dev = max(abs(chosen_weights[r] / chosen_total - uniform[r]) for r in scores)

    report = {
        "rule": "HyperAgents score_child_prop: sigmoid(10*(s-mid)) * exp(-(c/8)^3)",
        "setting": chosen_setting,
        "mid_of_top3": round(mid, 4),
        "candidates": [p.get("round") for p in candidates],
        "child_counts": {str(k): v for k, v in sorted(children.items())},
        "weights": {str(r): round(w, 6) for r, w in sorted(chosen_weights.items())},
        "selected_parent": chosen.get("round"),
        "best_round": best.get("round"),
        "chose_something_other_than_best": chosen.get("round") != best.get("round"),
        "is_uniform": max_dev < 1e-9,
        "deviation_from_uniform": round(max_dev, 9),
        "note": "gl_utils.py:570-584 uses the child counts; select_next_parent.py:"
                "49-57 computes them and then discards them in random.choice, which "
                "is why that branch -- and only that branch -- is uniform",
        "seed": round_index * 104729,
        "valid_parent_required": True,
        "selection": {
            "selected_parent": chosen.get("round"),
            "selected_parent_role": "task_agent_state",
            "deployment_status": "pending_trusted_runtime",
            "deployed_node": None,
        },
    }
    return chosen, report


def _state_dir(base: Path, round_no) -> str | None:
    import json
    index = editor.channel(base) / "states" / "index.json"
    if not index.exists():
        return None
    try:
        data = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get(f"round-{round_no}") or {}).get("harness_dir")


def build_prompt(base: Path, req: dict, parent: dict, report: dict) -> str:
    return (
        f"HyperAgents round {req.get('round_index', 1)}. The archive chose round "
        f"{parent.get('round')} as this round's parent (score {_score(parent):.3f} on "
        f"the tasks you can see; the best candidate is round {report['best_round']}).\n\n"
    )


def main() -> int:
    """**A B-shaped submission: one call, `k` children from sampled parents.**

    HyperAgents' rule is a population rule -- parent weights are a sigmoid centred on the
    top-three mean, times `exp(-(children/8)^3)`, and both terms are *about siblings*. Our
    selector follows upstream `utils/gl_utils.py:570-584`; the separate
    `select_next_parent.py:49-57` branch discards its statistics. One call
    draws `HG_HYPERAGENTS_CHILDREN` parents (default 3, from the same weight function) and
    submits changed children for measurement; missing parents and unusable edits may leave
    a partial batch, and the platform's budget caps how many states the run pays for.
    """
    req = editor.read_request()
    editor.require_api(req)
    base = Path(req["base_harness"])
    history = editor.load_history(base)
    round_index = int(req.get("round_index", 1))
    children = max(1, int(os.environ.get("HG_HYPERAGENTS_CHILDREN", "3")))
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
        report["child"] = child + 1
        report["children_this_call"] = children
        report["parent_round"] = parent.get("round")
        parent_dir = _state_dir(base, parent.get("round"))
        staged_score = protocol.studied_score(parent)
        staged_eval = HYPER_STAGED.HyperEvaluation(
            node_id=f"r{parent.get('round')}", staged_fraction=1.0,
            run_full_eval=bool(parent.get("run_full_eval")),
            score=staged_score if isinstance(staged_score, (int, float)) else None)
        report["staged_eval"] = HYPER_STAGED.HyperStagedScheduler().record(staged_eval)
        if not parent_dir or not Path(parent_dir).is_dir():
            reports.append({**report, "why": "the selected state is not staged"})
            continue

        dest = editor.candidate_path(req, f"child{child + 1}")
        try:
            reply = editor.ask(build_prompt(Path(parent_dir), req, parent, report),
                               system=HYPER_SYSTEM, base=base, parent=Path(parent_dir))
        except SystemExit:
            raise
        except Exception as exc:                                       # noqa: BLE001
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
        # The pre-registration goes into the record whether or not the edit landed, so the
        # next round can grade the prediction against what actually flipped.
        if isinstance(reply.get("predicted_affected"), list):
            report["predicted_affected"] = reply["predicted_affected"]
        patch_roles = HYPER_ARCHIVE.patch_identities(
            files, patch_id=f"generation-r{round_index}-child{child + 1}")
        task_patch = patch_roles["task"]
        meta_patch = patch_roles["meta"]
        reports.append({**report, "changed": bool(changed), "why": hypothesis})
        steps.append({
            "harness_dir": dest, "changed": changed, "files": files,
            "label": ((f"[HyperAgents parent r{parent.get('round')}] " + hypothesis[:50])
                      if changed else
                      f"HyperAgents: no edit (parent r{parent.get('round')})"),
            "edit_kind": "hyperagents_rule", "hypothesis": hypothesis,
            "method_reported": {
                "hyperagents": report,
                "hyper_parent": parent.get("round"),
                "hyper_parents": [r.get("parent_round") for r in reports],
                "child": child + 1,
                "artifact": {
                    "artifact_id": f"hyper-r{round_index}-child{child + 1}",
                    "parent_refs": [f"round-{parent.get('round')}"],
                    "immutable": True,
                },
                "role_refs": {
                    "task_agent": {
                        "declared_upstream_role": "task_agent",
                        "actual_execution": {"invoked": True,
                                              "method_model": editor.spent().get("model")},
                        "artifact_ref": f"task-r{round_index}-child{child + 1}",
                    },
                    "meta_agent": {
                        "declared_upstream_role": "meta_agent",
                        "declared_upstream_model": _meta_model(req),
                        "status": "not_invoked",
                        "artifact_ref": f"meta-r{round_index}-child{child + 1}",
                    },
                    "patch_identity": {
                        "task": task_patch.as_dict() if task_patch else None,
                        "meta": meta_patch.as_dict() if meta_patch else None,
                        "rollback": {
                            "task": {"status": "not_invoked"},
                            "meta": {"status": "not_invoked"},
                        },
                    },
                },
                "valid_parent": True,
                "evaluation_success": True,
                "selection": {
                    "selected_parent": parent.get("round"),
                    "selected_parent_ref": f"round-{parent.get('round')}",
                },
                "deployment": {
                    "deployed_node": None,
                    "status": "pending_trusted_runtime",
                    "reason": "method selection is separate from runtime deployment",
                },
            },
        })

    measured = [step for step in steps if step.get("changed")]
    parents = [r.get("parent_round") for r in reports]
    carried = {"hyperagents": reports,
               "hyper_parent": parents[0] if parents else None,
               "hyper_parents": parents,
               "children_this_call": children,
               "selection": {"selected_parent_refs": [f"round-{p}" for p in parents if p is not None]},
               "deployment": {"deployed_node": None, "status": "pending_trusted_runtime"},
               "role_refs": {"task_agent": {"declared_upstream_role": "task_agent",
                                              "actual_method_model": editor.spent().get("model")},
                             "meta_agent": {"declared_upstream_role": "meta_agent",
                                              "status": "not_invoked"}}}
    if not measured:
        # A total outage is `fail` (the call did not run); a draw where the model declined
        # is a report with `stop: false`, because one unlucky sample of parents is not a
        # reason to give up the rest of the budget -- the population draws again.
        if errors:
            return editor.fail(req, method="hyperagents", exc=errors[0], base=base,
                               state=carried)
        reasons = []
        for report in reports:
            why = str(report.get("why") or "")
            if why and why not in reasons:
                reasons.append(why)
        return editor.report(
            req, method="hyperagents", harness_dir=base, changed=False,
            hypothesis=(f"{len(steps)} child(ren) drew no usable edit this call: "
                        + " | ".join(reasons))[:200],
            edit_kind="hyperagents_rule", method_reported=carried,
            acceptance_rule=("HyperAgents' validity gate never looks at score; the "
                             "platform's measurement decides the score"),
            stop=False, label="HyperAgents: no child produced")

    return editor.report_many(
        req, method="hyperagents", steps=measured,
        acceptance_rule=("HyperAgents' validity gate never looks at score; the "
                         "platform's measurement decides the score"),
        # A finished batch ends the batch, not the run: the budget counts measured states
        # and `children` is clamped to the remaining budget so one call tiles it. See the
        # same fix in `methods/dgm/run.py` (measured 2026-10-20: `stop=True` here ended the
        # run at its first complete batch, AppWorld r3 of 10).
        stop=False,
        note=f"HyperAgents: {len(measured)} child(ren) submitted in one call",
        extra={"children_this_call": len(measured),
               "children_requested": children,
               "partial_batch": bool(errors or len(measured) < children)},
    )


if __name__ == "__main__":
    raise SystemExit(main())
