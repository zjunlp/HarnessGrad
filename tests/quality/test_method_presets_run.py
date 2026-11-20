"""Run built-in experiment recipes through the driver on the synthetic demo tasks.

Static checks cover the checked-in method entrypoints. Offline controls run the
echo method, a declared local CLI editor, and a held-out evaluation of a saved
checkpoint. Every model-driven method must refuse an explicitly disabled editor
before H0, even when a method-role HTTP channel is configured.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad.experiment import config as experiments               # noqa: E402

EXPERIMENTS = ROOT / "experiments"
MODEL_PRESETS = sorted(EXPERIMENTS.glob("method-*.json")) + [EXPERIMENTS / "gsm8k-dgm-study.json"]
QUICKSTART = EXPERIMENTS / "quickstart.json"
PRESETS = [*MODEL_PRESETS, QUICKSTART]


def _probe(tmp_path: Path, config: Path, *, run_id: str | None = None,
           rounds: int | None = 1, improver: str = "none",
           registry: Path | None = None) -> tuple[subprocess.CompletedProcess, list[dict]]:
    ident = run_id or f"{config.stem}-probe"
    runs_root = tmp_path / "runs"
    argv = [
        sys.executable, "driver.py", "--config", str(config),
        "--set", "dataset=demo", "--set", "sampling=all",
        *([] if rounds is None else ["--set", f"rounds={rounds}"]),
        "--set", "jobs=1", "--set", "trials=1", "--set", f"run_id={ident}",
        "--runs-root", str(runs_root), "--work-root", str(tmp_path / "work"),
        "--improver", improver, "--fail-on-incomplete",
    ]
    # The embedded Loop study version has the echo method's source anchor and
    # can solve the mock demo without building a managed environment or fetching wheels.
    if config == QUICKSTART or config.name == "eval-from-round.json" or registry:
        argv.extend(["--set", f"harness={ROOT / 'tools/assets/loop-study-0.2.0'}", "--no-sandbox"])
    if config.name == "gsm8k-dgm-study.json":
        argv.extend(["--set", "agent_backend=mock", "--set", "agent_model=fixture"])
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HG_", "HARNESSGRAD_", "DSH_")) and key != "DEEPSEEK_API_KEY"}
    env.update(HG_AGENT_BACKEND="mock", HG_REVIEWED_ENVIRONMENT_ONLY="1",
               HG_METHOD_BASE_URL="http://127.0.0.1:9/v1", HG_METHOD_MODEL="authored-role",
               HG_METHOD_API_KEY="offline-only", HG_HARNESS_RUNTIME_ROOT=str(tmp_path / "runtime"),
               HG_DGM_CHILDREN="1", UV_OFFLINE="1", TMPDIR=str(tmp_path))
    if registry:
        env["HG_IMPROVER_CONFIG"] = str(registry)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
    curve_path = runs_root / ident / "curve.jsonl"
    points = ([json.loads(line) for line in curve_path.read_text().splitlines() if line.strip()]
              if curve_path.is_file() else [])
    return proc, points


def _required_options(run_py: Path) -> list[str]:
    """Arguments `run_py` marks `required=True`, read as source, not imported.

    An experiment's `"method": "<name>"` renders `python methods/<name>/run.py` with no
    extra arguments, so a required option is a preset that stops in round 1. Parsing the
    source keeps this check cheap and independent of the method's own imports.
    """
    tree = ast.parse(run_py.read_text(encoding="utf-8"), filename=str(run_py))
    required: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        flag = keywords.get("required")
        if not (isinstance(flag, ast.Constant) and flag.value is True):
            continue
        names = [arg.value for arg in node.args
                 if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        # A positional argument has no `--` prefix; both kinds are unreachable from a
        # preset, which supplies no arguments at all.
        required.extend(names or ["<positional>"])
    return required


# ------------------------------------------------------------ static check ---

@pytest.mark.parametrize("config", PRESETS, ids=lambda path: Path(path).stem)
def test_preset_method_takes_no_argument_the_file_cannot_supply(config: Path) -> None:
    """The quickstart regression: a preset may only name a method it can actually start."""
    name = experiments.load(config).get("method")
    if not isinstance(name, str) or " " in name or Path(name).is_absolute():
        return  # an absolute path or a command line is not a method name to resolve
    entrypoint = ROOT / "methods" / name / "run.py"
    assert entrypoint.is_file(), (
        f"{config.name} names method {name!r}, but {entrypoint} does not exist")
    required = _required_options(entrypoint)
    assert not required, (
        f"{config.name} names method {name!r}, whose entrypoint requires {required} -- "
        "an experiment file supplies a method with no arguments, so round 1 would die. "
        "Name a method that needs nothing, or document the hand-written command.")


# ----------------------------------------------------------- dynamic check ---

@pytest.mark.parametrize("config", MODEL_PRESETS, ids=lambda path: path.stem)
def test_model_preset_refuses_disabled_editor_before_h0(tmp_path: Path, config: Path) -> None:
    proc, points = _probe(tmp_path, config)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 2, output
    assert "CLI Meta-Harness" in output, output
    assert points == []


def test_echo_preset_reaches_a_measured_round_without_an_editor(tmp_path: Path) -> None:
    proc, points = _probe(tmp_path, QUICKSTART)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [row["round"] for row in points] == [0, 1]
    assert points[-1]["n_scored"] > 0 and points[-1]["score"] is not None


def test_dgm_preset_edits_through_the_declared_cli(tmp_path: Path) -> None:
    home = tmp_path / "fixture-home"
    home.mkdir()
    launcher = tmp_path / "fixture-editor"
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('fixture-editor 1.0.0')\n"
        "else:\n"
        "    with Path('agent.py').open('a') as f:\n"
        "        f.write('\\n# Edited by the authored CLI.\\n')\n"
        "    print('Updated the mock harness.')\n"
    )
    launcher.chmod(0o755)
    registry = tmp_path / "improvers.json"
    registry.write_text(json.dumps({"default": "fixture", "improvers": {"fixture": {
        "name": "fixture", "kind": "cli", "argv_style": "dsh", "launcher": str(launcher),
        "home": str(home), "provider": "fixture", "profile": "headless", "model": "authored",
    }}}))
    proc, points = _probe(tmp_path, EXPERIMENTS / "method-dgm.json", improver="fixture", registry=registry)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [row["round"] for row in points] == [0, 1]
    assert points[1]["n_scored"] > 0 and points[1]["score"] is not None
    assert points[1]["identity"]["improver"]["name"] == "fixture"
    candidate = tmp_path / "runs/method-dgm-probe/state_after_round1/agent.py"
    assert "Edited by the authored CLI." in candidate.read_text()


# ------------------------------------------------- eval-side integration ---

def test_eval_from_round_measures_a_donor_run(tmp_path: Path) -> None:
    """`eval-from-round.json` names round 1 of run `quickstart`; build that donor first.

    This is the one preset that measures a *finished* run, so it cannot run alone. The
    donor is built from `quickstart.json`'s own settings under the run id the eval file
    names, which is what a user following the README would have on disk.
    """
    donor, donor_points = _probe(tmp_path, EXPERIMENTS / "quickstart.json",
                                 run_id="quickstart", rounds=None)
    donor_tail = (donor.stdout + donor.stderr)[-2500:]
    assert donor.returncode == 0, f"donor run failed:\n{donor_tail}"
    # The donor is built exactly as the file declares it: `eval-from-round.json` names one of
    # *its* rounds, and hard-coding a round count here is how the two files drift apart.
    wanted = int(json.loads((EXPERIMENTS / "eval-from-round.json").read_text(encoding="utf-8"))["from_round"])
    assert wanted in [point.get("round") for point in donor_points], donor_tail

    proc, points = _probe(tmp_path, EXPERIMENTS / "eval-from-round.json",
                          run_id="eval-from-round-probe")
    tail = (proc.stdout + proc.stderr)[-2500:]
    assert proc.returncode == 0, f"eval-from-round exited {proc.returncode}:\n{tail}"
    assert len(points) == 1, f"an eval run writes one point, got {len(points)}:\n{tail}"
    point = points[0]
    assert point.get("side") == "eval", point.get("side")
    # The point must name the donor and the round it took the state from, or the number
    # is attached to no reproducible source.
    evaluated = point.get("evaluated_from") or {}
    assert evaluated.get("run_id") == "quickstart", evaluated
    assert evaluated.get("round") == wanted, evaluated
    assert point.get("n_scored"), point.get("n_scored")
