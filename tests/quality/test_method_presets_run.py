"""Every checked-in preset must reach a measured round, not just an H0 point.

A preset that cannot finish round 1 is worse than no preset: an outside user's first
contact with the release is the wall it hits. The bug this module exists for was exactly
that shape -- `quickstart.json` named a wrapper whose entrypoint declares `--base` as
required, no experiment field can pass a method its own arguments, so round 1 died and the
curve stopped at H0. The driver still exited 0, so "the run succeeded" was true and
useless. That is why the assertion below is *exit 0 and a point for round 1*, never exit 0
alone.

Two layers, because they fail differently:

* **Static** -- the preset's method resolves to a real `methods/<name>/run.py` that needs no
  argument the file cannot supply. This is the quickstart class, and it costs a parse.
* **Dynamic** -- run the preset through `driver.py` with `HG_AGENT_BACKEND=mock` on the
  eight-task `demo` fixture, `rounds=1`, one job, one trial, in a temporary root. This
  catches a config key the driver no longer accepts, a stale run id, a renamed directory,
  and a round that produces no point.

**A method that needs a model is not silently passed.** Nine presets name published ports
whose editing layer is a chat endpoint (`HG_METHOD_BASE_URL / _API_KEY / _MODEL`, see
`methods/editor.py:model_settings`); `HG_AGENT_BACKEND=mock` covers the *harness* model and
deliberately not the method's, which is a separate budget. Model-free those runs stop at H0
with `a method needs HG_METHOD_BASE_URL` -- a real, named requirement, so the test skips
naming it rather than pretending the run passed. Any *other* reason for stopping at H0 is a
failure, which is what keeps the skip from swallowing a new bug.

The probe scrubs `HG_METHOD_*` and `DEEPSEEK_API_KEY` from the environment so a developer
with credentials exported gets the same no-cost result as CI, instead of quietly billing an
endpoint.
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

from harnessgrad import experiments                                    # noqa: E402

EXPERIMENTS = ROOT / "experiments"
PRESETS = sorted(EXPERIMENTS.glob("method-*.json")) + [EXPERIMENTS / "quickstart.json"]

#: The message a method's own `model_settings()` raises when it has no chat endpoint.
#: Named rather than pattern-matched loosely: it is one of the two legitimate reasons for a
#: preset to stop at H0 in this probe, so anything else must fail the test.
NEEDS_ENDPOINT = "a method needs HG_METHOD_BASE_URL"

#: The other one: a method whose *policy* needs an operator decision no experiment field can
#: carry. `modular_rsi` partitions the harness into modules and refuses to propose without the
#: map (`methods/modular_rsi/method.json:requires.modules`), and the map is a judgement about
#: the harness being improved, not a constant. The skip names it instead of pretending.
NEEDS_CONFIG = "requires explicit nonempty modules"

#: Variables that would turn the probe into a live/paid run, or select a CLI improver
#: that is not model-free. Scrubbed rather than assumed absent.
_LIVE_ENV = (
    "DEEPSEEK_API_KEY",
    "HG_AGENT_BASE_URL",
    "HG_AGENT_API_KEY",
    "HG_AGENT_MODEL",
)


def _model_free_env() -> dict[str, str]:
    """The probe's environment: mock harness, no method credentials, host temp dir."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("HG_METHOD_")
        and not key.startswith("HG_IMPROVER")
        and key not in _LIVE_ENV
    }
    env["HG_AGENT_BACKEND"] = "mock"
    return env


def _probe(tmp_path: Path, config: Path, *,
           run_id: str | None = None) -> tuple[subprocess.CompletedProcess, list[dict]]:
    """Run one preset as checked in, redirected to the demo fixture for one round."""
    ident = run_id or f"{config.stem}-probe"
    runs_root = tmp_path / "runs"
    argv = [
        sys.executable, "driver.py", "--config", str(config),
        "--set", "dataset=demo", "--set", "sampling=all",
        "--set", "rounds=1", "--set", "jobs=1", "--set", "trials=1",
        "--set", f"run_id={ident}",
        "--runs-root", str(runs_root), "--work-root", str(tmp_path / "work"),
        "--no-sandbox",
    ]
    env = _model_free_env()
    # Keep the run's own temp trees beside its roots, so the test leaves nothing on the
    # shared filesystem that pytest's cleanup does not own.
    env["TMPDIR"] = str(tmp_path)
    proc = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
    curve_path = runs_root / ident / "curve.jsonl"
    points = ([json.loads(line) for line in
               curve_path.read_text(encoding="utf-8").splitlines() if line.strip()]
              if curve_path.is_file() else [])
    return proc, points


