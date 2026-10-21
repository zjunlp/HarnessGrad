"""The dataset classification must stay total, closed, and coarse enough to classify.

`tools/dataset_classes.py` splits the classification into three kinds of axis on purpose:

* **derived** from the adapter and the shipped CLI (environment, verifier, interaction,
  counts) -- so it cannot go stale while nobody is looking;
* **judged**, and only the capability axis is judged: it is declared once in `CAPABILITY` as
  one `(demand, surface)` pair per dataset, and the tool refuses a published dataset without
  one;
* **measured** elsewhere (cost, difficulty, headroom), never in that file.

Two properties are held here because the file states them and a test is what makes a stated
property a checked one. The derived interaction axis must agree with the declared one -- that
is not ceremony, the first version derived it from a single verb (`do`) and silently called
`agentdojo` a single-shot dataset because `dojo` spells its action verb `call`. And the judged
axis must not degenerate: the flat vocabulary this replaced ended with thirteen buckets, seven
of them holding one dataset each, and nothing objected because nothing was checking.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import dataset_classes  # noqa: E402
from data.scaffolds import registry  # noqa: E402


def _rows() -> list[dict]:
    return dataset_classes.classify_all()


def _declared_rows() -> list[dict]:
    """Read the judged axis without requiring every dataset's external task pool."""
    return [
        {"dataset": name, "demand": demand, "surface": surface,
         "exercises": list(dataset_classes.EXERCISES[name])}
        for name, (demand, surface) in dataset_classes.CAPABILITY.items()
    ]


def test_every_published_dataset_is_classified_exactly_once():
    rows = _rows()
    names = [row["dataset"] for row in rows]
    assert sorted(names) == sorted(registry.published())
    assert len(names) == len(set(names))


def test_the_capability_vocabulary_is_closed_and_total():
    rows = _rows()
    declared = set(dataset_classes.CAPABILITY)
    assert declared == set(registry.published()), (
        "CAPABILITY must name exactly the published datasets; the tool raises otherwise, so "
        "this assertion documents the property rather than reimplementing it")
    for row in rows:
        assert row["demand"] in dataset_classes.DEMANDS, row
        assert row["surface"], row
    # A vocabulary with one member per dataset would classify nothing.
    demands = {row["demand"] for row in rows}
    surfaces = {row["surface"] for row in rows}
    assert len(demands) < len(rows)
    assert len(surfaces) < len(rows)


def test_no_demand_is_a_private_name_for_one_dataset():
    """The failure this taxonomy exists to fix.

    The flat list had `code_generation`, `text_to_sql`, `spreadsheet` and `terminal` as four
    top-level peers of `reasoning`, so `len(demands) == 13` with seven of them holding a
    single dataset described a list of names rather than a taxonomy. Checked through the
    declared minimum rather than a hard-coded 4, so adding a genuine category is a decision
    about that constant and not an edit to a test.
    """
    rows = _rows()
    for demand in dataset_classes.DEMANDS:
        if demand in dataset_classes.UNCOVERED_DEMANDS:
            continue  # a declared gap is a different state, checked below
        members = [row["dataset"] for row in rows if row["demand"] == demand]
        assert len(members) >= dataset_classes.MIN_MEMBERS_PER_DEMAND, (
            f"demand {demand!r} holds {members}; a one-dataset demand is a name, not a "
            f"category")
    assert dataset_classes.MIN_MEMBERS_PER_DEMAND >= 2


def test_an_uncovered_demand_is_declared_and_holds_no_adapters():
    """Declaring a demand is not covering it, and the difference is kept visible."""
    rows = _rows()
    uncovered = set(dataset_classes.UNCOVERED_DEMANDS)
    assert uncovered <= set(dataset_classes.DEMANDS), uncovered
    for demand in uncovered:
        assert not [row for row in rows if row["demand"] == demand], (
            f"{demand!r} is declared uncovered but holds adapters")
        assert dataset_classes.UNCOVERED_DEMANDS[demand], (
            f"{demand!r} is a gap with no recorded shape")


