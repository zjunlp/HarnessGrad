"""A harness must not die on a byte its own tool printed.

Measured 2026-10-15: one model-issued shell command emitted a non-UTF-8 byte, the harness's
`subprocess.run(..., text=True)` decoded it strictly, `UnicodeDecodeError` propagated out of the harness,
and the episode ended as a zero for a task nobody finished. Every runnable harness that runs a shell
command or an agent CLI had the same call, so the same crash was reachable from eight places.

This pins the property directly instead of trusting a grep: every text-mode subprocess in
the live harnesses, platform and non-scored helpers must say how to decode.

Scope, and why it stops here:

* `base_harness/` is what `driver.py --harness <name>` copies into a workspace, so it is the code the
  bug was in.
* `current_harness/` is **derived** - `tools/export_harnesses.py`'s output, a record of harnesses that
  already ran - and is deliberately not edited; a frozen record is not a place to fix a live bug.
* `eval/`, `harnessgrad/`, `tools/` and non-scored `data/` helpers are covered too: a platform
  decode failure can abort the same task from the other side of the subprocess boundary.
* Every `data/**/verify/check.py` remains excluded: these are scored artefacts with custody
  digests in `registry/`; changing a scorer requires its own reviewed pass. This is a path
  exclusion, not a claim about its current decoding policy. `legacy/` is historical.
* The generated Python actor in `tools/diagnose_appworld.py:ACTOR` is checked as Python too.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: 22 under `tools/` because `tools/quality_sweep.py` landed on `main` after this guard was written;
#: parent integration counted it and gave it `errors="replace"` rather than exempting it.
#: 10 under `eval/` because w-banhung moved the two native launch sites (`sandbox.run` and the
#: `--no-sandbox` path in `runner.py`) into the shared `eval/containers/process.py` launcher. Those
#: two were matched here as `subprocess.*` calls and no longer are, but the call they became is
#: matched in its new home - `process.run`'s `Popen` carries `errors="replace"` - so the property is
#: unchanged and the smaller count is the accurate one, not a gap.
EXPECTED_CALLS = {"base_harness": 8, "eval": 10, "data": 20, "harnessgrad": 6, "tools": 22}
EMBEDDED_SOURCES = {"tools/diagnose_appworld.py": ("ACTOR",)}

#: Sites that ask for text without `errors=`, each with the reason it is left strict.
#: An entry that no longer matches makes the test fail, so this cannot rot into a blanket exemption.
#:
#: The Node loader probe used to be the other entry. `w-utf8` (`2dcfc013`, the reporting session's own
#: fix) made it `errors="replace"` too, which is the operator's call to make, so the exemption was
#: removed rather than kept as a dead entry - the site now says how to decode and is covered by the
#: property instead of excused from it. The residual difference is only which error surfaces if the
#: probe ever emits a bad byte: a JSON parse error instead of `UnicodeDecodeError`.
EXCEPTIONS = {
    "eval/containers/harness_runtime.py:173": (
        "the host interpreter version probe: stdout is the platform-authored ASCII X.Y expression, "
        "not task/tool output. Keep a malformed version out of the overlay cache; _cached_version "
        "catches a decoding failure and returns None without aborting the task."
    ),
}


def _subprocess_text_calls(path: Path):
    """(lineno, has_errors) for every subprocess call in `path` that asks for text."""
    source = path.read_text(encoding="utf-8")
    trees = [(ast.parse(source, filename=str(path)), 0)]
    relative = path.relative_to(ROOT).as_posix()
    for variable in EMBEDDED_SOURCES.get(relative, ()):
        values = [node.value for node in trees[0][0].body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == variable
                          for target in node.targets)]
        assert len(values) == 1, f"{relative}:{variable}: embedded source missing or ambiguous"
        value = values[0]
        assert isinstance(value, ast.Constant) and isinstance(value.value, str)
        tree = ast.parse(value.value, filename=f"{path}:{variable}")
        assert any(isinstance(node, ast.Call) for node in ast.walk(tree))
        trees.append((tree, value.lineno - 1))
    found = []
    for node, offset in ((node, offset) for tree, offset in trees for node in ast.walk(tree)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in ("run", "Popen", "check_output", "check_call", "call"):
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        wants_text = any(
            (kw.arg == "text" and getattr(kw.value, "value", None) is True)
            or kw.arg in ("universal_newlines", "encoding")
            for kw in node.keywords
        )
        if wants_text:
            found.append((node.lineno + offset, "errors" in keywords))
    return found


def test_every_live_text_subprocess_says_how_to_decode():
    offenders, checked = [], {}
    for root in EXPECTED_CALLS:
        checked[root] = 0
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts or (
                    root == "data" and path.parts[-2:] == ("verify", "check.py")):
                continue
            relative = path.relative_to(ROOT).as_posix()
            for lineno, has_errors in _subprocess_text_calls(path):
                checked[root] += 1
                site = f"{relative}:{lineno}"
                if not has_errors and site not in EXCEPTIONS:
                    offenders.append(site)
    assert checked == EXPECTED_CALLS, (
        f"text-mode subprocess inventory changed: {checked}; expected {EXPECTED_CALLS}. "
        "Review new/removed calls and the matcher before updating these counts (66 total).")
    assert not offenders, (
        "these live subprocess calls request text without `errors=`, so one non-UTF-8 byte "
        "can abort capture or lose output in a reader thread:\n  "
        + "\n  ".join(offenders)
        + "\nAdd `errors=\"replace\"` (lossy on the offending byte, but the trace stays valid UTF-8) or "
          "record the site in EXCEPTIONS with the reason it must stay strict.")


def test_the_strict_exceptions_are_still_real_sites():
    """An exemption for a call that no longer exists is how a guard quietly stops guarding."""
    assert EXCEPTIONS, "the exception list must not be emptied to make this file pass"
    for site, reason in EXCEPTIONS.items():
        relative, lineno = site.rsplit(":", 1)
        path = ROOT / relative
        assert path.is_file(), f"{relative} is exempted but does not exist"
        strict = [lineno for lineno, has_errors in _subprocess_text_calls(path) if not has_errors]
        assert int(lineno) in strict, (
            f"{site} is exempted from the decoding rule but no longer matches a strict call; "
            "review or remove the exemption")
        assert len(reason.strip()) >= 30, f"{relative}: the exemption needs a written reason"


@pytest.mark.parametrize("mode", ["capture", "stream"])
def test_real_platform_capture_preserves_invalid_bytes_as_utf8_text(mode):
    """Exercise both run/communicate and Popen/reader-thread paths without Docker or a scorer."""
    from eval.containers.container import _run_streaming
    from tools.import_env import _run

    argv = [sys.executable, "-c",
            "import os; os.write(1, b'before\\xc0\\xffafter\\n'); "
            "os.write(2, b'err\\xc0\\xff\\n')"]
    proc = (_run(argv, timeout=10) if mode == "capture"
            else _run_streaming(argv, timeout=10))
    assert proc.returncode == 0
    assert proc.stdout == "before\ufffd\ufffdafter\n"
    assert proc.stderr == "err\ufffd\ufffd\n"
    payload = {"stdout": proc.stdout, "stderr": proc.stderr}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert json.loads(encoded) == payload
