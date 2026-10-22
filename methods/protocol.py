"""The contract between the platform and a method.

A method is code that lives OUTSIDE the harness. It is given a base harness and
produces candidate harnesses; the platform then measures those candidates itself.

Why outside
-----------
The first design put the method inside the harness (`base_harness/<name>/old-method-design/`).
That made the method inseparable from one artifact: it could only ever improve
the harness it shipped with, and two methods could not be compared because each
faced a different starting point. Keeping the method external is what makes
"the same method applied to any base harness" possible at all.

The exchange, both directions
-----------------------------
Platform -> method (stdin, one JSON object):

    {
      "platform_api_version": "0.1.0",
      "base_harness": "/abs/path/to/a/pristine/copy/of/the/base/harness",
      "workspace":    "/abs/path/the/method/may/write/in",
      "task_ids":     ["..."],           # what it will be measured on
      "trajectory_out": "/abs/path/where/to/write/the/trajectory.json",
      "improver": {                      # optional; present when the run resolved one
        "name": "codex", "version": "0.160.0", "sha256": "...",
        "model": "...", "path": "/abs/path/to/the/tool"
      }
    }

    The evidence the platform staged for this round is at
    `<workspace>/_harnessgrad/` -- `round.json`, `traces/`, `tasks/`, `history/`,
    `states/`, and the default `SKILL.md`. A method reads it from there rather than
    from the platform tree, which its sandbox hides.

    `improver` is the platform's own resolution, handed over because a method cannot
    resolve it: `tools/improver.py` is not on the method's disk either. `path` is the
    same one the curve point records, so a method cannot run a different improver than
    the one the run names.

Method -> platform (stdout, one JSON object): `{"steps": N, ...}`.

A method's own diagnostics belong in **`<workspace>/*.log`**. The platform copies those
files into `runs/<id>/method_logs/round-<n>/` before the workspace is deleted, so the one
question normally asked after a run -- "why did the improver change nothing?" -- has an
answer in the record. The workspace is otherwise temporary: anything not written to
`trajectory_out`, and not a `*.log`, is gone when the round ends.

The method writes its trajectory to `trajectory_out`. Each step names a
**directory holding a complete harness**, not a diff and not a commit:

    {"harness_dir": "/abs/path/...", "label": "...", "edit_kind": "...",
     "claimed_cost": {...}, "method_reported": {"score": ...}}

`harness_dir` must satisfy `docs/writing_a_harness.md` — the same contract the
base harness satisfies. That is the whole point: a method's output is measured by
the same rules as the base it started from.
"""
from __future__ import annotations

import json
import os
import sys

PLATFORM_API_VERSION = "0.1.0"


def read_request() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("method: empty request on stdin")
    return json.loads(raw)


def emit(response: dict) -> None:
    """The single response object. Nothing else may go to stdout -- diagnostics
    belong on stderr, or the platform's parser breaks and a working method looks
    like a failed one.

    Two fields are filled in here rather than left to each method, because every
    method owes the platform the same two answers and the ones that forget are the
    ones whose curves cannot be read afterwards:

    * `method_model` -- which model did the improving. The platform records the
      harness's model itself (`identity.agent_model`); the method's it can only be
      told. Without it, a curve that moved cannot say whether a better improver or
      a luckier harness produced the move. A method that sets the field explicitly
      wins: a method may use several models, and this is a declaration, not a fact
      the platform can check.
    * `changed` -- whether the method did anything. Defaulted to `False`, the
      conservative reading: claiming a change that did not happen mints a checkpoint
      that differs from its parent by nothing, which is how a control curve starts
      to move.
    """
    response.setdefault("method_model", os.environ.get("HG_METHOD_MODEL") or None)
    response.setdefault("changed", False)
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


def require_api(request: dict) -> None:
    got = request.get("platform_api_version")
    if got != PLATFORM_API_VERSION:
        raise SystemExit(
            f"method: platform_api_version {got!r} != {PLATFORM_API_VERSION!r}; "
            "the platform API is versioned and a method may not change it"
        )


# --------------------------------------------------- which score a method may read --

def studied_score(point: dict) -> float | None:
    """The score on the side this method was **allowed to study**, or None.

    `INTERFACE.md` §2.6: a run covers exactly one side, so `score_kind` says what
    `score` *is*.

    * `"training"` -- the method has read the traces of precisely the tasks it is being
      scored on (that is what training means), so `score` **is** the diagnostic score.
      `train_score` is absent by design on such a point: with one task set it would be
      a copy of `per_task`. `driver.py:_curve_point` says so beside the field.
    * `"exam"`, or a record predating `score_kind` entirely -- `score` is the exam and a
      method must not read it (§2.3). `train_score` is the diagnostic when the record
      is old enough to carry both sides.

    The branch is made on `score_kind` and **never** by trying `score` first and falling
    back: a fallback that reaches the exam is the leak this function exists to prevent.

    Why this is here rather than in each method: it is one rule, and six methods had
    written it six times. The first version of the rule was "read `train_score`", which
    was correct before §2.6 and is `null` on every train run after it. Measured on
    `loop` + `terminal_bench` + `rrsi`: `train_scores: [null, null, null]`,
    `S_star: null`, `rule_evaluated: false` -- the rule never fired, every round
    reported "no edit", and a crashed harness produced the same reading. Two of the six
    had a fallback that covered for it and four did not, which is what a duplicated rule
    looks like when the rule changes.
    """
    if point.get("score_kind") == "training":
        value = point.get("score")
    else:
        value = point.get("train_score")
    return float(value) if isinstance(value, (int, float)) else None


def studied_per_task(point: dict) -> dict:
    """Per-task scores on the side this method was allowed to study (see above).

    Same rule as `studied_score`, for the callers that need the breakdown rather than
    the mean -- attribution ("did my edit do what I predicted") is about which tasks
    moved, not about the average.
    """
    if point.get("score_kind") == "training":
        return dict(point.get("per_task") or {})
    if point.get("train_per_task"):
        return dict(point["train_per_task"])
    # No `train_per_task`: either a record predating the field, or a run with no split at
    # all. With no split there was only ever one measurement, so `per_task` is that one.
    # With a split present it is the exam and reading it is the leak -- return nothing
    # rather than the wrong side.
    if not point.get("split"):
        return dict(point.get("per_task") or {})
    return {}


def studied_task_ids(point: dict) -> list[str]:
    """The task ids behind `studied_per_task`, in the order the record carries them."""
    return sorted(studied_per_task(point))
