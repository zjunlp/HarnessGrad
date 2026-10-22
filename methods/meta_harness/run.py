#!/usr/bin/env python3
"""Meta-Harness's frontier rule, as a HarnessGrad method.

**A correction this file exists to carry.** Until 2026-10-04 this project recorded
Meta-Harness as *"a method that publishes an output and not a process"*, on the evidence of
`stanford-iris-lab/meta-harness-tbench2-artifact` -- five files, the paper's optimized
Terminal-Bench 2 harness. That was one half of the method. The process is
`stanford-iris-lab/meta-harness`, *"Official code for Meta-Harness (2603.28052)"*, and it
has been a port candidate the whole time. The false claim was written into four files
(`legacy/adapters/SOURCES.md`, `legacy/adapters/README.md`, `legacy/adapters/metaharness.py`,
`docs/methods_we_port.md`); the correction is recorded in each rather than deleted, because
reading one release and inferring the process from it is a shortcut anyone can take.

The rule, read from the shipped loop
------------------------------------
`reference_examples/terminal_bench_2/meta_harness.py` is the TB2 search loop. Its parts, in
the order the paper's abstract states them (*"an outer-loop system that searches over
harness code… an agentic proposer that accesses the source code, scores, and execution
traces of all prior candidates through a filesystem"*):

* `propose_claude(task_prompt, iteration, timeout=2400)` -- a **Claude Code** session reads a
  prompt rendered from the iteration and the accumulated history and writes new candidate
  harness classes.
* `validate_agent_class(import_path)` + `smoke_test(name, import_path, timeout=1800)` -- the
  gate before the expensive evaluation: the import path must exist, be a class, and subclass
  harbor's `Terminus2`, and it is run on a single task.
* `harbor_run(import_path, job_name, n_trials=2, n_concurrent=10)` -- the release default is
  **Opus 4.6, the full 89-task TB2 suite, 2 trials per task, concurrency 50**; a 30-task
  `hard` subset is the cheap bring-up, and `--full-eval` adds an optional 5-trial winner pass.
* `update_frontier(candidates_results)` -- **the rule this file is about**: for every task,
  the best pass rate seen and which agent produced it; plus `_best`, the overall best
  average. A per-task map, not a single incumbent.
* `update_evolution_summary(...)` -- one JSONL row per candidate: its declared `hypothesis`,
  its `changes`, `avg_pass_rate`, `per_task`, the `delta` against the best, an `outcome`
  string, and `rollout_metrics`. The delta is computed from measured scores; only the
  hypothesis comes from the proposer.

What is transplanted
--------------------
* `frontier_of()` / `update_frontier()` -- the per-task best map and `_best`, with the
  original's strict comparison: a later round ties, it does not dethrone the earliest holder.
* `candidate_row()` -- the evolution-summary row, computed from this platform's curve points
  (`hypothesis`, `edits_applied`, `cost`, per-task scores) instead of its own result files.
* `base_round()` -- the base for this round is the frontier's overall best round when its
  state is still staged, which is how a per-task frontier becomes an executable decision at
  mode A's granularity.
* The **evidence shape** of the prompt: the proposer reads the frontier table and every
  prior candidate's row, not only the last round's trace. That is the paper's own thesis --
  *"richer access to prior experience can enable automated harness engineering"*.

What mode A cannot express -- stated plainly
--------------------------------------------
1. **No smoke test before the evaluation.** Their gate runs *after* the proposal and
   *before* the full suite: import it, run one task, then pay. Mode A has no such step: the
   candidate a method hands over **is** the thing the platform measures, on the whole studied
   side. The platform's own `candidate.validate` supplies the import/manifest/compile half of
   that gate; the cheap-subset half does not exist here, and a port cannot fake it because
   the method cannot run a harness at all.
2. **The per-task frontier cannot be executed, only named.** A frontier says "round 3 is best
   at this task, round 5 at that one"; mode A advances to one tree. What this file can do is
   what HarnessX's revert does -- hand back the overall best staged state as this round's
   candidate -- and record the per-task map so the next round (and the reader) can see it.
3. **No arbitrary history depth.** Their proposer reads every prior candidate through a
   filesystem; this channel stages the newest `STATES_KEPT` (12) states plus every curve
   point. The rows are complete, the *trees* are windowed.
4. **The proposer is a tool-using agent in their release and a single-turn chat here.** Claude
   Code can read, run and diff; the shared `editor` asks for a JSON edit sequence. The
   platform's improver is a separate axis (`improvers/improvers.json`), so a run that wants
   the agentic proposer resolves `--improver codex` and pairs it with this rule.
5. **Search and report are the same suite for them.** Their default searches on the full 89
   TB2 tasks and reports on TB2. This platform separates the studied side from the exam side
   and records `selection_effect.selected_on_reported_set`, so the port reads only the
   studied side (`protocol.studied_per_task`) -- see the note below.

Which score the rule reads
--------------------------
**The studied side, never the exam.** `protocol.studied_per_task` / `studied_score` make that
decision once, by `score_kind`, for every method; reading `per_task` directly on an eval point
is the leak §2.3 exists to prevent, and `tests/quality/test_method_score_side.py` checks that
this file does not do it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor                                              # noqa: E402
import protocol                                            # noqa: E402

#: What this method's acceptance is. Meta-Harness keeps a frontier and does not gate on it;
#: the platform accepts unconditionally in mode A, so this file records what the frontier
#: says and never vetoes a candidate it was handed.
_ACCEPTANCE_RULE = (
    "Meta-Harness's rule is retention, not admission: every measured candidate enters the "
    "comparison, `update_frontier` keeps per-task bests and an overall best, and the next "
    "round edits the overall best when its state is still staged. mode A has no reject step, "
    "so this file re-derives the frontier from the platform's curve points and uses it to "
    "choose the base and to describe this round's candidate -- it never vetoes a measurement."
)

META_HARNESS_SYSTEM = """You are improving an agent harness.

