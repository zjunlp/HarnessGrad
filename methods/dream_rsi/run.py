#!/usr/bin/env python3
"""A round-score proxy inspired by Dream-RSI's policy selection.

Paper evidence (local source; no upstream implementation is available)
--------------------------------------------------------------------
The local PDF is **v1** (2026-09-14). It defines, at p.5–6, §3, Eq. (1):

    V_i^m = max_{v in T_i^{m,*}} s_v - b1*N_i^m + b2*N_i^m / max(1, k_i^{m,*})
    V^m   = (1/t) * sum_{i=1..t} V_i^m
    pi_{t+1} = pi_t^{m*},  m* in argmax_{m in {0..M-1}} V^m

N counts revealed non-root nodes (generation–evaluation requests); k counts completed
replay decision rounds, not parallel branches or trials. Each of M evaluated executable
exploration policies, including the current one, is evaluated on the same fixed history of
t recorded discovery trees. Feedback is available between revisions. The paper's guarantee
is solely about average replay score on that history (p.6), not future online quality.

What this implementation actually compares
-----------------------------------------
`version_row()` uses each round's studied score, its position in the evaluation sequence as
the request count `N`, and
`n_trials` as a denominator proxy. Those substitutions change the objective; they do not
follow from removing replay. The existing defaults b1=0.001 and b2=0.0 are port choices;
the paper gives no numerical values for its nonnegative coefficients (p.6, Eq. (1)).
Missing or nonpositive token counts omit both cost terms. `select()` maximizes these
recorded proxy values. Its incumbent is the last round if scored, otherwise the last scored
row. `guarantee_holds` checks only that arithmetic comparison. `base_round()` edits the
selected staged state when available and falls back to the incumbent otherwise. Editing
that state does not deploy the unchanged selected policy or guarantee the next score.

There is no discovery-tree replay, shared-world re-evaluation, offline M-version phase,
or explicit exploration-policy action interface here. The proposer edits a harness; v2's
fourth domain (§4.4) also optimizes a harness, so the surface is not foreign, but the
declarative exploration-policy layer that allocates discovery calls is absent.

**Upstream moved after this port was audited.** arXiv:2609.14858 v2 (2026-10-06) rewrote
§3 and promoted the objective v1 had only in Appendix B.2 (p.19, Listing 2 lines 12–21)
to the main Eq. (1):

    V = normalized hypervolume over the quality–cost frontier - lambda * parallelism penalty

The formula implemented below --- `max s_v - b1*N + b2*N/max(1,k)` --- is therefore the
**superseded v1** one. v2 also fixes M = 4 (current policy plus three new versions), sweeps
budget preferences rho in {0, 0.25, 0.5, 0.75, 1} instead of a beta knob, publishes no
value for lambda either, and adds a fourth domain (harness optimization, Terminal-Bench
2.1). Reaching v2 needs a replay world, which needs the still-unreleased code. The proxy
formula and coefficients are retained, and every point above is recorded in method.json and
docs/upstream-pins.md.

Only `protocol.studied_score` is read; the exam side stays outside the comparison.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor                                              # noqa: E402
import protocol                                            # noqa: E402

ACCEPTANCE_RULE = (
    "Dream-RSI (paper p.6, §3) selects the highest average replay score on a fixed history "
    "including the current policy. This port selects the highest recorded round-score "
    "proxy and edits its staged state when available. guarantee_holds covers recorded "
    "proxy values only; it establishes no replay, quality, deployment or next-candidate "
    "guarantee."
)

DREAM_RSI_SYSTEM = """You are improving an agent harness.

This Dream-RSI-inspired port uses recorded round-score proxies to choose an edit base.
The table gives studied quality minus a token-cost term plus a trial-denominator term.
These are substitutions for the paper's replay objective. k in the table is n_trials,
whereas the paper counts replay decision rounds. The base explanation says whether the
selected state is available. The guarantee covers recorded proxy values only; the next
candidate can regress. There is no discovery-tree replay in this port.

