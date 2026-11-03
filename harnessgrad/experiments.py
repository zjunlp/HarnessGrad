"""Checked-in experiment files: one reviewable name instead of a copied flag list.

`driver.py`'s real invocation is long -- dataset, harness, side, sampling, rounds,
jobs, trials, run id, roots, the method entrypoint. A command copied from an older
run is a *stale description of an experiment*: it still starts, it just measures
something other than what its reader thinks (a different sampling policy, a method
directory that has been renamed). So the experiment is a small file under
`experiments/`, reviewed and diffed like code, and a one-off change is a dotted
override (`rounds=3`) instead of a second hand-edited copy of the command.

Two rules this module keeps:

* **Nothing about this machine is baked into the file.** A file names a method
  (`"lightrsi"`); `to_argv` resolves that name against *this* checkout and prepends
  the interpreter. The driver runs a method with the harness worktree as its working
  directory and refuses a relative `*.py` argument, so emitting the relative
  `methods/<name>/run.py` would produce a command that is rejected at the door.
  An absolute path or a complete command string is passed through as written.
* **Credentials never enter the file.** A string value may spell `$VAR`; expansion
  happens in `to_argv` against `os.environ`, and a missing variable is an
  `ExperimentError` naming it, so a run never starts on an empty key. Nothing here
  ever writes a value back to disk.

JSON is always readable. YAML is read only when PyYAML happens to be importable:
the platform is standard-library-only, so a `.yaml` experiment fails with an
`ExperimentError` (not an `ImportError`) where PyYAML is absent, and `yaml` is
imported inside `load` so importing this module never needs it.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

#: The repository root. `methods/` hangs off it; an experiment names a method
#: relative to this checkout, never relative to the caller's working directory.
PLATFORM_ROOT = Path(__file__).resolve().parents[1]


class ExperimentError(ValueError):
    """An unreadable experiment file, a bad override, or a key the driver has no flag for."""


#: The whole supported surface, in the order a human writes the command. The value is
#: the exact flag `driver.py:main()` declares; `tests/quality/test_experiments.py`
#: re-reads `driver.py` and fails if one of these spellings drifts. A key that is not
#: here has no flag and is refused by name rather than silently dropped.
_FLAGS: dict[str, str] = {
    "dataset": "--dataset",
    "harness": "--harness",
    "side": "--side",
    "sampling": "--sampling",
    "pool": "--pool",
    "split": "--split",
    "rounds": "--rounds",
    "jobs": "--jobs",
    "trials": "--trials",
    "seed": "--seed",
    "run_id": "--run-id",
    "runs_root": "--runs-root",
    "work_root": "--work-root",
    "method": "--method-entrypoint",
    "from_run": "--from-run",
    "from_round": "--from-round",
    "no_sandbox": "--no-sandbox",
    "fail_on_incomplete": "--fail-on-incomplete",
}

#: The keys, for the error message and for a caller that wants to document them.
KNOWN_KEYS: tuple[str, ...] = tuple(_FLAGS)

#: Flags whose value argparse coerces with `type=int`; they are emitted as digits.
_INT_KEYS = frozenset({"rounds", "jobs", "trials", "from_round"})

_INT = re.compile(r"^[+-]?\d+$")
#: Coercion is stricter than validation: a zero-padded token is an identifier, not a
#: number, so a run id typed `007` stays `"007"` instead of silently becoming 7.
_INT_COERCE = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_FLOAT = re.compile(
    r"^(?:"
    r"[+-]?(?:(?:0|[1-9]\d*)\.\d*|\.\d+)(?:[eE][+-]?\d+)?"
    r"|"
    r"[+-]?(?:0|[1-9]\d*)[eE][+-]?\d+"
    r")$")
_ENV_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_METHOD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


# ----------------------------------------------------------------- reading ---

def load(path: Path) -> dict:
    """Read one experiment file.

    JSON always; YAML only when the suffix asks for it and PyYAML is importable.
    The result is returned unvalidated -- `to_argv` is what refuses keys with no
    flag, so `overrides` can be applied first.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExperimentError(f"cannot read experiment file {path}: {exc}") from exc
    if path.suffix.lower() in {".yaml", ".yml"}:
        config = _load_yaml(path, text)
    else:
        try:
            config = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ExperimentError(
            f"{path}: an experiment file is a JSON object of driver flags, "
            f"got {type(config).__name__}")
    return config


def _load_yaml(path: Path, text: str):
    """Parse YAML *if* PyYAML is installed; a missing dependency is not an ImportError."""
    try:
        import yaml  # noqa: PLC0415 -- optional by design, never a module-level import
    except ImportError:
        raise ExperimentError(
            f"{path} is YAML but PyYAML is not installed; use a .json experiment "
            "file (the platform adds no third-party dependency for this)") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ExperimentError(f"{path} is not valid YAML: {exc}") from exc


# --------------------------------------------------------------- overrides ---

