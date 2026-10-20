#!/usr/bin/env python3
"""DGM with a **specific** failure target -- the ablation `methods/dgm` cannot be asked for twice.

`methods/dgm/run.py` picks the round's target from the parent's per-task record: when half or
more of the tasks fail it says "N/90 tasks fail -- this is the broad failure mode" and stops
there. On AppWorld that is every round (56-89 of 90 fail), so the editor was asked to fix
"everything" fifteen times, wrote a new broad mechanism each time, and traded 17-20 of the 34
tasks the best round had solved for 5-14 new ones (findings §121). The curve shows it: .378 at
r10, then .289/.233/.289/.315/.244.

Upstream DGM does not work that way. `DGM_outer.py:111-148` chooses among concrete entries, and
two of the four are properties of the *episode* rather than of the expected answer:

* `solve_empty_patches` -- the agent produced no patch at all (we record whether any step
  submitted);
* `solve_contextlength` -- the agent's context was trimmed (we record `usage.context_trims`).

The platform withholds gold, so the other two (`solve_stochasticity`, and "the worst task" which
needs a continuous per-task score) stay out of reach. This variant uses the two that are
reachable, which makes the target a *group of tasks* instead of a sentence.

**This is a variant, not the port.** It exists so the target substitution can be measured
against the original, and it is named separately for exactly that reason: a run of this is
`dgm_targeted`, and its numbers are not DGM's. The whole delta is one environment variable read
by `methods/dgm/run.py`; everything else -- archive, `sigmoid(10*(s-0.5))/(1+children)`,
`keep_all`, the batch size -- is byte-identical.
"""
from __future__ import annotations

import os
import runpy
from pathlib import Path

os.environ["HG_DGM_TARGET_MODE"] = "trace"

# The port itself, executed as if it had been named on the command line: the method interface is
# stdio (a request JSON in, a reply JSON out), so delegating is a matter of running the same
# module with the same stream and letting its `__main__` guard fire.
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent.parent / "dgm" / "run.py"),
                   run_name="__main__")
