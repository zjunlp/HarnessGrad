"""`--config` / `--set` reach the driver as the flags they name.

The point of a checked-in experiment file is that it stops being a second, stale copy of a
command line: `--config experiments/quickstart.json --dataset alfworld` has to mean "the
same experiment, on another dataset". That only works if the expansion happens **before**
parsing and the generated flags come **first** — argparse keeps the last value for a
repeated flag, so the explicit one wins exactly as it does on a hand-written command.

`sys.argv` is deliberately left untouched by the expansion: `run_meta.json:argv` keeps
`--config <file>`, which is the provenance a reader needs, and the file itself is in the
repository under version control.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import driver  # noqa: E402


QUICKSTART = ROOT / "experiments/quickstart.json"


def _quickstart() -> dict:
    """The checked-in first experiment, read so these assertions cannot drift from it.

    A second copy of `quickstart.json` written into the test is a second place to update;
    it went stale once already, when the front door became the `demo` dataset.
    """
    return json.loads(QUICKSTART.read_text(encoding="utf-8"))


def test_no_experiment_flags_means_no_work():
    assert driver._expand_experiment_argv(["--dataset", "demo"]) is None


def test_config_is_expanded_before_explicit_flags():
    argv = driver._expand_experiment_argv(
        ["--config", "experiments/quickstart.json", "--dataset", "alfworld"])
    assert argv is not None
    assert "--config" not in argv and "experiments/quickstart.json" not in argv
    # The file's own dataset is generated first; the explicit one comes later.
    assert argv.count("--dataset") == 2
    assert argv[argv.index("--dataset") + 1] == _quickstart()["dataset"]
    assert argv[-2:] == ["--dataset", "alfworld"]


def test_set_alone_is_an_override_on_an_empty_experiment():
    argv = driver._expand_experiment_argv(["--set", "rounds=2"])
    assert argv == ["--rounds", "2"]


def test_two_config_flags_are_refused():
    with pytest.raises(SystemExit):
        driver._expand_experiment_argv(
            ["--config", "experiments/quickstart.json",
             "--config", "experiments/eval-from-round.json"])


def test_driver_applies_the_file_and_a_dotted_override(tmp_path):
    config = _quickstart()
    # A round count the file does not already carry, so "the override won" is observable.
    overridden = int(config["rounds"]) + 1
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--config", "experiments/quickstart.json",
         "--set", f"rounds={overridden}", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--no-sandbox", "--prepare-only"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prepared = Path(proc.stdout.strip().splitlines()[-1])
    record = json.loads((prepared / "run_meta.json").read_text())
    # From the file:
    assert record["dataset"] == config["dataset"]
    assert record["sampling_policy"] == config["sampling"]
    # From `--set`, which must win over the file's own round count:
    assert record["rounds"] == overridden
    # A prepared record never claims to have launched anything.
    assert record.get("launched") is False
