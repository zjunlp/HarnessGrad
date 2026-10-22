#!/usr/bin/env python3
"""The B shape: k candidates submitted in one call, with no feedback inside it.

This is ours. It is not a port of any published method and claims none; the
*shape* is the whole content, and the shape is:

    one call -> k candidates from the same parent -> the platform measures them
    afterwards, in order, so no candidate can be conditioned on a sibling's score

`k` is the shape; the number is a knob (`HG_B_CANDIDATES`, default 3), capped by
the states the run still pays for. The method sees the *previous call's* score
in `round.previous_score`, but it deliberately does not use it to shape the
batch: there is no selection to make inside the batch, because there is nothing
to select on yet. That absence is the shape, and the platform records it
(`steps_in_submission > 1`).

Everything else -- staging from a chosen parent, dispatch to the resolved
improver (a CLI gets paths, a chat endpoint gets inlined content, decided in
`editor.ask`), apply-and-validate with one repair, reporting, and error handling
-- is `methods/base.py`.

The platform protocol is `methods/protocol.py` and `INTERFACE.md` §4: one JSON
object in on stdin, one JSON object out on stdout, a trajectory at
`trajectory_out`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import base                                                      # noqa: E402

#: How many candidates one call submits. A knob, not the shape.
CANDIDATES = max(1, int(os.environ.get("HG_B_CANDIDATES", "3")))


class B(base.Method):
    """k candidates in one call, no score visible between them."""

    name = "b"
    candidates = CANDIDATES
    acceptance_rule = (
        "k candidates in one call, all staged from the same parent; no candidate "
        "can see a sibling's score, because the platform measures the batch only "
        "after the call returns")

    def build_prompt(self, rnd):
        return (
            f"{rnd.facts()}.\n\n"
            # The only thing B adds to the platform's ask is the fact that makes B a
            # different shape: siblings exist and their results are not visible. It does
            # **not** restate the ask itself -- see `methods/a/run.py` for why.
            "You are improving an agent harness. Use the evidence beside you: the "
            "tasks that failed, what the harness did on them, and its own source.\n\n"
            f"This is independent attempt {rnd.candidate} of {rnd.count} in this call. "
            "No sibling's result is available to you and none of them can see yours, "
            "so do not assume one of them worked and do not try to be a small "
            "variation of another: the value of a batch is coverage.\n\n"
            "State in one sentence what you changed and why. If the evidence does "
            "not support a change, say so instead of guessing."
        )

    def state(self, rnd):
        return {"candidates_per_call": rnd.count, "candidate": rnd.candidate}


def main() -> int:
    return base.run(B())


if __name__ == "__main__":
    raise SystemExit(main())
