"""The component-attribution report: two questions, one honest denominator.

These tests build synthetic run roots rather than touching the real archive (which, at
the time of writing, has ten curve points per run and zero component attributions). Each
one pins a distinction the tool must not lose:

* an attributed round is counted per component and its gain is arithmetic on the same
  run's previous round -- including when that gain is negative;
* an unattributed point is counted as unattributed and **never** as a component, not even
  when the field is present and empty;
* the narrow-vs-broad split partitions the attributed rounds and its mean/median are the
  hand-computed values, with `n` counting rounds;
* `--min-rounds` drops whole studies, and a dropped study contributes nothing;
* nothing attributed still exits 0, prints an empty table, and says so in one line;
* the JSON has no timestamp in it, so two runs produce identical bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import component_attribution as ca

_ABSENT = object()


# ------------------------------------------------------------------------- fixtures

def _point(run_id: str, round_no: int, score, components=_ABSENT, *,
           method: str | None = "ahe", unattributed: list[str] | None = None) -> dict:
    """One curve point. `components is _ABSENT` means the field is not on the point at all."""
    point: dict = {"run_id": run_id, "round": round_no, "score": score}
    if method is not None:
        point["identity"] = {"method": method}
    if components is not _ABSENT:
        block: dict = {"components": list(components)}
        if unattributed is not None:
            block["unattributed"] = list(unattributed)
        elif not components:
            # The archive's common case: `agent.py` moved and no map claimed it.
            block["unattributed"] = ["agent.py"]
        point["editable_surface_components"] = block
    return point


def write_run(root: Path, run_id: str, points: list[dict], *,
              method: str | None = None, meta: bool = True) -> Path:
    """A run directory shaped the way the platform writes one."""
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    if meta:
        record = {"run_id": run_id, "harness": "loop", "dataset": "appworld"}
        if method:
            record["method_entrypoint"] = f"python3 /opt/harnessgrad/methods/{method}/run.py"
        (run / "run_meta.json").write_text(json.dumps(record), encoding="utf-8")
    (run / "curve.jsonl").write_text(
        "\n".join(json.dumps(point) for point in points) + "\n", encoding="utf-8")
    return run


def component(payload: dict, name: str) -> dict:
    for entry in payload["edited"]["by_component"]:
        if entry["component"] == name:
            return entry
    raise AssertionError(f"{name!r} has no row; got "
                         f"{[e['component'] for e in payload['edited']['by_component']]}")


def component_set(payload: dict, components: list[str]) -> dict:
    for entry in payload["gains"]["by_component_set"]:
        if entry["components"] == components:
            return entry
    raise AssertionError(f"{components!r} has no row; got "
                         f"{[e['components'] for e in payload['gains']['by_component_set']]}")


def single_component(payload: dict, name: str) -> dict:
    for entry in payload["gains"]["by_single_component"]:
        if entry["component"] == name:
            return entry
    raise AssertionError(f"{name!r} has no single-component row")


# ---------------------------------------------------------------------------- tests

def test_attributed_rounds_give_per_component_counts_and_gains(tmp_path):
    """The arithmetic of both questions, including a negative gain.

    Round 0 is unattributed (`[]` + `agent.py`), so it is not a component and has no
    previous round. Round 1 is `context` (+5.00 pp), round 2 is `context + tools`
    (−2.00 pp: the negative case), round 3 is `tools` (+7.00 pp).
    """
    root = tmp_path / "runs"
    write_run(root, "alpha-run", [
        _point("alpha-run", 0, 0.50, [], method="alpha"),
        _point("alpha-run", 1, 0.55, ["context"], method="alpha"),
        _point("alpha-run", 2, 0.53, ["tools", "context"], method="alpha"),
        _point("alpha-run", 3, 0.60, ["tools"], method="alpha"),
    ], method="alpha")

    payload = ca.build_report([str(root)])

    assert payload["points"] == {
        "total": 4,
        "attributed": 3,
        "unattributed": 1,
        "with_field": 4,
        "without_field": 0,
        "in_discarded_runs": 0,
        "breakdown": {"field_absent": 0, "declaration_unreadable": 0,
                      "no_component_named": 1, "no_path_moved": 0},
    }
    assert payload["runs"] == {"discovered": 1, "included": 1, "skipped": 0,
                               "duplicate_run_ids": []}

    # -- question 1: which component did the rounds edit ---------------------------------
    context = component(payload, "context")
    assert context == {"component": "context", "rounds": 2, "runs": 1,
                       "methods": {"alpha": 2}}
    tools = component(payload, "tools")
    assert tools["rounds"] == 2 and tools["runs"] == 1 and tools["methods"] == {"alpha": 2}
    assert payload["edited"]["by_method"] == [{
        "method": "alpha", "rounds": 3, "runs": 1, "unattributed_points": 1,
        "components": {"context": 2, "tools": 2},
    }]

    # -- question 2: the gain, by exact set ----------------------------------------------
    context_only = component_set(payload, ["context"])
    assert (context_only["rounds"], context_only["n"], context_only["unknown"]) == (1, 1, 0)
    assert context_only["mean_pp"] == pytest.approx(5.0)
    mixed = component_set(payload, ["context", "tools"])
    assert mixed["mean_pp"] == pytest.approx(-2.0)
    assert mixed["median_pp"] == pytest.approx(-2.0)
    assert component_set(payload, ["tools"])["mean_pp"] == pytest.approx(7.0)

    # A component touched by two rounds gets both gains, not the set's mean.
    assert single_component(payload, "context")["mean_pp"] == pytest.approx(1.5)
    assert single_component(payload, "context")["n"] == 2
    assert single_component(payload, "tools")["mean_pp"] == pytest.approx(2.5)

    overall = payload["gains"]["overall"]
    assert overall["rounds"] == 3 and overall["n"] == 3 and overall["unknown"] == 0
    assert overall["mean_pp"] == pytest.approx(10.0 / 3.0)
    assert overall["median_pp"] == pytest.approx(5.0)

    negative = [obs for obs in payload["observations"] if obs["gain_pp"] < 0]
    assert len(negative) == 1
    assert negative[0]["round"] == 2
    assert negative[0]["gain_pp"] == pytest.approx(-2.0)
    assert negative[0]["components"] == ["context", "tools"]

    markdown = ca.render_markdown(payload)
    assert "-2.00" in markdown


def test_an_unattributed_point_is_never_a_component(tmp_path):
    """Three ways to fail to attribute, and none of them becomes a component row."""
    root = tmp_path / "runs"
    write_run(root, "quiet-run", [
        _point("quiet-run", 0, 0.40),                              # field absent
        _point("quiet-run", 1, 0.45, []),                          # empty + a moved path
        _point("quiet-run", 2, 0.50, [], unattributed=[]),         # empty + nothing moved
    ])

    payload = ca.build_report([str(root)])

    assert payload["points"] == {
        "total": 3,
        "attributed": 0,
        "unattributed": 3,
        "with_field": 2,
        "without_field": 1,
        "in_discarded_runs": 0,
        "breakdown": {"field_absent": 1, "declaration_unreadable": 0,
                      "no_component_named": 1, "no_path_moved": 1},
    }
    assert payload["edited"]["by_component"] == []
    assert payload["edited"]["by_method"] == []
    assert payload["gains"]["by_component_set"] == []
    assert payload["gains"]["by_single_component"] == []
    assert payload["observations"] == []
    assert payload["gains"]["overall"] == {
        "rounds": 0, "runs": 0, "n": 0, "unknown": 0,
        "mean_pp": None, "median_pp": None}
    assert payload["split"]["narrow"]["rounds"] == 0
    assert payload["split"]["broad"]["rounds"] == 0

    # The points are visible in the unattributed table, under their method.
    assert payload["unattributed_by_method"] == [
        {"method": "ahe", "points": 3, "runs": 1}]

    markdown = ca.render_markdown(payload)
    assert "no attribution recorded" in markdown
    assert "| context" not in markdown


def test_narrow_vs_broad_arithmetic_is_hand_computable(tmp_path):
    """`narrow` is prompt/context only; `broad` is anything else.

    narrow-run: 0.30 -> 0.35 (+5), 0.35 -> 0.39 (+4); round 0 has no predecessor.
    mixed-run: 0.50 -> 0.47 (-3, context+tools), 0.47 -> 0.54 (+7, tools); round 0 is
    `context` and is narrow but has no predecessor.

    narrow: rounds 4 (narrow-run r0/r1/r2 and mixed-run r0), n 2, gains +5/+4
            -> mean 4.5, median 4.5, unknown 2.
    broad:  rounds 2, n 2, gains -3/+7 -> mean 2.0, median 2.0, unknown 0.
    all:    rounds 6, n 4, gains -3/+4/+5/+7 -> mean 3.25, median 4.5, unknown 2.
    """
    root = tmp_path / "runs"
    write_run(root, "narrow-run", [
        _point("narrow-run", 0, 0.30, ["context"], method="n"),
        _point("narrow-run", 1, 0.35, ["context"], method="n"),
        _point("narrow-run", 2, 0.39, ["context"], method="n"),
    ], method="n")
    write_run(root, "mixed-run", [
        _point("mixed-run", 0, 0.50, ["context"], method="m"),
        _point("mixed-run", 1, 0.47, ["context", "tools"], method="m"),
        _point("mixed-run", 2, 0.54, ["tools"], method="m"),
    ], method="m")

    payload = ca.build_report([str(root)])
    narrow, broad = payload["split"]["narrow"], payload["split"]["broad"]

    assert narrow == {"rounds": 4, "runs": 2, "n": 2, "unknown": 2,
                      "mean_pp": pytest.approx(4.5),
                      "median_pp": pytest.approx(4.5)}
    assert broad == {"rounds": 2, "runs": 1, "n": 2, "unknown": 0,
                     "mean_pp": pytest.approx(2.0),
                     "median_pp": pytest.approx(2.0)}
    assert payload["gains"]["overall"] == {
        "rounds": 6, "runs": 2, "n": 4, "unknown": 2,
        "mean_pp": pytest.approx(3.25), "median_pp": pytest.approx(4.5)}
    # The split partitions the attributed rounds; it is not two overlapping views.
    assert narrow["rounds"] + broad["rounds"] == payload["gains"]["overall"]["rounds"]


def test_min_rounds_filters_studies(tmp_path, capsys):
    """`--min-rounds 2` keeps the study with two attributed rounds, drops the other."""
    root = tmp_path / "runs"
    write_run(root, "two-rounds", [
        _point("two-rounds", 0, 0.40, ["context"]),
        _point("two-rounds", 1, 0.43, ["context"]),
    ])
    write_run(root, "one-round", [
        _point("one-round", 0, 0.50, [], method="other"),
        _point("one-round", 1, 0.52, ["control"], method="other"),
    ], method="other")

    unfiltered = ca.build_report([str(root)])
    assert unfiltered["runs"]["included"] == 2
    assert component(unfiltered, "control")["rounds"] == 1

    filtered = ca.build_report([str(root)], min_rounds=2)
    assert filtered["runs"] == {"discovered": 2, "included": 1, "skipped": 1,
                                "duplicate_run_ids": []}
    assert filtered["skipped"] == [{
        "run_id": "one-round", "method": "other", "method_source": "identity",
        "reason": "below_min_rounds",
        "detail": "1 attributed round(s) from 2 curve point(s); --min-rounds is 2",
        "points": 2, "attributed_rounds": 1,
    }]
    assert [entry["component"] for entry in filtered["edited"]["by_component"]] == ["context"]
    assert filtered["points"]["total"] == 2
    assert filtered["points"]["unattributed"] == 0

    # The flag is wired to the CLI, not only to the function.
    json_path, md_path = tmp_path / "attr.json", tmp_path / "attr.md"
    assert ca.main(["--runs", str(root), "--min-rounds", "2",
                    "--json", str(json_path), "--md", str(md_path)]) == 0
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["runs"]["included"] == 1
    assert [entry["run_id"] for entry in written["skipped"]] == ["one-round"]
    assert "below_min_rounds" in md_path.read_text(encoding="utf-8")
    capsys.readouterr()


def test_empty_input_exits_zero_with_an_empty_table_and_says_so(tmp_path, capsys):
    """Nothing to read is a successful read, not an error and not a zero."""
    root = tmp_path / "empty-runs"
    root.mkdir()
    json_path, md_path = tmp_path / "attr.json", tmp_path / "attr.md"

    assert ca.main(["--runs", str(root), "--json", str(json_path),
                    "--md", str(md_path)]) == 0

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["runs"]["discovered"] == 0
    assert payload["points"] == {
        "total": 0, "attributed": 0, "unattributed": 0,
        "with_field": 0, "without_field": 0, "in_discarded_runs": 0,
        "breakdown": {"field_absent": 0, "declaration_unreadable": 0,
                      "no_component_named": 0, "no_path_moved": 0},
    }
    assert payload["edited"]["by_component"] == []
    assert payload["gains"]["by_component_set"] == []
    assert payload["split"]["narrow"]["rounds"] == 0

    markdown = md_path.read_text(encoding="utf-8")
    assert "no attribution recorded" in markdown
    assert "| component" in markdown          # the table header is present ...
    assert "_(empty" in markdown              # ... and it is explicitly empty.
    captured = capsys.readouterr()
    assert "no attribution recorded" in captured.err


def test_json_output_is_deterministic(tmp_path):
    """No timestamp, no set iteration order: the same archive, the same bytes."""
    root = tmp_path / "runs"
    write_run(root, "alpha-run", [
        _point("alpha-run", 0, 0.50, []),
        _point("alpha-run", 1, 0.55, ["context"]),
        _point("alpha-run", 2, 0.52, ["context", "tools"]),
    ])
    write_run(root, "beta-run", [
        _point("beta-run", 0, 0.10, []),
        _point("beta-run", 1, 0.16, ["memory"]),
    ], method="beta")

    first_json, first_md = tmp_path / "one.json", tmp_path / "one.md"
    second_json, second_md = tmp_path / "two.json", tmp_path / "two.md"
    for json_path, md_path in ((first_json, first_md), (second_json, second_md)):
        assert ca.main(["--runs", str(root), "--json", str(json_path),
                        "--md", str(md_path)]) == 0

    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_md.read_bytes() == second_md.read_bytes()
    # And the JSON re-parses to the expected shape.
    assert json.loads(first_json.read_text(encoding="utf-8"))["points"]["attributed"] == 3


def test_a_gap_or_a_missing_score_makes_the_gain_unknown_not_zero(tmp_path):
    """`unknown` and `0.00 pp` are different facts; the first must not become the second."""
    root = tmp_path / "runs"
    write_run(root, "gappy-run", [
        _point("gappy-run", 0, 0.50, ["context"]),          # no previous point
        _point("gappy-run", 1, None, ["context"]),          # not measured
        _point("gappy-run", 2, 0.60, ["context"]),          # previous score missing
        _point("gappy-run", 3, 0.70, ["context"]),          # +10.00
        _point("gappy-run", 4, 0.70, ["context"]),          # 0.00, a measured flat round
    ])

    payload = ca.build_report([str(root)])
    assert [obs["gain_pp"] for obs in payload["observations"]] == [
        None, None, None, pytest.approx(10.0), pytest.approx(0.0)]

    context = single_component(payload, "context")
    assert context == {"component": "context", "rounds": 5, "runs": 1, "n": 2,
                       "unknown": 3, "mean_pp": pytest.approx(5.0),
                       "median_pp": pytest.approx(5.0)}
    assert payload["points"]["total"] == 5
    assert payload["points"]["attributed"] == 5

    # The unknown rows print the marker, never `0.00`.
    markdown = ca.render_markdown(payload)
    assert "—" in markdown


def test_a_skill_like_component_is_narrow_when_the_record_has_one(tmp_path):
    """The ten-name vocabulary has no `skill`, but a future record may; it is prompt-like."""
    root = tmp_path / "runs"
    write_run(root, "skills-run", [
        _point("skills-run", 0, 0.10, ["skill"], method="s"),
        _point("skills-run", 1, 0.20, ["prompt", "context"], method="s"),
    ], method="s")

    payload = ca.build_report([str(root)])
    names = [entry["component"] for entry in payload["edited"]["by_component"]]
    assert names == ["context", "prompt", "skill"]
    assert payload["split"]["narrow"] == {
        "rounds": 2, "runs": 1, "n": 1, "unknown": 1,
        "mean_pp": pytest.approx(10.0), "median_pp": pytest.approx(10.0)}
    assert payload["split"]["broad"]["rounds"] == 0


def test_a_corrupt_or_empty_curve_is_skipped_with_its_reason(tmp_path):
    """A dropped line would move every later round's predecessor, so the run is dropped."""
    root = tmp_path / "runs"

    broken = write_run(root, "broken-run", [
        _point("broken-run", 0, 0.50, ["context"]),
        _point("broken-run", 1, 0.60, ["context"]),
    ])
    with (broken / "curve.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json\n")

    empty = root / "empty-run"
    empty.mkdir()
    (empty / "run_meta.json").write_text(
        json.dumps({"run_id": "empty-run"}), encoding="utf-8")
    (empty / "curve.jsonl").write_text("\n", encoding="utf-8")

    payload = ca.build_report([str(root)])
    assert payload["runs"]["included"] == 0
    reasons = {entry["run_id"]: entry["reason"] for entry in payload["skipped"]}
    assert reasons == {"broken-run": "corrupt_curve", "empty-run": "empty_curve"}
    assert payload["points"]["total"] == 0
    assert payload["gains"]["by_component_set"] == []
    markdown = ca.render_markdown(payload)
    assert "corrupt_curve" in markdown and "empty_curve" in markdown


