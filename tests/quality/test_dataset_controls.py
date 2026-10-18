"""Every published dataset must be able to show that its grade is reachable.

This is the gate that was missing. `tests/quality/test_dataset_lint.py` checks the *contract*
for every published dataset, but the two things that actually de-risk a dataset -- an oracle
control that reaches 1, a do-nothing control that reaches 0, and a test that pins the official
metric -- were per-dataset conventions. A new dataset could be green without them, and two
that already existed (measured 2026-10-07: `tau_bench`, `terminal_bench`) had never had an
oracle arm run at all.

**Why a table and not a name-matching heuristic.** The obvious implementation -- glob
`tests/quality/test_<name>_official_scorer.py` and `data/datasets/<name>/control.py` -- is wrong in
both directions here, and measurably so. `test_bigbench_official_scorers.py` pins three
datasets; `test_qa_dataset_contract.py` is parametrised over six; the shared QA control is
`data/scaffolds/qa/control.py --dataset <name>` rather than a per-dataset script; and a refusal has no
control by design. A glob would report false gaps for the datasets that are covered *better*
than the convention, and the repair for a false gap is to loosen the check. So each dataset
declares its own row, the coverage assertion makes the table total, and adding a dataset
without a row fails the suite at the point where the author still remembers why.

**What is run and what is not.** The by-hand arms of gates 1 and 2 run for every dataset with
a control -- measured at 0.5 s (`bfcl_v3`) to 9.3 s (`alfworld`), about 40 s for all ten. The
`--platform` arms are *not* run here: they multiply that by a sandboxed `run_one` each. They
live in each dataset's own test file, and `tests/quality/test_new_dataset.py` runs all four
arms against a generated scaffold, so the pattern itself stays proven.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from data.scaffolds import registry  # noqa: E402

QUALITY = ROOT / "tests" / "quality"

#: `control` is the argv after `python3`; `metric_tests` are the files that pin the official
#: metric. An empty `control` with an `exempt` sentence is a dataset that has no oracle arm,
#: and the sentence has to say why and what writing one would take.
ROWS: dict[str, dict] = {
    "cooperbench": {
        "control": ["data/datasets/cooperbench/control.py", "--task-id", "cooperbench/pallets_click_task/task2956/f1-2"],
        "metric_tests": ["test_cooperbench_dataset.py", "test_cooperbench_harbor_scoring.py", "test_cooperbench_observed_controls.py"],
    },
    "kumo": {"control": ["data/datasets/kumo/control.py"],
             "metric_tests": ["test_kumo_official_scorer.py", "test_kumo_dataset.py"]},
    "agentdojo": {
        "control": ["data/datasets/agentdojo/control.py", "--task-id", "agentdojo/0"],
        "metric_tests": ["test_agentdojo_official_scorer.py", "test_agentdojo_bridge.py"],
    },
    "alfworld": {
        "control": ["data/datasets/alfworld/control.py"],
        "metric_tests": ["test_alfworld_official_scorer.py", "test_alfworld_dataset.py"],
    },
    # The first adapter ported from Harbor rather than written against an upstream release
    # directly. Its scorer is a container script (`tests/test_outputs.py` under
    # `task-template/`), so `test_aime_official_scorer.py` runs Harbor's own code against a
    # real answer file and diffs our transcription against it, instead of comparing to a
    # package import the way the other QA scorers do.
    "aime": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "aime"],
        "metric_tests": ["test_aime_official_scorer.py", "test_qa_dataset_contract.py"],
    },
    "bfcl_v3": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "bfcl_v3"],
        "metric_tests": ["test_bfcl_v3_dataset.py", "test_qa_dataset_contract.py"],
    },
    "chessbench_puzzles": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "chessbench_puzzles"],
        "metric_tests": ["test_chessbench_puzzles_official_scorer.py", "test_qa_dataset_contract.py"],
    },
    "locomo": {
        "control": ["data/datasets/locomo/control.py", "--task-id", "locomo/conv-26"],
        "metric_tests": ["test_locomo_harbor_scoring.py", "test_locomo_dataset.py"],
    },
    "musique": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "musique"],
        "metric_tests": ["test_musique_official_scorer.py", "test_qa_dataset_contract.py"],
    },
    "gsm8k": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "gsm8k"],
        "metric_tests": ["test_gsm8k_official_scorer.py", "test_gsm8k_dataset.py"],
    },
    "ds1000": {
        "control": ["data/datasets/ds1000/control.py", "--task-id", "ds1000/333"],
        "metric_tests": ["test_ds1000_official_scorer.py"],
    },
    "nestful": {
        "control": [],
        "metric_tests": ["test_nestful_official_scorer.py", "test_dataset_refusals.py"],
    },
    "jericho": {
        # `--task-id` is required by this control (unlike the QA scaffold, it has no
        # default), so it has to be part of the declaration: a declared control
        # that cannot be invoked as declared is not a control.
        "control": ["data/datasets/jericho/control.py", "--task-id", "acorncourt"],
        # Jericho's metric is the package's own normalised score, so there is nothing to
        # transcribe; what has to be pinned is the maximum-score table and the game set.
        "metric_tests": ["test_jericho_dataset.py"],
    },
    "physicsquestions": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "physicsquestions"],
        "metric_tests": ["test_bigbench_official_scorers.py", "test_qa_dataset_contract.py"],
    },
    "scienceworld": {
        "control": ["data/datasets/scienceworld/control.py"],
        "metric_tests": ["test_scienceworld_dataset.py"],
    },
    "spreadsheetbench": {
        "control": ["data/datasets/spreadsheetbench/control.py"],
        "metric_tests": ["test_spreadsheetbench_official_scorer.py"],
    },
    "sportsunderstanding": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "sportsunderstanding"],
        "metric_tests": ["test_bigbench_official_scorers.py", "test_qa_dataset_contract.py"],
    },
    "strategyqa": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "strategyqa"],
        "metric_tests": ["test_bigbench_official_scorers.py", "test_qa_dataset_contract.py"],
    },
    "triviaqa": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "triviaqa"],
        "metric_tests": ["test_triviaqa_official_scorer.py", "test_qa_dataset_contract.py"],
    },
    "reasoning_gym": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "reasoning_gym"],
        "metric_tests": ["test_reasoning_gym_official_scorer.py",
                         "test_qa_dataset_contract.py"],
    },
    "twowikimultihopqa": {
        "control": ["data/scaffolds/qa/control.py", "--dataset", "twowikimultihopqa"],
        "metric_tests": ["test_twowikimultihopqa_official_scorer.py",
                         "test_qa_dataset_contract.py"],
    },
    "bird_minidev": {"control": ["data/datasets/bird_minidev/control.py"], "metric_tests": ["test_bird_minidev_official_scorer.py"]},
    "appworld": {"control": ["data/datasets/appworld/control.py", "--task-id", "e85d92a_1"], "metric_tests": ["test_appworld_official_scorer.py"]},
    "gaia": {
        # `--platform` because this dataset is an `exec` env: the verifier's paths are
        # container-absolute (`/check.py`, `/app/answer.txt`), so the by-hand arm has no
        # meaningful form. The arm that proves gates 1 and 2 is `run_one` in a real container --
        # 13 s per arm, and the arm that would catch an image or network mistake.
        "control": ["data/datasets/gaia/control.py", "--platform"],
        "metric_tests": ["test_gaia_official_scorer.py"],
    },
    "livecodebench": {
        # `--task-id` because the oracle is **authored** and covers 7 of the 1,055 problems:
        # only a covered task can show gate 1, and `oracle_files()` raises for the rest. This
        # replaced a refusal row, and the first attempt left **both** in the dict literal --
        # where a duplicate key is legal and the last one wins, so the tests read `control: []`
        # and reported a dataset with no control that plainly had one.
        "control": ["data/datasets/livecodebench/control.py", "--task-id", "livecodebench/800"],
        "metric_tests": ["test_livecodebench_official_scorer.py"],
    },
    "tau_bench": {
        "control": [],
        "metric_tests": [],
        "exempt": (
            "Predates the convention and has never had an oracle arm run. It was onboarded "
            "before the gate existed, and there is no `data/datasets/tau_bench/control.py` to run. "
            "Writing one is bounded and the data supports it: `data/datasets/tau_bench.py` already "
            "replays each task's ground-truth actions to build the gold database (line ~293, "
            "`for action in record['actions']`), so the oracle arm needs an agent that "
            "replays the same actions through the same tool classes and then stops. Until "
            "that exists, this dataset's grade is asserted by no measurement at all, and a "
            "curve point from it cannot be distinguished from an unreachable check."),
    },
    "terminal_bench": {
        "control": [],
        "metric_tests": [],
        "exempt": (
            "Predates the convention, and its oracle is driven by the platform rather than "
            "by a dataset control: with `HG_TB_ORACLE=1`, `data/datasets/terminal_bench.py` stages the "
            "task's own `solution/` directory at `.oracle` so `base_harness/oracle` can run "
            "it (data/datasets/terminal_bench.py, the `setup` branch). That is a real oracle path, but "
            "it is reachable only through the platform's own harness and only for tasks that "
            "ship `solution/`; nothing in the suite asserts that it reaches 1. A dataset "
            "control that sets the flag, runs `base_harness/oracle`, and asserts the score "
            "would close the gap. Note the dataset also cannot run here at all without "
            "Docker, which is why no arm has been exercised."),
    },
    "api_bank": {"control": [], "metric_tests": ["test_dataset_refusals.py"]},
    "fever": {"control": [], "metric_tests": ["test_qa_evidence_contract.py"], "exempt": "structured evidence contract is loadable; oracle/data provisioning preflight remains before a run"},
    "hotpotqa": {"control": [], "metric_tests": ["test_qa_evidence_contract.py"], "exempt": "structured evidence contract is loadable; oracle/data provisioning preflight remains before a run"},
    "sotuqa": {"control": [], "metric_tests": ["test_dataset_refusals.py"]},
    "tau2_bench": {"control": [], "metric_tests": ["test_dataset_refusals.py"]},
}

#: Datasets whose control is the refusal itself, so the runnable-control test skips them and
#: the refusal test below applies instead.
REFUSED = ("api_bank", "sotuqa", "tau2_bench", "nestful")

#: Datasets whose platform control is *refused* rather than run, because their declared
#: policy needs a trusted backend observation this checkout cannot supply: an `exec`
#: task on a container, a service network, or a writable-root/interactive environment.
#:
#: The refusal is the correct outcome, not a failure, so these are asserted as refusals
#: rather than skipped. Skipping would hide the guarantee; demanding `reward 1`/`0`
#: would demand a number that claims enforcement nobody verified.
#:
#: **Only `gaia` belongs here, and the reason is the control's form, not the dataset's
#: blockers.** This tuple is about a control that runs through `--platform` and is
#: therefore stopped by the fail-closed runner hook; `gaia` is the only declared control
#: that does (`data/datasets/gaia/control.py --platform`). `jericho` and `spreadsheetbench` were
#: listed here while their by-hand controls were broken -- one could not start the game
#: for want of a writable temp directory, the other could not submit for want of a
#: module in its `SETUP`. Both were repaired, and their by-hand arms now genuinely
#: measure `1` and `0`, so expecting a refusal from them would now be expecting the
#: platform to hide a working measurement. Their platform-side refusal is a separate
#: property and is pinned by the admission-wiring tests, not here.
POLICY_REFUSED = ("gaia",)


def _provisioned(name: str) -> bool:
    """The dataset's own answer to "are my bytes here", as the lint asks it."""
    module = registry.importlib.import_module(f"data.datasets.{name}")
    status = getattr(module, "provision_status", None)
    if not callable(status):
        return True
    try:
        return bool(status().get("present"))
    except Exception:  # noqa: BLE001 - an unreadable status is not a green light
        return False


