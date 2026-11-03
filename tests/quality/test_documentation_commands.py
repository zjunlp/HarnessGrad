"""Release documentation is prose that has to parse, and nothing else checks that.

`README.md`, `DOWNLOADS.md`, `DATASETS.md` and every `docs/*.md` tell a reader what to type.
Two days of merges can rename a flag, move a script or delete a test file faster than that
prose is edited, and the failure is invisible here: the suite tests code, not documents, so
the first evidence is a reader on release day whose copied command does not parse.

This test reads the documents as data and asserts four facts about them. Nothing is
executed -- a documented run needs a machine, a provisioned dataset, an endpoint and a
wallet, while parsing it needs none -- so this stays a sub-second static check:

a. every `python3 <path>` in a fenced command names a file that exists in this checkout;
b. every `--flag` in a documented `harnessgrad run --config ...` /
   `python3 driver.py --config ...` invocation is one `driver.py`'s argparse accepts;
c. every `python3 tools/<name>.py` command names a tool that exists;
d. every `harnessgrad split ...` command names a flag `harnessgrad/splits.py` accepts.

The parser here is the same rule the release audit used: a fenced line that starts with one
of `python3`, `python`, `harnessgrad`, `./install.sh`, `git`, `pip` or `docker`, plus the
lines that continue it (`--flag`, `|`, `&&`, a trailing `\\`). A `$` prompt and a leading
`VAR=value` are part of the notation, not of the command, so both are stripped.

**What it deliberately tolerates.** A path whose first segment is not a top-level entry of
this repository (`qa/bin/qa`, whose root is a provisioned dataset rather than the checkout)
is a payload-relative path and is skipped; a token carrying `<placeholders>`, `…`, `$VAR` or
a `|` alternation is a template and is skipped. `--config` and `--set` are the two flags
`driver.py` expands into other flags *before* argparse sees them
(`_expand_experiment_argv`), so they are legal without an `add_argument` line -- and the
exemption is pinned to the function that justifies it, because an exemption outliving its
reason is how the next `--config` starts to look legal everywhere.

**Division of labour.** Its sibling `tests/quality/test_documentation_links.py` owns the graph a
reader *clicks*: relative links, `#anchors` and inline `` `path` `` spans. This file owns what a
reader *types*: the command inside a fence, where the file name and the flags are the contract
and nothing is clickable.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "DOWNLOADS.md",
    ROOT / "DATASETS.md",
    *sorted((ROOT / "docs").glob("*.md")),
]

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_PROMPT = re.compile(r"^\$\s+")
_ENV_ASSIGNMENT = re.compile(r"^(?:[A-Z][A-Z0-9_]*=\S*\s+)+")
_COMMAND = re.compile(r"^(?:python3?|harnessgrad|\./install\.sh|git|pip|docker)\b")
_CONTINUES = ("--", "|", "&&", "\\", "2>", "1>")
_PYTHON_SCRIPT = re.compile(r"\bpython3?\s+(\$?PWD/)?([A-Za-z0-9_./-]+\.(?:py|sh))\b")
_TOOL = re.compile(r"^python3?\s+tools/([A-Za-z0-9_]+\.py)\b")
_DRIVER_INVOCATION = re.compile(r"\b(?:harnessgrad\s+run|python3?\s+driver\.py)\b")
_SPLIT_INVOCATION = re.compile(
    r"^(?:harnessgrad|python3?\s+-m\s+harnessgrad\.cli)\s+split\s+(?:create|check)\b")
_FLAG = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)")
_TEMPLATE = re.compile(r"[<>…{}]|\$[A-Za-z_]|\|")

#: Expanded into driver flags before argparse parses the line (`_expand_experiment_argv`).
PRE_PARSER_FLAGS = frozenset({"--config", "--set"})


def _fenced_commands(path: Path) -> list[tuple[int, str]]:
    """Every command in `path`'s fenced blocks, as (line number, one-line command).

    A block of output is not a block of commands, but the two are not distinguishable by
    syntax -- `git       yes  ok` is a table row and `git status` is a command. The caller
    only ever asks whether a *path*, a *flag* or a *tool* named by the line exists, so a
    false positive here costs nothing and a missed fence would cost a release.
    """
    commands: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    pending: list[str] = []
    start = 0
    in_fence = False

    def flush() -> None:
        nonlocal pending, start
        if pending:
            commands.append((start, " ".join(pending)))
        pending = []

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        match = _FENCE.match(line)
        if match:
            flush()
            opener = match.group(1)[:3]
            if not in_fence:
                in_fence = True
            elif stripped.startswith(opener):
                in_fence = False
            continue
        if not in_fence:
            continue
        body = _PROMPT.sub("", stripped)
        if not body or body.startswith("#"):
            flush()
            continue
        assignment = _ENV_ASSIGNMENT.match(body)
        core = body[assignment.end():] if assignment else body
        if pending and (body.startswith(_CONTINUES) or pending[-1].endswith("\\")):
            pending.append(body)
            continue
        flush()
        if _COMMAND.match(core):
            pending, start = [body], number
    flush()
    return commands


def _documents() -> list[tuple[Path, list[tuple[int, str]]]]:
    parsed = []
    for path in DOCUMENTS:
        assert path.is_file(), f"documented file is missing from the checkout: {path}"
        parsed.append((path, _fenced_commands(path)))
    return parsed


def _argparse_flags(source: Path) -> set[str]:
    """The `--flags` a parser module declares, in either quote style.

    Both spellings exist: `driver.py` uses `"` and `harnessgrad/splits.py` uses `'`.
    """
    return set(re.findall(r"""add_argument\(\s*['"](--[A-Za-z0-9-]+)['"]""",
                          source.read_text(encoding="utf-8")))