def overrides(pairs: list[str]) -> dict:
    """Turn `["a.b=3", "x=true", "s=null"]` into a nested patch.

    Dotted paths address nested keys, which is what makes a checked-in experiment
    adjustable without editing it. Values are typed: booleans, `null`, integers and
    floats are recognized; anything else stays a string (a run id such as `007`
    must not become the number 7). Strings are expanded later, in `to_argv`, so the
    patch (and any log of it) keeps the `$VAR` spelling rather than a secret.
    """
    patch: dict[str, Any] = {}
    for pair in pairs or ():
        if not isinstance(pair, str) or "=" not in pair:
            raise ExperimentError(f"override {pair!r} is not KEY=VALUE")
        key, _, raw = pair.partition("=")
        key = key.strip()
        if not key:
            raise ExperimentError(f"override {pair!r} has an empty key")
        parts = key.split(".")
        if any(not part for part in parts):
            raise ExperimentError(f"override {pair!r} has an empty path segment")
        node = patch
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ExperimentError(
                    f"override {pair!r} descends into {part!r}, which is already a "
                    "scalar; use one spelling of the key")
            node = child
        node[parts[-1]] = _coerce(raw.strip())
    return patch


def _coerce(text: str) -> Any:
    """Typed value for one override; unquoted and unrecognized text stays a string."""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if _INT_COERCE.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


# ------------------------------------------------------------------ merge ---

def merge(config: dict, patch: dict) -> dict:
    """Deep-merge `patch` over `config`; the patch wins at every leaf.

    Neither argument is mutated: `load` may be cached by a caller, and an override
    that quietly rewrote the loaded file in memory would make two launches of the
    same file disagree. Lists and scalars are replaced whole; nested objects merge.
    """
    if not isinstance(config, dict) or not isinstance(patch, dict):
        raise ExperimentError(
            "an experiment and its override patch must both be objects; got "
            f"{type(config).__name__} and {type(patch).__name__}")
    merged = copy.deepcopy(config)
    _merge_into(merged, patch)
    return merged


def _merge_into(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _merge_into(current, value)
        else:
            target[key] = copy.deepcopy(value)


# ------------------------------------------------------------------- argv ---

def to_argv(config: dict) -> list[str]:
    """Render a validated config as flags `driver.py` accepts.

    A key whose value is `null` emits no flag: dropping a default is a real edit, and
    `--run-id None` would be a different run id rather than an absent one. A switch
    (`no_sandbox`) is either the flag or nothing, never `--no-sandbox true`.
    """
    if not isinstance(config, dict):
        raise ExperimentError(
            f"an experiment must be an object of driver flags, got {type(config).__name__}")
    unknown = [key for key in config if key not in _FLAGS]
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        raise ExperimentError(
            f"unknown experiment key{'s' if len(unknown) > 1 else ''} {names}; "
            f"known keys: {', '.join(KNOWN_KEYS)}")
    argv: list[str] = []
    for key, flag in _FLAGS.items():
        if key not in config:
            continue
        value = config[key]
        if value is None:
            continue
        if key in ("no_sandbox", "fail_on_incomplete"):
            if not isinstance(value, bool):
                raise ExperimentError(
                    f"{key} must be true or false, got {value!r}")
            if value:
                argv.append(flag)
            continue
        if key == "method":
            argv.extend((flag, _method(value, key)))
            continue
        if key in _INT_KEYS:
            argv.extend((flag, str(_as_int(value, key))))
            continue
        if key == "side":
            if value not in ("train", "eval"):
                raise ExperimentError(
                    f"side must be 'train' or 'eval', got {value!r}")
            argv.extend((flag, value))
            continue
        argv.extend((flag, _as_string(value, key)))
    return argv


def _as_string(value: Any, key: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ExperimentError(
            f"{key} must be a string, got {type(value).__name__}")
    return _expand(str(value), key)


def _as_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ExperimentError(f"{key} must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT.match(value.strip()):
        return int(value)
    raise ExperimentError(f"{key} must be an integer, got {value!r}")


def _method(value: Any, key: str) -> str:
    """What `--method-entrypoint` receives: a name, an absolute path, or a command.

    A name (no separator) becomes `<python> <checkout>/methods/<name>/run.py`. The
    driver resolves neither an interpreter nor a relative script for the method --
    `harnessgrad.methods._check_method_entrypoint` refuses the relative form
    outright -- so the name is completed here, against this checkout, and the
    argument is one command string the driver can `shlex.split`.
    """
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{key} must be a non-empty string, got {value!r}")
    text = _expand(value.strip(), key)
    if Path(text).is_absolute():
        return text
    if " " in text or "\t" in text:
        return text  # a complete command line; the driver shlex-splits it
    if "/" in text or "\\" in text or text.endswith(".py"):
        raise ExperimentError(
            f"{key}: {text!r} is a relative path; name the method instead "
            f"(e.g. 'lightrsi'), give an absolute path, or give a full command "
            "line. A method runs with the harness worktree as its working "
            "directory, so a relative script path cannot resolve there")
    if not _METHOD_NAME.match(text):
        raise ExperimentError(
            f"{key}: {text!r} is neither a method name, an absolute path, nor a "
            "command line")
    return shlex.join([sys.executable,
                       str(PLATFORM_ROOT / "methods" / text / "run.py")])


def _expand(text: str, key: str) -> str:
    """Substitute `$VAR`/`${VAR}` from `os.environ`; a missing variable is an error."""
    def replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        try:
            return os.environ[name]
        except KeyError:
            raise ExperimentError(
                f"{key}: environment variable {name} is not set "
                f"(from {match.group(0)})") from None
    return _ENV_VAR.sub(replace, text)


# ------------------------------------------------------------- composition ---

def from_cli(path: Path, sets: list[str]) -> list[str]:
    """The argv for one experiment file with command-line overrides applied."""
    return to_argv(merge(load(path), overrides(sets)))
