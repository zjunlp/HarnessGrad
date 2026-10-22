#!/usr/bin/env python3
"""Lint every method in `methods/`. The counterpart of `tools/dataset_lint.py`.

A dataset declares what it is (`KIND`, `OFFICIAL_EVAL`, `SPLIT`) and the platform trusts that
declaration. A method declares what it is in `method.json`, and the platform trusts that
declaration in the Launch form, in `tools/serve_ui.py`, and eventually in a paper's baseline
table. Nothing checked it -- until this file.

**The measured cost of not checking.** `methods/dream_rsi/method.json` shipped with unescaped
quotes inside two of its string values, so it was not valid JSON. `serve_ui.method_catalogue()`
swallows that failure:

    except json.JSONDecodeError:
        meta = {}

so the method appeared in the chooser as `kind: "other"`, titled by its directory name, with an
empty note -- a method that looked undocumented rather than one whose declaration could not be
read. No test read the file. Every check below exists because some part of the platform, or a
reader, depends on the answer.

    python3 tools/method_lint.py [--method NAME]

Exit code 0 when every method passes, 1 when any does not.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ROOT / "methods"
TESTS = ROOT / "tests"

#: What a method may declare itself to be. `published` is a port of a named method;
#: `reference` and `control` are the platform's own, so several rules below exempt them;
#: `improver` is the editor (not a method); `other` exists so that a new kind is a decision.
KINDS = ("published", "reference", "control", "improver", "other")

#: Which way a deviation moves the port relative to the method as published. `unknown` is the
#: honest value for a changed hyper-parameter or a platform setting: it changes what the
#: method optimises, so "weaker" would understate and "stronger" would flatter.
DIRECTIONS = ("weaker", "stronger", "neutral", "unknown")

#: A method that starts its own model client has left the platform's contract: the improver is
#: resolved once (`tools/improver.py`) and every port calls `methods/editor.py`, which is what
#: makes two ports comparable at all. Measured: 0 of the 11 declared methods does this.
#: A rule test has to name the rule it implements, or it is a test of whatever the port
#: happens to do today. An arXiv id, a DOI, a `<file>.py:<line>` from the original repository,
#: or a section marker all qualify.
CITE_RE = re.compile(r"arxiv|arXiv|10\.48550|\.py:[0-9]|§")


class Result:
    """One method's verdict. Errors fail the lint; there is no warning tier yet, on purpose:
    a check that does not fail is a check nobody reads."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.errors: list[str] = []
        self.kind = "?"
        self.deviations = 0
        self.rule_test: str | None = None

    def error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors


def _py_text(path: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(path.glob("*.py")))