def test_a_silent_gap_is_refused(monkeypatch):
    """Removing a demand's adapters must not silently claim complete coverage."""
    dataset_classes._check_judged_axis(_declared_rows())
    remaining = {name: pair for name, pair in dataset_classes.CAPABILITY.items()
                 if pair[0] != "memory"}
    monkeypatch.setattr(dataset_classes, "CAPABILITY", remaining)
    monkeypatch.setattr(dataset_classes, "UNCOVERED_DEMANDS", {})
    with pytest.raises(SystemExit, match="no datasets.*memory"):
        dataset_classes._check_judged_axis(_declared_rows())


def test_a_stale_coverage_declaration_is_refused(monkeypatch):
    """The other direction: a demand that gains an adapter must lose its gap entry."""
    monkeypatch.setattr(dataset_classes, "UNCOVERED_DEMANDS",
                        {"memory": ("supplied_history",)})
    with pytest.raises(SystemExit, match="now hold datasets.*memory"):
        dataset_classes._check_judged_axis(_declared_rows())


def test_a_gap_with_no_recorded_shape_is_refused(monkeypatch):
    monkeypatch.setattr(dataset_classes, "UNCOVERED_DEMANDS", {"memory": ()})
    with pytest.raises(SystemExit, match="memory.*no planned surface"):
        dataset_classes._check_judged_axis(_declared_rows())


@pytest.mark.parametrize("name", ["personamem_v2", "memoryagentbench"])
def test_sequential_memory_cli_declares_state_advancing_ack(name):
    cli = "personamem" if name == "personamem_v2" else "mab"
    source = ROOT / "data/datasets" / name / "cli" / cli
    verbs = dataset_classes._declared_verbs(source)
    assert verbs == {"next", "ack", "submit"}
    assert verbs & set(dataset_classes.ACT_VERBS) == {"ack"}


def test_a_surface_belongs_to_exactly_one_demand():
    """Levels that overlap are not levels."""
    owners: dict[str, set[str]] = {}
    for demand, surface in dataset_classes.CAPABILITY.values():
        owners.setdefault(surface, set()).add(demand)
    shared = {s: d for s, d in owners.items() if len(d) > 1}
    assert not shared, f"these surfaces sit under several demands: {shared}"


def test_a_thin_demand_is_refused_not_classified():
    """The anti-degeneracy rule has to be able to fire, or it is a comment."""
    original = dict(dataset_classes.CAPABILITY)
    try:
        # Move every member of `environment` except one into `reasoning`, leaving
        # `environment` thin: the refusal must name it.
        for name, (demand, surface) in original.items():
            if demand == "environment" and name != "alfworld":
                dataset_classes.CAPABILITY[name] = ("reasoning", surface)
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "environment" in str(excinfo.value)
    finally:
        dataset_classes.CAPABILITY.clear()
        dataset_classes.CAPABILITY.update(original)
    assert dataset_classes.classify_all()  # restored


def test_an_unknown_demand_is_refused_not_classified():
    original = dict(dataset_classes.CAPABILITY)
    try:
        dataset_classes.CAPABILITY["gsm8k"] = ("vibes", "reasoning")
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "gsm8k" in str(excinfo.value)
    finally:
        dataset_classes.CAPABILITY.clear()
        dataset_classes.CAPABILITY.update(original)
    assert dataset_classes.classify_all()  # restored


def test_a_surface_under_two_demands_is_refused():
    original = dict(dataset_classes.CAPABILITY)
    try:
        dataset_classes.CAPABILITY["gsm8k"] = ("artifact", "reasoning")
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "reasoning" in str(excinfo.value)
    finally:
        dataset_classes.CAPABILITY.clear()
        dataset_classes.CAPABILITY.update(original)
    assert dataset_classes.classify_all()  # restored


