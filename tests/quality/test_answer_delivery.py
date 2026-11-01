"""Who owns `answer.txt`: the dataset asks for it, the harness must not lose it.

A dataset's goal is what tells the agent what to deliver -- gsm8k's `_goal` says to write
the final number to `answer.txt` (or submit it with `python3 qa submit`), and datasets
graded on their own tests ask for other files entirely. The harness's only job is to hand
the platform what the run produced.

That distinction is invisible when it is wrong: a harness that deletes `answer.txt` and
replaces it with the CLI's final message still produces a well-formed trace, still passes
the contract, and still scores -- against the model's prose instead of the file the task
asked for. Measured 2026-11-01: `codex` and `deepseek-harness` both did exactly that. This
file pins the rule for every harness that decides the answer itself:

    a file the agent wrote during the run wins  -> answer_source: artifact
    otherwise the final message is the answer   -> answer_source: final_message
    and the source is on the trace either way

Harnesses that delegate the choice (opencode, miniswe, miniagent, openclaw) already pass
the same rule through their own `save_answer`; their implementations are covered beside
them, and `test_opaque_adapters` pins what each can see.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

DECIDERS = ("codex", "deepseek-harness", "loop")


def _load(name: str):
    path = ROOT / "base_harness" / name / "agent.py"
    spec = importlib.util.spec_from_file_location(f"hg_delivery_{name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", DECIDERS)
def test_every_harness_that_decides_the_answer_has_a_stamp(name):
    """The mechanism, not the outcome: a decision without a prior stamp cannot tell a
    task-written file from an untouched placeholder."""
    source = (ROOT / "base_harness" / name / "agent.py").read_text()
    assert "_answer_stamp(" in source or "answer_stamp(" in source, name


def _message(module, name: str, tmp_path: Path, text: str):
    """Codex hands its message over as a file (`-o`), DSH as its stdout string."""
    if name == "codex":
        path = tmp_path / "last-message.txt"
        path.write_text(text)
        return path
    return text


@pytest.mark.parametrize("name", ("codex", "deepseek-harness"))
def test_a_task_written_answer_wins_and_the_final_message_does_not_overwrite_it(
        name, tmp_path):
    module = _load(name)
    work = tmp_path / "work"
    work.mkdir()
    answer = work / "answer.txt"
    answer.write_text("391\n")
    record = module.submitted(answer, None, _message(
        module, name, tmp_path, "I computed 17*23 and wrote 391."))
    assert record["answer_source"] == "artifact", name
    assert record["submitted"] is True
    assert answer.read_text() == "391\n", name


@pytest.mark.parametrize("name", ("codex", "deepseek-harness"))
def test_the_final_message_is_the_fallback_when_the_task_wrote_nothing(name, tmp_path):
    module = _load(name)
    work = tmp_path / "work"
    work.mkdir()
    answer = work / "answer.txt"
    record = module.submitted(answer, None, _message(module, name, tmp_path, "391"))
    assert record["answer_source"] == "final_message", name
    assert answer.read_text().strip() == "391", name


@pytest.mark.parametrize("name", ("codex", "deepseek-harness"))
def test_a_placeholder_the_task_pre_seeded_is_not_taken_as_an_answer(name, tmp_path):
    """Datasets materialise inputs before the run; an untouched file is not an answer."""
    module = _load(name)
    work = tmp_path / "work"
    work.mkdir()
    answer = work / "answer.txt"
    answer.write_text("TODO\n")
    record = module.submitted(answer, b"TODO\n", _message(module, name, tmp_path, "391"))
    assert record["answer_source"] == "final_message", name
    assert answer.read_text().strip() == "391", name


def test_codex_and_deepseek_harness_agree_on_the_rule():
    """Two adapters, one rule. A difference here is a difference between two candidates
    that the platform made rather than the method."""
    sources = {name: (ROOT / "base_harness" / name / "agent.py").read_text()
               for name in ("codex", "deepseek-harness")}
    for name, source in sources.items():
        assert '"artifact"' in source and '"final_message"' in source, name


def test_the_loop_baseline_keeps_a_file_the_agent_wrote(tmp_path, monkeypatch):
    """End to end through `loop.run`, with the model's replies scripted.

    The task asks for `answer.txt`; the scripted agent writes it with a shell command and
    then submits a *summary* as its answer. The file must survive -- that file is what the
    task's verifier reads.
    """
    loop = _load("loop")
    replies = iter([json.dumps({"tool": "bash",
                                "command": "printf 391 > answer.txt"}),
                    json.dumps({"answer": "I wrote the result to answer.txt"})])
    monkeypatch.setattr(loop, "_complete", lambda messages: next(replies))
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "task.json").write_text(json.dumps(
        {"goal": "Write the final answer to `answer.txt` in your working directory."}))
    loop.run(json.loads((tmp_path / "task.json").read_text()), work)
    rows = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    sources = [row["answer_source"] for row in rows if "answer_source" in row]
    assert sources == ["artifact"], rows[-4:]
    assert (work / "answer.txt").read_text() == "391"
