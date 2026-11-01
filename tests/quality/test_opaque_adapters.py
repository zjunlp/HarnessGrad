"""The two adapters whose CLI reports no structure, and what they can honestly say.

`deepseek-harness` prints its reasoning to stderr and its final message to stdout;
`cli_agent` wraps an arbitrary CLI and sees only an exit code. Neither can invent per-step
feedback, and the point of these tests is that they do not: what is checked is that a
successful run is not reported as a crashed one, that a multi-line answer is not reported as
several model turns, and that the wrapper's single action is on the record with its exit code.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.tracing import loop_facts                                   # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _facts(rows: list[dict]) -> dict:
    import json
    return loop_facts("\n".join(json.dumps(row) for row in rows))


def test_a_multiline_answer_is_one_turn_not_several():
    """A record per stdout line made a five-line answer look like five model turns."""
    dsh = _load("_dsh", ROOT / "base_harness" / "deepseek-harness" / "agent.py")
    rows: list[dict] = []
    dsh._record_streams("alpha\nbeta\n\ngamma\n", "thinking\nmore thinking\n", rows.append)
    replies = [row["reply"] for row in rows if "reply" in row]
    assert replies == ["alpha\nbeta\ngamma"], replies
    assert _facts(rows)["model_calls"] == 1
    assert [row["reasoning"] for row in rows if "reasoning" in row] == \
        ["thinking", "more thinking"]


def test_a_successful_dsh_run_is_not_reported_as_a_crash():
    """`_ended` reads a missing `usage` as `crashed` -- a harness killed before its own end.
    DSH reports no token counts, so without a usage record every good run looked like a
    death."""
    dsh = _load("_dsh2", ROOT / "base_harness" / "deepseek-harness" / "agent.py")
    rows: list[dict] = []
    dsh._record_streams("alpha\n", "", rows.append)
    usage = [row["usage"] for row in rows if "usage" in row][-1]
    assert usage["calls"] == 1
    assert "no token counts" in usage["note"], "the number says what it is"
    assert loop_facts("\n".join(__import__("json").dumps(r) for r in rows))["ended"] \
        != "crashed"


def test_the_wrapper_records_one_action_with_its_exit_code():
    """`cli_agent` executes exactly one program and can see its exit code; that is the whole
    of what it knows, and the record says exactly that."""
    source = (ROOT / "tests" / "fixtures" / "base_harness" / "cli_agent" / "agent.py").read_text()
    assert '"command": " ".join(argv)' in source
    assert '"observation"' in source
    assert '"kind": "other"' in source
