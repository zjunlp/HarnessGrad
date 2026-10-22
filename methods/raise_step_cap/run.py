#!/usr/bin/env python3
"""Positive control: one known-good edit, so the instrument can be tested before a method is.

Every other method here proposes something whose value is unknown, which is what makes a null
result from one uninterpretable: "the instrument cannot see a real improvement" and "the method
proposed nothing useful" look identical on the curve, and 101 of the corpus's 145 candidate
rounds have no discordant pair at all. This method removes that ambiguity for one run. It makes
a single edit whose direction is argued from the harness's own source, so the curve either moves
or the measurement chain is what is broken.

The edit is `MAX_STEPS`, 32 -> 50. The harness already names this lever and states the condition
under which it is legitimate (`base_harness/loop/agent.py:54-57`):

    Raising this does not make the harness cleverer -- `legacy/adapters/harnessgrad_domain/`
    already names this lever as the trap ("helps only if the model is already using the steps
    it has"). Here the trace shows it *was* using them, which is exactly when the lever is
    legitimate.

Measured on alfworld's 134 archived trajectories, the condition holds: of the 71 failures, 37
stop at exactly the 32-step cap while passing tasks need a median of 14 iterations, and there
are no parse errors and no context trims. 50 is not a round number chosen for effect -- it is
that dataset's own `STEP_BUDGET` (`data/datasets/alfworld/lib/gym_env.py:44`), and a harness cap
below it makes the *harness* the thing that ends the episode early.

One line, one file, so the attribution is unambiguous: whatever `editable_surface_touched`
reports should be `agent.py` and nothing else.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import emit, read_request, require_api  # noqa: E402

#: The exact line in `base_harness/loop/agent.py`. Absent means this is not that harness, and
#: the method reports `changed: false` rather than claiming an edit it did not make -- the same
#: discipline `echo_base` uses for its marker.
MARKER = 'MAX_STEPS = int(os.environ.get("HG_AGENT_MAX_STEPS", "32"))'

REPLACEMENT = (
    "# Raised from 32 by `methods/raise_step_cap` (the positive control). Measured on\n"
    "# alfworld's 134 archived trajectories: 37 of the 71 failures stopped at exactly this\n"
    "# cap while passing tasks needed a median of 14 iterations, so the condition this file\n"
    "# states above -- the lever is legitimate when the model is already using the steps it\n"
    "# has -- holds here. 50 is that dataset's own STEP_BUDGET, so the harness stops being\n"
    "# the thing that ends the episode before the environment does.\n"
    'MAX_STEPS = int(os.environ.get("HG_AGENT_MAX_STEPS", "50"))'
)

EDIT_KIND = "control_max_steps"
LABEL = "raise MAX_STEPS to the environment's budget"


def _one_step(base: Path, dest: Path) -> bool:
    """Copy the harness and raise the cap. `False` when the marker is not there."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    agent = dest / "agent.py"
    if not agent.exists():
        return False
    src = agent.read_text()
    if MARKER not in src:
        return False
    agent.write_text(src.replace(MARKER, REPLACEMENT, 1))
    return True


def main() -> int:
    req = read_request()
    require_api(req)

    base = Path(req["base_harness"])
    mode = req.get("mode", "B")

    if mode == "A":
        dest = Path(req["workspace"]) / "next"
        changed = _one_step(base, dest)
        Path(req["trajectory_out"]).write_text(json.dumps({
            "steps": [{
                "harness_dir": str(dest),
                "label": LABEL,
                "edit_kind": EDIT_KIND,
                "claimed_cost": {"generation_tokens": 0},
                "method_reported": {"score": None},
            }],
            "trajectory_shape": "sequence",
            "nominated": 0,
            "provenance": {"acceptance_rule": {
                "text": "strict improvement", "source": "positive control",
                "calibrated": False}},
        }, indent=1))
        emit({"steps": 1, "changed": changed, "generation_tokens": 0})
        return 0

    work = Path(req["workspace"])
    work.mkdir(parents=True, exist_ok=True)

    # Step 0: the base harness verbatim. H0 has to be this run's own baseline, not an
    # archived number, because the archived alfworld figure predates the fidelity fixes.
    step0 = work / "step0"
    if step0.exists():
        shutil.rmtree(step0)
    shutil.copytree(base, step0)

    # Step 1: the one edit, in a fresh copy.
    step1 = work / "step1"
    changed = _one_step(base, step1)

    Path(req["trajectory_out"]).write_text(json.dumps({
        "steps": [
            {"harness_dir": str(step0), "label": "base (unmodified)",
             "edit_kind": "none", "claimed_cost": {},
             "method_reported": {"score": None}},
            {"harness_dir": str(step1), "label": LABEL,
             "edit_kind": EDIT_KIND, "claimed_cost": {"generation_tokens": 0},
             "method_reported": {"score": None}},
        ],
        "nominated": 1,
        "trajectory_shape": "sequence",
        "curve_drawn": "per_step",
        "selected_on_reported_set": False,
        "selection_pool_size": 2,
        "rhythm": {"note": "positive control; two scripted steps, no model in the method"},
        "provenance": {
            "method": "raise_step_cap (positive control)",
            "acceptance_rule": {"text": "strict improvement",
                                "source": "positive control", "calibrated": False},
        },
    }, indent=1))

    emit({"steps": 2, "changed": changed, "generation_tokens": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