def test_adversarial_is_a_condition_and_not_a_demand():
    """AgentDojo's demand and surface are ordinary; only its threat model is not."""
    rows = {row["dataset"]: row for row in _rows()}
    adversarial = {name for name, row in rows.items() if row["adversarial"]}
    assert adversarial == set(dataset_classes.ADVERSARIAL)
    assert "agentdojo" in adversarial
    for name in adversarial:
        assert rows[name]["demand"] in dataset_classes.DEMANDS, rows[name]
        assert rows[name]["surface"] in {s for _, s in dataset_classes.CAPABILITY.values()}


def test_every_dataset_names_at_least_one_harness_function():
    """A dataset that stresses nothing cannot support a harness-design claim."""
    rows = _rows()
    for row in rows:
        assert row["exercises"], row
        for function in row["exercises"]:
            assert function in dataset_classes.FUNCTIONS, (row["dataset"], function)


def test_the_function_coverage_floor_matches_what_is_declared():
    """Same two-way rule as the demands: below the floor is declared, and a declaration means
    still below the floor."""
    rows = _rows()
    counts = dataset_classes.function_family_counts(rows)
    below = sorted(f for f in dataset_classes.FUNCTIONS
                   if counts[f] < dataset_classes.MIN_MEMBERS_PER_FUNCTION)
    assert below == sorted(dataset_classes.UNCOVERED_FUNCTIONS), (below, counts)
    assert set(dataset_classes.UNCOVERED_FUNCTIONS) <= set(dataset_classes.FUNCTIONS)
    assert counts["tools"] > counts["memory"], "the scaffolds we reuse over-sample their own"


def test_costbench_protocols_do_not_close_independent_cost_coverage():
    rows = _declared_rows()
    cost_rows = [row for row in rows if "cost" in row["exercises"]]
    assert {row["dataset"] for row in cost_rows} == {"costbench", "costbench_dynamic"}
    assert dataset_classes.function_family_counts(rows)["cost"] == 1
    assert "cost" in dataset_classes.UNCOVERED_FUNCTIONS
    dataset_classes._check_functions(rows)
    dataset_classes._check_judged_axis(rows)
    independent = {"dataset": "independent_cost_benchmark", "exercises": ["cost"]}
    assert dataset_classes.function_family_counts(cost_rows + [independent])["cost"] == 2


def test_a_function_inside_one_demand_is_refused():
    """The rule that removed `reflection`.

    `reflection` held exactly the reasoning datasets, so it carried nothing the demand axis did
    not already have -- the same two-names-for-one-fact defect as `tool_calling` beside
    `tool_dialogue`. A covered function has to cross at least two demands.
    """
    rows = _rows()
    for function in dataset_classes.FUNCTIONS:
        if function in dataset_classes.UNCOVERED_FUNCTIONS:
            continue  # below the floor and declared; exempt
        demands = {row["demand"] for row in rows if function in row["exercises"]}
        assert len(demands) >= 2, f"{function} sits inside {demands}"

    original = dict(dataset_classes.EXERCISES)
    try:
        # Strip `verification` from everything outside the artifact demand, so it is left
        # holding one demand only -- the state `reflection` was in.
        for name in list(dataset_classes.EXERCISES):
            demand = dataset_classes.CAPABILITY[name][0]
            if "verification" in dataset_classes.EXERCISES[name] and demand != "artifact":
                dataset_classes.EXERCISES[name] = tuple(
                    f for f in dataset_classes.EXERCISES[name] if f != "verification")
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "verification" in str(excinfo.value)
    finally:
        dataset_classes.EXERCISES.clear()
        dataset_classes.EXERCISES.update(original)
    assert dataset_classes.classify_all()  # restored


def test_an_undeclared_function_gap_is_refused():
    original = dict(dataset_classes.UNCOVERED_FUNCTIONS)
    try:
        dataset_classes.UNCOVERED_FUNCTIONS.pop("delegation")
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "delegation" in str(excinfo.value)
    finally:
        dataset_classes.UNCOVERED_FUNCTIONS.clear()
        dataset_classes.UNCOVERED_FUNCTIONS.update(original)
    assert dataset_classes.classify_all()  # restored