def _call_name(node: ast.AST) -> tuple[str, ...]:
    """Return a dotted call target, omitting the receiver of a method call."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _starts_private_model_client(path: Path) -> bool:
    """Detect client construction/API calls without matching model names in strings."""
    for source in sorted(path.glob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"),
                             filename=str(source))
        except SyntaxError:
            continue  # Other tooling reports syntax errors; avoid a false client finding.

        openai_names = {"OpenAI"}
        openai_modules = {"openai"}
        anthropic_names = {"Anthropic"}
        anthropic_modules = {"anthropic"}
        requests_names = {"requests"}
        requests_functions = set()
        litellm_names = {"litellm"}
        litellm_functions = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if node.module == "openai" and alias.name == "OpenAI":
                        openai_names.add(alias.asname or alias.name)
                    if node.module == "requests" and alias.name == "post":
                        requests_functions.add(alias.asname or alias.name)
                    if node.module == "litellm" and alias.name in {"completion", "acompletion"}:
                        litellm_functions.add(alias.asname or alias.name)
                    if node.module == "anthropic" and alias.name in {"Anthropic", "Client"}:
                        anthropic_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root == "requests":
                        requests_names.add(alias.asname or root)
                    if root == "openai":
                        openai_modules.add(alias.asname or root)
                    if root == "litellm":
                        litellm_names.add(alias.asname or root)
                    if root == "anthropic":
                        anthropic_modules.add(alias.asname or root)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _call_name(node.func)
            if len(target) == 1 and target[0] in openai_names | anthropic_names:
                return True
            if len(target) == 2 and target[0] in openai_modules and target[1] == "OpenAI":
                return True
            if len(target) == 2 and target[0] in anthropic_names and target[1] in {"Anthropic", "Client"}:
                return True
            if len(target) == 2 and target[0] in anthropic_modules and target[1] in {"Anthropic", "Client"}:
                return True
            if len(target) == 2 and target[0] in requests_names and target[1] == "post":
                return True
            if len(target) == 1 and target[0] in requests_functions:
                return True
            if len(target) == 2 and target[0] in litellm_names and target[1] in {"completion", "acompletion"}:
                return True
            if len(target) == 1 and target[0] in litellm_functions:
                return True
            if target[-3:] == ("chat", "completions", "create") or target[-2:] == ("messages", "create"):
                return True
    return False


def lint_one(path: Path) -> Result:
    """Every check, on one method directory."""
    result = Result(path.name)
    meta_path = path / "method.json"

    if not meta_path.exists():
        result.error("no method.json: a method declares its kind, title, note and deviations "
                     "there (see tools/dataset_lint.py for the dataset counterpart)")
        return result

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # The dream_rsi failure, and the reason this file exists.
        result.error(f"method.json is not valid JSON: {exc}")
        return result

    kind = meta.get("kind")
    result.kind = kind if isinstance(kind, str) else "?"
    if kind not in KINDS:
        result.error(f"kind {kind!r} is outside {list(KINDS)}")

    for field in ("title", "note"):
        value = meta.get(field)
        if not isinstance(value, str) or not value.strip():
            result.error(f"{field} is empty: the chooser and the paper both read it")

    deviations = meta.get("deviations")
    if not isinstance(deviations, list):
        result.error("deviations must be a list (`[]` when the method is the platform's own): "
                     "a port always differs from its original somewhere, and the difference "
                     "decides whether a comparison against the published number is fair")
    else:
        result.deviations = len(deviations)
        for index, entry in enumerate(deviations):
            if not isinstance(entry, dict):
                result.error(f"deviations[{index}] is not an object")
                continue
            for field in ("what", "why"):
                if not str(entry.get(field) or "").strip():
                    result.error(f"deviations[{index}].{field} is empty")
            if entry.get("direction") not in DIRECTIONS:
                result.error(f"deviations[{index}].direction {entry.get('direction')!r} is "
                             f"outside {list(DIRECTIONS)}")
        if kind == "published" and not deviations:
            result.error("a published port deviates from its original somewhere -- the platform "
                         "withholds the gold, owns the schedule, or supplies the proposer. "
                         "Declare it, even if the answer is 'neutral'")

    text = _py_text(path)
    if _starts_private_model_client(path):
        result.error("the method starts its own model client; the improver is resolved by the "
                     "run and reached through methods/editor.py")
    if kind not in ("reference", "control") and "editor" not in text:
        result.error("no reference to `editor`: a port proposes through the shared editor")

    # The name has to appear as a **token**, not as a substring: a bare `*a*.py` matched
    # `test_adapters.py` for the method named `a`, which would have made the check pass or
    # fail on an unrelated file. Found by running this lint for the first time.
    #
    # The token must also be the **first** one after `test_`: a rule test is named after the
    # method, and a later token is a different label that happens to end in the same letter.
    # Measured 2026-10-15: the loose form matched `test_scorer_isolation_option_b.py` for the
    # method named `b` -- "option B" of scorer isolation, nothing to do with the shape in
    # `methods/b/run.py` -- and demanded a citation for a rule that file does not implement.
    # Anchoring keeps every real mapping (`test_dgm_rule.py`, `test_rrsi_fidelity.py`, ...)
    # and drops that one.
    token = re.compile(rf"^test_{re.escape(path.name)}(_|\.)")
    found = sorted(f for f in TESTS.rglob("test_*.py") if token.search(f.name))
    if found:
        result.rule_test = found[0].name
        if not CITE_RE.search(found[0].read_text(encoding="utf-8", errors="replace")):
            result.error(f"{found[0].name} does not cite the rule it implements (an arXiv id, "
                         f"a `<file>.py:<line>` from the original, or a section marker)")
    elif kind == "published":
        result.error("no rule test: nothing pins the rule this port claims to implement")

    return result


def lint_all() -> list[Result]:
    """Every directory under `methods/` that ships a `run.py`, in name order."""
    return [lint_one(d) for d in sorted(METHODS.iterdir())
            if d.is_dir() and (d / "run.py").exists()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--method", default=None, help="only this method")
    args = parser.parse_args(argv)

    results = lint_all()
    if args.method:
        results = [r for r in results if r.name == args.method]
        if not results:
            print(f"no method named {args.method!r}", file=sys.stderr)
            return 1

    failed = 0
    for result in results:
        if result.ok:
            test = f", rule test {result.rule_test}" if result.rule_test else ""
            print(f"ok   {result.name} ({result.kind}, {result.deviations} deviation(s){test})")
        else:
            failed += 1
            print(f"FAIL {result.name}: " + "; ".join(result.errors))

    print(f"\n{len(results) - failed} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