Meta-Harness's method is to give the proposer *more of the prior experience*, not a better
edit: the frontier below says which previous candidate is best at which task, and every
candidate's stated hypothesis and measured outcome is listed. Read them before proposing.

Propose the bounded edit sequence your diagnosis requires -- one or more edits. Prefer
targeted replacements where you can name the text
being replaced. Do not rewrite a file wholesale unless the change is genuinely
whole-file. Return the JSON envelope described below and nothing else.
"""

META_HARNESS_DESIGN_SYSTEM = """You are designing agent-harness candidates, not editing code.
Read the failed AND successful trajectories, frontier, prior hypotheses and source evidence.
One mechanism per candidate; each candidate tests exactly one falsifiable hypothesis.
Candidate mechanisms must be mutually different, with specific, distinct changes.
Return only a JSON object with a candidates array. Each entry must contain nonempty string
fields name (snake_case), hypothesis, and changes. Do not return files or implement edits.
"""


# --------------------------------------------------------------- the rule ---

def _agent_name(point: dict) -> str:
    """What `update_frontier` calls `best_agent`: this platform's name for that candidate."""
    return f"r{point.get('round', 0)}"


def candidate_row(point: dict, frontier: dict) -> dict:
    """One `update_evolution_summary` row, from one curve point.

    The original writes `hypothesis` and `changes` from the candidate's own submission and
    `avg_pass_rate` / `per_task` / `delta` from the measurement. Here the hypothesis is
    `method_hypothesis`, the changes are `edits_applied`, and the score comes from
    `protocol.studied_score` -- the studied side only. `delta` is against the frontier's
    overall best *after* this candidate was folded in, which is what the original's
    ordering does (it updates the frontier first), and is why a new best has `delta = 0`.
    """
    per_task = protocol.studied_per_task(point)
    avg = protocol.studied_score(point)
    best = (frontier.get("_best") or {}).get("avg_pass_rate")
    delta = (avg - best) if (avg is not None and best is not None) else None
    name = _agent_name(point)
    row = {
        "iteration": point.get("round"),
        "agent": name,
        "harness_sha": (point.get("identity") or {}).get("harness_sha"),
        "avg_pass_rate": None if avg is None else round(avg, 3),
        "per_task": {k: round(float(v), 3) for k, v in sorted(per_task.items())},
        "hypothesis": point.get("method_hypothesis") or "",
        "changes": list(point.get("edits_applied") or []),
        "delta": None if delta is None else round(delta, 3),
    }
    if avg is not None:
        # The original's `outcome` string, which is a human-readable restatement of the
        # two numbers rather than a new fact -- kept because it is what a reader of their
        # summary greps for, and because a percent-formatted delta is hard to misread.
        row["outcome"] = (f"{avg:.1%} ({delta:+.1%})" if delta is not None
                          else f"{avg:.1%}")
    else:
        row["outcome"] = "not measured"
    cost = point.get("cost") or {}
    row["rollout_metrics"] = {
        "harness_tokens": cost.get("harness_tokens"),
        "harness_model_calls": cost.get("harness_model_calls"),
        "wall_clock_s": cost.get("wall_clock_s"),
    }
    return row


