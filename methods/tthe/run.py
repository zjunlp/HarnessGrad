#!/usr/bin/env python3
"""TTHE's per-round decision rule, as a HarnessGrad method.

Why this file exists
--------------------
TTHE does not improve a harness with a single hill-climb step. Its live loop
(`text_to_sql/optimize.py`; that file's own header says the sibling modules are
diagnostics/ablations, NOT this loop) runs, per unlabeled batch of questions:

    G fixed branches x R rounds; each branch edits ONLY its own previous
    harness; every branch deep-reads every active candidate's traces as peer
    evidence; a round's child is accepted only if it imports and passes the
    frozen-solver / label-free audit and leaves a valid proposal card; after R
    rounds one agentic judge picks among EVERY harness observed in the batch,
    with the incoming incumbent always in the pool.

This file ports that decision rule onto HarnessGrad's mode A, where the platform
owns the schedule and measures exactly one candidate per round.

The rule, and where it lives in TTHE
------------------------------------
* Fixed-branch search with shared evidence — `optimize.py:381` (`branches =
  [H] * args.group`), `optimize.py:383-384` ("each proposer receives its own
  branch as the mechanically pre-copied edit base"), `proposer.py:306-332`
  (peers are listed as PEER EVIDENCE, the assigned base is edited in place).
* A fixed branch-role schedule — `proposer.py:122-142` (`DIVERSITY`),
  `proposer.py:145-147` (`candidate_diversity(gi) -> DIVERSITY[gi % 3]`, whose
  docstring says the generation round never changes it).
* A validity-only acceptance gate — `optimize.py:404-406`
  (`accepted = child if child and _loadable(child, db0) else base`),
  `optimize.py:497-516` (`_loadable`: it imports AND passes the AST/token
  audit), `proposer.py:456-515` (a child with no valid proposal card is
  `None`), `proposer.py:154-193` (`valid_proposal_card`, the fixed key/type
  check). No score and no gold enter this gate.
* The rollback gate — `optimize.py:437-458`: the judge chooses from every
  harness observed this batch, the incumbent is in the pool, and a judge failure
  keeps it (`H = picked if picked in final_candidates else H`), so the
  accumulated harness cannot regress by construction.
* Label-free evidence discipline — `proposer.py:326,344-351,359-371,387`, with
  the invariants named in `harness_base.py:20-40` and checked in
  `audit_harness.py:25-37,40-97`.

What is transplanted (and how mode A expresses it)
--------------------------------------------------
* **The branch-role schedule**: `DIVERSITY[round_index % 3]` (`role_for` below).
  TTHE fixes the role by branch index `gi` across all rounds of a batch
  (`proposer.py:145-147`); mode A exposes one branch per round, so the index
  that varies is the round index and the period stays 3. The role wording is
  adapted for a foreign harness (mode A has no peers and no back-translation);
  the three-way schedule is TTHE's.
* **The validity gate, as "do not spend a round on a challenger that would be
  indistinguishable from the incumbent".** TTHE drops an invalid child back to
  its parent (`optimize.py:404-406`). Mode A cannot reject a candidate it has
  already measured, so the gate is moved to the only place a method controls:
  around the model call. If the staged evidence shows no failing question
  (`_validity_gate`), the round stops with `changed: false`; if the reply leaves
  no valid proposal card, or no usable edit, the method names the incumbent.
* **The rollback gate, as "the incumbent is always an admissible outcome".**
  TTHE's judge pool always contains the incoming `H`. Mode A has no judge, so
  every failure path (`no_change`, a missing/mistyped card, an unusable edit, a
  provider error) returns the incumbent unchanged with `changed: false`; by
  construction this method cannot hand back something worse than what it was
  given. `changed: false` is also how mode A is told not to spend another round
  (`INTERFACE.md` §4.55).
* **The label-free discipline, as prompt constraints** on the shared editor: no
  hardcoded task values, the task description / Hint is authoritative, no custom
  parser or backtracking regex (it hangs the GIL), the controller — not the
  proposer — executes the child exactly once per question, and target the
  failing questions with general mechanisms.
* **The proposal-card requirement, as a structured self-report recorded in
  `method_reported`.** The model supplies the four content lists; the method
  fills the controller-known fields (`candidate`, `branch_id`,
  `generation_round`, `base_candidate`, `peer_candidates`, `role`) and validates
  the result the way `proposer.py:154-193` does. CLI proposers write the lists
  to `artifacts/proposal_card.json`, transported by the editor; endpoint replies
  may still carry `proposal_card` directly, which takes precedence.

What is NOT transplanted
------------------------
TTHE's scaffolding: the `SQLHarness` class, its DB bridge, the Claude-Code worker
subprocesses with `killpg` hard deadlines, `isolated_solve`, the per-run trace
directory layout, and its transductive batch stream. None of it is the decision
rule; the platform already supplies a task set, execution, scoring, and a record
of every round. Reimplementing it here would be reimplementing the platform.

What is NOT reproduced — and why the port says so instead of pretending
-----------------------------------------------------------------------
Mode A hands one pristine incumbent, measures one candidate per round, and
accepts it unconditionally; there is no reject step. So the following parts of
TTHE **cannot be expressed here**, and this port does not fake them:

* **The `G x R` same-batch candidate pool, and therefore the agentic judge.**
  `optimize.py:437-458` picks among several same-batch harnesses. Mode A never
  exposes several same-batch candidates, and a method that scores its own
  candidate is exactly what the platform forbids (`INTERFACE.md` §0). TTHE's
  judge is deliberately *not* `argmax(score)` — it re-runs its own probes and
  reads proposal cards — so reimplementing it as an argmax would be inventing a
  decision TTHE does not make.
* **The transductive per-batch scoring protocol** (`optimize.py:358-361,464-471`):
  adapt to a batch, then score with the harness chosen for that same batch. Mode
  A scores on a fixed task set selected once from H0 (`INTERFACE.md` §2.1).
* **TTHE's label-free proxies** — the back-translation (`bt.py:1-10`,
  `proposer.py:338-340`) and the metamorphic paraphrase-INVariance /
  counterfactual-SENSitivity fitness (`evaluator.py:1-10`). Both presuppose a
  natural-language question with a literal answer key and a rewriter model; a
  foreign base harness has neither, so there is no honest way to compute them.
* **The concrete audit symbol names.** `audit_harness.py`'s `FROZEN_ATTRS`,
  `GOLD_NAMES`, and friends are TTHE's own identifiers. Only the *intent*
  travels (frozen solver, label-free), and the platform already validates a
  candidate by measuring it — better evidence than a name list.

The honest summary: **the per-round decision rule is TTHE's; the schedule, the
scoring, and the acceptance are the platform's.** A run of this method is
evidence about TTHE's decisions under mode A, not a reproduction of TTHE's
results.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Called through the module, never `from editor import ask`: a method that binds
# the editor's functions at import time cannot be tested with the model stubbed
# out, and the stub is the only way to check what the method actually asked for.
import editor                                                  # noqa: E402
import protocol                                                # noqa: E402


# ------------------------------------------------- TTHE 的分支角色表 ---

# proposer.py:122-142, adapted. TTHE's own wording names its DB probes and its
# back-translation; a foreign harness has neither, so the *intent* of each role
# travels and the concrete evidence channel does not. The three-way schedule is
# the transplanted part; the wording is not (see the module docstring).
CONSERVATIVE_REPAIR = (
    "CONSERVATIVE REPAIR ROLE: preserve every incumbent behaviour that has sound "
    "trace evidence. Make the smallest general change that fixes one concrete, "
    "observed failure or runtime error. Do not turn one task-specific "
    "interpretation into a universal rule."
)
INDEPENDENT_EXPLORATION = (
    "INDEPENDENT EXPLORATION ROLE: test a genuinely different causal hypothesis "
    "from the conservative one. Enumerate the plausible mechanisms the incumbent "
    "could be missing, then implement the simplest general mechanism that "
    "survives the evidence; do not copy the incumbent's approach merely because "
    "it is the current default."
)
ADVERSARIAL_AUDIT = (
    "ADVERSARIAL AUDIT AND SYNTHESIS ROLE: start from the observed failures and "
    "from any disagreement between what the task asks and what the harness does. "
    "Falsify unsupported mechanisms against the traces, then synthesize only the "
    "strengths the evidence independently supports. Never equate 'it ran' or "
    "'it returned something' with correctness."
)

#: `DIVERSITY` order is part of the rule: `candidate_diversity(gi)` returns
#: `DIVERSITY[gi % len(DIVERSITY)]`, so the index -> role mapping may not drift.
ROLES = [
    ("conservative-repair", CONSERVATIVE_REPAIR),
    ("independent-exploration", INDEPENDENT_EXPLORATION),
    ("adversarial-audit", ADVERSARIAL_AUDIT),
]

#: proposer.py:372-388 ("EXACTLY these keys") + proposer.py:154-193 (the check).
CARD_KEYS = (
    "candidate",
    "branch_id",
    "generation_round",
    "base_candidate",
    "peer_candidates",
    "role",
    "behavior_changes",
    "preserved_behaviors",
    "verification",
    "risks",
)

EDIT_KIND_EDIT = "tthe_branch"
EDIT_KIND_STOP = "tthe_validity_gate"

#: What TTHE accepts, reduced to the only form mode A can express. Quoted on the
#: curve point so a reader can see the rule without reading this file.
ACCEPTANCE_RULE = (
    "TTHE accepts a child only if it is loadable under the frozen-solver / "
    "label-free audit AND leaves a valid proposal card (optimize.py:404-406,"
    "497-516; proposer.py:154-193,456-515); the incumbent is always in the "
    "judge's pool, so the harness cannot regress (optimize.py:437-458). Mode A "
    "has no judge and measures one candidate per round, so this method reduces "
    "the gate to: keep the incumbent (changed:false) unless a valid, carded "
    "challenger exists. The platform's measurement decides the score."
)

TTHE_SYSTEM = """You are evolving an agent harness, on behalf of the published \
method TTHE, whose per-round decision rule has already chosen a fixed branch role \
for this round.

