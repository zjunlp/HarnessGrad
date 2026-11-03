"""FlashRSI evoprompt/__init__.py:132-214: GA survivors and pairwise DE."""

import random
import pytest
from flashrsi_helpers import P, member


def test_ga_standard_and_elitist_make_different_measured_survivors():
    seed = member(0, 0.9)
    first = [member(i, 0.8 - i * 0.1, info={"generation": 1}) for i in (1, 2)]
    child = [
        member(3, 0.1, info={"generation": 2}),
        member(4, 0.2, info={"generation": 2}),
    ]
    standard = P.evoprompt_population([seed, *first, *child], 2, survivor="standard")
    elite = P.evoprompt_population([seed, *first, *child], 2, survivor="elitist")
    assert [m.round for m in standard] == [3, 4]
    assert [m.round for m in elite] == [0, 1]


def test_de_requires_four_positions_and_replaces_only_its_target():
    with pytest.raises(ValueError):
        P.evoprompt_population([], 3, variant="de")
    seed = member(0, 0.5)
    init = [member(i, 0.4, info={"generation": 1}) for i in range(1, 4)]
    child = member(4, 0.6, info={"generation": 2, "target_slot": 2})
    pop = P.evoprompt_population([seed, *init, child], 4, variant="de")
    assert [m.round for m in pop] == [0, 1, 4, 3]
    parents = P.de_parents(pop, 0, random.Random(3))
    assert parents[0] == pop[0] and parents[-1] == P.best(pop)


def test_final_deployment_restores_global_best_even_if_latest_generation_lost_it(
    tmp_path, monkeypatch
):
    from flashrsi_helpers import runtime, fixture, capture

    points = [
        {
            "round": 0,
            "score_kind": "training",
            "score": 1,
            "per_task": {"t1": 1, "t2": 1},
        },
        {
            "round": 1,
            "score_kind": "training",
            "score": 0,
            "per_task": {"t1": 0, "t2": 0},
            "method_reported": {"flashrsi_port": {"generation": 1, "invocation": 1}},
        },
    ]
    base, req = fixture(tmp_path, points)
    req["epochs_remaining"] = 1
    (base / "_harnessgrad/states/round-0/prompt.md").write_text(
        "SELECTED_GLOBAL_BEST\n"
    )
    (base / "prompt.md").write_text("inferior generation\n")
    out = capture(monkeypatch)

    def forbidden(*a, **kw):
        raise AssertionError("final selection may not make an unmeasured proposal")

    monkeypatch.setattr(runtime.editor, "ask", forbidden)
    runtime.run("evoprompt", req)
    assert "failure" not in out, out
    assert (
        out["steps"][0]["harness_dir"] / "prompt.md"
    ).read_text() == "SELECTED_GLOBAL_BEST\n"
    assert out["stop"] is True and out["steps"][0]["changed"] is True
