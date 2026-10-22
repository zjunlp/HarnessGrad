#!/usr/bin/env python3
"""The A shape: one candidate per call, with the previous round's score in hand.

This is ours. It is not a port of any published method and claims none; the
*shape* is the whole content, and the shape is:

    one call -> one candidate -> the platform measures it -> the next call sees
    that measurement before it proposes again

So the method can use feedback, and `accept` below is the entirety of that use:
it is run with the previous round's studied score in `round.previous_score`, and
it decides whether another candidate is worth spending. A perfect score leaves
nothing to win, so the search stops there.

Everything else -- staging from a chosen parent, dispatch to the resolved
improver (a CLI gets paths, a chat endpoint gets inlined content, decided in
`editor.ask`), apply-and-validate with one repair, reporting, and error handling
-- is `methods/base.py`. This file is deliberately only the rule.

The platform protocol is `methods/protocol.py` and `INTERFACE.md` §4: one JSON
object in on stdin, one JSON object out on stdout, a trajectory at
`trajectory_out`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import base                                                      # noqa: E402


class A(base.Method):
    """One candidate per call, informed by the previous round's score."""

    name = "a"
    candidates = 1
    acceptance_rule = (
        "one candidate per call; the previous round's measured score is read "
        "before the next proposal, and the search stops once it reaches 1.0 "
        "(nothing left to win)")

    def build_prompt(self, rnd):
        # **No "propose one change" sentence here, on purpose.** The platform already
        # states the ask (`cli_improver.CHANGE_REQUEST`, prepended by `editor.ask`), and
        # it says the opposite: as many independent edits as the diagnosis requires, in
        # as many files as it takes. Measured (`v4`-`v10`): a rule that restated it as
        # "one change" added a second, weaker ask in the position the model reads last,
        # and seven of eight rules had done exactly that. The rule's own text is for what
        # is specific to *this* rule -- the shape and the round's state.
        return (
            f"{rnd.facts()}.\n\n"
            "You are improving an agent harness. Use the evidence beside you: the "
            "tasks that failed, what the harness did on them, and its own source. "
            "State in one sentence what you changed and why. If the evidence does "
            "not support a change, say so instead of guessing."
        )

    def accept(self, rnd):
        # The reason the A shape exists: the score from the previous round is in
        # hand *before* this decision. Nothing is left to win at a perfect score.
        return rnd.previous_score is None or rnd.previous_score < 1.0

    def state(self, rnd):
        return {"previous_score": rnd.previous_score}


def main() -> int:
    return base.run(A())


if __name__ == "__main__":
    raise SystemExit(main())
