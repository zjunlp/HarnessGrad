"""What a candidate harness *is*, as code -- a diagnostic, never a score.

**Why this exists.** HarnessDev measured it on the harnesses their creators wrote and
evolution produced: *"of 169 new functions or classes, 113 are reachable from the entry
point, 31 are reachable only through dead code, and 25 have no caller"* (§4.3). Without a
number like that, this platform scores a candidate that appended 25 uncalled functions
exactly like one that changed nothing -- and dead code that never runs *looks* like work in
a diff, in a token count, and in "the method made 14 edits this round". So the count is
recorded beside the score, where a reader can see it.

**What it deliberately is not.** Not a quality metric, not an input to any score, and not a
claim about what executed. It is a static, Python-only, name-based approximation, and the
three ways it is wrong are worth more than the number itself:

* **Dynamic dispatch is invisible.** `getattr(module, name)`, `importlib`, `exec`, a
  dictionary of handlers, a string in a config file, a subprocess call to a sibling script:
  a function reached only that way reads as *unreferenced*. The name-scan below counts
  string literals as references for exactly this reason -- it reduces the false positives
  without pretending to remove them.
* **Python only.** A harness written as a shell script or a compiled program gets
  `language: "other"` and no reachability fields at all. Zeroes would read like a
  measurement, and "we could not look" is not "we looked and found nothing".
* **Reachable is not executed.** "Reachable" means *named in a file the entrypoint imports,
  transitively*. A function on a branch that never ran in the measured round is reachable.
  The measured round's own trace is where "what ran" lives, and this module does not read
  it.

Pure on purpose: paths in, a dict out. No docker, no network, no git -- so it can be
computed for a candidate that was rejected and never measured, which is one of the cases
where a reader most wants to know what the method actually did.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

#: Directories that are never part of the harness's own source: version control, caches,
#: and dependency trees. A harness *may* ship a virtualenv (the platform binds one when the
#: manifest declares dependencies), and counting site-packages as "the harness's code"
#: would report hundreds of unreferenced functions that belong to somebody else's library.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env", "site-packages", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "_harnessgrad", ".state",
})

#: Names the runtime calls, not the harness. Excluded from "has no caller" because their
#: absence of a textual caller says nothing.
DUNDER_EXEMPT = frozenset({
    "__init__", "__main__", "__enter__", "__exit__", "__call__", "__repr__", "__str__",
    "__eq__", "__hash__", "__iter__", "__next__", "__len__", "__getitem__", "__setitem__",
    "__contains__", "__post_init__", "__init_subclass__", "__class_getitem__",
})


def _python_files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            out.append(path)
    return out


def _module_name(root: Path, path: Path) -> str:
    """The dotted name a file would have if `root` were on `sys.path`."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _entrypoint(root: Path) -> Path | None:
    """The file `harness.json` names, resolved. None when the manifest does not say.

    Read here rather than imported from `harnessgrad.manifest`: `harnessgrad/` sits above
    this module in the layering (`harnessgrad/__init__.py`), and a candidate that has been
    rejected may not even have a manifest -- in which case the conventional `agent.py` is
    tried, and failing that there is simply no entrypoint to trace from.
    """
    declared = None
    manifest = root / "harness.json"
    if manifest.is_file():
        try:
            declared = (json.loads(manifest.read_text()) or {}).get("entrypoint")
        except (OSError, json.JSONDecodeError):
            declared = None
    for candidate in ([declared] if declared else []) + ["agent.py"]:
        path = root / str(candidate)
        if path.is_file():
            return path
    return None


def _imports_of(tree: ast.AST, module: str) -> list[str]:
    """Every dotted module name this file imports, relative imports resolved.

    Resolution is by name convention, not by the interpreter: `from . import x` in
    `pkg.mod` is `pkg.x`, `from .x import y` is `pkg.x`. A name that does not correspond to a
    file is kept in the list and dropped later -- it is either the standard library or a
    dependency, and neither is the harness's own code.
    """
    package = module.rsplit(".", 1)[0] if "." in module else ""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:                        # `from . import x` / `from ..pkg import y`
                up = package.split(".") if package else []
                if node.level - 1:
                    up = up[: max(0, len(up) - (node.level - 1))]
                base = ".".join([p for p in up if p] + ([base] if base else []))
            found.append(base)
            found += [f"{base}.{alias.name}" if base else alias.name
                      for alias in node.names]
    return [name for name in found if name]


