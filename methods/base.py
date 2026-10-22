#!/usr/bin/env python3
"""The shared machinery behind `methods/a` and `methods/b`.

Why this file exists
--------------------
Ten methods live beside this one, and every one of them re-implements the same
lifecycle: stage a candidate tree from a parent, assemble a prompt, dispatch it
to the resolved improver, apply the reply and check the result is still a
harness, report to the platform, and survive its own errors. They differ only in
*what they decide*. That is a missing abstraction, and its cost was measured:
one fix -- removing an inlined-evidence section, changing a closing sentence,
threading a new prompt section -- had to be applied ten times.

A method now declares four things:

* ``build_prompt(round)`` -- how the ask is built from the round's state;
* ``candidates``          -- 1 (the A shape: one candidate per call) or k (the
                             B shape: a batch, with no score visible inside it);
* ``accept(round)``       -- the selection rule, run with the previous round's
                             measured score in hand;
* ``state(round)``        -- any extra state the method wants recorded;

and inherits everything else: candidate staging from a chosen parent, improver
dispatch (CLI or chat endpoint), apply-and-validate with a repair attempt,
reporting to the platform, and the error path.

Which reader the improver is, is decided in ``editor.ask`` and nowhere else. A
CLI improver has a working directory, so it is given ``editor.evidence_pointer``
(paths it can read); a chat endpoint cannot read files, so it is given
``editor.evidence_content`` (the material inlined). This file does not duplicate
that decision and neither does a method built on it.

What this file is not
---------------------
It is not a port of any published method and claims none. The published ports
live beside it, unchanged: the shape of a submission is a different question
from whose rule produced it. The two shapes here are ours and honestly named:

    A   one candidate per call; the next call sees the previous score
    B   k candidates in one call; no score is visible inside the batch

The platform protocol (`methods/protocol.py`, `INTERFACE.md` §4) is what both
shapes satisfy: one JSON request on stdin, one JSON object on stdout, a
trajectory at ``trajectory_out`` naming directories that hold complete harnesses.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import editor                                                  # noqa: E402
import protocol                                                # noqa: E402

#: How many times one proposal is asked for before the round reports nothing.
#: Two is the first ask plus **one repair**: a sequence that does not apply is
#: handed back to the improver with the reason, once. A method may raise it with
#: ``HG_BASE_PROPOSAL_ATTEMPTS``; two is the floor because "with a repair" is
#: the contract the platform verifies (`INTERFACE.md` §4.45).
PROPOSAL_ATTEMPTS = max(2, int(os.environ.get("HG_BASE_PROPOSAL_ATTEMPTS", "2")))


# ----------------------------------------------------------------- the round ---

def _index(req: dict, history: list) -> int:
    """The 1-based round number, from the request or from the record.

    ``round_index`` is what the platform sends. The fallbacks are for a method
    invoked by hand (the next person will do it) and they are deliberately the
    same number the platform would have sent.
    """
    try:
        return max(1, int(req.get("round_index") or len(history) or 1))
    except (TypeError, ValueError):
        return max(1, len(history) or 1)


def _remaining(req: dict) -> int:
    """How many states this run still pays for, at least 1.

    ``--rounds N`` is a budget of **measured states** (`INTERFACE.md` §4.1.1), so
    a batch must not plan more than the budget has room for: the platform would
    truncate it, and a truncated batch is a batch the method planned wrong.
    ``epochs_measured`` is absent from an older request, which reads as 0.
    """
    try:
        total = int(req.get("epochs_total") or 0)
        measured = int(req.get("epochs_measured") or 0)
    except (TypeError, ValueError):
        return 1
    return max(1, total - measured) if total else 1


class Round:
    """One round's state: everything a method's four decisions may read.

    ``base`` is the incumbent (``request["base_harness"]``) and ``pristine`` is
    H0 (``request["pristine_harness"]``), both staged by the platform. Earlier
    rounds' complete trees are under ``<base>/_harnessgrad/states/`` for a rule
    that wants to build on a state that is not the incumbent; ``parent`` is the
    tree this round's candidate is staged from.
    """

    def __init__(self, req: dict, base, history: list):
        self.req = req
        self.base = Path(base)
        pristine = req.get("pristine_harness")
        self.pristine = Path(pristine) if pristine else None
        self.history = list(history or [])          # every point, oldest first
        self.index = _index(req, self.history)
        self.previous_point = self.history[-1] if self.history else {}
        # **The studied score, never the exam.** Which field holds it depends on
        # the record's `score_kind` (§2.6) and the rule is written once in
        # `protocol.studied_score`; reading `score` here would read the exam on
        # an eval-side run, which is the leak the split exists to prevent.
        self.previous_score = (protocol.studied_score(self.previous_point)
                               if self.previous_point else None)
        self.remaining = _remaining(req)
        # Filled by the engine before a prompt is built.
        self.parent = self.base
        self.candidate = 1
        self.count = 1

    def facts(self) -> str:
        """The round, in the form a prompt can quote."""
        previous = ("none yet" if self.previous_score is None
                    else f"{self.previous_score:.3f}")
        return (f"round {self.index}, {self.remaining} state(s) left in the budget; "
                f"the previous round's score was {previous}")


# --------------------------------------------------------------- the method ---

class Method:
    """What a method declares. Everything else lives in `run` below."""

    #: Identity on the record and in the trajectory's provenance.
    name = "method"

    #: Candidates submitted in one call. 1 is the A shape (a measured score is
    #: visible between calls); k > 1 is the B shape (none is visible inside the
    #: batch, because the platform measures the batch after the call returns).
    candidates = 1

    #: One line the record keeps, beside the code that implements the rule.
    acceptance_rule = ""

    #: The improver's system prompt. The platform's own facts, skill and evidence
    #: are added by `editor.ask`; this is only the method's framing.
    system = editor.DEFAULT_SYSTEM

    # ------------------------------------------------------- what is declared --
    def build_prompt(self, rnd: Round) -> str:
        """The ask for this round, built from the round's state."""
        raise NotImplementedError(f"{self.name} declares no build_prompt")

    def accept(self, rnd: Round) -> bool:
        """The selection rule, run with the previous round's score in hand.

        Returning False means "nothing more to say": the submission reports a
        no-change and asks the platform to stop. The default proposes again.
        """
        return True

    def state(self, rnd: Round) -> dict:
        """Extra state this method wants recorded with the round. Default: none."""
        return {}

    def parent(self, rnd: Round) -> Path:
        """Which staged tree the candidate is built from. Default: the incumbent.

        A rule whose content is *which previous state to build on* overrides
        this; ``rnd.pristine`` and the trees under ``rnd.base/_harnessgrad/states``
        are the other trees the platform staged.
        """
        return rnd.base