def _recorded_failure(ident: str, tmp_path: Path) -> str:
    """The run's own words about a failed step, not the console's truncated line.

    The progress lines cut a message at a fixed width (`... ✗ _BASE_URL / HG_METHOD_API_KEY
    / HG_METHOD_MODEL. A method brings its own credentials ...`), so the documented prefix
    "a method needs HG_METHOD_BASE_URL" never appears in stdout. `run_meta.json` keeps the
    step's whole text, which is what a skip has to be decided on: without this, a probe
    that stopped for precisely the documented reason failed the test instead of skipping.
    """
    meta = tmp_path / "runs" / ident / "run_meta.json"
    if not meta.is_file():
        return ""
    try:
        document = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return json.dumps(document, ensure_ascii=False)


def _declared_dataset_missing(config: Path) -> str | None:
    """The dataset a preset declares, when this machine cannot load it.

    `provision_status` returns `None` for a fixture that makes no claim (e.g. `demo`),
    which is not a missing dataset; only an explicit `present: False` or an adapter that
    cannot be imported is. Returning the name lets the caller skip *naming the dataset*,
    as a missing external artifact must never read as a silent pass.
    """
    import data.scaffolds.registry as registry                      # noqa: PLC0415

    name = experiments.load(config).get("dataset")
    if not isinstance(name, str) or not name:
        return None
    try:
        status = registry.provision_status(name)
    except ImportError:
        return name
    if status is not None and not status.get("present"):
        return name
    return None


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

@pytest.mark.parametrize("config", PRESETS, ids=lambda path: Path(path).stem)
def test_preset_reaches_a_measured_round(tmp_path: Path, config: Path) -> None:
    """Run the file as checked in and require a curve point for round 1."""
    missing = _declared_dataset_missing(config)
    if missing:
        pytest.skip(f"{config.name} declares dataset {missing!r}, which is not "
                    "provisioned on this machine")

    proc, points = _probe(tmp_path, config)
    tail = (proc.stdout + proc.stderr)[-2500:]
    assert proc.returncode == 0, f"{config.name} exited {proc.returncode}:\n{tail}"
    rounds = [point.get("round") for point in points]
    assert 0 in rounds, f"{config.name} wrote no H0 point:\n{tail}"

    if 1 not in rounds:
        # One stalled point, exactly H0. A repeated or partial point is a different bug,
        # not the documented endpoint wall, so it must fail rather than skip.
        assert rounds == [0], (
            f"{config.name} wrote curve rounds {rounds}; a run that cannot finish round 1 "
            f"must still leave exactly one H0 point:\n{tail}")
        # The only honest reason to stop at H0 here: the method's own editing layer needs
        # a chat endpoint, which is a separate budget from the mocked harness model.
        recorded = _recorded_failure(f"{config.stem}-probe", tmp_path)
        if NEEDS_ENDPOINT in tail or NEEDS_ENDPOINT in recorded:
            pytest.skip(
                f"{config.name} names a published method whose editor needs an improver "
                "endpoint (HG_METHOD_BASE_URL / HG_METHOD_API_KEY / HG_METHOD_MODEL); "
                "model-free it stops at H0 with 'a method needs HG_METHOD_BASE_URL'. "
                "Set those, or use a CLI improver, to complete the round.")
        if NEEDS_CONFIG in tail or NEEDS_CONFIG in recorded:
            pytest.skip(
                f"{config.name} names a method whose policy needs a configured module map "
                "(HG_POLICY_CONFIG='{\"modules\": {...}}', declared in "
                "methods/modular_rsi/method.json:requires); it refuses with a named error "
                "rather than guessing one.")
        pytest.fail(f"{config.name} stopped at H0 for a reason that is not the "
                    f"documented endpoint requirement:\n{tail}")

    # One point per round: H0 and the single improvement step the preset declares.
    assert rounds == [0, 1], f"{config.name} curve rounds are {rounds}, expected [0, 1]"
    round_one = points[-1]
    # A point that scored nothing would satisfy "a point exists" while measuring nothing.
    assert round_one.get("n_scored"), (
        f"{config.name} recorded round 1 with no scored task: {round_one.get('n_scored')}")
    assert round_one.get("score") is not None, (
        f"{config.name} recorded round 1 without a score")


# ------------------------------------------------- eval-side integration ---

def test_eval_from_round_measures_a_donor_run(tmp_path: Path) -> None:
    """`eval-from-round.json` names round 1 of run `quickstart`; build that donor first.

    This is the one preset that measures a *finished* run, so it cannot run alone. The
    donor is built from `quickstart.json`'s own settings under the run id the eval file
    names, which is what a user following the README would have on disk.
    """
    donor, donor_points = _probe(tmp_path, EXPERIMENTS / "quickstart.json",
                                 run_id="quickstart")
    donor_tail = (donor.stdout + donor.stderr)[-2500:]
    assert donor.returncode == 0, f"donor run failed:\n{donor_tail}"
    assert [point.get("round") for point in donor_points] == [0, 1], donor_tail

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
    assert evaluated.get("round") == 1, evaluated
    assert point.get("n_scored"), point.get("n_scored")
