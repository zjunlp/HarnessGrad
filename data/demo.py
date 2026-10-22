"""A tiny local dataset adapter.

Purpose: exercise the whole loop (rounds, curve points, ckpts, caching,
cross-method comparison) with zero network and zero cost, so the framework can
be validated before any paid evaluation is wired in.

The reference `loop` harness scores 0/8 on this by construction -- it has no
file-reading ability and always answers with the same token. That is intended:
a base harness with a visible gap is what makes the first curve a curve rather
than a flat line. Scoring is exact-match and lives in `eval/`, never here.
"""


from __future__ import annotations


#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "fixture"
#: The demo fixture's oracle submits the fixture's own reference; see
#: `data/dataset.schema.json` (`module.oracle_about`). Declared even though the lint
#: only requires it of published datasets, because `driver.py` records it on every run
#: and `tests/quality/test_split.py` reads the field off a real demo run.
ORACLE_SOURCE = "released"

TASKS: list[dict] = [
    {"task_id": "t01", "goal": "Answer with the word alpha."},
    {"task_id": "t02", "goal": "Answer with the word beta."},
    {"task_id": "t03", "goal": "Answer with the word gamma."},
    {"task_id": "t04", "goal": "Answer with the word delta."},
    {"task_id": "t05", "goal": "Answer with the word epsilon."},
    {"task_id": "t06", "goal": "Answer with the word zeta."},
    {"task_id": "t07", "goal": "Answer with the word eta."},
    {"task_id": "t08", "goal": "Answer with the word theta."},
]

SCORABLE: dict[str, str] = {
    "t01": "alpha", "t02": "beta", "t03": "gamma", "t04": "delta",
    "t05": "epsilon", "t06": "zeta", "t07": "eta", "t08": "theta",
}

#: The first four are the method's to study; the last four are the exam. A
#: dataset that declares no split is not silently split for it -- see
#: `data/scaffolds/registry.py`.
SPLIT = {
    "train": ["t01", "t02", "t03", "t04"],
    "eval": ["t05", "t06", "t07", "t08"],
}


def load() -> tuple[list[dict], dict[str, str]]:
    return TASKS, SCORABLE