# --------------------------------------------------------------- the engine ---

def _record(method: Method, rnd: Round, shape: str, **extra) -> dict:
    """The method's own state, for `method_reported` on every path."""
    record = {"shape": shape, "round": rnd.index,
              "previous_score": rnd.previous_score}
    record.update(method.state(rnd) or {})
    record.update(extra)
    return record


def _propose(method: Method, rnd: Round, dest: Path):
    """Ask for one candidate at ``dest``. Returns ``(reply, note, changed, files)``.

    The machinery is `editor`'s: the prompt is the method's plus
    ``editor.repair_block``; the dispatch is ``editor.ask_for_json``, which
    reaches a CLI improver or a chat endpoint and decides which reader gets
    paths and which gets content; the applying and the harness check are
    ``editor.apply_edits``. The one thing this function owns is that a batch
    needs its own destination and that a candidate is staged from **the chosen
    parent**, not from the incumbent.
    """
    parent = rnd.parent
    outcome: dict = {}

    def accept(reply: dict) -> str:
        if isinstance(reply, dict) and "no_change" in reply:
            return ""
        # `apply_edits(base, dest, edits) -> (changed, applied_paths, problems)`
        _, applied, problems = editor.apply_edits(parent, dest, reply.get("files"))
        outcome["applied"] = applied
        outcome["problems"] = "; ".join(problems)
        return outcome["problems"]

    def prompt_for(problems: str) -> str:
        return (method.build_prompt(rnd)
                + editor.repair_block(problems, expect=editor.EDIT_EXPECT))

    try:
        reply, problems = editor.ask_for_json(
            prompt_for, system=method.system, base=rnd.base,
            attempts=PROPOSAL_ATTEMPTS, parent=parent, accept=accept,
            what="an edit sequence")
    except SystemExit as exc:
        # `editor.model_settings` exits when no improver is configured at all.
        # That is "this round has nothing to propose", not a crash: it is
        # reported as a no-change so the platform still records the round it
        # paid for. (A wrong platform API version also raises SystemExit, but
        # `require_api` has already refused that before any of this runs.)
        reply, problems = {}, f"no improver available: {exc}"
    except Exception as exc:                                   # noqa: BLE001
        reply, problems = {}, f"{type(exc).__name__}: {exc}"

    if isinstance(reply, dict) and "no_change" in reply:
        note = str(reply["no_change"])[:300]
    elif outcome.get("applied") and not problems:
        return reply, "", True, list(outcome["applied"])
    else:
        note = problems or "the improver returned nothing usable"

    # Reset `dest` to a clean copy of the parent. A failed apply can leave a
    # half-edited tree behind, and reporting that as "no change" would hand the
    # platform a candidate nobody chose.
    editor.apply_edits(parent, dest, [])
    return reply, note, False, []


