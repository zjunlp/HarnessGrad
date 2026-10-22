#!/usr/bin/env python3
"""Reference method: returns the base harness unchanged, plus one edit.

It exists to make the decoupling testable. The first step is the base harness
untouched -- the control every method must beat, and proof that the platform
measures what the method actually produced rather than what it claims. The
second step changes one file, so the curve has something in it.

A real method replaces this file. Nothing else about the platform changes.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import emit, read_request, require_api  # noqa: E402


def _one_step(base: Path, dest: Path) -> bool:
    """Produce the next harness from the given one. Returns whether it changed.

    Mode A calls a method once per round with the *current* harness as the base;
    mode B hands over the original base and lets the method run its own loop. The
    same file serves both -- which is the point of putting the method outside the
    harness: how often it is called is the platform's business, what it does is
    the method's.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    agent = dest / "agent.py"
    if not agent.exists():
        return False
    src = agent.read_text()
    old = '''    if "answer.txt exists" in task_text or "already" in task_text:'''
    new = '''    import re as _re
    _m = _re.search(r"the word ([A-Za-z_]+)", task_text)
    if _m:
        return json.dumps({"answer": _m.group(1)})
    if "answer.txt exists" in task_text or "already" in task_text:'''
    if old not in src:
        return False
    agent.write_text(src.replace(old, new, 1))
    return True


def main() -> int:
    req = read_request()
    require_api(req)

    base = Path(req["base_harness"])
    mode = req.get("mode", "B")

    if mode == "A":
        # One round: improve the current harness by one step and report it.
        dest = Path(req["workspace"]) / "next"
        changed = _one_step(base, dest)
        Path(req["trajectory_out"]).write_text(json.dumps({
            "steps": [{
                "harness_dir": str(dest),
                "label": "one improvement step",
                "edit_kind": "prompt_rule",
                "claimed_cost": {"generation_tokens": 0},
                "method_reported": {"score": None},
            }],
            "trajectory_shape": "sequence",
            "nominated": 0,
            "provenance": {"acceptance_rule": {
                "text": "strict improvement", "source": "reference",
                "calibrated": False}},
        }, indent=1))
        emit({"steps": 1, "changed": changed, "generation_tokens": 0})
        return 0
    work = Path(req["workspace"])
    work.mkdir(parents=True, exist_ok=True)

    # Step 0: the base harness, copied verbatim. A method that cannot beat this
    # has not improved anything.
    step0 = work / "step0"
    if step0.exists():
        shutil.rmtree(step0)
    shutil.copytree(base, step0)

    # Step 1: one edit, in a fresh copy.
    step1 = work / "step1"
    if step1.exists():
        shutil.rmtree(step1)
    shutil.copytree(base, step1)
    agent = step1 / "agent.py"
    if agent.exists():
        src = agent.read_text()
        old = '''    if "answer.txt exists" in task_text or "already" in task_text:'''
        new = '''    import re as _re
    _m = _re.search(r"the word ([A-Za-z_]+)", task_text)
    if _m:
        return json.dumps({"answer": _m.group(1)})
    if "answer.txt exists" in task_text or "already" in task_text:'''
        if old in src:
            agent.write_text(src.replace(old, new, 1))

    Path(req["trajectory_out"]).write_text(json.dumps({
        "steps": [
            {"harness_dir": str(step0), "label": "base (unmodified)",
             "edit_kind": "none", "claimed_cost": {},
             "method_reported": {"score": None}},
            {"harness_dir": str(step1), "label": "read the answer from the task",
             "edit_kind": "prompt_rule", "claimed_cost": {"generation_tokens": 0},
             "method_reported": {"score": 1.0}},
        ],
        "nominated": 1,
        "trajectory_shape": "sequence",
        "curve_drawn": "per_step",
        "selected_on_reported_set": True,
        "selection_pool_size": 2,
        "rhythm": {"note": "reference method; two scripted steps"},
        "provenance": {
            "method": "echo_base (reference only)",
            "acceptance_rule": {"text": "strict improvement",
                                "source": "reference", "calibrated": False},
        },
    }, indent=1))

    emit({"steps": 2, "generation_tokens": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