def _defined_names(tree: ast.AST) -> list[tuple[str, str]]:
    """`(name, kind)` for every module-level definition, in source order."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, "class"))
    return out


def _referenced_names(trees: dict[Path, ast.AST]) -> dict[str, int]:
    """How many times each identifier is *used* (not defined) anywhere in the tree.

    Names and attribute accesses both count, because `x.foo()` and `foo()` are the same
    question. String constants count too -- see the module docstring on dynamic dispatch.
    """
    counts: dict[str, int] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                counts[node.id] = counts.get(node.id, 0) + 1
            elif isinstance(node, ast.Attribute):
                counts[node.attr] = counts.get(node.attr, 0) + 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                counts[node.value] = counts.get(node.value, 0) + 1
    return counts


def measure(harness_dir: Path) -> dict:
    """The candidate as code, as far as static analysis can honestly go.

    Fields, all of them countable from the tree alone:

    * `language` -- `"python"` when there is at least one `.py` file, else `"other"`.
    * `python_files`, `functions`, `classes` -- sizes.
    * `entrypoint` -- the file reachability was traced from, or None.
    * `unreachable_modules` -- `.py` files **not** in the entrypoint's transitive import
      closure. This is HarnessDev's "reachable only through dead code", at file
      granularity.
    * `unreferenced` -- `[{file, name, kind}]` for module-level definitions whose name never
      appears anywhere in the tree. HarnessDev's "no caller". `__init__` and friends are
      exempt; see `DUNDER_EXEMPT`.
    * `parse_errors` -- files that could not be parsed, with the error. A candidate that
      does not compile is a candidate a reader should be told about, not a candidate whose
      metrics are silently computed from the files that did parse.

    Compare two rounds by comparing their `unreferenced` lists: that is the "this round
    added three functions and two of them have no caller" reading, without needing the
    parent tree here.
    """
    root = Path(harness_dir)
    files = _python_files(root) if root.is_dir() else []
    measure_out: dict = {
        "kind": "python-static-v1",
        "language": "python" if files else "other",
        "python_files": len(files),
        "functions": 0, "classes": 0,
        "entrypoint": None,
        "unreachable_modules": [],
        "unreferenced": [],
        "parse_errors": [],
    }
    if not files:
        return measure_out

    trees: dict[Path, ast.AST] = {}
    for path in files:
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError) as exc:
            measure_out["parse_errors"].append(
                {"file": str(path.relative_to(root)), "error": f"{type(exc).__name__}: {exc}"})

    by_module = {_module_name(root, path): path for path in trees}
    # `import pkg.mod` may name a package (`pkg/mod/__init__.py`) or a module; and
    # `from helpers import x` names `helpers`, not `helpers.x`. Both spellings are tried
    # before a name is given up on.
    def resolve(name: str) -> Path | None:
        for dotted in (name, name + ".__init__"):
            if dotted in by_module:
                return by_module[dotted]
        if "." in name and name.split(".")[-1] in by_module:
            return by_module[name.split(".")[-1]]
        return None

    edges: dict[Path, set[Path]] = {}
    for path, tree in trees.items():
        targets = set()
        for name in _imports_of(tree, _module_name(root, path)):
            target = resolve(name)
            if target is not None:
                targets.add(target)
        edges[path] = targets

    entry = _entrypoint(root)
    measure_out["entrypoint"] = str(entry.relative_to(root)) if entry else None
    reachable: set[Path] = set()
    if entry in edges:
        frontier = [entry]
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            frontier += [p for p in edges.get(current, ()) if p not in reachable]
    measure_out["unreachable_modules"] = sorted(
        str(path.relative_to(root)) for path in trees if path not in reachable)

    used = _referenced_names(trees)
    unreferenced = []
    for path, tree in trees.items():
        for name, kind in _defined_names(tree):
            measure_out["functions" if kind == "function" else "classes"] += 1
            if name in DUNDER_EXEMPT:
                continue
            # One use is the definition's own `Name` node in an assignment/annotation path;
            # a module-level `def` binds the name without a `Name` node for it, so a
            # genuinely unused function scores 0. `def` bodies that *call* a sibling bump
            # that sibling's count, which is the behaviour that matters.
            if used.get(name, 0) == 0:
                unreferenced.append({"file": str(path.relative_to(root)), "name": name,
                                     "kind": kind,
                                     "reachable": path in reachable})
    measure_out["unreferenced"] = unreferenced
    return measure_out