def test_markdown_states_the_gain_definition_and_that_n_is_rounds(tmp_path):
    """The header is part of the evidence: a reader must not have to guess the formula."""
    root = tmp_path / "runs"
    write_run(root, "one-run", [
        _point("one-run", 0, 0.40, []),
        _point("one-run", 1, 0.44, ["context"]),
    ])
    markdown = ca.render_markdown(ca.build_report([str(root)]))

    assert ("`gain_pp = (this round's score − the previous round's score) × 100`"
            in markdown)
    assert "**within one run only**" in markdown
    assert "`n` is the number of **rounds** with a computable gain" in markdown
    assert "**not** the number of runs" in markdown
    assert "It is never a zero." in markdown


def test_method_is_reconstructed_from_the_run_id_when_no_point_names_it(tmp_path):
    """A point that predates `identity.method` still lands under a method when the id says it."""
    root = tmp_path / "runs"
    write_run(root, "appworld-dev-ahe-r3-20261019T2049Z", [
        _point("appworld-dev-ahe-r3-20261019T2049Z", 0, 0.30, method=None),
        _point("appworld-dev-ahe-r3-20261019T2049Z", 1, 0.33, method=None),
    ], meta=False)

    payload = ca.build_report([str(root)])
    assert payload["unattributed_by_method"] == [
        {"method": "ahe", "points": 2, "runs": 1}]
    assert payload["edited"]["by_method"] == []


