"""The improver dispatch, the evidence budget and the shrink guard (findings §116–§120).

Three regressions, each one paid for by a real run:

* a method that edits an *older* state handed `cli_improver_identity` a directory with no
  `_harnessgrad/`, the lookup answered None, and `ask()` silently used the single-shot chat
  endpoint -- `dream_rsi` r12 rebuilt a 200 KB harness from the 60 KB the prompt carried and
  deleted 3,443 lines (0/90 on ninety tasks);
* the caps that produced that 60 KB truncation (200,000 per file, 60,000 total, 24,000 for the
  skill) were sized for an argv transport the endpoint does not use, and they truncated *silently*
  while the prompt asked for "the complete contents of every file";
* nothing checked that a candidate was still the size of the thing it claimed to edit, so the
  collapse above was measured rather than rejected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))

import editor                                              # noqa: E402


def _harness(root: Path, body: str = "x = 1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(json.dumps({
        "name": "loop", "version": "0.2.0", "path": ".", "entrypoint": "agent.py",
        "backend": "cli", "install": "requirements.txt", "env_kinds": ["files", "exec"],
    }))
    (root / "agent.py").write_text(body)
    (root / "requirements.txt").write_text("")
    return root


def _channel(run: Path, improver: dict | None = None) -> Path:
    channel = run / editor.CHANNEL
    (channel / "states").mkdir(parents=True, exist_ok=True)
    if improver is not None:
        (channel / "round.json").write_text(json.dumps({"identity": {"improver": improver}}))
    return channel


def test_a_staged_state_resolves_the_runs_improver(tmp_path: Path) -> None:
    """The regression: editing an older state must not lose the CLI improver.

    A state is `<incumbent>/_harnessgrad/states/round-N` and `git archive` leaves the channel out
    of it, so a lookup that only reads `channel(base)/round.json` finds nothing for exactly the
    rounds where a rule chose an older base -- and `ask()` then answers with a chat completion
    that can only see what fits in the prompt.
    """
    run = tmp_path / "run"
    channel = _channel(run, {"kind": "cli", "path": "/usr/bin/true"})
    state = channel / "states" / "round-3"
    _harness(state)

    assert editor._enclosing_channel(state) == channel
    assert editor.cli_improver_identity(state)["kind"] == "cli"
    # ...and a plain workspace still resolves through its own channel, unchanged.
    plain = _harness(tmp_path / "plain")
    assert editor._enclosing_channel(plain) is None
    assert editor.cli_improver_identity(plain) is None


def test_an_endpoint_run_is_still_an_endpoint_run(tmp_path: Path) -> None:
    """The fix must not turn a genuinely endpoint-based run into a CLI one."""
    run = tmp_path / "run"
    channel = _channel(run, {"kind": "endpoint", "path": "https://example.invalid/v1",
                             "base_url": "https://example.invalid/v1"})
    state = channel / "states" / "round-1"
    _harness(state)
    assert editor.cli_improver_identity(state) is None


def test_the_harness_is_shown_whole_or_refused(tmp_path: Path) -> None:
    """`load_sources` may not truncate: a reader shown part of a file cannot return the whole.

    The old behaviour kept head and tail and inserted a TRUNCATED note, which the caller then
    wrapped in a request for "the complete contents of every file it names".
    """
    big = _harness(tmp_path / "big", body="x = 1\n")
    (big / "extra.py").write_text("y" * (editor.SOURCE_TOTAL_LIMIT + 1_000))
    with pytest.raises(editor.EvidenceTooLarge) as exc:
        editor.load_sources(big)
    assert "budget" in str(exc.value)

    small = _harness(tmp_path / "small")
    assert sorted(editor.load_sources(small)) == ["agent.py", "requirements.txt"]


def test_the_skill_is_refused_rather_than_cut_short(tmp_path: Path, monkeypatch) -> None:
    run = tmp_path / "run"
    channel = _channel(run, None)
    (channel / "SKILL.md").write_text("s" * 100)
    monkeypatch.setattr(editor, "SKILL_LIMIT", 10)
    with pytest.raises(editor.EvidenceTooLarge):
        editor.skill_block(run)


def test_a_candidate_that_deletes_most_of_the_tree_fails_to_apply(tmp_path: Path) -> None:
    """Measured 2026-10-20: 3,953 lines -> 980, still importable, 0/90 on ninety tasks."""
    base = _harness(tmp_path / "base", body="a" * 2_000)
    dest = _harness(tmp_path / "dest")
    edits = [{"path": "agent.py", "content": "a" * 200}]
    changed, _paths, problems = editor.apply_edits(base, dest, edits)
    assert not changed
    assert "keeps" in problems

    # A normal edit of the same tree is not affected by the guard.
    same = _harness(tmp_path / "same")
    edits = [{"path": "agent.py", "content": "a" * 1_900}]
    changed, _paths, problems = editor.apply_edits(base, same, edits)
    assert changed and not problems