def test_a_stale_function_gap_is_refused():
    """Listing a covered function as a gap is a coverage claim that is now false."""
    original = dict(dataset_classes.UNCOVERED_FUNCTIONS)
    try:
        dataset_classes.UNCOVERED_FUNCTIONS["tools"] = "no longer true"
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "tools" in str(excinfo.value)
    finally:
        dataset_classes.UNCOVERED_FUNCTIONS.clear()
        dataset_classes.UNCOVERED_FUNCTIONS.update(original)
    assert dataset_classes.classify_all()  # restored


def test_a_function_outside_the_vocabulary_is_refused():
    original = dict(dataset_classes.EXERCISES)
    try:
        dataset_classes.EXERCISES["gsm8k"] = ("context", "vibes")
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "gsm8k" in str(excinfo.value)
    finally:
        dataset_classes.EXERCISES.clear()
        dataset_classes.EXERCISES.update(original)
    assert dataset_classes.classify_all()  # restored


def test_a_dataset_that_names_no_function_is_refused():
    original = dict(dataset_classes.EXERCISES)
    try:
        dataset_classes.EXERCISES["gsm8k"] = ()
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "gsm8k" in str(excinfo.value)
    finally:
        dataset_classes.EXERCISES.clear()
        dataset_classes.EXERCISES.update(original)
    assert dataset_classes.classify_all()  # restored


def test_the_derived_interaction_agrees_with_the_declared_one():
    rows = _rows()
    for row in rows:
        if row["refused"]:
            assert row["interaction"] == "n/a (refused)"
            continue
        assert row["interaction"] == dataset_classes.EXPECTED_INTERACTION[row["dataset"]], row


def test_the_cross_check_actually_fires():
    """Two sides that can never disagree are one side with extra steps."""
    original = dict(dataset_classes.EXPECTED_INTERACTION)
    try:
        dataset_classes.EXPECTED_INTERACTION["agentdojo"] = "single_shot"
        with pytest.raises(SystemExit) as excinfo:
            dataset_classes.classify_all()
        assert "agentdojo" in str(excinfo.value)
        assert "agentdojo" in str(excinfo.value)
    finally:
        dataset_classes.EXPECTED_INTERACTION.clear()
        dataset_classes.EXPECTED_INTERACTION.update(original)
    assert dataset_classes.classify_all()  # restored


def test_a_container_environment_is_always_a_closed_loop():
    for row in _rows():
        if "exec" in row["env_kinds"]:
            assert row["interaction"] == "closed_loop", row


def test_every_closed_loop_dataset_ships_a_program_or_is_a_container():
    """The derivation reads a shipped program; a `files` closed loop with none means it read
    nothing. An `exec` dataset is the other case on purpose: the agent gets a shell, so there
    is no CLI to ship and the container itself is the interface."""
    for row in _rows():
        if row["interaction"] != "closed_loop" or "exec" in row["env_kinds"]:
            continue
        assert row["action_surface"], row


def test_every_shipped_program_declares_a_readable_verb_set():
    """The failure mode this replaced: a new spelling silently yielding `single_shot`."""
    for name in registry.published():
        module = registry.importlib.import_module(f"data.datasets.{name}")
        if getattr(module, "REFUSED", None):
            continue
        task_id = module.load()[0][0]["task_id"]
        for script in dataset_classes._cli_scripts(module, task_id):
            verbs = dataset_classes._declared_verbs(script)
            assert verbs, (f"{script} declares no verb set this tool can read; a third "
                           f"spelling has to be added deliberately")


def test_an_unreadable_verb_set_is_reported_not_defaulted(tmp_path):
    """A CLI spelled some third way must be loud, not quietly classified."""
    weird = tmp_path / "weird_cli"
    weird.write_text("#!/usr/bin/env python3\nimport sys\nmode = sys.argv[1]\n"
                     "if mode in {'alpha', 'beta'}:\n    pass\n")
    assert dataset_classes._declared_verbs(weird) == set()