def test_every_published_dataset_has_a_row():
    """The table must be total: a new dataset cannot be added without declaring its shape."""
    published = set(registry.published())
    declared = set(ROWS)
    assert not published - declared, (
        "published datasets with no row in tests/quality/test_dataset_controls.py: "
        f"{sorted(published - declared)}. Add a row naming the control to run and the test "
        "that pins the official metric -- or an exemption saying why there is none.")
    assert not declared - published, (
        f"rows for names that are not published datasets: {sorted(declared - published)}. "
        "A stale row is a check that has stopped covering anything.")


def test_the_control_and_the_metric_test_are_declared_together():
    """A control with no metric test, or a metric test with no control, is half a gate."""
    half = []
    for name, row in ROWS.items():
        has_control = bool(row["control"])
        has_metric = bool(row["metric_tests"])
        if has_control != has_metric and name not in REFUSED and not row.get("exempt"):
            half.append(f"{name} (control={has_control}, metric_tests={has_metric})")
    assert not half, (
        "a dataset must declare both an oracle control and the test that pins its metric: "
        + "; ".join(half))


def test_exemptions_say_why_and_what_it_would_take():
    """An exemption is a recorded debt, so it has to name the work, not just the absence."""
    thin = [name for name, row in ROWS.items()
            if not row["control"] and name not in REFUSED and not row.get("exempt")
            and len(row.get("exempt", "")) < 200]
    assert not thin, (
        f"these datasets have no control and no real explanation: {thin}. An exemption has "
        f"to say why the arm was never run and what writing one would take.")