Propose the bounded edit sequence your diagnosis requires for the version named above --
one or more edits. Prefer targeted replacements
where you can name the text being replaced. Return the JSON envelope described below and
nothing else.
"""


# --------------------------------------------------------------- the rule ---

def config() -> dict:
    """Existing proxy coefficients and units, retained without retuning.

    v1's Eq. (1) (local PDF p.6) specifies fixed beta1,beta2 >= 0 per generation–
    evaluation request, with numerical defaults unspecified; v2 superseded that formula
    with a Pareto hypervolume minus a lambda penalty and still publishes no coefficient
    value. This port instead uses kilo-tokens and defaults (0.001, 0.0). Neither these
    values nor the earlier port value 0.05 can be attributed to either version.
    Environment overrides are parsed as floats without enforcing the paper's nonnegative
    coefficient constraint.
    """
    def number(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    return {"b1": number("HG_DREAM_RSI_BETA1", 0.001),
            "b2": number("HG_DREAM_RSI_BETA2", 0.0),
            "kilo_tokens": 1000.0}


def version_row(point: dict, cfg: dict, requests: int = 1) -> dict:
    """One round's proxy `V`, and the numbers it came from.

    **`N` is a request count, as Eq. (1) says.** The earlier version of this port priced
    `harness_tokens / 1000` instead, which put `N` in the thousands: with `beta1 = 0.001`
    the cost term reached ~8.5 while `quality` is at most 1, so the argmax followed *cost*
    rather than quality and the method kept whatever was cheapest. Measured on the
    2026-10-17 run: `quality 0.53, cost_kilo_tokens 8530, v = -8.000045`. Tokens are still
    recorded (`cost_kilo_tokens`) so a reader can price the same policy differently.

    `k` is `n_trials` here where the paper counts completed replay decision rounds; the
    replay tree is absent (see `method.json` deviations), so this remains a proxy.
    """
    quality = protocol.studied_score(point)
    cost = (point.get("cost") or {})
    tokens = cost.get("harness_tokens")
    k = int(point.get("n_trials") or 1)
    row = {"round": point.get("round"), "quality": quality, "n_requests": requests,
           "cost_kilo_tokens": None, "k": k, "v": quality,
           "cost_source": "request_count"}
    if quality is None:
        return row
    if isinstance(tokens, (int, float)) and tokens > 0:
        row["cost_kilo_tokens"] = round(float(tokens) / cfg["kilo_tokens"], 4)
    row["v"] = round(quality - cfg["b1"] * requests
                     + cfg["b2"] * requests / max(1, k), 6)
    return row


def select(history: list[dict], cfg: dict | None = None) -> dict:
    """`argmax` over scored round proxies, with an arithmetic-only comparison.

    Dream-RSI/papers/Dream-RSI.pdf: p.6, §3, compares policies re-evaluated on one fixed
    replay history. Here each row uses its own recorded score and cost; an unscored last
    round falls back to the last scored row as the comparison incumbent. The scope label
    prevents treating this check as the paper's replay or deployment guarantee. Values,
    fallback behavior, rounding and earliest-round tie breaking remain unchanged.
    """
    cfg = cfg or config()
    # The evaluation sequence is the request count the paper prices: round 0 is request 1.
    rows = [version_row(p, cfg, requests=index + 1) for index, p in enumerate(history)]
    scored = [r for r in rows if r["v"] is not None]
    incumbent = next((r for r in reversed(scored)
                      if r["round"] == (history[-1].get("round") if history else None)),
                     scored[-1] if scored else None)
    best = max(scored, key=lambda r: (r["v"], -(r["round"] or 0))) if scored else None
    return {
        "kind": "dream_rsi_versions_v1",
        "beta": {k: cfg[k] for k in ("b1", "b2")},
        "versions": rows,
        "selected": (best or {}).get("round"),
        "v_selected": (best or {}).get("v"),
        "incumbent": (incumbent or {}).get("round"),
        "v_incumbent": (incumbent or {}).get("v"),
        "guarantee_holds": bool(best and incumbent and best["v"] >= incumbent["v"] - 1e-9),
        "guarantee_scope": "recorded_proxy_values_only",
        "cost_reported_by_rounds": sum(1 for r in rows if r["cost_kilo_tokens"] is not None),
    }


def _state_dir(channel_base: Path, round_no) -> str | None:
    """Where the platform staged a round's harness, from the index (never a guessed path)."""
    index = editor.channel(channel_base) / "states" / "index.json"
    if not index.exists():
        return None
    try:
        data = json.loads(index.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return (data.get(f"round-{round_no}") or {}).get("harness_dir")


def base_round(selection: dict, history: list[dict], channel_base: Path,
               ) -> tuple[Path, str]:
    """The version this round edits: the argmax-`V` one, when its state is staged.

    Dream-RSI/papers/Dream-RSI.pdf: p.6, §3, deploys the selected policy unchanged. Here
    the selected state is only an edit base, and an unstaged selection falls back to the
    incumbent. The resulting candidate has no non-regression guarantee.
    """
    selected = selection.get("selected")
    if selected is None:
        return channel_base, "no measured version yet: this round edits the incumbent"
    incumbent_round = history[-1].get("round") if history else None
    if selected == incumbent_round:
        return channel_base, (f"r{selected} has the highest V and is already the incumbent")
    staged = _state_dir(channel_base, selected)
    if staged and Path(staged).is_dir():
        return Path(staged), (
            f"r{selected} has the highest V ({selection.get('v_selected')} vs the "
            f"incumbent's {selection.get('v_incumbent')}), so this round revises that "
            f"version's staged state")
    return channel_base, (
        f"r{selected} has the highest V but its state is not staged (outside the window, or "
        f"it did not materialize); this round revises the incumbent r{incumbent_round}")


def build_prompt(edit_base: Path, channel_base: Path, req: dict, selection: dict,
                 base_why: str) -> str:
    """The version table, the selected one, and the harness sources of the tree being edited.

    Sources come from the **edit base** (which may be a staged state) and the channel from the
    incumbent: a staged state is a harness commit with no `_harnessgrad/` in it, so reading the
    traces from the edit base would show the proposer nothing on exactly the rounds where the
    selection moved.
    """
    table = "\n".join(
        f"| r{r['round']} | {r['quality']} | {r['cost_kilo_tokens']} | {r['k']} | {r['v']} |"
        for r in selection["versions"])
    return (
        f"Round {req.get('round_index', 1)}. Dream-RSI's version comparison "
        f"(b1={selection['beta']['b1']}/kilo-token, b2={selection['beta']['b2']}):\n\n"
        f"| version | quality | cost (kilo-tokens) | k | V |\n"
        f"| --- | --- | --- | --- | --- |\n{table}\n\n"
        f"selected: r{selection['selected']} (V={selection['v_selected']}), "
        f"incumbent: r{selection['incumbent']} (V={selection['v_incumbent']}), "
        f"guarantee V* >= V^0 holds: {selection['guarantee_holds']} "
        f"(recorded proxy values only; no replay or next-candidate guarantee)\n\n"
        f"## Why this round edits what it edits\n{base_why}\n\n"
    )


# ------------------------------------------------------------------ main ---

def main() -> int:
    req = editor.read_request()
    editor.require_api(req)

    incumbent = Path(req["base_harness"])
    history = editor.load_history(incumbent)
    cfg = config()
    selection = select(history, cfg)
    edit_base, base_why = base_round(selection, history, incumbent)

    def report(**kw):
        return editor.report(
            req, method="dream_rsi", harness_dir=kw.pop("harness_dir", incumbent),
            method_reported={"dream_rsi": selection, "dream_rsi_base": base_why,
                             **kw.pop("reported", {})},
            acceptance_rule=ACCEPTANCE_RULE,
            extra={"selection_rule": "argmax over scored recorded proxies, comparison "
                                     "incumbent included",
                   "v_selected": selection["v_selected"],
                   "v_incumbent": selection["v_incumbent"],
                   "guarantee_holds": selection["guarantee_holds"],
                   "guarantee_scope": selection["guarantee_scope"]},
            **kw)

    dest = editor.candidate_path(req)
    try:
        reply, dest, apply_problems = editor.propose_and_apply(
            edit_base, req, DREAM_RSI_SYSTEM,
            lambda problems: build_prompt(edit_base, incumbent, req, selection, base_why)
                             + editor.repair_block(problems, expect=editor.EDIT_EXPECT))
        if apply_problems:
            return report(harness_dir=dest, changed=False, stop=False, edit_kind="none",
                          hypothesis=f"the edit sequence did not apply: {apply_problems}",
                          reported={"rejected": apply_problems})
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return editor.fail(req, method="dream_rsi", exc=exc, base=incumbent,
                           state={"dream_rsi": selection, "dream_rsi_base": base_why})

    if isinstance(reply, dict) and "no_change" in reply:
        editor.apply_edits(edit_base, dest, [])
        return report(harness_dir=dest, changed=False, stop=False, edit_kind="none",
                      hypothesis=str(reply["no_change"])[:200],
                      label="model: no change")

    changed, files, note = editor.apply_edits(edit_base, dest, reply.get("files"))
    if not changed:
        return report(harness_dir=dest, changed=False, stop=False, edit_kind="none",
                      hypothesis=f"unusable proposal: {note}",
                      reported={"rejected": note}, label="unusable proposal")

    hypothesis = editor.hypothesis_of(reply)[:200]
    return report(harness_dir=dest, changed=True, files=files, hypothesis=hypothesis,
                  edit_kind="dream_rsi_rule",
                  label=(f"[dream-rsi r{selection['selected']} "
                         f"V={selection['v_selected']}] {hypothesis[:50]}"))


if __name__ == "__main__":
    raise SystemExit(main())
