"""Experiment files are a checked-in command line, so the spellings must be the driver's.

These tests never launch `driver.py` and never write a run directory: they read the
argparse surface out of the run parser as source, and the only files they create are
temporary experiment fixtures under pytest's `tmp_path`.
"""
from __future__ import annotations

import ast
import shlex
import sys
from pathlib import Path

import pytest

from harnessgrad.experiments import (
    ExperimentError,
    KNOWN_KEYS,
    from_cli,
    load,
    merge,
    overrides,
    to_argv,
)

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = ROOT / "experiments/quickstart.json"
EVAL_FROM_ROUND = ROOT / "experiments/eval-from-round.json"

#: A method name is completed against this checkout, with the interpreter, because the
#: driver runs a method with the *harness worktree* as its working directory and
#: `_check_method_entrypoint` refuses a relative `*.py` argument.
ECHO_SCRIPT = ROOT / "methods/echo_base/run.py"
ECHO_BASE = shlex.join([sys.executable, str(ECHO_SCRIPT)])


def _by_flag(argv: list[str]) -> dict[str, str | bool]:
    """Argument list as flag -> value, so assertion order does not matter."""
    by_flag: dict[str, str | bool] = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        assert flag.startswith("--"), f"argv item {flag!r} is not a flag"
        assert flag not in by_flag, f"{flag} appears twice"
        if flag in ("--no-sandbox", "--fail-on-incomplete"):
            by_flag[flag] = True
            index += 1
            continue
        assert index + 1 < len(argv), f"{flag} has no value"
        by_flag[flag] = argv[index + 1]
        index += 2
    return by_flag


def _driver_flags() -> set[str]:
    """Every run flag, read from its canonical parser as source."""
    tree = ast.parse((ROOT / "harnessgrad/entrypoints/run_parser.py").read_text(encoding="utf-8"),
                     filename="harnessgrad/entrypoints/run_parser.py")
    flags = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        for argument in node.args:
            if (isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.value.startswith("--")):
                flags.add(argument.value)
    return flags


# ---------------------------------------------------------------- example 1 ---

def test_quickstart_argv_is_the_explicit_command() -> None:
    """Test 1: the checked-in example renders to exactly the flags a human would type."""
    expected = [
        "--dataset", "demo",
        "--harness", "loop",
        "--side", "train",
        "--sampling", "all",
        "--rounds", "2",
        "--jobs", "1",
        "--trials", "1",
        "--seed", "0",
        "--run-id", "quickstart",
        "--method-entrypoint", ECHO_BASE,
    ]
    assert _by_flag(to_argv(load(QUICKSTART))) == _by_flag(expected)


def test_quickstart_method_runs_without_extra_setup() -> None:
    """The first experiment file must run as checked in: no wrapper, no required flag.

    `lightrsi` is the better demonstration but it is a *wrapper*: `methods/lightrsi/run.py`
    declares `--base` as required, and an experiment file has no field that reaches a
    method's own arguments, so naming it here would ship an example that stops in round 1.
    The reference method is deliberately the one that needs nothing.
    """
    config = load(QUICKSTART)
    entrypoint = ROOT / "methods" / str(config["method"]) / "run.py"
    assert entrypoint.is_file(), entrypoint
    source = entrypoint.read_text(encoding="utf-8")
    assert "add_argument(\"--base\", required=True" not in source


def test_every_emitted_flag_exists_in_the_driver_parser() -> None:
    """The spellings are checked against `driver.py`, not against a second table."""
    maximal = {
        "dataset": "demo", "harness": "loop", "side": "train",
        "sampling": "all", "pool": "dev", "rounds": 1, "jobs": 1, "trials": 1,
        "seed": "0", "run_id": "maximal", "runs_root": "runs",
        "work_root": "../work", "method": "lightrsi", "from_run": "other",
        "from_round": 1, "no_sandbox": True,
    }
    driver = _driver_flags()
    emitted = [flag for flag in to_argv(maximal) if flag.startswith("--")]
    assert emitted
    assert set(emitted) <= driver, sorted(set(emitted) - driver)
    # The examples are covered by the same check, so an example cannot drift alone.
    assert set(_by_flag(to_argv(load(QUICKSTART)))) <= driver
    assert set(_by_flag(from_cli(EVAL_FROM_ROUND, []))) <= driver


# ---------------------------------------------------------------- unknown ---

def test_unknown_key_is_named_and_known_keys_are_listed() -> None:
    """Test 2: the failure says which key is wrong and what the surface is."""
    with pytest.raises(ExperimentError) as caught:
        from_cli(QUICKSTART, ["mode=A"])  # an old copied command's flag, not a key here
    message = str(caught.value)
    assert "'mode'" in message
    assert "known keys:" in message
    for key in ("dataset", "harness", "rounds", "no_sandbox"):
        assert key in message
    # The message is the documentation the next reader uses, so every key is in it.
    for key in KNOWN_KEYS:
        assert key in message


