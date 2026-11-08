"""The `harnessgrad` command: a console script, and a shim onto `driver.py`.

`driver.py` is and stays the entrypoint. INTERFACE.md documents it by that path,
`tools/serve_ui.py` launches it by that path, and every recorded run's argv names it. An
installable project must not fork that program into a second implementation, so
`harnessgrad run ...` locates the same file relative to the installed package and runs it
with the remaining arguments unchanged. `python3 driver.py --dataset demo` and
`harnessgrad run --dataset demo` are one program launched two ways, and a run receipt
cannot tell which spelling started it -- which is the point.

Nothing here imports third-party code and nothing happens at import time: `--version`
reads installed metadata (falling back to the package's own constant when the checkout is
not installed), and the driver file is only located and executed when `run` is invoked.
The driver is executed, not imported at module scope, because importing it here would
pull the whole platform into the CLI's process for a `--version` that does not need it.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
from importlib import resources
import re
import runpy
import sys
from pathlib import Path

from harnessgrad import FRAMEWORK_VERSION

#: The name a user types, and the distribution name in `pyproject.toml`. Keeping them the
#: same is what lets `--version` ask installed metadata instead of keeping a second version
#: constant that can drift from the one on every point.
PROGRAM = "harnessgrad"
DISTRIBUTION = "harnessgrad"

_HELP = """\
{program}: measure agent-harness self-improvement, round by round.

usage:
  {program} run [driver arguments ...]   run driver.py with those arguments
  {program} run --preset quickstart ...  use a bundled experiment recipe
  {program} preset path NAME             print a bundled recipe path
  {program} split create|check ...      create or validate an experiment partition
  {program} version                     print the installed version
  {program} --version                   the same, as a flag

`{program} run --help` prints driver.py's own help: the driver owns its command line and
this command forwards it unchanged rather than keeping a second copy of the flags.
Bundled presets may require separately provisioned datasets, split files and runtimes.

options:
  -h, --help     show this help and exit
  -V, --version  print the installed version and exit
""".format(program=PROGRAM)


def version() -> str:
    """The installed distribution's version, or the package constant when not installed.

    `pip install .` and `pip install -e .` both record the version in distribution
    metadata. A plain `git clone` has no metadata at all, and `--version` still has to
    answer there, so the fallback is `harnessgrad.FRAMEWORK_VERSION` -- the same number
    recorded on every curve point. The CLI and the records therefore cannot disagree
    about which rules produced a number.
    """
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return FRAMEWORK_VERSION


def driver_path() -> Path:
    """Locate the `driver.py` that belongs to this installed package.

    Ranked, because the two installation shapes differ and neither may win by accident:

    * beside the package (`<package's parent>/driver.py`) -- true for an editable install,
      where the package is in the repository root, and for a wheel, where `driver.py` is
      installed as a top-level module in the same site-packages directory;
    * the `driver` module the import system would resolve -- the fallback for a layout
      that puts the package somewhere other than beside its module.

    The file is deliberately *not* treated as importable-under-any-circumstance: a
    `driver` module resolved from an unrelated place on `sys.path` is not this platform.
    """
    beside_package = Path(__file__).resolve().parents[1] / "driver.py"
    if beside_package.is_file():
        return beside_package
    try:
        spec = importlib.util.find_spec("driver")
    except (ImportError, ValueError):        # a broken/absent parent package
        spec = None
    if spec is not None and spec.origin and Path(spec.origin).is_file():
        return Path(spec.origin)
    raise RuntimeError(
        "cannot find driver.py: expected it beside the harnessgrad package "
        f"({beside_package}) or installed as a top-level `driver` module")


def _exit_code(code: object) -> int:
    """Turn a `SystemExit.code` into the process exit status it means."""
    if code is None:
        return 0
    if isinstance(code, bool):
        return int(code)
    if isinstance(code, int):
        return code
    # `sys.exit("message")` prints the message and exits 1; so does this.
    print(code, file=sys.stderr)
    return 1


def _driver_argv(argv: list[str]) -> list[str]:
    """Drop `--` separators, which belong to this command line, not to the driver.

    `harnessgrad run -- --help` is how a user says "the next token is the driver's"; the
    driver's own argparse would instead consume the `--` and treat the rest as positional
    arguments. A clone typing `python3 driver.py --help` and one typing
    `harnessgrad run -- --help` must get the same help text, so the separator stops here.
    """
    return [argument for argument in argv if argument != "--"]


def _run(argv: list[str]) -> int:
    """Execute `driver.py` with `argv` and return its exit status.

    The driver runs as `__main__`, exactly as `python3 driver.py` runs it, so its own
    `if __name__ == "__main__"` block (which maps a tampered run to exit 3) is part of the
    delegation rather than something reimplemented here. `sys.argv[0]` is the driver's
    path, so argparse reports the program a user actually launched, and the driver's
    directory is first on `sys.path` while it runs -- script execution does that, and a
    clone's sibling packages (`scenarios/`) are only importable because of it.
    """
    try:
        path = driver_path()
    except RuntimeError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location("driver", path)
    if spec is None or spec.loader is None or spec.origin is None:
        print(f"{PROGRAM}: cannot load {path}", file=sys.stderr)
        return 1
    saved_argv = sys.argv
    saved_path = sys.path[:]
    sys.argv = [str(path), *argv]
    sys.path.insert(0, str(path.parent))
    try:
        runpy.run_path(spec.origin, run_name="__main__")
    except SystemExit as exit_signal:
        return _exit_code(exit_signal.code)
    finally:
        sys.argv = saved_argv
        sys.path[:] = saved_path
    return 0


def _preset_path(name: str) -> Path:
    """Resolve a packaged, explicitly named recipe without consulting the cwd."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("preset name must contain only letters, digits, underscores or hyphens")
    resource = resources.files("experiments").joinpath(name + ".json")
    if not resource.is_file():
        raise ValueError(f"unknown bundled preset: {name}")
    # Wheels are installed unpacked; driver receives a real path, preserving --config behavior.
    return Path(str(resource))


