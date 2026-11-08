"""Experiment-file expansion and declared configuration help for run entrypoints."""
from __future__ import annotations

from pathlib import Path

from harnessgrad import config as platform_config
from harnessgrad import experiments


def expand_experiment_argv(argv: list[str]) -> list[str] | None:
    """`--config PATH` / `--set a.b=c` as the flags they name.

    **The file is expanded before parsing, and the flags it produces go first.** A later
    explicit flag then wins exactly as it does on a hand-written command line, which is the
    property that makes a checked-in experiment usable: `--config experiments/quickstart.json
    --dataset alfworld` is "the same experiment, on another dataset", not two overlapping
    declarations. `sys.argv` is left alone so the record still names the file that was
    read; the expansion is only what the parser sees (`run_meta.json:argv` keeps
    `--config experiments/quickstart.json`, and the file is in the repository).

    Returns `None` when neither flag is present, so the ordinary path does no work.
    """
    if not any(a == "--config" or a.startswith("--config=") or a == "--set"
               or a.startswith("--set=") for a in argv):
        return None
    config: Path | None = None
    sets: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("--config", "--set"):
            if index + 1 >= len(argv):
                raise SystemExit(f"driver.py: error: {item} needs a value")
            value = argv[index + 1]
            index += 2
        elif item.startswith("--config=") or item.startswith("--set="):
            item, value = item.split("=", 1)
            index += 1
        else:
            rest.append(item)
            index += 1
            continue
        if item == "--config":
            if config is not None:
                # One file, one experiment: two `--config` flags would make the merge order
                # the reader's guess.
                raise SystemExit("driver.py: error: --config may be given once")
            config = Path(value)
        else:
            sets.append(value)
    if config is None:
        # `--set` alone: the same dotted overrides applied to an empty experiment.
        return [*experiments.to_argv(experiments.overrides(sets)), *rest] if sets else None
    return [*experiments.from_cli(config, sets), *rest]


def config_help() -> str:
    """The configuration surface, rendered for `--help`.

    Derived from `harnessgrad.config.describe()`, so the help text and the declared
    defaults are one thing: a second hand-written table is how `--help` starts lying.
    """
    lines = ["configuration (every value is an environment variable; the effective set "
             "of a run is recorded in run_meta.json under `config`):"]
    for row in platform_config.describe():
        default = row["default"]
        shown = "" if default in (None, "<redacted>") else f"={default}"
        lines.append(f"  {row['variable'] + shown:<36} {row['what']}")
    return "\n".join(lines)

