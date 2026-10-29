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
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

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


def _owned(root: Path, path: Path) -> Path:
    """Resolve before reading; a lexical child may point outside the artifact."""
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"unsafe path {path}: target is outside the harness")
    return resolved


def _tree_files(root: Path, ignored=(), *, boundary: Path | None = None) -> list[tuple[str, Path]]:
    """Logical relative names and owned files, including safe directory aliases.

    Track ancestors, not a global visited set: two aliases are two input paths,
    while a directory pointing to its ancestor is a cycle, not another subtree.
    """
    files: list[tuple[str, Path]] = []
    boundary = boundary if boundary is not None else root

    def walk(directory: Path, relative: Path, ancestors: frozenset):
        owned = _owned(boundary, directory)
        if owned in ancestors:
            raise ValueError(f"unsafe path {relative}: directory link cycle")
        for child in owned.iterdir():
            if child.name in ignored:
                continue
            name = relative / child.name
            target = _owned(boundary, child)
            if target.is_dir():
                walk(target, name, ancestors | {owned})
            elif target.is_file():
                files.append((name.as_posix(), target))
            else:
                raise ValueError(f"unsafe path {name}: not a regular file or directory")

    walk(root, Path(), frozenset())
    return sorted(files, key=lambda item: item[0].encode('utf-8'))


def source_identity(root: Path) -> dict:
    """SHA-256 of the input tree, before staging creates Git/runtime state.

    Sort regular-file paths by their relative POSIX UTF-8 bytes. For each path,
    update SHA-256 with path bytes, one NUL byte, then the original file bytes.
    `entries` counts these file paths; directories contribute no entry. Safe
    in-tree links use their logical path and target bytes; escaping, broken or
    cyclic links are refused. No input files are excluded (including Bun state).
    This is the submission contract's `path\\0content` hash, not a Git tree SHA.
    """
    root = Path(root).resolve(strict=True)
    files = _tree_files(root)
    digest = hashlib.sha256()
    for name, path in files:
        digest.update(name.encode('utf-8') + b'\0')
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return {'sha256': digest.hexdigest(), 'entries': len(files)}


def _compile_problems(base: Path, ignored=_NOT_SOURCE, *, boundary: Path | None = None) -> list[str]:
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
    for name, path in _tree_files(base, ignored, boundary=boundary):
        rel = Path(name)
        if rel.suffix != '.py':
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


def check(root: Path) -> list[dict]:
    """Static problems shared by the CLI, driver and submission gate.

    No code is imported/executed and nothing is printed. The legacy `validate`
    adapter below retains string diagnostics for existing candidate callers.
    """
    problems: list[dict] = []

    def problem(code, detail, field=None):
        item = {'code': code, 'detail': detail}
        if field is not None:
            item['field'] = field
        problems.append(item)

    try:
        root = Path(root).resolve(strict=True)
    except (OSError, ValueError) as exc:
        return [{'code': 'manifest-missing', 'detail': f'harness directory could not be read: {exc}'}]
    man_path = root / "harness.json"
    try:
        _owned(root, man_path)
    except ValueError as exc:
        return [{'code': 'unsafe-path', 'detail': str(exc), 'field': 'harness.json'}]
    except OSError as exc:
        return [{'code': 'manifest-missing', 'detail': f'no readable harness.json at {root}: {exc}'}]
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [{'code': 'manifest-invalid', 'detail': f'harness.json is not valid JSON: {exc}'}]
    except (OSError, UnicodeError) as exc:
        return [{'code': 'manifest-invalid', 'detail': f'harness.json could not be read as UTF-8 JSON: {exc}'}]
    if not isinstance(man, dict):
        return [{'code': 'manifest-invalid', 'detail': 'harness.json must contain a JSON object'}]

    for field in REQUIRED:
        if not isinstance(man.get(field), str) or not man[field].strip():
            problem('manifest-invalid', f"harness.json field {field!r} must be a non-empty string", field)

    backend = man.get("backend", "cli")
    if backend != "cli":
        problem('backend-unsupported', f"backend {backend!r} is not implemented; only 'cli' is", 'backend')

    def declared_path(base, value, field):
        if not isinstance(value, str) or not value:
            problem('manifest-invalid', f'`{field}` must be a non-empty string', field)
            return None
        if Path(value).is_absolute() or PureWindowsPath(value).drive or '..' in Path(value).parts:
            problem('unsafe-path', f'`{field}` must be a relative path inside the harness: {value!r}', field)
            return None
        try:
            return _owned(root, base / value)
        except ValueError as exc:
            problem('unsafe-path', str(exc), field)
        except OSError as exc:
            code = {'entrypoint': 'entrypoint-missing', 'install': 'install-missing'}.get(field, 'manifest-invalid')
            problem(code, f'{field} {value!r} could not be read: {exc}', field)
        return None

    sub = man.get("path", ".")
    base = declared_path(root, sub, 'path')
    if base is not None and not base.is_dir():
        problem('manifest-invalid', '`path` must name a directory', 'path')
        base = None
    declared = man.get("entrypoint")
    if base is not None and isinstance(declared, str) and declared:
        entry = declared_path(base, declared, 'entrypoint')
        if entry is not None and not entry.is_file():
            problem('entrypoint-missing', f'entrypoint {declared!r} is not a regular file', 'entrypoint')

    install = man.get('install')
    if base is not None and install is not None:
        entry = declared_path(base, install, 'install')
        if entry is not None and not entry.is_file():
            problem('install-missing', f'install file {install!r} is not a regular file', 'install')

    kinds = man.get('env_kinds')
    if kinds is not None and (not isinstance(kinds, list) or any(not isinstance(k, str) or not k for k in kinds)):
        problem('manifest-invalid', '`env_kinds` must be a list of non-empty strings', 'env_kinds')

    role = man.get("role", "harness")
    if role not in ROLES:
        problem('manifest-invalid', f"role {role!r} is not one of {list(ROLES)}", 'role')

    from eval.ckpt.source_tree import BUN_GENERATED, runtime_spec
    try:
        runtime = runtime_spec(root)
    except (OSError, ValueError) as exc:
        problem('manifest-invalid', str(exc), 'runtime')
        runtime = None
    ignored = BUN_GENERATED if runtime else _NOT_SOURCE
    try:
        # Check links throughout the source, not only the declared entrypoint.
        _tree_files(root, ignored)
        if base is not None:
            for detail in _compile_problems(base, ignored, boundary=root):
                problem('manifest-invalid', detail)
    except (OSError, ValueError) as exc:
        problem('unsafe-path', f'harness source could not be read safely: {exc}')
    return problems


def validate(root: Path, smoke: bool) -> list[str]:
    """Compatibility adapter for existing CLI and candidate string diagnostics."""
    root = Path(root)
    errs = [p['detail'] for p in check(root)]
    if errs:
        return errs
    try:
        man = json.loads((root/'harness.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [f'harness.json could not be read as UTF-8 JSON: {exc}']
    if not isinstance(man, dict):
        return ['harness.json must contain a JSON object']
    unknown = [k for k in man if k not in KNOWN and not k.startswith('_')]
    if unknown:
        print(f"  note: extra manifest keys ignored by the platform: {unknown}")
    if smoke:
        base = root / man.get('path', '.')
        errs.extend(_smoke(base, base / man['entrypoint']))

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