def test_every_declared_control_exists():
    missing = [row["control"][0] for row in ROWS.values()
               if row["control"] and not (ROOT / row["control"][0]).is_file()]
    assert not missing, f"declared controls that are not there: {sorted(set(missing))}"


def test_every_declared_metric_test_exists():
    missing = [name for row in ROWS.values() for name in row["metric_tests"]
               if not (QUALITY / name).is_file()]
    assert not missing, f"declared metric tests that are not there: {sorted(set(missing))}"


def test_the_refusals_are_where_the_refusal_contract_lives():
    """The refusal is a first-class result, so it is declared in the file that tests it."""
    source = (QUALITY / "test_dataset_refusals.py").read_text(encoding="utf-8")
    match = re.search(r"REFUSING\s*=\s*\[(.*?)\]", source, re.S)
    assert match, "test_dataset_refusals.py no longer declares REFUSING"
    listed = set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))
    assert listed == set(REFUSED), (
        f"the two lists disagree: this file says {sorted(REFUSED)}, "
        f"test_dataset_refusals.py says {sorted(listed)}")


@pytest.mark.parametrize("name", sorted(n for n, r in ROWS.items() if r["control"]))
def test_the_control_reaches_one_and_the_noop_reaches_zero(name):
    """Gates 1 and 2, by hand. Measured 0.5-9.3 s per dataset, ~40 s for all of them."""
    if not _provisioned(name):
        pytest.skip(f"{name}: not provisioned on this machine")
    argv = ROWS[name]["control"]
    if name in POLICY_REFUSED:
        # Both arms must be refused, and a refusal must never look like a score. The
        # do-nothing arm is the one this guards hardest: `None` compared as `0.0` used
        # to make a refused run read as "the noop arm behaved", which is a fabricated
        # control result. The controls now exit 2 with CONTROL UNMEASURED.
        for arm in ("--oracle", "--noop"):
            proc = subprocess.run([sys.executable, *argv, arm], cwd=ROOT,
                                  capture_output=True, text=True, timeout=900)
            combined = proc.stdout + proc.stderr
            assert proc.returncode != 0, (
                f"{name} {arm} reported success for a policy the platform refuses:\n"
                f"{combined[-1200:]}")
            assert "UNMEASURED" in combined or "no score" in combined, (
                f"{name} {arm} was refused without saying so:\n{combined[-1200:]}")
            assert not re.search(r"reward\s+[01](?![\d.])", proc.stdout), (
                f"{name} {arm} produced a reward for a refused task:\n{proc.stdout[-800:]}")
        return
    for arm, expected in (("--oracle", "1"), ("--noop", "0")):
        proc = subprocess.run([sys.executable, *argv, arm], cwd=ROOT,
                              capture_output=True, text=True, timeout=900)
        assert proc.returncode == 0, (
            f"{name} {arm} exited {proc.returncode}; a dataset whose own control fails is "
            f"not gradeable:\n{proc.stdout[-1200:]}\n{proc.stderr[-600:]}")
        assert re.search(rf"reward\s+{expected}(?![\d.])", proc.stdout), (
            f"{name} {arm} did not report reward {expected}:\n{proc.stdout[-1200:]}")