@pytest.fixture(scope="module")
def classified():
    """Classify once for the whole module.

    `classify_all()` reads every dataset, which is the right cost for the one check
    that asserts the population, and far too much to pay again for each filter
    combination. The filter assertions below call the same `select()` the CLI uses,
    so this is the shipped path, just not re-entered through a subprocess 34 times.
    """
    return dataset_classes.classify_all()


def test_the_json_view_is_machine_readable():
    proc = subprocess.run([sys.executable, "tools/dataset_classes.py", "--json"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-400:]
    rows = json.loads(proc.stdout)
    assert len(rows) == len(registry.published())
    assert all({"dataset", "demand", "surface", "adversarial", "exercises",
                "interaction", "tasks"} <= set(row) for row in rows)
    # One name per datum: the flat vocabulary's `domain` was `surface` plus half of
    # `interaction`, and carrying both spellings is how the two drifted apart.
    assert all("domain" not in row for row in rows)


def test_the_interaction_and_runnable_flags_reach_the_cli():
    """One real CLI invocation, so the flag wiring itself is exercised end to end.

    The filter *semantics* are covered in process below; what this adds is that
    `--interaction` and `--runnable-only` are actually parsed and forwarded.
    """
    proc = subprocess.run([sys.executable, "tools/dataset_classes.py", "--json",
                           "--interaction", "closed_loop", "--runnable-only"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-400:]
    rows = json.loads(proc.stdout)
    assert rows and all(row["interaction"] == "closed_loop" and not row["refused"]
                        for row in rows)
    assert {row["dataset"] for row in rows} == {
        "agentdojo", "alfworld", "appworld", "cooperbench", "gaia", "jericho", "kumo", "memoryagentbench", "personamem_v2", "scienceworld", "tau3_bench", "tau_bench", "terminal_bench"}


def test_the_filters_select_what_they_say(classified):
    rows = dataset_classes.select(classified, interaction="closed_loop", runnable_only=True)
    assert rows and all(row["interaction"] == "closed_loop" and not row["refused"]
                        for row in rows)
    assert {row["dataset"] for row in rows} == {
        "agentdojo", "alfworld", "appworld", "cooperbench", "gaia", "jericho", "kumo", "memoryagentbench", "personamem_v2", "scienceworld", "tau3_bench", "tau_bench", "terminal_bench"}


def test_the_demand_surface_and_adversarial_filters_select_what_they_say(classified):
    def run(*flags, **kwargs):
        return dataset_classes.select(classified, **kwargs)

    demands = classified
    for demand in dataset_classes.DEMANDS:
        filtered = run(demand=demand)
        if demand in dataset_classes.UNCOVERED_DEMANDS:
            assert filtered == [], f"{demand!r} is declared uncovered yet filtered non-empty"
            continue
        assert filtered and all(row["demand"] == demand for row in filtered)
        assert {row["dataset"] for row in filtered} == {
            row["dataset"] for row in demands if row["demand"] == demand}

    for surface in sorted({row["surface"] for row in demands}):
        filtered = run(surface=surface)
        assert filtered and all(row["surface"] == surface for row in filtered)

    adversarial = run(adversarial=True)
    assert {row["dataset"] for row in adversarial} == set(dataset_classes.ADVERSARIAL)

    for function in dataset_classes.FUNCTIONS:
        filtered = run(function=function)
        assert {row["dataset"] for row in filtered} == {
            row["dataset"] for row in demands if function in row["exercises"]}, function


def test_the_table_view_prints_both_levels(capsys):
    """The table is the human view, so it has to show the hierarchy rather than the pair."""
    dataset_classes.main([])
    out = capsys.readouterr().out
    for demand in dataset_classes.DEMANDS:
        assert demand in out, demand
    assert "adversarial" in out
    assert out.count("information_seeking") >= 2  # a row and its demand's surface summary
    for demand, surfaces in dataset_classes.UNCOVERED_DEMANDS.items():
        assert "no adapter yet" in out
        for surface in surfaces:
            assert surface in out, f"{demand} plans {surface} but the table hides it"
