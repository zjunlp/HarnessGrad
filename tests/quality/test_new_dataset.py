"""The generator's promise: a scaffold that already passes the five gates.

`tools/new_dataset.py` is only worth having if the thing it generates works. A skeleton with
TODOs would be *less* useful than copying a working dataset, because it would look finished.
So the promise is exact -- generate, lint, oracle 1, noop 0, by hand and through the
platform -- and this file is what holds the tool to it.

The loop is run against throwaway names under `data/`, and the fixture removes every artefact
whether the test passes or fails. A scaffold left behind by a failing test would be a scaffold
that the next full-suite run lints as a published dataset.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import dataset_lint  # noqa: E402
import new_dataset  # noqa: E402
from data.scaffolds import command_control  # noqa: E402
from data.scaffolds import registry  # noqa: E402

SOURCE = {
    "url": "https://example.invalid/scaffold-probe.tar.gz",
    "version": "v1.2.3",
    "size": "1 MB",
    "note": "A probe source used by tests/quality/test_new_dataset.py; never fetched, so "
            "it has no traps to record.",
}

KINDS = {
    "qa": {"module": "scaffold_probe_qa", "control": ["data/scaffolds/qa/control.py"],
           "control_args": ["--dataset", "scaffold_probe_qa"]},
    "command": {"module": "scaffold_probe_cmd", "control": ["data/datasets/scaffold_probe_cmd/control.py"],
                "control_args": []},
    "files": {"module": "scaffold_probe_files",
              "control": ["data/datasets/scaffold_probe_files/control.py"], "control_args": []},
}


def _artifacts(name: str) -> list[Path]:
    return [ROOT / "data" / "datasets" / f"{name}.py", ROOT / "data" / "datasets" / name]


def _clean(name: str) -> None:
    """Remove a scaffold's artefacts, and the cached module.

    The cache matters: the lint imports the module, and a cached entry would shadow the
    next generation of the same name.
    """
    for path in _artifacts(name):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    sys.modules.pop(f"data.datasets.{name}", None)


@pytest.fixture(params=sorted(KINDS))
def scaffold(request):
    """Generate the scaffold, hand it to the test, then remove every artefact."""
    kind = request.param
    name = KINDS[kind]["module"]
    _clean(name)
    new_dataset.generate(name, kind, SOURCE)
    try:
        yield kind, name
    finally:
        _clean(name)


@pytest.fixture
def files_scaffold():
    """The `files` scaffold on its own, for the tests about its shape rather than its kind."""
    name = KINDS["files"]["module"]
    _clean(name)
    new_dataset.generate(name, "files", SOURCE)
    try:
        yield name
    finally:
        _clean(name)


def _run(argv: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout)


def test_the_scaffold_is_not_a_published_dataset(scaffold):
    """`KIND = "fixture"` is what keeps a scaffold out of the chooser until it is real."""
    _, name = scaffold
    assert name not in registry.published()


def test_the_scaffold_passes_the_lint(scaffold):
    kind, name = scaffold
    result = dataset_lint.lint_dataset(name, dataset_lint.load_schema())
    assert result.ok, f"{kind} scaffold: " + "; ".join(result.errors)
    assert not result.unprovisioned, "the scaffold must be lintable, not skipped"


def test_the_scaffold_declares_pinned_provenance(scaffold):
    """A dataset must name where its bytes came from -- the lint refuses an empty list."""
    _, name = scaffold
    module = registry.importlib.import_module(f"data.datasets.{name}")
    entries = module.downloads()
    assert entries, "downloads() is empty; the contract requires provenance"
    for entry in entries:
        assert entry["version"] == SOURCE["version"]
        assert entry["url"] == SOURCE["url"]
        assert len(entry["note"]) >= 20


@pytest.mark.parametrize("arm,expected", [("--oracle", "1"), ("--noop", "0")])
def test_the_scaffold_controls_reach_the_expected_reward(scaffold, arm, expected):
    kind, name = scaffold
    argv = KINDS[kind]["control"] + KINDS[kind]["control_args"] + [arm]
    proc = _run(argv)
    assert proc.returncode == 0, f"{kind} {arm}: {proc.stdout[-800:]} {proc.stderr[-400:]}"
    # The two controls pad differently (`reward  1` vs `reward 1: matches`), so the number
    # is parsed rather than the spacing matched. `(?![\d.])` keeps `1` from matching the
    # `1` of `1.0`, which is what the platform arm reports.
    assert re.search(rf"reward\s+{re.escape(expected)}(?![\d.])", proc.stdout), \
        f"{kind} {arm} did not report reward {expected}: {proc.stdout[-800:]}"


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_the_scaffold_controls_pass_through_the_platform(scaffold, arm, expected):
    """The second half of gates 1 and 2: `SETUP -> harness -> VERIFY` via `run_one`."""
    kind, name = scaffold
    argv = KINDS[kind]["control"] + KINDS[kind]["control_args"] + [arm, "--platform"]
    proc = _run(argv)
    assert proc.returncode == 0, f"{kind} {arm} --platform: {proc.stdout[-800:]}"
    assert f"score    {expected}" in proc.stdout, \
        f"{kind} {arm} --platform reported {proc.stdout[-600:]}"


def test_the_generator_refuses_to_overwrite(scaffold):
    kind, name = scaffold
    with pytest.raises(SystemExit) as excinfo:
        new_dataset.generate(name, kind, SOURCE)
    assert "refusing to overwrite" in str(excinfo.value)


def test_the_generator_refuses_an_unpinned_source(tmp_path):
    """An unpinned scaffold teaches the wrong habit on its first line."""
    with pytest.raises(SystemExit) as excinfo:
        new_dataset.generate("scaffold_probe_unpinned", "qa",
                             {**SOURCE, "version": "main"})
    assert "branch, not a pin" in str(excinfo.value)
    assert not (ROOT / "data" / "datasets" / "scaffold_probe_unpinned.py").exists()


def test_the_generator_refuses_a_source_with_no_trap_recorded():
    with pytest.raises(SystemExit) as excinfo:
        new_dataset.generate("scaffold_probe_bare", "qa", {**SOURCE, "note": "todo"})
    assert "at least 20 characters" in str(excinfo.value)
    assert not (ROOT / "data" / "datasets" / "scaffold_probe_bare.py").exists()


def test_the_files_check_separates_unmeasurable_from_failed(files_scaffold):
    """The two outcomes a state check must never confuse.

    This is the `files` kind's own contract, and it is the one a scaffold author copies:
    a missing **interface** file is a measured `0` (the agent failed to act), while a missing
    **platform** input -- the gold -- is not a grade at all. No reward file is written and
    the evidence says `measured: false`, which `eval/verification/verifier.py` reports as unmeasured
    rather than as a zero the agent earned.
    """
    module = registry.importlib.import_module(f"data.datasets.{files_scaffold}")
    task = module.TASKS[0]
    workdir = Path(tempfile.mkdtemp(prefix="hg-files-probe-"))
    try:
        # The agent does nothing. That is a measured 0 -- and the state half must fail,
        # or the toy is not testing the comparison at all.
        assert command_control.run_local(module, task, oracle=False, workdir=workdir) == 0
        reward = (workdir / "verifier" / "reward.txt").read_text().strip()
        evidence = json.loads((workdir / "verifier" / "evidence.json").read_text())
        assert reward == "0"
        assert evidence["measured"] is True
        assert evidence["state_matches"] is False, "a no-op must not match the reference state"

        # Now take away the platform's own input and run the check exactly as the platform
        # does -- it materializes its inputs first, so the removal has to come after that.
        (workdir / "verifier" / "reward.txt").unlink()
        (workdir / "gold.json").unlink()
        verifier = module.VERIFY[task["task_id"]]
        proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                              capture_output=True, text=True)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        evidence = json.loads((workdir / "verifier" / "evidence.json").read_text())
        assert evidence["measured"] is False
        assert "cannot be measured" in evidence["why"]
        assert not (workdir / "verifier" / "reward.txt").exists(), \
            "an unmeasurable task must not leave a reward behind"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_the_files_oracle_changes_the_state_it_claims_to(files_scaffold):
    """The oracle arm has to be a real solution, not a copy of the starting state.

    Measured while writing this scaffold: the first toy used `ticket_id = i + 1` over an
    initial state of three tickets, so most tasks asked to close a ticket that was not
    there, the reference state equalled the initial one, and **the do-nothing arm passed the
    state comparison** -- it still scored 0 only because the audit was missing. The tell was
    in the evidence (`state_matches=True` on a no-op), which is why the evidence line exists.
    """
    module = registry.importlib.import_module(f"data.datasets.{files_scaffold}")
    for record in module._RECORDS:
        initial = record["state"]
        gold = module._closed_state(record)
        assert gold != initial, f"{record['task_id']}: closing the ticket changes nothing"
        assert record["ticket_id"] in [t["id"] for t in initial["tickets"]], \
            f"{record['task_id']}: the ticket is not in the initial state"
        files = module.oracle_files({"task_id": record["task_id"], "goal": "", "inputs": []})
        assert set(files) == {"state/tickets.json", "state/audit.log"}
        assert json.loads(files["state/tickets.json"]) == gold