def update_frontier(frontier: dict, per_task: dict, avg: float | None,
                    agent: str) -> dict:
    """`update_frontier`'s body: fold one candidate into the per-task map and `_best`.

    Strictly greater, in both places. That is not an accident of their code and it is worth
    keeping: with `>`, the earliest holder of a best survives every later tie, so the map
    names the first candidate that reached a score rather than the most recent one, and the
    frontier does not churn on noise.
    """
    for task, rate in (per_task or {}).items():
        current = (frontier.get(task) or {}).get("pass_rate", -1)
        if float(rate) > current:
            frontier[task] = {"best_agent": agent, "pass_rate": round(float(rate), 3)}
    if avg is not None:
        current_best = (frontier.get("_best") or {}).get("avg_pass_rate", -1)
        if float(avg) > current_best:
            frontier["_best"] = {"agent": agent, "avg_pass_rate": round(float(avg), 3)}
    return frontier


def frontier_of(history: list[dict]) -> dict:
    """The whole frontier and every candidate row, from the platform's curve points.

    One pass, in round order, updating before writing the row -- the original's ordering
    (`update_frontier` is called, then `update_evolution_summary` reads the frontier file),
    so a candidate that becomes the new best reports `delta = 0` rather than a delta against
    its own predecessor.
    """
    frontier: dict = {}
    rows: list[dict] = []
    for point in history:
        per_task = protocol.studied_per_task(point)
        avg = protocol.studied_score(point)
        update_frontier(frontier, per_task, avg, _agent_name(point))
        rows.append(candidate_row(point, frontier))
    # **`_best` keeps the original's name**, and the per-task map is nested under `tasks`
    # rather than sitting at the top level beside it. In `frontier_val.json` a task's id *is*
    # a top-level key next to `_best`, which works only because no task is named `_best`; a
    # record that can be corrupted by a task id is worth one level of nesting.
    return {
        "kind": "meta_harness_frontier_v1",
        "tasks": {k: v for k, v in sorted(frontier.items()) if k != "_best"},
        "_best": frontier.get("_best") or {},
        "rows": rows,
        "rounds": len(history),
    }


def _state_dir(channel_base: Path, round_no) -> str | None:
    """Where the platform staged a previous round's harness, from the index.

    Read from `states/index.json` rather than guessed from the path, for the reason
    `harnessx.run` records: a state that could not be materialized is deliberately **absent**
    from the index, so a guessed path finds an empty directory and the method reports a
    failure the platform caused. `None` means "not staged", which is a fact the record keeps.
    """
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


