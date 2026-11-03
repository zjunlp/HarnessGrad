"""`--check-only` and `--prepare-only`: "will this run?" without spending a model call.

The defect these tests guard is not a crash, it is a *discovery order*. Every
prerequisite of a run -- a dataset whose bytes are absent, a `bwrap` that cannot build a
namespace, a harness manifest naming an entrypoint that is not on disk -- used to be
found *inside* the run, after round 0 had already been paid for. A user's first question
is whether the run can happen, and the only answer the platform had was to spend the
budget finding out.

So the assertions below are about four properties, each of which was a way to get this
wrong:

* the table has one row per check, and a probe that fails is reported as that check with
  the name of the thing that is broken in its detail (not a traceback, not a bare
  "unavailable");
* one long detail cannot break the table's shape, because a reader scans it;
* a required failure is exit 2 and the offending name is on stdout where the user is
  looking;
* nothing is created by `--check-only`, and `--prepare-only` writes a record whose
  credential is `{"present": bool}` -- never the key.

No network, no model calls, no Docker: the driver subprocesses use `--no-sandbox`, and
the one dataset they need is found by asking the registry rather than hardcoded. The
in-process tests monkeypatch the probes they exercise.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.scaffolds.registry as datasets   # noqa: E402
import harnessgrad.readiness as readiness    # noqa: E402

#: The seven checks every run plans, in the order a run meets them. `endpoint` is the
#: eighth and only exists when `--check-net` asks for it, so it is asserted separately.
CHECKS = ["python", "git", "bwrap", "dataset", "harness", "writable", "disk"]

#: The key the `--prepare-only` test plants. A value nothing else on this machine can
#: contain, so finding it in `run_meta.json` is a leak and not a coincidence.
PLANTED_KEY = "sk-readiness-test-2f8a1c-must-not-be-written"


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    """The namespace a real driver argv produces, after `_SCENARIO_ARG_DEFAULTS` is applied.

    A namespace rather than a parsed command line on purpose: `plan_checks` is the thing
    under test here, and building it from `argparse` would also test the parser. The
    fields that are *not* defaults (`sandbox`, `harness_path`, `check_net`) are the ones
    the checks branch on, so they are named even when they are `False`.
    """
    values = dict(
        dataset="demo",          # a fixture: published() will not contain it
        harness="loop",
        side="train",
        sampling="diagnostic",
        rounds=0,
        trials=1,
        jobs=1,
        split=None,
        pool=None,
        runs_root=str(tmp_path / "runs"),
        work_root=str(tmp_path / "work"),
        sandbox=False,           # keep the probe local: bwrap is not a test dependency
        harness_path=None,
        check_net=False,
        method_entrypoint="python methods/noop/run.py",
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _published_without_data() -> str:
    """A published dataset this machine has not provisioned -- found, never hardcoded.

    Hardcoding one was how the earlier generated tests rotted: `bfcl_v3` was unprovisioned
    when the test was written and became provisioned, so the test then asserted exit 2
    against a machine that was ready -- a green suite that proved nothing. The scan is
    `published()` + each adapter's own `provision_status()`, the same two facts the
    driver's `dataset` check reads.
    """
    for name in datasets.published():
        module = importlib.import_module(datasets.module_name(name))
        status = getattr(module, "provision_status", None)
        if not callable(status):
            continue
        try:
            if not status().get("present"):
                return name
        except Exception:                                      # noqa: BLE001
            return name   # a status that raises is not a provisioned dataset either
    pytest.skip("every published dataset is provisioned on this machine")


def _driver(*argv: str, cwd: Path = ROOT, env: dict | None = None):
    """Run the real driver in its own process -- the flags exist for humans, so ask it."""
    return subprocess.run([sys.executable, "driver.py", *argv], cwd=cwd,
                          capture_output=True, text=True, timeout=300,
                          env={**os.environ, **(env or {})})


# ------------------------------------------------------------- the table ---

def test_plan_checks_has_one_row_per_check_and_names_a_failing_dataset(tmp_path, monkeypatch):
    """Every check is a row, and a dataset that will not load is *that row's* failure.

    `load()` is the probe that fails here, and the assertion is about the detail: a user
    whose run cannot load its data has to be told which dataset and why. "dataset: FAIL"
    alone sends them to the wrong fix (or to `--help`).
    """
    # The registry enumerates every adapter for `published()`, which is 39 imports and
    # nothing this test is about. Pinning it also makes the test independent of which
    # adapters happen to be importable on the machine running it.
    monkeypatch.setattr(readiness.datasets, "published", lambda: ("demo",))
    monkeypatch.setattr(readiness.datasets, "load", _raise_no_data)

    checks = readiness.plan_checks(_args(tmp_path))

    assert [check.name for check in checks] == CHECKS
    dataset_row = next(check for check in checks if check.name == "dataset")
    assert dataset_row.required is True
    assert dataset_row.ok is False
    assert "demo" in dataset_row.detail, dataset_row.detail
    assert "no data at /nonexistent/demo" in dataset_row.detail, dataset_row.detail
    assert readiness.all_required_ok(checks) is False

    # The sandbox is off in these arguments, so its row is present but not required --
    # "not needed" and "not working" must be distinguishable from the table alone.
    bwrap_row = next(check for check in checks if check.name == "bwrap")
    assert bwrap_row.required is False and bwrap_row.ok is True
    assert "no-sandbox" in bwrap_row.detail


def _raise_no_data(name: str):
    raise ValueError(f"{name}: no data at /nonexistent/{name}")


def test_a_fixture_the_driver_runs_is_not_reported_as_a_typo(tmp_path):
    """`--dataset demo` runs, so the preflight that certifies a run must not refuse it.

    Measured 2026-11-02: `driver.py --dataset demo --check-only` printed "demo is not a
    published dataset" while the same arguments minus `--check-only` ran 8 tasks and wrote a
    curve. The preflight is read *before* anyone spends money, so a false refusal there is
    worse than no preflight: it tells a new user their working setup is broken.
    """
    tasks = {"demo": [{"task_id": "t01"}]}

    def fake_load(name):
        return tasks[name], {}

    def fake_kind(name):
        if name == "demo":
            return "fixture"
        raise ImportError(f"no module for {name}")

    args = _args(tmp_path)
    args.dataset = "demo"
    original = (readiness.datasets.published, readiness.datasets.load,
                readiness.datasets.kind_of)
    try:
        readiness.datasets.published = lambda: ()          # nothing is "published" here
        readiness.datasets.load = fake_load
        readiness.datasets.kind_of = fake_kind
        fixture_row = next(check for check in readiness.plan_checks(args)
                           if check.name == "dataset")
        # A name nothing answers to is still a failure, with the published-list hint.
        args.dataset = "not_a_dataset"
        unknown_row = next(check for check in readiness.plan_checks(args)
                           if check.name == "dataset")
    finally:
        (readiness.datasets.published, readiness.datasets.load,
         readiness.datasets.kind_of) = original

    assert fixture_row.ok is True, fixture_row.detail
    assert "fixture" in fixture_row.detail and "1 tasks" in fixture_row.detail
    assert unknown_row.ok is False
    assert "not a published dataset" in unknown_row.detail


def test_render_names_every_check_and_one_long_detail_cannot_break_it():
    """The table keeps its columns whatever a probe puts in `detail`.

    A detail is the least controlled string in the output: an adapter names a data path, a
    sandbox names a kernel error, a dataset names its tasks. `render` must survive that --
    collapsing newlines and cutting at a fixed width -- or one failure in a 60-column
    terminal makes the other seven rows unreadable.
    """
    checks = [
        readiness.Check("python", True, True, "python 3.11.9"),
        readiness.Check("bwrap", True, False, "a kernel message\nwith a second line"),
        readiness.Check("dataset", True, False, "some_dataset: " + "x" * 500),
        readiness.Check("disk", False, True, "12.0 GB free"),
    ]
    text = readiness.render(checks)
    lines = text.splitlines()

    assert len(lines) == len(checks) + 1, "one line per check plus the header"
    for check in checks:
        assert check.name in text
    # Stable width: every line fits a terminal, and the long detail was cut, not wrapped.
    assert max(len(line) for line in lines) <= 120, text
    assert "x" * 500 not in text
    assert "..." in text
    assert "ok" in text and "FAIL" in text
    # `all_required_ok` is the other half of the same reading: the `disk` row above is
    # optional and failing, and must not decide the exit code by itself.
    assert readiness.all_required_ok(checks) is False
    assert readiness.all_required_ok([c for c in checks if c.required is False]) is True


# --------------------------------------------------------- the flags ---

def test_check_only_exits_2_and_names_an_unprovisioned_dataset(tmp_path):
    """A required failure is exit 2, and the dataset is named on stdout.

    This is the whole feature in one assertion: before it, this machine's answer was
    "spend a round and find out". `--no-sandbox` keeps bwrap (and the host's user
    namespaces) out of it -- the check under test is the dataset one.
    """
    unprovisioned = _published_without_data()
    proc = _driver(
        "--check-only", "--dataset", unprovisioned, "--harness", "loop",
        "--runs-root", str(tmp_path / "runs"), "--work-root", str(tmp_path / "work"),
        "--no-sandbox")

    assert proc.returncode == 2, f"{proc.returncode}\n{proc.stdout[-1200:]}\n{proc.stderr[-800:]}"
    assert unprovisioned in proc.stdout, proc.stdout[-1200:]
    assert "dataset" in proc.stdout
    # Named on the row, so a reader can tell *why* it is unprovisioned without a rerun.
    row = next(line for line in proc.stdout.splitlines() if line.startswith("dataset"))
    assert "FAIL" in row, row


def test_check_only_creates_no_run_directory(tmp_path):
    """Nothing is created: not the run directory, not the runs root, not the work root.

    `demo` is a fixture the driver runs, so this preflight now passes (measured
    2026-11-02; it used to be refused as "not a published dataset", which was wrong). The
    property under test is unchanged either way: reaching a verdict, pass or fail, leaves
    the filesystem exactly as it was found. A preflight that creates the directory it is
    checking has already answered the wrong question.
    """
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    proc = _driver(
        "--check-only", "--dataset", "demo", "--harness", "loop", "--run-id",
        "readiness-probe", "--runs-root", str(runs), "--work-root", str(work),
        "--no-sandbox")

    assert proc.returncode == 0, f"{proc.returncode}\n{proc.stdout[-1200:]}\n{proc.stderr[-800:]}"
    assert "check" in proc.stdout, "the table is what --check-only prints"
    assert "fixture" in proc.stdout, "the fixture is named as one, not as a published dataset"
    assert not runs.exists(), list(runs.iterdir()) if runs.exists() else runs
    assert not work.exists(), "the work root is a run artifact, not a preflight one"
    assert not (tmp_path / "readiness-probe").exists()


def test_prepare_only_writes_a_redacted_run_meta_and_launches_nothing(tmp_path):
    """The two artifacts a run costs nothing for: its directory and its record.

    The record is read by people and written to disk, so the credential rule is asserted
    at its strongest -- the planted key must not appear *anywhere* in the bytes, and the
    `present` flag is how an operator still sees that the deployment has one. No curve, no
    events, no workspace: a prepared directory is a promise, not a measurement.
    """
    runs = tmp_path / "runs"
    proc = _driver(
        "--prepare-only", "--dataset", "demo", "--harness", "loop", "--run-id",
        "prepared-probe", "--sampling", "all",
        "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
        "--method-entrypoint", "python methods/noop/run.py",
        env={"HG_AGENT_API_KEY": PLANTED_KEY, "HG_AGENT_MODEL": "readiness-model",
             "HG_AGENT_BACKEND": "mock"})

    assert proc.returncode == 0, f"{proc.returncode}\n{proc.stdout[-800:]}\n{proc.stderr[-1200:]}"
    run_dir = runs / "prepared-probe"
    assert run_dir.is_dir(), proc.stdout
    assert str(run_dir) in proc.stdout, "the flag prints the directory it prepared"

    raw = (run_dir / "run_meta.json").read_text()
    assert PLANTED_KEY not in raw, "a credential value was written to run_meta.json"
    meta = json.loads(raw)
    assert meta["run_id"] == "prepared-probe"
    assert meta["dataset"] == "demo"
    assert meta["prepared_by"] == "--prepare-only"
    assert meta["launched"] is False
    assert meta["config"]["agent"]["api_key"] == {"present": True}
    # `dump()` is the same redaction a real run's first write uses; nothing else in the
    # record may name a credential by value either.
    assert all(PLANTED_KEY not in json.dumps(value) for value in meta["config"].values())

    # Nothing was launched: a prepared directory holds the record and nothing that a
    # reader could mistake for a measured run.
    assert not (run_dir / "curve.jsonl").exists()
    assert not (run_dir / "events.jsonl").exists()
    assert not (Path(tmp_path / "work") / "prepared-probe").exists()
