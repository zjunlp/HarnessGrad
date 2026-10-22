#!/usr/bin/env python3
"""The generic diagnose-and-edit method -- and the editor every other method composes with.

This is the loop every published method runs in some form:

    1. read the base harness
    2. read the traces the platform staged  (`_harnessgrad/traces/`)
    3. ask a model what to change, given only what it can see
    4. write the changed files into a candidate directory
    5. hand the candidate back; the PLATFORM measures it

Step 5 is where this file stops on purpose. A method that scores its own candidate
is a method that can report an improvement it did not make.

What it can and cannot see
--------------------------
It sees the trace of each task: the tool calls, their outputs, and how the run
ended. It does **not** see the expected answers -- those are the platform's, and a
method that can read them is not being measured, it is being helped. That
restriction is why a diagnosis can fail: on a task set where the base harness is
failing for a reason no edit can fix (the model simply does not know the answer),
the honest outcome is `no_change`, and this method is allowed to say so.

A defect this file carried for its whole life
---------------------------------------------
Until the shared editor was extracted, this method read the traces from
`request["workspace"]`. That is the method's own empty scratch directory.
`INTERFACE.md` §4.7 puts `_harnessgrad/` inside the **harness** directory, so the
read always failed and every prompt this method ever built said
`(no traces available)`. It proposed edits from the harness source alone while
this docstring claimed it read how the harness behaved.

Measured, not guessed: with a trace staged at the contract path and an empty
workspace, the old code reported `(no traces available)` and the current code
reports the trace. `methods/editor.py:channel()` is now the only place that
decides where to look, so this cannot regress in one method without regressing in
all of them.

What this method is *not*
-------------------------
It is not an RSI method with a rule of its own: it is the plain hill-climb with no
selection statistic, no archive, no acceptance test beyond the platform's own
measurement. Published methods differ from it precisely in the rule they add
around it -- SICA's confidence bound, DGM's archive, AHE's constraint level. It is
kept because it is the shared editor and because it is the honest control for
"does the rule add anything over the plain loop".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Called through the module, never `from editor import ask`. A method that binds
# the editor's functions at import time cannot be tested with the model stubbed
# out, and the stub is the only way to check what the method actually asked for.
import editor                                                  # noqa: E402


def failure_digest(base: Path) -> str:
    """Why each task failed, taken from the platform's per-task pages.

    The traces say what the harness *did*; they do not say why the task failed, and a
    model asked to improve a harness from traces alone has to infer the goal. The
    platform already writes the answer on every task page (§4.7): the check's own
    report -- which test failed, and the assertion -- and, when the harness died, its
    own stderr.

    Measured without this: `headless-terminal` scored 0 with six failing tests, the
    prompt held only the sources, and the model's edit deleted 147 lines of comment
    and changed no behaviour. It was not being lazy; it had nothing to reason from.
    """
    pages = sorted((editor.channel(base) / "tasks").glob("*.json"))
    entries = []
    for page in pages:
        try:
            data = json.loads(page.read_text())
        except (OSError, ValueError):
            continue
        detail = str(((data.get("verdict") or {}).get("detail")) or "")
        failed = [part.strip() for part in detail.split(" | ") if part.startswith("FAILED ")]
        lines = [f"- {data.get('task_id')}: score {data.get('score')}"]
        lines += [f"    {line}" for line in failed[:6]]
        if not failed and detail:
            lines.append(f"    {detail[:200]}")
        harness = data.get("harness") or {}
        if harness.get("exit_code"):
            tail = " ".join(str(harness.get("stderr") or "").split())[-240:]
            lines.append(f"    the harness exited {harness['exit_code']}: {tail}")
        entries.append("\n".join(lines))
    return "\n".join(entries)


def build_prompt(base: Path, req: dict) -> str:
    """The plain framing: everything the contract allows, nothing interpreted."""
    round_index = req.get("round_index", 1)
    incumbent = req.get("incumbent_score")
    failures = failure_digest(base)
    # No skill block here: the platform's skill is staged for every method and injected by
    # `editor.ask` on the way out (`editor.skill_block`), so pasting it in here too would
    # send it twice and make the point's `identity.skill_sha` name a prompt that does not
    # exist. A method that wants a *different* skill reads its own file and passes
    # `skill=False` to `editor.ask` / `editor.propose_and_apply`.
    return (
        f"Round {round_index}. The harness scored {incumbent} on the task set.\n\n"
        + (f"## Why each task failed, as the checks reported it\n{failures}\n\n"
           if failures else "")
    )


def main() -> int:
    req = editor.read_request()
    editor.require_api(req)
    base = Path(req["base_harness"])

    # **This method does not drive a CLI improver, and that is a decision left open.**
    # `methods/cli_improver.py` is where driving one lives (extracted from the deleted
    # `methods/codex`); this method could call it when `cli_improver.applies(req)`, and for
    # one afternoon it did -- which silently changed what a *default* run does, because the
    # registry's default improver is codex, a CLI. The trade-off is recorded as an open
    # decision in `INTERFACE.md` §4.49 rather than settled here.

    try:
        reply = editor.ask(build_prompt(base, req), base=base)
    except SystemExit:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return editor.fail(req, method="llm_improver", exc=exc, base=base)

    dest = editor.candidate_path(req)
    if "no_change" in reply:
        changed, files, note = editor.apply_edits(base, dest, [])
        return editor.report(req, method="llm_improver", harness_dir=dest, changed=False,
                      hypothesis=str(reply["no_change"])[:200], edit_kind="none",
                      acceptance_rule="a model proposes one edit; the platform "
                                      "decides whether it helped",
                      method_reported={"rounds_seen":
                                       [p.get("round") for p in editor.load_history(base)]})

    changed, files, note = editor.apply_edits(base, dest, reply.get("files"))
    if not changed:
        # Reported, not swallowed: a dropped edit and a method that chose to do
        # nothing are different events, and the platform must be able to tell them
        # apart in the record.
        return editor.report(req, method="llm_improver", harness_dir=dest, changed=False,
                      hypothesis=f"unusable proposal: {note}", edit_kind="none",
                      method_reported={"rejected": note})

    return editor.report(req, method="llm_improver", harness_dir=dest, changed=True,
                  files=files, hypothesis=editor.hypothesis_of(reply)[:200],
                  edit_kind="harness_source",
                  acceptance_rule="a model proposes one edit; the platform decides "
                                  "whether it helped",
                  label=f"{', '.join(files)}: {editor.hypothesis_of(reply)[:60]}")


if __name__ == "__main__":
    raise SystemExit(main())