def test_a_point_naming_no_component_is_not_called_outside_a_declared_map(tmp_path):
    """State names say what the record shows; `components.attribute` cannot show "outside".

    A harness that declares no map and a harness whose map misses the moved path both
    record `{"components": [], "unattributed": [path]}`, so a state that called this
    "outside the declared map" would be asserting a cause the record does not carry. The
    fixture's path is the one `base_harness/loop` declares as `control` today -- which is
    exactly the state of every such point in the 2026-11-02 archive.
    """
    root = tmp_path / "runs"
    write_run(root, "loop-run", [
        _point("loop-run", 0, 0.50),
        _point("loop-run", 1, 0.55, [], unattributed=["agent.py"]),
    ])

    payload = ca.build_report([str(root)])
    assert payload["points"]["breakdown"] == {
        "field_absent": 1, "declaration_unreadable": 0,
        "no_component_named": 1, "no_path_moved": 0}

    markdown = ca.render_markdown(payload)
    assert "no_component_named" in markdown
    assert "outside_declared_map" not in markdown
    assert "outside the declared map" not in markdown


def test_the_attribution_denominator_is_the_points_that_carry_the_field(tmp_path):
    """`with_field` is the denominator a ratio about attribution belongs over.

    Four points, two of which carry no `editable_surface_components` (an old record, and an
    H0 point). "0 of 4" would read as four failed attributions; only the two with the field
    were ever candidates, and the markdown must say so.
    """
    root = tmp_path / "runs"
    write_run(root, "mixed-run", [
        _point("mixed-run", 0, 0.10),                       # H0: no field, no round
        _point("mixed-run", 1, 0.12),                       # an older record: no field
        _point("mixed-run", 2, 0.11, [], unattributed=["agent.py"]),
        _point("mixed-run", 3, 0.13, [], unattributed=[]),
    ])

    payload = ca.build_report([str(root)])
    assert payload["points"] == {
        "total": 4, "attributed": 0, "unattributed": 4,
        "with_field": 2, "without_field": 2, "in_discarded_runs": 0,
        "breakdown": {"field_absent": 2, "declaration_unreadable": 0,
                      "no_component_named": 1, "no_path_moved": 1},
    }

    markdown = ca.render_markdown(payload)
    assert "0 of 2 curve point(s) that carry the field name a component (0 of 4 in total)" in markdown