def base_round(frontier: dict, history: list[dict], channel_base: Path,
               ) -> tuple[Path, str]:
    """The tree this round edits, and why. The frontier's decision, made executable.

    `update_frontier`'s `_best` is an *agent*, and in mode A an agent is a round whose state
    is staged for the newest `STATES_KEPT` points. So the faithful move is: build this
    round's candidate on the overall best **staged** state rather than on the incumbent. When
    that state is not staged -- outside the window, or absent from `index.json` -- the
    incumbent is edited and the reason is recorded, because a frontier that silently falls
    back is a frontier that lies about what it edited.

    The **channel is a different tree from the edit base** and this function only chooses the
    latter: `history/`, `traces/`, `tasks/` and `SKILL.md` live in the incumbent's
    `_harnessgrad/`, and a staged state is a harness commit with no channel in it. Reading the
    prompt's traces from the edit base would therefore show the proposer nothing on exactly
    the rounds where the frontier moved.
    """
    best = (frontier.get("_best") or {}).get("agent")
    if not best or not history:
        return channel_base, "no measured round yet: this round edits the incumbent"
    best_round = int(str(best).lstrip("r") or 0)
    incumbent_round = history[-1].get("round")
    if best_round == incumbent_round:
        return channel_base, (f"the incumbent is r{incumbent_round}, which the frontier "
                              f"already holds")
    staged = _state_dir(channel_base, best_round)
    if staged and Path(staged).is_dir():
        return Path(staged), (
            f"the frontier's overall best is r{best_round} "
            f"({frontier['_best'].get('avg_pass_rate')}), so this round edits that staged "
            f"state instead of the incumbent r{incumbent_round}")
    return channel_base, (
        f"the frontier's overall best is r{best_round}, but that state is not staged -- "
        f"outside the staged window, or it did not materialize; this round edits the "
        f"incumbent r{incumbent_round}")


def validity_gate(history: list[dict], per_task: dict) -> str | None:
    """Why this round should not be spent, or None.

    Their gate is a *validity* gate that runs after the proposal (import it, smoke-test it on
    one task) and mode A cannot reproduce it -- see the module docstring. What can be checked
    before spending is whether there is anything to work on at all: a studied side where every
    task already passes at the frontier has no failure evidence to propose against, and a
    round spent there costs a model call to stand still. That is the same judgement TTHE's
    validity gate makes in this platform, arrived at independently.
    """
    if not history:
        return None
    if not per_task:
        return "no per-task evidence on the studied side"
    if all(float(v) >= 1.0 for v in per_task.values()):
        return ("every studied task already passes at the frontier; there is no failure "
                "evidence for this round to propose against")
    return None


# ------------------------------------------------------------- the prompt ---

def _frontier_table(frontier: dict, per_task: dict) -> str:
    lines = ["| task | frontier best | currently |", "| --- | --- | --- |"]
    for task in sorted(set(frontier["tasks"]) | set(per_task)):
        best = frontier["tasks"].get(task) or {}
        now = per_task.get(task)
        lines.append(f"| {task} | {best.get('best_agent', '—')} "
                     f"({best.get('pass_rate', '—')}) | "
                     f"{'—' if now is None else now} |")
    return "\n".join(lines)