def _label(method: Method, changed: bool, hypothesis: str, prefix: str = "") -> str:
    if changed:
        return (prefix + (hypothesis or "candidate"))[:80]
    return (f"{method.name}: no change" + (f" -- {prefix}" if prefix else ""))[:80]


def _one(method: Method, req: dict, rnd: Round) -> int:
    """The A shape: one candidate this call; the next call sees its score.

    The submission explicitly does **not** stop, even when it changed the
    harness: A's whole content is that the score the platform is about to
    measure is read before the next proposal. Only the rule's own `accept`
    ends it.
    """
    rnd.candidate, rnd.count = 1, 1
    dest = editor.candidate_path(req)
    reply, note, changed, files = _propose(method, rnd, dest)
    hypothesis = editor.hypothesis_of(reply) if changed else note
    return editor.report(
        req, method=method.name, harness_dir=dest, changed=changed, files=files,
        hypothesis=hypothesis, method_reported=_record(method, rnd, "A"),
        acceptance_rule=method.acceptance_rule,
        # `stop=False` is the shape: keep the loop alive so the next call reads
        # this candidate's score. A failure to propose is retried next round,
        # not turned into the end of the run.
        stop=False, label=_label(method, changed, hypothesis),
        extra={"shape": "A", "candidates_per_call": 1},
        note=(f"{method.name}: one candidate" if changed
              else f"{method.name}: nothing proposed -- {note}"))


def _batch(method: Method, req: dict, rnd: Round) -> int:
    """The B shape: k candidates from one parent, in one call.

    Nothing inside this loop reads a score: the platform measures the whole
    batch after the call returns, so a candidate here cannot be conditioned on a
    sibling's result. That absence *is* the shape, and the point records it
    (`steps_in_submission > 1`, `INTERFACE.md` §4.1.1).
    """
    count = max(1, min(int(method.candidates), rnd.remaining))
    rnd.count = count
    steps = []
    for i in range(count):
        rnd.candidate = i + 1
        dest = editor.candidate_path(req, f"candidate{i + 1}")
        reply, note, changed, files = _propose(method, rnd, dest)
        hypothesis = (editor.hypothesis_of(reply) if changed else note)
        steps.append({
            "harness_dir": dest, "changed": changed, "files": files,
            "label": _label(method, changed, hypothesis, prefix=f"[{i + 1}/{count}] "),
            "edit_kind": "harness_source" if changed else "none",
            "hypothesis": hypothesis,
            "method_reported": _record(method, rnd, "B", candidate=i + 1,
                                       candidates=count),
        })
    return editor.report_many(
        req, method=method.name, steps=steps,
        acceptance_rule=method.acceptance_rule,
        # A batch is the method's own schedule, so it says what it had to say.
        stop=True,
        note=f"{method.name}: {count} candidate(s) in one call",
        extra={"shape": "B", "candidates_per_call": count})


def _declined(method: Method, req: dict, rnd: Round) -> int:
    """The rule read the previous score and chose to propose nothing more."""
    dest = editor.candidate_path(req, "declined")
    editor.apply_edits(rnd.parent, dest, [])       # a clean, unchanged harness
    why = (f"{method.name}: the rule declined another proposal "
           f"(previous score {rnd.previous_score})")
    return editor.report(
        req, method=method.name, harness_dir=dest, changed=False,
        hypothesis=why, edit_kind="none",
        method_reported=_record(method, rnd, "declined"),
        acceptance_rule=method.acceptance_rule, stop=True,
        label=f"{method.name}: declined", note=why)


def run(method: Method) -> int:
    """The whole lifetime of one method invocation, from stdin to stdout.

    A method subclasses `Method`, declares its four decisions, and calls this.
    """
    req = editor.read_request()
    editor.require_api(req)
    base = Path(req["base_harness"])
    rnd = Round(req, base, editor.load_history(base))
    try:
        rnd.parent = Path(method.parent(rnd))
    except Exception:                                          # noqa: BLE001
        rnd.parent = rnd.base
    try:
        if not method.accept(rnd):
            return _declined(method, req, rnd)
        if int(method.candidates) <= 1:
            return _one(method, req, rnd)
        return _batch(method, req, rnd)
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        # A failure this function did not anticipate is a real error, not a
        # decision: `editor.fail` records the reason and writes a trajectory, so
        # the platform reports "the method failed" instead of "no trajectory".
        return editor.fail(req, method=method.name, exc=exc, base=base,
                           state={"shape": "A" if int(method.candidates) <= 1
                                  else "B", "round": rnd.index})