def _where(path: Path, number: int) -> str:
    return f"{path.relative_to(ROOT)}:{number}"


def _missing(pairs: list[tuple[Path, int, str]]) -> list[str]:
    return [_where(path, number) + "  " + detail for path, number, detail in pairs]


def test_documented_python_scripts_exist():
    """(a) `python3 <path>` in a fence names a file the reader will actually find.

    A script that moved is the classic post-merge drift: the flag still parses, the file does
    not exist, and the reader gets `No such file or directory` instead of the step they
    followed. `$PWD/` is resolved because the documents use it for the same checkout.
    """
    problems: list[tuple[Path, int, str]] = []
    top_level = {entry.name for entry in ROOT.iterdir()}
    for path, commands in _documents():
        for number, command in commands:
            for prefix, target in _PYTHON_SCRIPT.findall(command):
                if _TEMPLATE.search(target):
                    continue
                head = target.split("/", 1)[0]
                if head not in top_level:
                    continue                      # payload-relative (a dataset's own tree)
                if not (ROOT / target).exists():
                    problems.append((path, number, target))
    assert not problems, "documented python3 commands name missing files:\n  " + \
        "\n  ".join(_missing(problems))


def test_documented_driver_flags_exist():
    """(b) A documented `--flag` is a flag `driver.py` accepts.

    Only `--config`-bearing invocations are checked, because that is the shape a reader
    copies from `README.md` and `docs/custom-dataset-splits.md`: the experiment file plus the
    overrides. `--config`/`--set` are exempt from the argparse rule *and* the function that
    makes them legal is pinned, so the exemption cannot quietly become a blanket one.
    """
    driver = ROOT / "driver.py"
    accepted = _argparse_flags(driver) | PRE_PARSER_FLAGS | {"--help"}
    assert "_expand_experiment_argv" in driver.read_text(encoding="utf-8"), (
        "driver.py no longer expands --config/--set before argparse; drop the exemption "
        "instead of letting it hide unknown flags")
    problems: list[tuple[Path, int, str]] = []
    for path, commands in _documents():
        for number, command in commands:
            if not _DRIVER_INVOCATION.search(command) or "--config" not in command:
                continue
            for flag in _FLAG.findall(command):
                if flag not in accepted:
                    problems.append((path, number, flag))
    assert not problems, "documented driver invocations use unknown flags:\n  " + \
        "\n  ".join(_missing(problems))


def test_documented_tools_exist():
    """(c) `python3 tools/<name>.py` names a tool in `tools/`.

    The tools directory is where a rename is cheapest to make and easiest to forget: nothing
    imports these programs, so a rename breaks no call site -- only the prose.
    """
    problems: list[tuple[Path, int, str]] = []
    for path, commands in _documents():
        for number, command in commands:
            match = _TOOL.match(command)
            if match and not (ROOT / "tools" / match.group(1)).is_file():
                problems.append((path, number, f"tools/{match.group(1)}"))
    assert not problems, "documented tools do not exist:\n  " + "\n  ".join(_missing(problems))


def test_documented_split_flags_exist():
    """(d) A documented `harnessgrad split ...` flag is one `harnessgrad/splits.py` accepts.

    The split subcommands have their own parser (`harnessgrad/splits.py`), not the driver's,
    so (b) cannot see them. They are the newest documented surface -- the one most likely to
    be re-spelled in a merge -- and `docs/custom-dataset-splits.md` is a page of commands.
    """
    accepted = _argparse_flags(ROOT / "harnessgrad" / "splits.py") | {"--help"}
    problems: list[tuple[Path, int, str]] = []
    for path, commands in _documents():
        for number, command in commands:
            if not _SPLIT_INVOCATION.match(command):
                continue
            for flag in _FLAG.findall(command):
                if flag not in accepted:
                    problems.append((path, number, flag))
    assert not problems, "documented split invocations use unknown flags:\n  " + \
        "\n  ".join(_missing(problems))


def test_the_parser_sees_the_commands_it_is_supposed_to():
    """A parser that silently returns nothing would pass every assertion above.

    Pinned by shape rather than by count: the documents must yield the two spellings that
    exist in the tree today, so a broken fence rule fails loudly instead of going quiet.
    """
    commands = {command.split()[0]
                for _, parsed in _documents() for _, command in parsed}
    for spelling in ("python3", "harnessgrad", "./install.sh", "git", "docker"):
        assert spelling in commands, f"fence parser found no {spelling!r} command"
