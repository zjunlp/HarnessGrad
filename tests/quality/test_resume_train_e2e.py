"""End-to-end: a one-round run continued to two rounds.

The unit tests cover the loader and its refusals; this one covers the *wiring*, which is
where a resume silently goes wrong: the round-0 block must be skipped, the curve must be
appended to rather than replaced, and `--rounds` must mean "total measured rounds" so that
re-running the command twice does not double the curve.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*extra: str, rounds: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", rounds, "--run-id", "resumer", "--dataset", "demo",
            "--sampling", "all",
            "--runs-root", str(TMP / "runs"), "--work-root", str(TMP / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}",
            *extra]
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})


TMP: Path


def _curve() -> list[dict]:
    path = TMP / "runs" / "resumer" / "curve.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_resume_appends_instead_of_remeasuring(tmp_path: Path) -> None:
    global TMP
    TMP = tmp_path
    first = _run(rounds="1")
    assert first.returncode == 0, f"{first.returncode}\n{first.stdout[-900:]}\n{first.stderr[-900:]}"
    before = _curve()
    assert [p["round"] for p in before] == [0, 1], before

    second = _run("--resume", rounds="2")
    assert second.returncode == 0, (
        f"{second.returncode}\n{second.stdout[-1200:]}\n{second.stderr[-1200:]}")
    after = _curve()
    assert [p["round"] for p in after] == [0, 1, 2], (
        "a resume appends the rounds it measured; it must not restart the curve")
    # The rounds that were already measured are untouched -- same content address, same score.
    for old, new in zip(before, after[:2]):
        assert old["identity"]["harness_sha"] == new["identity"]["harness_sha"]
        assert old["score"] == new["score"]
    # The new point says it came after a resume, so a stitched curve is readable as stitched.
    assert after[2].get("resumed_from") == 1

    meta = json.loads((TMP / "runs" / "resumer" / "run_meta.json").read_text())
    assert meta.get("resumed"), "run_meta must record that this curve was continued"
    assert meta["resumed"][-1]["from_round"] == 1


def test_resume_with_nothing_to_do_is_not_an_error(tmp_path: Path) -> None:
    global TMP
    TMP = tmp_path
    first = _run(rounds="1")
    assert first.returncode == 0
    again = _run("--resume", rounds="1")
    assert again.returncode == 0, "asking to resume a finished budget is not a failure"
    assert len(_curve()) == 2, "nothing may be measured twice"