# --------------------------------------------------------------- overrides ---

def test_overrides_are_typed_and_deep_merge() -> None:
    """Test 3a: dotted paths merge; only unambiguous literals are coerced."""
    patch = overrides(["a.b=3", "x=true", "s=null", "f=2.5", "run_id=007"])
    assert patch == {"a": {"b": 3}, "x": True, "s": None, "f": 2.5,
                     "run_id": "007"}
    base = {"a": {"b": 1, "c": True}, "keep": ["as", "is"]}
    merged = merge(base, patch)
    assert merged["a"] == {"b": 3, "c": True}
    assert merged["keep"] == ["as", "is"]
    assert base["a"] == {"b": 1, "c": True}, "merge must not mutate the loaded config"


def test_no_sandbox_override_is_a_switch() -> None:
    """Test 3b: `no_sandbox=true` is the flag itself, never `--no-sandbox true`."""
    enabled = _by_flag(from_cli(QUICKSTART, ["no_sandbox=true"]))
    assert enabled["--no-sandbox"] is True
    disabled = _by_flag(from_cli(QUICKSTART, ["no_sandbox=false"]))
    assert "--no-sandbox" not in disabled


def test_null_run_id_drops_the_flag() -> None:
    """Test 3c: null means "no flag", not the string "None" as a run id."""
    argv = from_cli(QUICKSTART, ["run_id=null"])
    assert "--run-id" not in argv
    assert "None" not in argv


# ------------------------------------------------------------ environment ---

def test_env_var_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 4a: `$VAR` is read from the environment at argv-build time."""
    monkeypatch.setenv("HG_TEST_EXPERIMENT_RUN", "env-run-id")
    argv = from_cli(QUICKSTART, ["run_id=$HG_TEST_EXPERIMENT_RUN"])
    assert _by_flag(argv)["--run-id"] == "env-run-id"
    monkeypatch.setenv("HG_TEST_EXPERIMENT_DATASET", "demo")
    braced = _by_flag(to_argv(merge(load(QUICKSTART),
                                    overrides(["dataset=${HG_TEST_EXPERIMENT_DATASET}"]))))
    assert braced["--dataset"] == "demo"


def test_missing_env_var_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 4b: a missing variable stops the run and says which one, never blanks it."""
    monkeypatch.delenv("HG_TEST_EXPERIMENT_MISSING", raising=False)
    with pytest.raises(ExperimentError) as caught:
        to_argv({"run_id": "$HG_TEST_EXPERIMENT_MISSING"})
    assert "HG_TEST_EXPERIMENT_MISSING" in str(caught.value)


# ------------------------------------------------------------- eval run ---

def test_eval_from_round_targets_a_train_run() -> None:
    """Test 5: the eval example measures a named round of a named train run."""
    flags = _by_flag(from_cli(EVAL_FROM_ROUND, []))
    assert flags["--side"] == "eval"
    assert flags["--from-run"] == "quickstart"
    assert flags["--from-round"] == "2"
    assert "--method-entrypoint" not in flags, "eval measures a run, it does not run a method"


# ------------------------------------------------------- file and methods ---

def test_method_name_absolute_path_and_command_are_three_spellings() -> None:
    # A wrapper needs its own `--base`, which no experiment field reaches; that is exactly
    # why it is spelled as a bare name rather than used as the checked-in example.
    wrapper = ROOT / "methods/lightrsi/run.py"
    assert _by_flag(to_argv({"method": "lightrsi"}))["--method-entrypoint"] == shlex.join(
        [sys.executable, str(wrapper)])
    assert wrapper.is_file()
    absolute = str(ROOT / "methods/noop/run.py")
    assert to_argv({"method": absolute}) == ["--method-entrypoint", absolute]
    command = "python3 methods/noop/run.py"
    assert to_argv({"method": command}) == ["--method-entrypoint", command]
    with pytest.raises(ExperimentError, match="relative path"):
        to_argv({"method": "methods/noop/run.py"})


def test_load_refuses_a_document_that_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(ExperimentError, match="JSON object"):
        load(path)


def test_yaml_needs_pyyaml_and_fails_without_an_import_error(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Optional YAML is a file-format convenience, never an added dependency."""
    path = tmp_path / "experiment.yaml"
    path.write_text("dataset: demo\nrounds: 1\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # makes `import yaml` raise ImportError
    with pytest.raises(ExperimentError, match="PyYAML"):
        load(path)


def test_importing_the_module_does_not_import_yaml() -> None:
    source = (ROOT / "harnessgrad/experiments.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="harnessgrad/experiments.py")
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(alias.name != "yaml" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "yaml"