def test_points_in_discarded_runs_are_counted_so_the_denominator_is_auditable(tmp_path):
    """A discarded run contributes no evidence, but its points must not vanish silently.

    The header's denominator is "curve points in included studies"; without a count beside
    the discarded runs, a reader cannot tell how much of the archive that excludes.
    """
    root = tmp_path / "runs"
    write_run(root, "kept-run", [
        _point("kept-run", 0, 0.20),
        _point("kept-run", 1, 0.22, ["context"]),
    ])
    discarded = root / "runs_discarded" / "dropped-run"
    discarded.mkdir(parents=True)
    (discarded / "run_meta.json").write_text(
        json.dumps({"run_id": "dropped-run"}), encoding="utf-8")
    (discarded / "curve.jsonl").write_text(
        "\n".join(json.dumps(point) for point in [
            _point("dropped-run", 0, 0.90),
            _point("dropped-run", 1, 0.95, ["control"]),
        ]) + "\n", encoding="utf-8")

    payload = ca.build_report([str(root)])
    assert payload["points"]["total"] == 2                 # the kept run only
    assert payload["points"]["attributed"] == 1            # the discarded one is not evidence
    assert payload["points"]["in_discarded_runs"] == 2
    assert payload["skipped"] == [{
        "run_id": "dropped-run", "method": "unknown", "method_source": "none",
        "reason": "discarded",
        "detail": "under a directory the archive labels as discarded; not read as evidence",
        "points": 2, "attributed_rounds": None,
    }]

    markdown = ca.render_markdown(payload)
    assert "2 more curve point(s) sit in runs the archive labels discarded" in markdown