def build_prompt(edit_base: Path, channel_base: Path, req: dict, frontier: dict,
                 base_why: str) -> str:
    """What the proposer is shown: the frontier and every prior candidate's row.

    This is the whole methodological point of the port. Every other method here shows the
    *last* round's traces; Meta-Harness shows the accumulated experience of every candidate,
    and its paper's claim is that this is what makes automated harness engineering work.

    Two trees, on purpose: the **sources** come from the tree the edits will land in
    (`edit_base`, which may be a staged state), and the **channel** -- traces, task pages,
    the platform skill -- comes from the incumbent, because a staged state carries no
    `_harnessgrad/`.
    """
    latest = (req.get("history") or [{}])[-1] if req.get("history") else {}
    rows = "\n".join(
        f"- {row['agent']} · {row['avg_pass_rate']} · {row['outcome']} · "
        f"hypothesis: {row['hypothesis'][:160] or '(none)'} · "
        f"changes: {', '.join(row['changes']) or '(none)'}"
        for row in frontier["rows"])
    # No skill block here: `editor.ask` injects the platform's skill by default
    # (`editor.skill_block`), and pasting it in twice would make the prompt and the point's
    # `skill_sha` disagree.
    return (
        f"Round {req.get('round_index', 1)}. The harness scored "
        f"{latest.get('score')} on the studied task set; "
        f"{len(frontier['rows'])} candidate(s) have been measured so far.\n\n"
        f"## Meta-Harness frontier (which candidate is best at which task)\n"
        f"overall best: {frontier['_best'] or 'none yet'}\n\n"
        f"{_frontier_table(frontier, protocol.studied_per_task(latest))}\n\n"
        f"## Every prior candidate, as the evolution summary records it\n{rows or '(none)'}\n\n"
        f"## Why this round edits what it edits\n{base_why}\n\n"
          "or say no_change."
    )


def parse_candidates(reply: object, limit: int) -> list[dict]:
    """Keep well-formed designs in order, deduplicating their changes before capping.

    Upstream reference_examples/terminal_bench_2/.claude/skills/
    meta-harness-terminal-bench-2/SKILL.md:65,83-98 specifies one mechanism and
    passes its name + changes to a separate implementer. Text deduplication is a
    local guard against repeated designs, not a claim to verify semantic diversity.
    """
    entries = reply.get("candidates") if isinstance(reply, dict) else None
    if not isinstance(entries, list):
        return []
    candidates: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) and entry[key].strip()
                for key in ("name", "hypothesis", "changes")):
            continue
        candidate = {key: entry[key].strip() for key in ("name", "hypothesis", "changes")}
        changes = " ".join(candidate["changes"].split()).casefold()
        if changes in seen:
            continue
        seen.add(changes)
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


# ------------------------------------------------------------------ main ---

