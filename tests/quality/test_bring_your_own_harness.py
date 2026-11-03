"""The on-ramp in `docs/bring-your-own-harness.md` works, on the shipped examples.

**Why this file exists.** The page tells an outside researcher what to run; a page whose
commands no longer run is worse than no page, because it is believed. Two drifts were live
when it was written, and neither was caught by anything:

* `examples/bash-harness` named `run.sh` as its `entrypoint`. The platform starts every
  entrypoint as `<python> <entrypoint>` (`eval/run/runner.py`), so the example scored
  `0.000` with `n_harness_failed: 4` and `SyntaxError: unmatched ')'` on every task, and
  `tools/validate_harness.py --smoke` failed it identically. `console/uat/harness-upload-5min.md`
  said shell entrypoints ran under the shell interpreter; they do not.
* neither example declared a `components` map, so a point measured on one could never say
  which component an edit moved -- the same gap `test_base_harness_components.py` was
  written to close for `base_harness/`, left open for the two trees a contributor copies.

So the assertions are the page's own claims, in the page's own words: the smoke command, the
demo command, the shell body still being a shell body, the shim being a shim, and the maps
being readable. The last one pins the **limitation** rather than a behaviour: a shell
entrypoint without a shim is not measurable, and if that ever changes this test fails and the
page must be re-verified instead of quietly becoming wrong.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
BASH = EXAMPLES / "bash-harness"
REACT = EXAMPLES / "python-react-harness"


def _env() -> dict:
    return {**os.environ, "HG_AGENT_BACKEND": "mock", "HG_BASH_EXAMPLE_ANSWER": ""}


def _run(*argv: object, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *map(str, argv)], cwd=cwd, env=_env(),
                          capture_output=True, text=True, timeout=300)


def test_the_smoke_command_passes_on_both_examples() -> None:
    """§2 of the page: `--smoke`, twice, on the two trees a reader copies."""
    for example in (BASH, REACT):
        proc = _run("tools/validate_harness.py", "--smoke", example)
        assert proc.returncode == 0, example.name + "\n" + proc.stdout + proc.stderr
        assert "smoke run produced an answer" in proc.stdout, proc.stdout


def test_the_demo_command_measures_the_bash_example(tmp_path: Path) -> None:
    """§3 of the page: the external path, one point, a *measured* zero.

    `score 0.0` with `n_harness_failed: 0` is the whole distinction the page spends §4.1
    on: the harness ran and answered `BASH_HARNESS_OK` where `demo` expects `alpha`. It is
    not a crash, and a regression to one would otherwise read as the same zero.
    """
    proc = _run("driver.py", "--dataset", "demo",
                "--harness-path", BASH, "--sampling", "all", "--side", "train",
                "--rounds", "0", "--trials", "1", "--jobs", "1",
                "--run-id", "byo-test",
                "--runs-root", tmp_path / "runs", "--work-root", tmp_path / "work")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    curve = (tmp_path / "runs/byo-test/curve.jsonl").read_text().splitlines()
    assert len(curve) == 1, curve
    point = json.loads(curve[0])
    assert point["identity"]["harness_name"] == "bash-example"
    assert point["n_scored"] == 4
    assert point["n_harness_failed"] == 0, point.get("harness_failed")
    assert point["score"] == 0.0
    assert set(point["per_task"]) == {"t01", "t02", "t03", "t04"}
    verdict = json.loads((tmp_path / "runs/byo-test/verdicts/round-0/t01.json").read_text())
    assert verdict["passed"] is False
    assert verdict["harness"] == {"exit_code": 0, "stderr": ""}
    assert "BASH_HARNESS_OK" in verdict["detail"] and "alpha" in verdict["detail"]


def test_the_bash_example_body_is_still_a_shell_harness() -> None:
    """`run.sh` is the harness and stays runnable by hand (`console/uat/…` does exactly this)."""
    manifest = json.loads((BASH / "harness.json").read_text())
    assert manifest["entrypoint"] == "run.py"
    assert "run.sh" in manifest["components"]["control"]
    with tempfile.TemporaryDirectory() as tmp:
        task = Path(tmp) / "task.json"
        task.write_text(json.dumps({"task_id": "demo", "goal": "Write an answer."}))
        work = Path(tmp) / "result"
        proc = subprocess.run(["bash", str(BASH / "run.sh"),
                               "--task", str(task), "--workdir", str(work)],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (work / "answer.txt").read_text().strip()
        rows = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
        assert any("harness_identity" in row for row in rows)


def test_a_shell_entrypoint_without_a_shim_is_not_measurable(tmp_path: Path) -> None:
    """The measured limitation §1.4 of the page documents, pinned where it would break.

    If this ever passes the smoke, the platform learned to honour a non-Python entrypoint
    and `docs/bring-your-own-harness.md` §1.4 plus `docs/writing_a_harness.md` §4 have to be
    re-verified -- which is the point: the page must not be right by accident.
    """
    copy = tmp_path / "shell-only"
    shutil.copytree(BASH, copy)
    (copy / "run.py").unlink()
    manifest = json.loads((copy / "harness.json").read_text())
    manifest["entrypoint"] = "run.sh"
    (copy / "harness.json").write_text(json.dumps(manifest))
    proc = _run("tools/validate_harness.py", "--smoke", copy)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SyntaxError" in proc.stdout, proc.stdout


def test_both_examples_declare_a_readable_components_map() -> None:
    """§5.2 of the page: the map is what makes `editable_surface_components` non-empty."""
    sys.path.insert(0, str(ROOT))
    from harnessgrad import components

    for example in (BASH, REACT):
        declared = components.declared_components(example)
        assert declared, example.name
        assert set(declared) & set(components.COMPONENTS), declared
        for name, paths in declared.items():
            if name == components.UNATTRIBUTED:
                continue
            assert name in components.COMPONENTS
            for path in paths:
                assert (example / path).exists(), (example.name, path)
        assert declared == components.declared_components(example)  # no error on re-read
