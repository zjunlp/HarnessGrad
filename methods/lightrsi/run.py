#!/usr/bin/env python3
"""LightRSI: a wrapper method that decides whether a round is worth measuring.

It is a *method*, not a change to the platform: the platform calls this entrypoint exactly
as it would call any other method, and this entrypoint calls the base method as a subprocess
over the same stdin/stdout contract. The base method is not modified, and it does not know it
is wrapped -- which is what makes `sica_ci` and `sica_ci+LightRSI` comparable from one
starting point.

What it decides in this round
-----------------------------
Two cases, both decidable before any task runs, and both of them rounds the platform would
otherwise measure from scratch:

* **the candidate is byte-identical to the incumbent.** The base method returned an
  unmodified tree, or edited something it then reverted. Measured in this project's own
  runs: 3 of 76 rounds in one dataset and 15 of 199 in another.
* **the incumbent passes every scored task.** Nothing can improve on a full score, so the
  round cannot change a number. Measured: 19 of 76 rounds in one dataset, 0 of 199 in the
  other.

In both cases the reply declares `decision: "no_change"` with the `incumbent_round` it
means. The platform checks the claim (`mode_a._declared_no_change`): the round must exist,
its `harness_sha` must equal the tree just committed, and the earlier measurement must have
used the same `trials`. It then reuses that measurement instead of spending a task-set run.
A claim the platform cannot verify is recorded on the point (`declaration_ignored`) and the
candidate is measured normally -- so a wrapper that says "no change" while handing over a
changed tree leaves a mark rather than a silent skip.

Why the wrapper makes the claim rather than a command-line flag
--------------------------------------------------------------
`--reuse-identical` exists and does the reuse, but it is a platform option: turning it on
for one arm of a comparison and not the other makes the comparison differ by a flag instead
of by a decision. Here the trigger is the method's own rule, its record names the round it
kept, and both arms run the same command line.

The saturation case has no flag at all: nothing in the platform stops a round whose
incumbent already passes everything, so the wrapper is the only thing that can.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor  # noqa: E402  (the shared editor: the failure path is the platform's)
from protocol import emit, read_request, require_api  # noqa: E402

#: The platform stages the method's evidence here, inside the harness tree it hands over.
#: It is the *method's* input, not part of the artifact being measured -- `mode_a` deletes
#: it from the workspace before committing a candidate -- so every hash this file computes
#: skips it, and a copy of a harness omits it.
CHANNEL = "_harnessgrad"

NO_CHANGE = "no_change"


def tree_digest(root: Path) -> str:
    """A content address over a harness tree: file paths and bytes, nothing else.

    Two trees get the same digest exactly when they would be committed as the same state.
    `.git` and the channel are excluded for the reason `CHANNEL` documents: neither is part
    of the artifact, and `commit_state` does not see them either.
    """
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in (CHANNEL, ".git"):
            continue
        digest.update(rel.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_harness(src: Path, dst: Path) -> None:
    """Copy a harness without its `.git` or its channel, so the copy is the artifact."""
    def ignore(_dir, names):
        return [n for n in names if n in (CHANNEL, ".git")]
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def incumbent_point(base: Path, round_index: int) -> dict | None:
    """The incumbent's curve point as the platform staged it, or None when there is none.

    Round 1's incumbent is round 0 (H0), so the index is `round_index - 1`. The file is the
    same record the platform keeps, which means the judgement below is made on the numbers
    the run reports rather than on a re-derivation.
    """
    path = Path(base) / CHANNEL / "history" / f"round-{max(round_index - 1, 0)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def all_passed(point: dict | None) -> bool:
    """True when every task the incumbent was scored on passed, and none was unmeasured.

    `per_task` is the authority rather than `n_passed`: one point in this project's record
    carries `n_passed: 99` beside a score of 1.0 and 100 values of 1.0, so a check on the
    counter would have declared that round unsaturated. An unmeasured task is not a pass
    (`invalid != 0`), so a point with any invalid task is not saturated either.
    """
    if not point:
        return False
    per_task = point.get("per_task") or {}
    if not per_task:
        return False
    if point.get("n_invalid") or point.get("n_harness_failed"):
        return False
    return all(value is not None and value >= 1 for value in per_task.values())


def base_entrypoint(base: str) -> Path:
    """Where the base method's `run.py` is.

    A bare name resolves inside this platform's `methods/` directory, which the method's
    sandbox exposes by design (`harnessgrad/methods.py:_method_sandbox_plan`: "the platform
    is hidden except the directory its own program lives in"). A path is taken as given, so
    a test fixture outside the platform can be wrapped too.
    """
    if "/" in base or base.endswith(".py"):
        return Path(base).resolve()
    return Path(__file__).resolve().parents[1] / base / "run.py"


def call_base(entry: Path, request: dict, *, workspace: Path,
              trajectory_out: Path) -> tuple[dict, dict | None]:
    """Run the base method over the same contract, and read what it produced.

    The request is passed through unchanged except for the two paths this process owns: the
    base method's workspace (so its scratch files land somewhere this process can clean up)
    and its trajectory (so this process can read the steps before writing its own). Everything
    else -- the evidence channel, the task ids, the round index, the resolved improver -- is
    the platform's own request, because a wrapper that rewrote those would be changing the
    question instead of the answer.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    inner = {**request, "workspace": str(workspace),
             "trajectory_out": str(trajectory_out)}
    try:
        proc = subprocess.run([sys.executable, str(entry)], input=json.dumps(inner),
                              capture_output=True, text=True, errors="replace")
    except OSError as exc:
        return {"_error": f"the base method could not start: {exc}"}, None
    if proc.returncode != 0:
        return ({"_error": f"the base method exited {proc.returncode}",
                 "_stderr": proc.stderr[-1200:]}, None)
    try:
        reply = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return ({"_error": f"the base method's stdout is not JSON: {exc}",
                 "_stdout": proc.stdout[:400]}, None)
    trajectory = None
    if trajectory_out.is_file():
        try:
            trajectory = json.loads(trajectory_out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            trajectory = None
    return reply, trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True,
                        help="the method to wrap: a name under methods/, or a path to its run.py")
    args, _ = parser.parse_known_args()

    request = read_request()
    require_api(request)

    base_tree = Path(request["base_harness"])
    workspace = Path(request["workspace"])
    round_index = int(request.get("round_index") or 1)
    offset = round_index - 1
    point = incumbent_point(base_tree, round_index)

    # ------------------------------------------------------------------ case 1: saturated
    #
    # No score can improve on a full score, so the round cannot change any number this run
    # reports. The base method is not called at all: its proposal would be measured on a set
    # where every task already passes, which is the most expensive way to learn nothing.
    if all_passed(point):
        dest = workspace / f"incumbent_round{offset}"
        if dest.exists():
            shutil.rmtree(dest)
        copy_harness(base_tree, dest)
        step = {
            "harness_dir": str(dest),
            "label": f"unchanged: the incumbent (round {offset}) passes every scored task",
            "edit_kind": "none",
            "claimed_cost": {"generation_tokens": 0},
            "decision": NO_CHANGE,
            "incumbent_round": offset,
            "method_reported": {
                "lightrsi": {
                    "decision": NO_CHANGE,
                    "reason": "saturated incumbent: every scored task passes",
                    "incumbent_round": offset,
                    "incumbent_score": (point or {}).get("score"),
                    "base_method_called": False,
                    "digest": tree_digest(dest),
                },
            },
        }
        Path(request["trajectory_out"]).write_text(json.dumps({
            "steps": [step],
            "trajectory_shape": "sequence",
            "nominated": 0,
            "selection_pool_size": 1,
            "provenance": {
                "method": f"{args.base}+LightRSI",
                "base_method": args.base,
                "acceptance_rule": {
                    "text": "no change when the incumbent already passes every scored task",
                    "source": "lightrsi",
                    "calibrated": False,
                },
            },
        }, indent=1, ensure_ascii=False))
        review = workspace / "lightrsi.log"
        review.write_text(
            f"round {round_index}: incumbent round {offset} passes every scored task; "
            f"declared no_change without calling {args.base}\n", encoding="utf-8")
        # `changed` stays true: this round bought no measurement, but the run is not over.
        # Ending it is a separate decision (the round-level stop), not a side effect of one
        # saturated round.
        emit({"steps": 1, "changed": True, "generation_tokens": 0})
        return 0

    # ------------------------------------------------- case 2: ask the base, then compare
    reply, trajectory = call_base(
        base_entrypoint(args.base), request,
        workspace=workspace / "base_workspace",
        trajectory_out=workspace / "base_trajectory.json")
    if "_error" in reply:
        # The base method failed. Reported through the shared editor rather than swallowed:
        # a wrapper that hid its base's failure would put the blame on the harness, and the
        # platform's own step handler already knows how to record "the step did not finish".
        # `editor.fail` passes `stop=False`, so one transient failure costs a round, not the
        # rest of the budget.
        return editor.fail(request, method=f"{args.base}+LightRSI", base=base_tree,
                           exc=f"the base method failed: {reply['_error']}",
                           state={"base_stderr": (reply.get("_stderr") or "")[-600:]})
    steps = list((trajectory or {}).get("steps") or [])
    here = tree_digest(base_tree)
    declared = 0
    for step in steps:
        candidate = step.get("harness_dir")
        if not candidate or not Path(candidate).is_dir():
            continue
        if tree_digest(Path(candidate)) != here:
            continue
        # The base method produced the incumbent. Saying so is what turns a full re-measurement
        # into a record of the round that already holds the number.
        step["decision"] = NO_CHANGE
        step["incumbent_round"] = offset
        step.setdefault("method_reported", {})
        step["method_reported"] = {
            **step["method_reported"],
            "lightrsi": {
                "decision": NO_CHANGE,
                "reason": "the candidate's content is identical to the incumbent",
                "incumbent_round": offset,
                "digest": here,
            },
        }
        declared += 1

    provenance = dict((trajectory or {}).get("provenance") or {})
    provenance["base_provenance"] = dict(provenance)
    provenance["method"] = f"{args.base}+LightRSI"
    provenance["base_method"] = args.base
    provenance.setdefault("acceptance_rule", {
        "text": "no change when the candidate's content equals the incumbent's",
        "source": "lightrsi",
        "calibrated": False,
    })
    Path(request["trajectory_out"]).write_text(json.dumps({
        **{k: v for k, v in (trajectory or {}).items() if k not in ("provenance", "steps")},
        "steps": steps,
        "provenance": provenance,
    }, indent=1, ensure_ascii=False))
    emit({"steps": len(steps),
          "changed": bool(reply.get("changed", True)),
          "generation_tokens": int(reply.get("generation_tokens") or 0),
          "declared_no_change": declared})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