def main() -> int:
    """**A B-shaped submission: one call, `k` proposals from the frontier's base.**

    Meta-Harness' rule is a *frontier* over proposals -- **per task** the best candidate and
    its pass rate, plus an overall best -- and its proposer reads every historical
    candidate's sources, scores and traces. Our port was one-candidate-per-round to fit mode
    A, so a "frontier" had exactly one member per round and the rule degenerated into
    hill-climbing on the incumbent.

    One design call requests `HG_META_HARNESS_CANDIDATES` candidates (default 3), then
    separate implementation calls edit the same overall best staged state chosen by
    `base_round`. Diversity comes from explicitly different mechanism descriptions in the
    implementation prompts. A single candidate skips design; unusable/repeated designs
    fall back to one implementation, with the reason carried on the wire.
    """
    req = editor.read_request()
    editor.require_api(req)

    incumbent = Path(req["base_harness"])
    history = editor.load_history(incumbent)
    frontier = frontier_of(history)

    # The **edit base** may be a different staged state than the incumbent (see `base_round`);
    # the **channel** -- history, traces, task pages, SKILL.md -- is always the incumbent's,
    # because a staged state is a harness commit with no `_harnessgrad/` in it.
    edit_base, base_why = base_round(frontier, history, incumbent)
    per_task = protocol.studied_per_task(history[-1]) if history else {}
    reason = validity_gate(history, per_task)

    design_record: dict = {}

    def generation_of(history_rows: list) -> int:
        """Which generation this call is.

        A generation is one design call and the candidates it produced. The record says both
        how many states that call bought (`steps_in_submission`) and which of them a row is
        (`step_index`), so a batch is **one** generation and its k rows are k measurements of
        it. Counting distinct `round`s instead made a three-candidate design advance the index
        by three: measured on `rsi10-20261018T062351Z-meta_harness-train`, rounds 1-3 (one
        design, three implementations) were recorded as generations 2, 3 and 4, so the record
        claimed four designs where two had happened. Reported beside the round index because
        the two are different units: a three-candidate generation costs three rounds.
        """
        starts = sum(1 for row in history_rows
                     if row.get("steps_in_submission") and not row.get("step_index"))
        return starts + 1

    generation = generation_of(history)

    def carried(children: int, parent_rounds: list) -> dict:
        return {"meta_harness_frontier": frontier, "meta_harness_base": base_why,
                "meta_harness_generation": generation,
                "meta_harness_children": children,
                "meta_harness_parent": parent_rounds[0] if parent_rounds else None,
                "meta_harness_parents": parent_rounds,
                "meta_harness_design": design_record}

    if reason is not None:
        return editor.report(
            req, method="meta_harness", harness_dir=incumbent, changed=False, stop=False,
            edit_kind="none", hypothesis=reason,
            method_reported={**carried(0, []), "declined": reason},
            acceptance_rule=_ACCEPTANCE_RULE,
            extra={"selection_rule": "per-task frontier; base = overall best staged state",
                   "frontier_best": frontier["_best"]},
            label=f"the gate declined to spend this round: {reason[:60]}")

    candidates = max(1, int(os.environ.get("HG_META_HARNESS_CANDIDATES", "3")))
    remaining = int(req.get("epochs_remaining") or req.get("epochs_total") or candidates)
    candidates = max(1, min(candidates, remaining))
    parent_round = (frontier.get("_best") or {}).get("round")
    if parent_round is None:
        parent_round = (history[-1] or {}).get("round") if history else None

    context = build_prompt(edit_base, incumbent, req, frontier, base_why)
    designs: list[dict] = []
    design_record = {"requested": candidates, "mode": "direct_single"}
    if candidates > 1:
        # Upstream reference_examples/terminal_bench_2/meta_harness.py:431-456
        # makes one proposer session per iteration, not independent sampled sessions.
        # Its .claude/skills/meta-harness-terminal-bench-2/SKILL.md:70-98 first
        # analyzes trajectories, then gives the implementer the named design.
        design_prompt = (
            context + f"\n\n## Design stage: propose {candidates} candidates\n"
            "Analyze failed AND successful trajectories and earlier outcomes. Return "
            f"{candidates} candidates as "
            '{"candidates": [{"name": "snake_case", "hypothesis": "falsifiable claim", '
            '"changes": "specific implementation changes"}]}. '
            "One mechanism per candidate. All mechanisms and changes must be mutually "
            "different; do not split the same mechanism into renamed candidates. "
            "Do not implement anything or return an edit envelope in this stage.")
        design_record["mode"] = "designed"
        try:
            # This is a schema question. A CLI editor returns file edits instead;
            # use the shared editor's endpoint route, with the incumbent's channel
            # and the selected edit base's source evidence.
            reply = editor.ask(design_prompt, system=META_HARNESS_DESIGN_SYSTEM,
                               base=incumbent, parent=edit_base, endpoint_only=True)
            designs = parse_candidates(reply, candidates)
        except editor.UnparseableReply as exc:
            design_record["fallback_reason"] = f"unparseable design: {exc}"
        except SystemExit:
            raise
        except Exception as exc:                                       # noqa: BLE001
            design_record["mode"] = "failed"
            return editor.fail(req, method="meta_harness", exc=exc, base=incumbent,
                               state=carried(0, [parent_round]))
        design_record["unique_candidates"] = len(designs)
        if len(designs) < 2:
            design_record["mode"] = "single_fallback"
            design_record.setdefault("fallback_reason", "fewer than 2 distinct valid changes")
        design_record["candidates"] = designs
        candidates = max(1, len(designs))

    steps: list[dict] = []
    errors: list[BaseException] = []
    reported_tail: dict = {}
    for child in range(candidates):
        dest = editor.candidate_path(req, f"child{child + 1}")
        # propose_and_apply chooses workspace/candidate internally. Give each
        # implementation its own workspace so later children cannot overwrite it.
        child_req = {**req, "workspace": str(dest)}
        design = designs[child] if designs else None
        implementation_prompt = context
        if design is not None:
            implementation_prompt += (
                "\n\n## Implement this candidate only\n"
                + json.dumps(design, ensure_ascii=False)
                + "\nImplement exactly this named candidate's changes, testing its one "
                  "hypothesis. Do not add another mechanism. Return the edit JSON envelope.")
        try:
            reply, dest, apply_problems = editor.propose_and_apply(
                edit_base, child_req, META_HARNESS_SYSTEM,
                lambda problems: implementation_prompt
                                 + editor.repair_block(problems, expect=editor.EDIT_EXPECT),
                parent=edit_base)
        except SystemExit:
            raise
        except Exception as exc:                                       # noqa: BLE001
            errors.append(exc)
            continue
        if apply_problems:
            reported_tail = {"rejected": apply_problems}
            continue
        if isinstance(reply, dict) and "no_change" in reply:
            editor.apply_edits(edit_base, dest, [])
            reported_tail = {"declined": str(reply["no_change"])[:200]}
            continue
        changed, files, note = editor.apply_edits(edit_base, dest, reply.get("files"))
        if not changed:
            reported_tail = {"rejected": note}
            continue
        hypothesis = editor.hypothesis_of(reply)[:200]
        steps.append({
            "harness_dir": dest, "changed": True, "files": files,
            "edit_kind": "meta_harness_rule", "hypothesis": hypothesis,
            "label": f"[Meta-Harness child {child + 1}] {hypothesis[:50]}",
            "method_reported": {**carried(candidates, [parent_round]),
                                "child": child + 1,
                                "meta_harness_candidate": design},
        })

    if not steps:
        if errors:
            return editor.fail(req, method="meta_harness", exc=errors[0], base=incumbent,
                               state=carried(candidates, [parent_round]))
        # The model's own sentence beats my summary of it: `no_change` is a diagnosis a
        # reader can act on, and "N proposals came back empty" is not. (Measured by a test
        # that looked for `nothing left to fix` and got the summary instead.)
        hypothesis = (str(reported_tail.get("declined") or reported_tail.get("rejected") or "")
                      or f"{candidates} proposal(s) came back with nothing usable this call")
        return editor.report(
            req, method="meta_harness", harness_dir=incumbent, changed=False, stop=False,
            edit_kind="none",
            hypothesis=hypothesis[:200],
            method_reported={**carried(candidates, [parent_round]), **reported_tail},
            acceptance_rule=_ACCEPTANCE_RULE,
            extra={"selection_rule": "per-task frontier; base = overall best staged state",
                   "frontier_best": frontier["_best"]},
            label="Meta-Harness: no candidate produced")

    # **One generation, not the whole search.** The platform owns how many rounds a study
    # gets, and `--rounds` counts *measured states*: a three-candidate generation therefore
    # costs three of them. Stopping here (`stop=True`) ended the search after the first
    # generation -- measured on 2026-10-17, that was the entire study for this method: one
    # call, three candidates, no feedback. `stop=False` lets the next call build the next
    # generation from the frontier this one updated, which is the loop the paper describes.
    return editor.report_many(
        req, method="meta_harness", steps=steps,
        acceptance_rule=_ACCEPTANCE_RULE,
        stop=False,
        note=f"Meta-Harness: {len(steps)} candidate(s) submitted in one call",
        extra={"selection_rule": "per-task frontier; base = overall best staged state",
               "frontier_best": frontier["_best"],
               "children_this_call": len(steps)},
    )


if __name__ == "__main__":
    raise SystemExit(main())