def _expand_preset(argv: list[str]) -> list[str]:
    result = []
    preset = None
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--preset" or value.startswith("--preset="):
            if preset is not None:
                raise ValueError("--preset may be supplied only once")
            if value == "--preset":
                index += 1
                if index >= len(argv):
                    raise ValueError("--preset requires a name")
                preset = argv[index]
            else:
                preset = value.split("=", 1)[1]
            result.extend(["--config", str(_preset_path(preset))])
        else:
            result.append(value)
        index += 1
    if preset is not None and sum(x == "--config" or x.startswith("--config=") for x in result) != 1:
        raise ValueError("--preset cannot be combined with --config")
    return result


def _print_help(stream=None) -> None:
    print(_HELP, end="", file=stream or sys.stdout)


def main(argv: list[str] | None = None) -> int:
    """Dispatch one `harnessgrad` command line; return the process exit status.

    Parsing is deliberate rather than argparse-based: every token after `run` belongs to
    `driver.py`, and an argparse subparser would claim the driver's `--help` and its
    `--flag value` pairs before they could be forwarded.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _print_help()
        return 0
    command, rest = arguments[0], arguments[1:]
    if command in ("-h", "--help"):
        _print_help()
        return 0
    if command in ("-V", "--version", "version"):
        if command == "version" and rest:
            print(f"{PROGRAM}: 'version' takes no arguments", file=sys.stderr)
            return 2
        print(f"{PROGRAM} {version()}")
        return 0
    if command == "run":
        try:
            resolved = _expand_preset(_driver_argv(rest))
        except ValueError as exc:
            print(f"{PROGRAM}: {exc}", file=sys.stderr)
            return 2
        return _run(resolved)
    if command == "preset":
        if len(rest) != 2 or rest[0] != "path":
            print(f"{PROGRAM}: usage: {PROGRAM} preset path NAME", file=sys.stderr)
            return 2
        try:
            print(_preset_path(rest[1]))
        except ValueError as exc:
            print(f"{PROGRAM}: {exc}", file=sys.stderr)
            return 2
        return 0
    if command == 'split':
        from harnessgrad.splits import main as split_main
        return split_main(rest)
    print(f"{PROGRAM}: unknown command {command!r}", file=sys.stderr)
    print(f"Try '{PROGRAM} --help' for the commands: run, split, version.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