You will be shown the harness's source and the record of running it on a set of \
tasks, and the proposal card that justifies each change.

Reply with JSON only:

  {"files": [{"path": "<relative path>", "content": "<complete new file contents>"}],
   "hypothesis": "<one sentence: what you changed and why>",
   "proposal_card": {"behavior_changes": [...], "preserved_behaviors": [...],
                     "verification": [...], "risks": [...]}}

Rules:
- `path` is relative to the harness root. `harness.json` may not be changed.
- `content` must be the COMPLETE new file, not a diff. This may create a new file.
- If the evidence does not support any change, reply {"no_change": "<reason>"}.
  That is a valid and useful answer, and TTHE's rollback gate keeps the incumbent.
"""


# ------------------------------------------------------- TTHE 的规则本体 ---

def role_for(round_index: int) -> tuple[int, str, str]:
    """The fixed branch-role schedule: `DIVERSITY[round_index % 3]`.

    proposer.py:145-147 is `return DIVERSITY[gi % len(DIVERSITY)]` and its
    docstring says "generation round never changes it". TTHE varies the role by
    *branch index* within a batch; mode A has one branch per round, so the index
    this method feeds the same schedule is the round index. Returns
    `(index, name, instruction)` so a caller can record which branch fired.
    """
    idx = int(round_index) % len(ROLES)               # proposer.py:145-147
    name, text = ROLES[idx]
    return idx, name, text


def valid_proposal_card(card, *, candidate: str, base_candidate: str,
                        branch_id: int, generation_round: str,
                        peer_candidates: list) -> bool:
    """TTHE's `valid_proposal_card` (proposer.py:154-193): fixed keys, fixed types.

    TTHE's version reads the card from disk and checks:
      * `candidate` matches the child it belongs to;
      * `branch_id` is an `int` and matches the proposer's `gi`;
      * `generation_round` / `base_candidate` are `str` and match;
      * `peer_candidates` is a list and matches the expected peer set (sorted);
      * `role` is a `str`;
      * `behavior_changes`, `preserved_behaviors`, `verification`, `risks` are
        lists.
    The prompt additionally says "EXACTLY these keys" (proposer.py:372), so an
    unexpected key is rejected here too. A `None` child is what makes
    `sample_branches` return `None` and `optimize.py:405` fall back to the base.
    """
    if not isinstance(card, dict) or set(card) != set(CARD_KEYS):
        return False
    if card.get("candidate") != candidate:
        return False
    if not isinstance(card.get("branch_id"), int) or card["branch_id"] != branch_id:
        return False
    if card.get("generation_round") != generation_round:
        return False
    if not isinstance(card.get("base_candidate"), str) \
            or card["base_candidate"] != base_candidate:
        return False
    if not isinstance(card.get("peer_candidates"), list):
        return False
    if sorted(card["peer_candidates"]) != sorted(peer_candidates):
        return False
    if not isinstance(card.get("role"), str):
        return False
    for key in ("behavior_changes", "preserved_behaviors", "verification", "risks"):
        if not isinstance(card.get(key), list):
            return False
    return True


def _candidate_name(round_index: int) -> str:
    """Mode A has no names of its own, so a candidate is named by its round.

    TTHE's candidates are `cand_<run>_<tag>_g<gi>` (proposer.py:470); the shape
    matters only because `base_candidate` must name the parent, and in mode A the
    parent of round N is the state measured at round N-1.
    """
    return f"cand_round{int(round_index)}"


def _round_tag(round_index: int) -> str:
    """TTHE's `generation_round` is the controller's `tag` (proposer.py:375)."""
    return f"round{int(round_index)}"


def _card_ok(card: dict, round_index: int, incumbent_name: str) -> bool:
    """Validate a card against the fields only the controller knows."""
    return valid_proposal_card(
        card,
        candidate=_candidate_name(round_index),
        base_candidate=incumbent_name,
        branch_id=int(round_index),
        generation_round=_round_tag(round_index),
        # proposer.py:377-379 says concurrent sibling proposals are NOT active
        # candidates and must not be listed; optimize.py:386 passes only the
        # active set. Mode A stages exactly one incumbent, so the peer set is
        # genuinely empty -- an empty list is a reading, not a placeholder.
        peer_candidates=[],
    )


def _proposal_content(reply: dict) -> tuple[object, str, str]:
    """Restore disk-card transport (text_to_sql/proposer.py:150-164,372-388).

    Endpoint cards keep precedence, including invalid ones: an invalid reply
    must not be rescued by a second card. Artifact keys are data, never paths
    to open here; reject unsafe keys explicitly rather than ignoring them.
    """
    source = ("reply: proposal_card" if "proposal_card" in reply
              else "file: artifacts/proposal_card.json")
    artifacts = reply.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return None, source, "artifacts must be an object with relative path keys"
    for name in artifacts:
        problem = editor.artifact_path_error(name)
        if problem:
            return None, source, problem
    if "proposal_card" in reply:
        return reply["proposal_card"], source, ""
    if "proposal_card.json" not in artifacts:
        return None, source, "missing proposal_card.json (reply has no proposal_card)"
    raw = artifacts["proposal_card.json"]
    if not isinstance(raw, str):
        return None, source, "proposal_card.json must be UTF-8 text"
    try:
        return json.loads(raw), source, ""
    except json.JSONDecodeError as exc:
        return None, source, f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"


def _content_problem(content) -> str:
    """Explain a gate failure; admission still uses the unchanged _card_ok."""
    if not isinstance(content, dict):
        return "expected an object containing the four content lists"
    keys = ("behavior_changes", "preserved_behaviors", "verification", "risks")
    problems = []
    missing = [key for key in keys if key not in content]
    if missing:
        problems.append("missing " + ", ".join(missing))
    problems.extend(f"{key} must be a list (got {type(content[key]).__name__})"
                    for key in keys if key in content and not isinstance(content[key], list))
    return "; ".join(problems) or "card failed controller identity/key/type validation"


def _stop_card(round_index: int, incumbent_name: str, role_name: str,
               reason: str, trace_note: str) -> dict:
    """The card for a round that proposes nothing.

    TTHE always generates, so its card always describes a change. Mode A's stop
    is the rollback gate, and a stop is a decision too; recording it with the
    same ten keys keeps the card well-formed on both paths. The empty
    `behavior_changes` is the truthful part: nothing changed.
    """
    return {
        "candidate": _candidate_name(round_index),
        "branch_id": int(round_index),
        "generation_round": _round_tag(round_index),
        "base_candidate": incumbent_name,
        "peer_candidates": [],
        "role": role_name,
        "behavior_changes": [],
        "preserved_behaviors": [
            f"{incumbent_name} is kept unchanged (TTHE's rollback gate, "
            "optimize.py:437-458, expressed in mode A as changed:false)"
        ],
        # proposer.py:384-385: verification entries describe only evidence that
        # exists, never an execution of the new child.
        "verification": [{"trace": trace_note,
                          "runtime_status": "the child was not built and never ran",
                          "evidence": reason}],
        "risks": [
            "a failure that only shows on the platform's eval side is invisible "
            "to a method shown the train side only (INTERFACE.md §2.3)"
        ],
    }


# ------------------------------------------------------------- 证据与闸门 ---

def _round_point(base: Path) -> dict:
    """This round's curve point, as staged at the contract path."""
    path = editor.channel(base) / "round.json"
    try:
        point = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return point if isinstance(point, dict) else {}


