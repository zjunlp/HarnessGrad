#!/usr/bin/env python3
"""Control method: produces the base harness, unmodified, and nothing else.

Every real method must beat this. It is a *method* rather than a harness variant
because methods now live outside the harness: the control for "did the method
improve anything" has to be a method that does nothing, not a harness that cannot
be improved.

Its curve is the floor. Under mode A it reports `changed: false` so the platform
stops after one round instead of paying for repeats of the same state; under mode
B it emits a single step. Either way the curve should be flat, and a flat curve
here is what makes a rising curve elsewhere mean something.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import emit, read_request, require_api  # noqa: E402


def main() -> int:
    req = read_request()
    require_api(req)

    dest = Path(req["workspace"]) / "unmodified"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(Path(req["base_harness"]), dest)

    step = {
        "harness_dir": str(dest),
        "label": "base harness (control)",
        "edit_kind": "none",
        "claimed_cost": {"generation_tokens": 0},
        "method_reported": {"score": None,
                            "note": "control method; it changes nothing"},
    }
    Path(req["trajectory_out"]).write_text(json.dumps({
        "steps": [step],
        "trajectory_shape": "sequence",
        "nominated": 0,
        "selection_pool_size": 1,
        "provenance": {
            "method": "noop (control)",
            "acceptance_rule": {"text": "accept nothing", "source": "control",
                                "calibrated": False},
        },
    }, indent=1))

    # `changed: false` is how a method tells the platform there is no point
    # running another round from this state.
    emit({"steps": 1, "changed": False, "generation_tokens": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
