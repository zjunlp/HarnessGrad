"""PTC-Bench pass semantics, ordered state and trusted final-turn handoff."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from data.datasets import ptc_bench

ASSETS = Path(__file__).resolve().parents[2] / "data/datasets/ptc_bench"


def _load(name):
    spec = spec_from_file_location(f"ptc_test_{name}", ASSETS / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ptc_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ASSETS))
    monkeypatch.delitem(sys.modules, "source", raising=False)
    return _load("episode"), _load("check")


def test_followup_only_leaves_relay_after_first_submission(monkeypatch, ptc_modules):
    module, _ = ptc_modules
    monkeypatch.setattr(module, "_load_apis", lambda task: ({}, [], {}))
    task = {"question": [[{"content": "First request"}], [{"content": "Hidden follow-up"}]]}
    episode = module.Episode(task)
    assert episode.handle("status", {})["question"] == "First request"
    assert episode.snapshot()["released_turns"] == 1
    assert episode.handle("answer", {"text": "first response"})["question"] == "Hidden follow-up"
    assert episode.snapshot()["turn_answers"] == ["first response"]
    assert episode.snapshot()["final_answer"] is None
    assert episode.handle("answer", {"text": "second response"})["complete"]
    assert episode.snapshot()["final_answer"] == "second response"
    assert "error" in episode.handle("answer", {"text": "overwrite"})


def test_actor_file_needs_released_final_turn(tmp_path, ptc_modules):
    _, checker = ptc_modules
    config = {"task": {"question": [[], []]}}
    artifact = tmp_path / "answer.txt"
    artifact.write_text("operator answer")
    pending = {"released_turns": 1, "final_answer": None, "turn_answers": [], "complete": False}
    assert checker.with_answer_artifact(config, pending, artifact) is pending
    after_reply = {**pending, "released_turns": 2, "turn_answers": ["first turn"]}
    settled = checker.with_answer_artifact(config, after_reply, artifact)
    assert settled["final_answer"] == "operator answer" and settled["complete"]
    assert settled["turn_answers"] == ["first turn"]
    from_relay = {**after_reply, "final_answer": "relay wins"}
    assert checker.with_answer_artifact(config, from_relay, artifact) is from_relay


def test_actor_answer_symlink_cannot_read_verifier_inputs(tmp_path, ptc_modules):
    _, checker = ptc_modules
    private = tmp_path / "ptc_private_case.json"
    private.write_text("private inputs")
    artifact = tmp_path / "answer.txt"
    artifact.symlink_to(private)
    config = {"task": {"question": [[]]}}
    payload = {"released_turns": 1, "final_answer": None, "turn_answers": [], "complete": False}
    assert checker.with_answer_artifact(config, payload, artifact) is payload


def test_api_failures_are_returned_as_business_results(monkeypatch, ptc_modules):
    module, _ = ptc_modules

    def transient():
        raise RuntimeError("scheduled failure")

    bound = {"simulation.valid_operation": transient}
    monkeypatch.setattr(module, "_load_apis", lambda task: ({}, [], bound))
    episode = module.Episode({"question": [[{"content": "First"}]]})
    assert episode.handle("call", {"name": "simulation.valid_operation", "arguments": {}}) == {
        "error": "RuntimeError: scheduled failure"}
    assert episode.api_calls == 1 and episode.api_errors == 1
    assert episode.handle("call", {"name": "simulation.__dict__", "arguments": {}}) == {
        "error": "unknown documented tool"}
    assert episode.api_calls == 1


def test_official_exception_uses_cli_failure_semantics(monkeypatch, ptc_modules):
    _, checker = ptc_modules
    upstream = ModuleType("ptc_bench.eval_checker.verifiers")

    def official(**kwargs):
        if kwargs["final_answer"] == "bad":
            raise AttributeError("private scorer detail")
        return SimpleNamespace(passed=False, score=0.8)

    upstream.verify = official
    monkeypatch.setitem(sys.modules, upstream.__name__, upstream)
    config = {"task": {"id": "ptc_case", "initial_config": {}}, "ground_truth": {}}
    payload = {"final_answer": "bad", "final_state": {}, "turn_answers": []}
    assert checker.grade(config, payload) == {
        "passed": False, "partial_score": 0.0, "verifier_error": "AttributeError",
    }
    assert checker.grade(config, {**payload, "final_answer": "partial"}) == {
        "passed": False, "partial_score": 0.8,
    }


def test_pins_and_split_metadata(ptc_modules):
    _, checker = ptc_modules
    assert checker.REVISION == ptc_bench.REVISION
    assert ptc_bench.downloads()[0]["version"] == ptc_bench.REVISION
    assert ptc_bench.SPLIT is None and ptc_bench.OFFICIAL_EVAL is None


def test_pinned_upstream_scores_malformed_grocery_answer_as_zero():
    if not ptc_bench.provision_status().get("present"):
        pytest.skip("pinned PTC-Bench source and execution runtime are not provisioned")
    row = ptc_bench.catalog()["ptc_bench/main/ptc_grocery_gift_card_0"]
    program = """
import sys
sys.path.insert(0, sys.argv[1])
from check import grade
from episode import Episode
from source import bootstrap, verified_case
bootstrap(sys.argv[2])
case = verified_case(sys.argv[3], sys.argv[4])
episode = Episode(case['task'])
episode.handle('status', {})
episode.handle('answer', {'text': '1'})
assert grade(case, episode.snapshot()) == {
    'passed': False, 'partial_score': 0.0, 'verifier_error': 'AttributeError'}
"""
    subprocess.run([sys.executable, "-I", "-B", "-c", program, str(ASSETS),
                    str(ptc_bench._bundle()), str(ptc_bench._case(row)), row["sha256"]],
                   check=True, capture_output=True, text=True, timeout=15, env={})