def read_evidence(base: Path) -> dict:
    """What the method is shown, counted rather than interpreted.

    Traces are the label-free evidence (proposer.py:326); the per-task scores of the
    diagnostic side the platform stages into `round.json` and is the method's
    only rank-ordered signal (`editor.failing_first` reads the same field).
    """
    trace_dir = editor.channel(base) / "traces"
    traces = sorted(p.stem for p in trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
    point = _round_point(base)
    scores = protocol.studied_per_task(point)
    numeric = {t: v for t, v in scores.items() if isinstance(v, (int, float))}
    failing = sorted((t for t, v in numeric.items() if v < 1.0),
                     key=lambda t: numeric[t])
    return {
        "round": point.get("round"),
        "traces_staged": len(traces),
        "trace_tasks": traces,
        "train_tasks_scored": len(numeric),
        "failing_tasks": failing,
        "worst_task": failing[0] if failing else (traces[0] if traces else "-"),
        "best_score": max(numeric.values()) if numeric else None,
    }


def validity_gate(evidence: dict) -> tuple[bool, str]:
    """TTHE's validity gate, in the only form mode A can express.

    TTHE never spends a branch update on a child that is not admissible
    (`optimize.py:404-406`). Mode A cannot reject a candidate after measuring it,
    so the gate has to fire *before* the round is spent: with no failing question
    staged, any challenger is a coin flip that the incumbent's behaviour cannot
    distinguish from itself, which is the same "no admissible child" situation.
    The return is `(worth_spending, why)` and the reason is recorded verbatim.
    """
    if not evidence["traces_staged"]:
        return False, (
            "no traces were staged this round, so there is no observed behaviour "
            "to target a general mechanism at; a challenger built without evidence "
            "would be indistinguishable from the incumbent")
    scored = evidence["train_tasks_scored"]
    failing = evidence["failing_tasks"]
    if scored and not failing:
        return False, (
            f"all {scored} staged question(s) already score at the ceiling "
            f"(best {evidence['best_score']:.3f}); TTHE's proposer targets the "
            "FAILING questions (proposer.py:344-346), and with none there is "
            "nothing a general mechanism could fix")
    if failing:
        return True, (
            f"{len(failing)}/{scored} staged question(s) below the ceiling; the "
            f"worst is {failing[0]!r}")
    return True, (
        f"{evidence['traces_staged']} trace(s) staged and no per-task scores to "
        "rank them; too little evidence to call the round a no-op")


def peer_state_count(base: Path) -> int:
    """How many previous harness states the channel offers as peer evidence.

    The platform's channel (`harnessgrad/channel.py:_stage_for_method`) materialises
    the newest `STATES_KEPT` old states
    under `states/`. TTHE's discipline is that peers are EVIDENCE, not parents
    (proposer.py:329-334), so this method records that they exist and does not
    re-parent to them: unlike SICA or DGM, TTHE's search edits its own branch.
    """
    path = editor.channel(base) / "states" / "index.json"
    try:
        index = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    kept = index.get("kept") if isinstance(index, dict) else None
    return len(kept) if isinstance(kept, list) else 0


# ----------------------------------------------------------------- 提示词 ---

def _render_prior_rounds(history: list[dict]) -> str:
    """The method's own record of earlier rounds — evidence, not scores.

    TTHE's proposer never sees a score at all (`proposer.py:326,387`), so only
    the role and decision of each prior round are shown. The platform's curve
    points also carry eval scores; surfacing them here would hand the method a
    signal the published rule does not use.
    """
    rows = []
    for point in history:
        tthe = (point.get("method_reported") or {}).get("tthe") or {}
        rows.append(
            f"  - round {point.get('round')}: role={tthe.get('role') or 'n/a'}, "
            f"decision={tthe.get('decision') or 'n/a'}, "
            f"label={(point.get('label') or '(no label)')[:70]}")
    return "\n".join(rows[-6:]) if rows else "  (no previous rounds staged)"


def build_prompt(base: Path, request: dict, history: list[dict], round_index: int,
                 role_name: str, role_text: str, incumbent_name: str) -> str:
    """TTHE's proposer prompt (proposer.py:323-389), against a foreign harness."""
    peers = peer_state_count(base)
    return (
        f"Round {round_index}. You are handed the incumbent harness "
        f"`{incumbent_name}` and must return exactly ONE challenger; the platform "
        f"measures it, you do not.\n\n"

        f"## Your fixed branch role\n"
        f"TTHE assigns each branch a role by a fixed schedule, "
        f"`DIVERSITY[round_index % 3]` (proposer.py:122-147), and the role does "
        f"not change within the round. THIS ROUND: **{role_name}**\n{role_text}\n\n"

        f"## Shared evidence, fixed parent\n"
        f"Your edit base is `{incumbent_name}`. Other rounds' harnesses are peer "
        f"evidence you may learn from ({peers} previous state(s) staged under "
        f"`_harnessgrad/states/`), but TTHE's rule is that a branch edits only its "
        f"own previous harness, never another branch's — do not switch parent or "
        f"replace the target wholesale with a peer (proposer.py:329-334).\n"
        f"Previous rounds:\n{_render_prior_rounds(history)}\n\n"

        f"## Label-free discipline (TTHE proposer.py:326,344-351,359-371,387)\n"
        f"- You CANNOT see gold answers or grading results. This method is "
        f"label-free; never claim an absolute score and never mention gold.\n"
        f"- GENERAL — never hardcode a task's literal value, column, or answer. "
        f"The task description (and any Hint it carries) is authoritative: follow "
        f"it exactly, do not override or second-guess it.\n"
        f"- Target the FAILING questions visible in the traces, with general "
        f"mechanisms rather than per-task special cases.\n"
        f"- Do NOT write a custom parser, tokenizer, or backtracking regex over "
        f"the task text or the harness's inputs: it hangs the GIL "
        f"(proposer.py:350-351). Inspect behaviour by executing, not by parsing "
        f"text.\n"
        f"- The controller — not you — executes the child exactly once per "
        f"question (proposer.py:359-371; optimize.py:346-348). NEVER execute, "
        f"import, or instantiate the harness yourself, and do not claim the child "
        f"ran or improved.\n\n"


        f"## Proposal card (mandatory; proposer.py:372-388)\n"
        # Upstream text_to_sql/proposer.py:372-388 instructs a disk write;
        # _ask_cli names the actual workspace/artifacts directory for the CLI.
        f"For a CLI improver, write `artifacts/proposal_card.json` in the "
        f"method artifact directory named by the editor, outside the harness. "
        f"The file must contain one JSON object, with no proposal_card wrapper. "
        f"For an endpoint, include that object as `proposal_card` in your JSON "
        f"reply instead. The controller fills `candidate`, "
        f"`branch_id`, `generation_round`, `base_candidate`, `peer_candidates` and "
        f"`role`; you supply exactly these four lists:\n"
        f'  {{\n'
        f'    "behavior_changes": [{{"trace": "...", "observed_issue": "...", '
        f'"change": "...", "evidence": "...", "expected_effect": "..."}}],\n'
        f'    "preserved_behaviors": ["..."],\n'
        f'    "verification": [{{"trace": "...", "runtime_status": "...", '
        f'"evidence": "..."}}],\n'
        f'    "risks": ["..."]\n'
        f'  }}\n'
        f"A missing or mistyped card is discarded and the incumbent is kept "
        f"(proposer.py:154-193,456-515). `verification` may describe only the "
        f"traces the platform staged; the child is unexecuted.\n\n"
        f'Write the file (CLI) or include proposal_card in your JSON reply (endpoint), '
        f'or reply {{"no_change": "<reason>"}}.'
    )


# ----------------------------------------------------------------- 主流程 ---

def main() -> int:
    request = editor.read_request()
    editor.require_api(request)

    base = Path(request["base_harness"])
    history = editor.load_history(base)
    round_index = int(request.get("round_index") or (len(history) or 1))
    role_index, role_name, role_text = role_for(round_index)

    # Mode A names the parent by the round it was measured at; round N builds on
    # round N-1, which is TTHE's "edit only your own previous harness".
    incumbent_name = _candidate_name(max(round_index - 1, 0))

    evidence = read_evidence(base)
    worth, gate_why = validity_gate(evidence)

    record: dict = {
        "method": "TTHE (published): fixed branch-role schedule + validity gate "
                  "+ rollback gate",
        "source": "methods_src/tthe/text_to_sql/optimize.py + proposer.py",
        "round_index": round_index,
        "branch_id": round_index,
        "role_index": role_index,
        "role": role_name,
        "role_schedule": "DIVERSITY[round_index % 3] (proposer.py:122-147)",
        "incumbent_candidate": incumbent_name,
        "peer_candidates": [],
        "peer_states_available": peer_state_count(base),
        "evidence": evidence,
        "validity_gate": gate_why,
    }

    # ---- the pre-flight validity gate: the round is not worth spending --------
    if not worth:
        card = _stop_card(round_index, incumbent_name, role_name, gate_why,
                          evidence["worst_task"])
        record.update({"decision": "stop", "reason": gate_why,
                       "card_valid": _card_ok(card, round_index, incumbent_name),
                       "proposal_card": card})
        return editor.report(
            request, method="tthe", harness_dir=base, changed=False,
            stop=False,
            hypothesis=f"stopped by TTHE's validity gate: {gate_why}",
            edit_kind=EDIT_KIND_STOP, method_reported={"tthe": record},
            acceptance_rule=ACCEPTANCE_RULE,
            extra={"role_schedule": "DIVERSITY[round_index % 3]"},
            label=f"TTHE[{role_name}]: stop (nothing to fix)")

    prompt = build_prompt(base, request, history, round_index, role_name,
                          role_text, incumbent_name)
    try:
        reply = editor.ask(prompt, system=TTHE_SYSTEM, base=base)
    except SystemExit:
        # `require_api` / `model_settings` use SystemExit for a *configuration*
        # failure (the method cannot start), not for an undecided round. Every
        # method here re-raises it so the platform records `entrypoint_error`
        # with the reason (INTERFACE.md §4.1); swallowing it into `changed:false`
        # would make a missing credential look like a considered stop.
        raise
    except Exception as exc:                                   # noqa: BLE001
        # `editor.fail()` writes a trajectory but cannot carry this method's own
        # decision record, and TTHE's record must be visible even when the
        # provider fails. So the error path goes through `report()` with the same
        # fields `fail()` would set, plus the record, and prints the reason where
        # `fail()` puts it.
        why = f"{type(exc).__name__}: {str(exc)[:300]}"
        card = _stop_card(round_index, incumbent_name, role_name, why, "-")
        record.update({"decision": "error", "reason": why,
                       "card_valid": _card_ok(card, round_index, incumbent_name),
                       "proposal_card": card})
        # `editor.fail` carries the record and continues the run. TTHE's own loop
        # keeps its incumbent and waits for the next batch; ending the whole run on
        # one provider error is the platform's choice, not TTHE's, and it cost this
        # method the rest of its budget while five sibling ports carried on.
        return editor.fail(request, method="tthe", exc=why, base=base,
                           state={"tthe": record})

    # ---- the model declined: the incumbent stays ------------------------------
    if "no_change" in reply:
        reason = str(reply["no_change"])[:200]
        card = _stop_card(round_index, incumbent_name, role_name, reason,
                          evidence["worst_task"])
        record.update({"decision": "stop", "reason": reason,
                       "card_valid": _card_ok(card, round_index, incumbent_name),
                       "proposal_card": card})
        return editor.report(
            request, method="tthe", harness_dir=base, changed=False,
            stop=False,
            hypothesis=reason, edit_kind=EDIT_KIND_STOP,
            method_reported={"tthe": record},
            acceptance_rule=ACCEPTANCE_RULE,
            extra={"role_schedule": "DIVERSITY[round_index % 3]"},
            label=f"TTHE[{role_name}]: no change")

    # ---- the proposal-card gate: no card, no admissible child -----------------
    # text_to_sql/proposer.py:163 loads the disk card; :500-515 rejects a
    # missing/invalid one. Keep that gate, adding only a transport fallback.
    content, card_source, content_error = _proposal_content(reply)
    record["proposal_card_source"] = card_source
    card = {
        "candidate": _candidate_name(round_index),
        "branch_id": int(round_index),
        "generation_round": _round_tag(round_index),
        "base_candidate": incumbent_name,
        "peer_candidates": [],
        "role": role_name,
        "behavior_changes": content.get("behavior_changes") if isinstance(content, dict) else None,
        "preserved_behaviors": content.get("preserved_behaviors") if isinstance(content, dict) else None,
        "verification": content.get("verification") if isinstance(content, dict) else None,
        "risks": content.get("risks") if isinstance(content, dict) else None,
    }
    if not _card_ok(card, round_index, incumbent_name) or content_error:
        detail = content_error or _content_problem(content)
        reason = (f"invalid proposal card from {card_source}: {detail} "
                  "(proposer.py:154-193); "
                  "TTHE treats that as no child, so the incumbent is kept")
        stop = _stop_card(round_index, incumbent_name, role_name, reason,
                          evidence["worst_task"])
        record.update({"decision": "stop", "reason": reason, "card_valid": False,
                       "proposal_card": stop, "rejected_card": card})
        return editor.report(
            request, method="tthe", harness_dir=base, changed=False,
            stop=False,
            hypothesis=f"unusable proposal: {reason}", edit_kind=EDIT_KIND_STOP,
            method_reported={"tthe": record},
            acceptance_rule=ACCEPTANCE_RULE,
            extra={"role_schedule": "DIVERSITY[round_index % 3]"},
            label=f"TTHE[{role_name}]: invalid proposal card")

    dest = editor.candidate_path(request)
    changed, files, note = editor.apply_edits(base, dest, reply.get("files"))
    if not changed:
        reason = f"unusable proposal: {note}"
        stop = _stop_card(round_index, incumbent_name, role_name, reason,
                          evidence["worst_task"])
        record.update({"decision": "stop", "reason": reason, "card_valid": True,
                       "proposal_card": card, "rejected": note})
        return editor.report(
            request, method="tthe", harness_dir=base, changed=False,
            stop=False,
            hypothesis=reason, edit_kind=EDIT_KIND_STOP,
            method_reported={"tthe": record},
            acceptance_rule=ACCEPTANCE_RULE,
            extra={"role_schedule": "DIVERSITY[round_index % 3]"},
            label=f"TTHE[{role_name}]: unusable proposal")

    # ---- an admissible, carded challenger --------------------------------------
    record.update({"decision": "propose",
                   "reason": "a valid edit and a valid proposal card exist; the "
                             "platform measures the challenger",
                   "card_valid": True, "proposal_card": card, "files": files})
    return editor.report(
        request, method="tthe", harness_dir=dest, changed=True, files=files,
        hypothesis=(f"[TTHE {role_name} r{round_index}] "
                    + str(reply.get("hypothesis", "")))[:300],
        edit_kind=EDIT_KIND_EDIT, method_reported={"tthe": record},
        acceptance_rule=ACCEPTANCE_RULE,
        extra={"role_schedule": "DIVERSITY[round_index % 3]"},
        label=f"[TTHE {role_name}] " + str(reply.get("hypothesis", ""))[:50])


if __name__ == "__main__":
    raise SystemExit(main())
