"""A finished run leaves its one page behind: `end-summary.md` and `end-summary.json`.

`tools/run_summary.py` already knows how to turn a run directory into that page; the gap
this file pins is that a reader had to know to run it. These tests ask a **real** driver
(mock backend, one improvement round, no sandbox) whether the page is there when the run
returns, whether `--no-summary` keeps it away, and whether a summary that blows up changes
anything a reader relies on -- the exit code and `curve.jsonl`.

The failure case runs the driver in-process on purpose: `monkeypatch` is how a broken
summary is injected, and a subprocess would not see the patch. The fake `summarise()` reads
the curve at the moment the driver calls it, which is also the moment the record is final,
and compares those bytes with the file left after the failure.

The last test runs `--prepare-only`: it measures nothing, so there is no curve to describe
and no page may appear.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import driver  # noqa: E402
from harnessgrad import config  # noqa: E402


def run_argv(tmp_path: Path, run_id: str, *extra: object) -> list[str]:
    """The smallest measurable run: four demo tasks, H0 plus the noop method's one round."""
    return [
        "driver.py", "--harness", "loop_plain", "--mode", "A",
        "--rounds", "1", "--run-id", run_id, "--dataset", "demo",
        "--sampling", "all", "--runs-root", str(tmp_path / "runs"),
        "--work-root", str(tmp_path / "work"), "--no-sandbox",
        # Absolute: the driver executes the method from the harness workspace.
        "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}",
        *map(str, extra),
    ]


def run_driver(tmp_path: Path, run_id: str, *extra: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *run_argv(tmp_path, run_id, *extra)],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})


def curve_points(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in
            (run_dir / "curve.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_a_finished_run_leaves_the_page_beside_its_curve(tmp_path):
    proc = run_driver(tmp_path, "auto-summary")
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]

    run_dir = tmp_path / "runs" / "auto-summary"
    assert (run_dir / "curve.jsonl").is_file()
    page = run_dir / "end-summary.md"
    payload = run_dir / "end-summary.json"
    assert page.is_file(), "a finished run left no end-summary.md"
    assert payload.is_file(), "a finished run left no end-summary.json"

    summary = json.loads(payload.read_text(encoding="utf-8"))
    assert summary["schema"] == "harnessgrad.run-summary.v1"
    assert summary["run_id"] == "auto-summary"
    # The page and the curve it describes are the same record: H0 plus round 1.
    assert summary["rounds"] == len(curve_points(run_dir)) == 2
    assert summary["score"]["r0"] == summary["score"]["last"] == 0.0
    assert page.read_text(encoding="utf-8").startswith("# Run summary: auto-summary")

    # It is announced with the other artifacts, not left for the user to discover.
    assert "end-summary.md" in proc.stdout


def test_no_summary_flag_leaves_the_run_without_the_page(tmp_path):
    proc = run_driver(tmp_path, "auto-summary-off", "--no-summary")
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]

    run_dir = tmp_path / "runs" / "auto-summary-off"
    # The record itself is untouched by the flag: only the derived page is absent.
    assert (run_dir / "curve.jsonl").is_file()
    assert len(curve_points(run_dir)) == 2
    assert not (run_dir / "end-summary.md").exists()
    assert not (run_dir / "end-summary.json").exists()


def test_summary_dir_redirects_the_page(tmp_path):
    pages = tmp_path / "pages"
    proc = run_driver(tmp_path, "auto-summary-elsewhere", "--summary-dir", pages)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]

    run_dir = tmp_path / "runs" / "auto-summary-elsewhere"
    assert (pages / "end-summary.md").is_file()
    assert (pages / "end-summary.json").is_file()
    assert json.loads((pages / "end-summary.json").read_text(encoding="utf-8"))["run_id"] \
        == "auto-summary-elsewhere"
    assert not (run_dir / "end-summary.md").exists()
    assert not (run_dir / "end-summary.json").exists()


def test_prepare_only_measures_nothing_and_writes_no_page(tmp_path):
    proc = subprocess.run(
        [sys.executable, *run_argv(tmp_path, "auto-summary-prepared", "--prepare-only")],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]

    prepared = Path(proc.stdout.strip().splitlines()[-1])
    assert (prepared / "run_meta.json").is_file()
    assert not (prepared / "curve.jsonl").exists(), "prepare-only must measure nothing"
    assert not (prepared / "end-summary.md").exists()
    assert not (prepared / "end-summary.json").exists()


def test_a_summary_failure_changes_neither_the_exit_code_nor_the_curve(
        tmp_path, monkeypatch, capsys):
    seen: dict[str, bytes] = {}

    def exploding_summarise(run_dir):
        # This is the moment the record is final: the curve the page is derived from has
        # already been written, and the failure below happens after it.
        seen["curve"] = (Path(run_dir) / "curve.jsonl").read_bytes()
        raise RuntimeError("the page cannot be rendered today")

    monkeypatch.setattr(driver.run_summary, "summarise", exploding_summarise)
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setattr(sys, "argv", run_argv(tmp_path, "auto-summary-boom"))
    # An in-process run writes run-level facts into the environment (the seed and the
    # work root) and leaves the console attached; put the process back as this test found
    # it, so the next test does not inherit a run it never started.
    environment = dict(os.environ)
    config.reset()
    try:
        result = driver.main()
    finally:
        config.reset()
        driver.console.close()
        os.environ.clear()
        os.environ.update(environment)
    captured = capsys.readouterr()

    # A summary failure is not the run's failure.
    assert result == 0, captured.err
    run_dir = tmp_path / "runs" / "auto-summary-boom"
    assert "curve" in seen, "the summary was never attempted after the curve was final"
    assert seen["curve"] == (run_dir / "curve.jsonl").read_bytes(), \
        "a failed summary changed curve.jsonl"
    assert len(curve_points(run_dir)) == 2
    assert not (run_dir / "end-summary.md").exists()
    assert not (run_dir / "end-summary.json").exists()

    # One line, naming the page and the failure -- and only one.
    warnings = [line for line in captured.err.splitlines() if "end-summary" in line]
    assert len(warnings) == 1, captured.err
    assert "warning:" in warnings[0] and "RuntimeError" in warnings[0]
    assert "cannot be rendered today" in warnings[0]
