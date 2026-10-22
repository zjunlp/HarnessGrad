#!/usr/bin/env python3
"""Check that a directory is a harness the platform can import and measure.

The point of a validator rather than a paragraph: "does my harness conform?" is a
question a contributor should be able to answer by running something, and the
platform should be able to refuse a non-conforming harness before spending money
on it.

Usage:
    python tools/validate_harness.py <harness-dir> [--smoke]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED = ("name", "version", "entrypoint")
#: Keys a harness author may use. This list is what the *platform reads*, and it has to
#: be kept in step with it: it was stale the moment `env_kinds` and `runtime_paths`
#: landed, and it then reported them as "ignored by the platform" while the platform
#: was refusing runs on the strength of them. A validator that misdescribes the
#: contract is worse than no validator, because it is believed.
KNOWN = ("name", "version", "path", "entrypoint", "backend", "install",
         "env_kinds", "runtime_paths", "runtime", "role", "notes")

#: What a harness *is*. `control` is for something the platform ships to check its own
#: instruments rather than to be measured -- `base_harness/oracle` runs the dataset's own
#: reference solution. It is a real distinction and it has to be declared rather than
#: guessed from a name or a docstring: the UI lists harnesses, and a control that appears
#: there is a control whose curve somebody will eventually quote.
ROLES = ("harness", "control")

#: Keys that are allowed and read by nobody -- documentation for the next reader.
DOCUMENTATION_ONLY = ("notes",)

#: Directories inside a harness that are not the harness. `.git` is the checkpoint
#: machinery, `__pycache__` is output, and `_harnessgrad` is the platform<->method channel
#: (`methods/apply.py:CHANNEL`) -- a harness that shipped a copy of it would be carrying
#: the platform's own files into a measured tree.
_NOT_SOURCE = (".git", "__pycache__", "_harnessgrad", "node_modules", ".state")


def _compile_problems(base: Path) -> list[str]:
    """Python in this harness that does not compile.

    **Not part of `smoke`.** `smoke` runs the harness, and the candidate path turns it
    off -- `eval/run/candidate.py` calls `validate(..., smoke=False)`, because running a
    candidate to decide whether it is a candidate is circular and expensive. So the syntax
    of a candidate tree was never checked on the one path where a tree is most likely to
    be broken: a method wrote it.

    Measured (2026-10-04, `B2-llm`): the method's edit replaced `agent.py` with two lines
    and a `SyntaxError`; the candidate validated (re-verified by running this validator on
    the saved state: "NO PROBLEMS"), and the run recorded **five `harness_failed` tasks
    and a 0.000 curve point** where one named problem belonged. `eval/run/candidate.py`'s own
    docstring already listed "Python that does not compile" as one of the three outcomes
    its check exists to separate.

    Every `.py` file, not only the entrypoint's import closure: the tree *is* the artifact,
    a file that does not compile cannot be imported by whatever the method adds next, and
    the message names the file, so the fix is obvious. It is a `compile()` call, so it
    costs no subprocess and cannot execute anything.
    """
    problems: list[str] = []
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base)
        if any(part in _NOT_SOURCE for part in rel.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{rel} could not be read as text: {exc}")
            continue
        try:
            compile(source, str(rel), "exec")
        except SyntaxError as exc:
            problems.append(f"{rel} does not compile: {exc.msg} (line {exc.lineno})")
        except ValueError as exc:
            # A null byte in the source raises ValueError, not SyntaxError.
            problems.append(f"{rel} cannot be compiled: {exc}")
    return problems


def validate(root: Path, smoke: bool) -> list[str]:
    errs: list[str] = []

    man_path = root / "harness.json"
    if not man_path.exists():
        return [f"no harness.json at {root} (required: F1 in "
                "docs/writing_a_harness.md)"]
    try:
        man = json.loads(man_path.read_text())
    except json.JSONDecodeError as exc:
        return [f"harness.json is not valid JSON: {exc}"]

    for field in REQUIRED:
        if not man.get(field):
            errs.append(f"harness.json is missing required field {field!r}")

    unknown = [k for k in man if k not in KNOWN and not k.startswith("_")]
    if unknown:
        # A warning rather than an error: extra keys are how a harness carries
        # its own configuration, and the platform ignores them.
        print(f"  note: extra manifest keys ignored by the platform: {unknown}")

    backend = man.get("backend", "cli")
    if backend != "cli":
        errs.append(f"backend {backend!r} is not implemented; only 'cli' is")

    # The harness may be a subdirectory of a larger repo.
    #
    # Both of these are type-checked rather than trusted: a validator that raises on the
    # input it exists to reject is worse than no validator, because the caller gets a
    # traceback instead of a reason. Measured: `"entrypoint": ["python", "agent.py"]` --
    # the argv shape the *old* method design used -- reached `Path / list` and came out as
    # `TypeError: unsupported operand type(s) for /`, thirteen call sites deep.
    sub = man.get("path", ".")
    if not isinstance(sub, str):
        errs.append(f"`path` must be a string, not {type(sub).__name__}")
        sub = "."
    base = root / sub
    declared = man.get("entrypoint")
    entry = None
    if declared is not None:
        if not isinstance(declared, str):
            errs.append(
                "`entrypoint` must be a string naming one file (INTERFACE.md §1.1), "
                f"not {type(declared).__name__}: {declared!r}")
        else:
            entry = base / declared
            if not entry.is_file():
                errs.append(f"entrypoint {declared!r} not found under {sub!r}")

    role = man.get("role", "harness")
    if role not in ROLES:
        errs.append(f"role {role!r} is not one of {list(ROLES)}")

    from ckpt.source_tree import runtime_spec
    try:
        runtime_spec(root)
    except ValueError as exc:
        errs.append(str(exc))

    # Independent of `smoke`, and that is the whole point: see `_compile_problems`.
    errs.extend(_compile_problems(base))

    install = man.get("install")
    if install and not (base / install).is_file():
        errs.append(f"install file {install!r} not found under "
                    f"{man.get('path', '.')}")

    if smoke and entry and entry.is_file() and not errs:
        errs.extend(_smoke(base, entry))

    return errs


def _smoke(base: Path, entry: Path) -> list[str]:
    """Run the harness on a trivial task and check it writes an answer.

    Deliberately trivial: this checks the *contract*, not the harness's quality.
    """
    errs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hg-validate-") as tmp:
        tmpd = Path(tmp)
        task = tmpd / "task.json"
        task.write_text(json.dumps({
            "task_id": "smoke",
            "goal": "Write the word ok into answer.txt.",
        }))
        try:
            proc = subprocess.run(
                [sys.executable, str(entry.resolve()), "--task", str(task),
                 "--workdir", str(tmpd)],
                capture_output=True, text=True, errors="replace", timeout=120, cwd=tmpd,
            )
        except subprocess.TimeoutExpired:
            return ["smoke run timed out after 120s"]
        except OSError as exc:
            return [f"smoke run could not start: {exc}"]

        if proc.returncode != 0:
            errs.append(f"smoke run exited {proc.returncode}; stderr tail: "
                        f"{proc.stderr[-300:]}")
        if not (tmpd / "answer.txt").exists():
            errs.append("smoke run did not write answer.txt (required: F3)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harness")
    ap.add_argument("--smoke", action="store_true",
                    help="also run the harness on a trivial task")
    args = ap.parse_args()

    root = Path(args.harness)
    print(f"validating {root}")
    errs = validate(root, args.smoke)
    if errs:
        for e in errs:
            print(f"  FAIL  {e}")
        return 1
    print(f"  OK    harness.json valid"
          + (", smoke run produced an answer" if args.smoke else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
