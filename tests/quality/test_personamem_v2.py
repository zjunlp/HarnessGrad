"""Strict memory delivery, native divisions, official MCQ parity and sandbox controls."""
import copy
import csv
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import textwrap
import types

import pytest

from data.datasets import personamem_v2 as dataset
from data.datasets.personamem_v2 import control
from data.datasets.personamem_v2.lib import inputs, storage
from data.datasets.personamem_v2.lib.protocol import Session, canonical, digest, REVISION, CODE_REVISION, METRIC
from data.datasets.personamem_v2.lib.scoring import score_answer
from data.datasets.personamem_v2.verify.check import verify
from data.scaffolds import registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/personamem_v2"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "prepared"
    tid = storage.task_id("train", 78)
    qid = inputs.question_id(tid, "FUTURE_QUESTION: choose a drink")
    payload = {"schema": "personamem-v2-input-v1", "task_id": tid,
               "chunks": ["OLD_PREFERENCE: tea", "NEW_PREFERENCE: coffee"],
               "questions": [{"id": qid, "text": "FUTURE_QUESTION: choose a drink"}]}
    payload["input_sha256"] = digest(payload)
    gold = {"schema": "personamem-v2-gold-v1", "task_id": tid, "metric": METRIC,
            "split": "train", "persona_id": 78, "input_sha256": payload["input_sha256"],
            "chunk_count": 2, "history_sha256": "a" * 64, "source_manifest_sha256": "b" * 64,
            "original_question_count": 1, "excluded_questions": [],
            "questions": [{"id": qid, "correct_letter": "c", "row_index": 1,
                           "pref_type": "ask_to_forget", "updated": "True", "sensitive_info": "False"}]}
    row = {"task_id": tid, "split": "train", "persona_id": 78, "input_sha256": payload["input_sha256"],
           "question_count": 1, "chunk_count": 2, "original_question_count": 1, "excluded_questions": []}
    artifacts = {"pool.json": [row], f"service/{tid}.json": payload, f"gold/{tid}.json": gold}
    hashes = {}
    for name, value in artifacts.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value) + "\n")
        hashes[name] = storage.file_sha(path)
    (root / "manifest.json").write_text(canonical({
        "schema": "personamem-v2-manifest-v1", "dataset_revision": REVISION,
        "code_revision": CODE_REVISION, "metric": METRIC, "artifacts": hashes}))
    monkeypatch.setattr(dataset, "PREPARED_ROOT", root)
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("PATH", str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ["PATH"])
    dataset.rows.cache_clear()
    yield tid, payload, gold
    dataset.rows.cache_clear()


def test_registered_memory_classification_uses_sequential_cli(prepared):
    from tools import dataset_classes

    assert dataset.KIND == "dataset"
    assert "personamem_v2" in registry.published()
    row = dataset_classes.classify("personamem_v2")
    assert (row["demand"], row["surface"]) == ("memory", "supplied_history")
    assert row["interaction"] == "closed_loop"
    assert row["action_surface"] == ["personamem[ack,next,submit]"]
    assert set(row["exercises"]) == {"context", "memory"}
    assert not row["adversarial"]


def completed_record(payload, answer=r"\boxed{c}"):
    session = Session(payload)
    while True:
        item = session.handle("next", {})
        if item["kind"] == "done":
            break
        if item["kind"] == "chunk":
            response = session.handle("ack", {"chunk_id": item["chunk_id"], "request_id": item["chunk_id"]})
        else:
            response = session.handle("submit", {"question_id": item["question_id"],
                                                 "answer": answer, "request_id": item["question_id"]})
        assert response["accepted"]
    return {"schema": "harnessgrad.interaction-record.v1",
            "binding": {"task_id": payload["task_id"], "input_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest(),
                        "run_id": "test", "trial_id": "trial-1"},
            "payload": session.record()}


def test_sequential_history_and_delayed_queries(prepared):
    _, payload, _ = prepared
    session = Session(payload)
    assert not session.handle("ack", {"chunk_id": "chunk:0", "request_id": "a"})["accepted"]
    assert session.handle("next", {}) == {"kind": "chunk", "chunk_id": "chunk:0", "text": payload["chunks"][0]}
    assert not session.handle("submit", {"question_id": payload["questions"][0]["id"], "answer": "x", "request_id": "s"})["accepted"]
    assert not session.handle("ack", {"chunk_id": "chunk:1", "request_id": "a"})["accepted"]
    assert session.handle("ack", {"chunk_id": "chunk:0", "request_id": "a"})["accepted"]
    assert session.handle("ack", {"chunk_id": "chunk:0", "request_id": "a"})["accepted"]
    assert session.handle("next", {})["text"] == payload["chunks"][1]
    assert not session.handle("ack", {"chunk_id": "chunk:1", "request_id": "a"})["accepted"]
    assert session.handle("ack", {"chunk_id": "chunk:1", "request_id": "b"})["accepted"]
    assert session.handle("next", {})["kind"] == "question"
    qid = payload["questions"][0]["id"]
    assert session.handle("submit", {"question_id": qid, "answer": "", "request_id": "s"})["accepted"]
    assert session.handle("submit", {"question_id": qid, "answer": "", "request_id": "s"})["accepted"]
    assert not session.handle("submit", {"question_id": qid, "answer": "changed", "request_id": "s"})["accepted"]
    assert session.handle("next", {})["kind"] == "done"
    assert session.record()["complete"]


def test_cli_only_retains_current_input_and_harness_owns_memory(prepared, tmp_path, monkeypatch, capsys):
    session = Session(prepared[1])
    transport = types.ModuleType("personamem_transport")
    transport.call = lambda _socket, operation, args, **_kw: session.handle(operation, args)
    monkeypatch.setitem(sys.modules, "personamem_transport", transport)
    loader = SourceFileLoader("personamem_cli_test", str(dataset.ASSETS / "cli/personamem"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    actor = tmp_path / "actor"
    actor.mkdir()
    monkeypatch.chdir(actor)
    def command(*args):
        monkeypatch.setattr(sys, "argv", ["personamem", *args])
        assert cli.main() == 0
        return json.loads(capsys.readouterr().out)
    first = command("next")
    assert Path(first["path"]).read_text() == prepared[1]["chunks"][0]
    # Retention is an explicit harness action, not a CLI-created transcript.
    Path("my-memory.txt").write_text(Path(first["path"]).read_text())
    command("ack", "--chunk-id", "chunk:0", "--request-id", "a0")
    assert not cli.CURRENT.exists() and not cli.CURRENT_ID.exists()
    second = command("next")
    assert second["path"] == first["path"]
    command("ack", "--chunk-id", "chunk:0", "--request-id", "a0")
    assert cli.CURRENT.read_text() == prepared[1]["chunks"][1]
    command("ack", "--chunk-id", "chunk:1", "--request-id", "a1")
    assert {p.name for p in actor.iterdir()} == {"my-memory.txt"}
    assert command("next")["kind"] == "question"
    assert "OLD_PREFERENCE" not in cli.CURRENT.read_text()
    # The service exposes the current question, never a past history chunk.
    assert session.handle("next", {})["kind"] == "question"
    assert not session.handle("next", {"chunk_id": "chunk:0"})["accepted"]


@pytest.mark.parametrize("operation", ["seal", "reset", "all", "get_gold", "select", "judge"])
def test_no_actor_admin_operations(prepared, operation):
    session = Session(prepared[1])
    assert not session.handle(operation, {})["accepted"]
    assert not session.record()["complete"]
    assert not session.handle("next", {})["accepted"]


def test_private_future_and_gold_are_not_staged(prepared):
    tid, payload, _ = prepared
    setup = dataset.SETUP[tid]
    assert json.loads(setup["tool_relay"]["content"]) == payload
    assert {item["path"] for item in setup["files"]} == {"personamem", "personamem_transport.py"}
    assert "FUTURE_QUESTION" not in dataset.control_task(tid)["goal"]
    assert "OLD_PREFERENCE" not in dataset.control_task(tid)["goal"]
    assert dataset.VERIFY[tid]["inputs"][1]["immutable"]
    assert dataset.ENV[tid] == {"kind": "files"}
    assert all(setup["tool_relay"][key] is None for key in ("max_calls", "deadline_s", "max_line_bytes"))


def test_history_preserves_native_order_and_hides_persona_annotation():
    raw = {"chat_history": [{"role": "system", "content": "SECRET explicit persona"},
                            {"role": "user", "content": "I like tea"},
                            {"role": "assistant", "content": "Understood"},
                            {"role": "user", "content": "Forget that preference"}]}
    chunks = inputs.history_chunks(raw)
    assert "\n\n".join(chunks) == (
        "Message 1 (user): I like tea\n\nMessage 2 (assistant): Understood\n\n"
        "Message 3 (user): Forget that preference")
    assert "SECRET" not in "".join(chunks)
    assert inputs.history_chunks([{"role": "user", "content": ""},
                                  {"role": "assistant"},
                                  {"role": "user", "content": "next"}]) == [
        "Message 1 (user): ", "Message 3 (user): next"]


def test_released_trailing_null_is_not_an_option_and_missing_options_are_not_invented():
    row = {"user_query": "{'content': 'What now?'}", "correct_answer": "correct",
           "incorrect_answers": '["one", "two", "three", null]'}
    text, letter = inputs.question(row, 2631)
    regular = dict(row, incorrect_answers="['one', 'two', 'three']")
    assert (text, letter) == inputs.question(regular, 2631)
    assert "null" not in text
    with pytest.raises(ValueError, match="missing MCQ distractors"):
        inputs.question(dict(row, incorrect_answers=""), 949)


def test_excluded_source_question_does_not_block_valid_answers_or_count_as_zero(prepared):
    tid, payload, gold = prepared
    gold = copy.deepcopy(gold)
    gold.update(original_question_count=2, excluded_questions=[{
        "split": "train", "row_index": 949, "persona_id": 78,
        "row_sha256": "c" * 64, "reason": "missing_mcq_distractors"}])
    pool = dataset.rows()
    pool[tid].update(original_question_count=2, excluded_questions=gold["excluded_questions"])
    sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
    for answer, expected in [(r"\boxed{c}", 1), (r"\boxed{a}", 0)]:
        result = verify(gold, completed_record(payload, answer), task_id=tid,
                        input_sha256=payload["input_sha256"], relay_sha256=sha)
        assert result["measured"] and result["score"] == expected
        assert result["original_questions"] == 2 and result["expected_questions"] == 1
        assert result["excluded_question_count"] == 1
        summary = dataset.aggregate([result], [tid])["train"]
        assert summary["score"] == expected and summary["measured_questions"] == 1
        assert summary["original_questions"] == 2 and summary["excluded_questions"] == gold["excluded_questions"]
        forged = copy.deepcopy(result)
        forged["excluded_questions"] = []
        with pytest.raises(ValueError, match="source coverage"):
            dataset.aggregate([forged], [tid])
    incomplete = completed_record(payload)
    incomplete["payload"].update(answers=[], complete=False)
    result = verify(gold, incomplete, task_id=tid, input_sha256=payload["input_sha256"], relay_sha256=sha)
    assert result["reason"] == "incomplete_episode" and not result["measured"]
    assert dataset.aggregate([result], [tid])["train"]["score"] is None
    legacy = dict(result, metric="personamem_v2.text32k.memagent_mcq_accuracy.v1")
    with pytest.raises(ValueError, match="incompatible metric"):
        dataset.aggregate([legacy], [tid])


def test_prepared_pool_rejects_old_scoring_scope(prepared):
    path = dataset.PREPARED_ROOT / "manifest.json"
    value = json.loads(path.read_text())
    value["metric"] = "personamem_v2.text32k.memagent_mcq_accuracy.v1"
    path.write_text(canonical(value))
    with pytest.raises(ValueError, match="prepare a fresh pool"):
        storage.pool(dataset.PREPARED_ROOT)


@pytest.mark.parametrize("change", ["original_count", "bad_gold", "excluded_index"])
def test_verifier_rejects_inconsistent_source_coverage(prepared, change):
    tid, payload, gold = prepared
    gold = copy.deepcopy(gold)
    if change == "original_count":
        gold["original_question_count"] += 1
    elif change == "bad_gold":
        gold["questions"][0]["correct_letter"] = "y"
    else:
        gold.update(original_question_count=2, excluded_questions=[{
            "split": "train", "row_index": 1, "persona_id": 78,
            "row_sha256": "c" * 64, "reason": "missing_mcq_distractors"}])
    with pytest.raises(ValueError):
        verify(gold, completed_record(payload), task_id=tid, input_sha256=payload["input_sha256"],
               relay_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def test_official_scorer_and_mcq_fixture_pins():
    pins = json.loads((dataset.ASSETS / "specs/scorer.json").read_text())
    assert storage.file_sha(FIXTURES / "official_scorer.py") == pins["scorer"]["sha256"]
    assert storage.file_sha(FIXTURES / "official_mcq_fragment.txt") == pins["preprocess"]["fragment_sha256"]


def test_mcq_preprocessing_matches_official_fragment_without_global_rng_changes():
    fragment = textwrap.dedent((FIXTURES / "official_mcq_fragment.txt").read_text())
    row = {"correct_answer": "coffee", "incorrect_answers": "['tea', 'juice', 'water']",
           "user_query": "{'role': 'user', 'content': 'A drink?'}"}
    original = random.getstate()
    for index in (0, 1, 78, 5000, 18548):
        expected = {"correct_answer_text": "coffee", "incorrect_answers": ["tea", "juice", "water"],
                    "random": random, "idx": index, "is_mcq": True, "question": "A drink?"}
        exec(fragment, expected)
        random.setstate(original)
        text, letter = inputs.question(row, index)
        assert random.getstate() == original
        assert text == expected["question"]
        assert letter == expected["correct_letter"]


def test_actor_question_identity_does_not_reveal_the_official_shuffle_seed():
    tid = storage.task_id("train", 78)
    row = {"user_query": "{'content':'Preferred drink?'}", "correct_answer": "coffee",
           "incorrect_answers": "['tea','juice','water']"}
    qid = inputs.question_id(tid, row["user_query"])
    outcomes = set()
    for original_index in (1, 17, 949, 2631, 18548):
        prompt, letter = inputs.question(row, original_index)
        permutation = list("abcd")
        random.Random(42 + original_index).shuffle(permutation)
        # The exposed index used to reveal this exact correct option position.
        assert letter == chr(97 + permutation.index("a"))
        outcomes.add(letter)
        public = {"id": inputs.question_id(tid, row["user_query"]), "text": prompt}
        assert public["id"] == qid
        assert set(public) == {"id", "text"}
        assert f"-r{original_index:05d}" not in canonical(public)
        assert "row_index" not in canonical(public) and "seed" not in canonical(public)
    # Identical public IDs occur for different secret correct positions.
    assert len(outcomes) > 1


def test_official_score_differential():
    spec = importlib.util.spec_from_file_location("personamem_official", FIXTURES / "official_scorer.py")
    official = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    cases = ["", "a", "Final Answer: a", r"\boxed{a}", r"\boxed{A}", r"\boxed{ a }",
             r"\boxed a$", r"\fbox{a}", r"\boxed{a}\boxed{b}", r"\boxed{a", r"\boxed{{a}}",
             "\\boxed{\na\n}", r"\boxed a$ followed by \boxed{b}", r"\boxedx{a}", r"\boxed a trailing"]
    rng = random.Random(91)
    cases += ["".join(rng.choices(["x", "{", "}", "\\boxed", "\\fbox", " ", "a", "b", "\n"], k=10))
              for _ in range(300)]
    for letter in "abcd":
        for answer in cases:
            assert score_answer(answer, letter) == official.compute_score(answer, {"correct_answer": f"({letter}) text"})


@pytest.mark.parametrize("change", ["binding", "task_id", "progress", "answer_order", "complete", "question_ids"])
def test_verifier_rejects_corrupted_records(prepared, change):
    tid, payload, gold = prepared
    record = completed_record(payload)
    if change == "binding":
        record["binding"]["input_sha256"] = "0" * 64
    elif change == "task_id":
        record["payload"]["task_id"] = "another"
    elif change == "progress":
        record["payload"]["acked_chunks"] = 1
    elif change == "answer_order":
        record["payload"]["answers"][0]["question_id"] = "future"
    elif change == "complete":
        record["payload"]["complete"] = False
    else:
        record["payload"]["question_ids"] = []
    with pytest.raises(ValueError):
        verify(gold, record, task_id=tid, input_sha256=payload["input_sha256"],
               relay_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())


def test_native_split_mapping_keeps_shared_persona_episodes_separate(monkeypatch):
    rows = {storage.task_id(split, 78): {"split": split, "persona_id": 78, "chunk_count": 2,
                                        "question_count": count}
            for split, count in (("train", 20), ("val", 2), ("benchmark", 6))}
    monkeypatch.setattr(dataset, "rows", lambda: rows)
    tasks, _, division = registry.load_split("personamem_v2")
    assert division["train"] == dataset.NATIVE_SPLITS["train"] + dataset.NATIVE_SPLITS["val"]
    assert division["eval"] == dataset.NATIVE_SPLITS["benchmark"]
    assert registry.official_split("personamem_v2", tasks) == division
    assert len(set(division["train"]) | set(division["eval"])) == 3


@pytest.mark.parametrize("mode,score", [("oracle", 1), ("wrong", 0), ("noop", None)])
@pytest.mark.parametrize("platform", [False, True])
def test_controls_real_relay_and_verifier(prepared, tmp_path, monkeypatch, mode, score, platform):
    work = tmp_path / "control"
    work.mkdir()
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work))
    result = control.run(mode, platform, prepared[0], work, recordings_root=tmp_path / "records")
    assert result["score"] == score, result
    if mode == "noop":
        assert result["evidence"]["reason"] == "incomplete_episode"
    else:
        assert result["evidence"]["expected_questions"] == 1
    if platform:
        assert result["detail"]["runtime_services"]["tool_relay"]["cleanup"] == {"complete": True, "errors": []}
    else:
        assert result["detail"]["cleanup"] == {"complete": True, "errors": []}


def test_actor_cannot_shadow_verifier(prepared, tmp_path, monkeypatch):
    work = tmp_path / "control"
    work.mkdir()
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work))
    result = control.run("wrong", True, prepared[0], work, recordings_root=tmp_path / "records",
                         actor_preload={"personamem_scorer/lib/scoring.py": "raise RuntimeError('ACTOR SCORER')",
                                        "personamem_verify/gold.json": "{}",
                                        "trusted_episode/episode.json": "{}"})
    assert result["score"] == 0, result


def test_reporting_refuses_incomplete_denominators(prepared):
    tid, payload, gold = prepared
    evidence = verify(gold, completed_record(payload), task_id=tid, input_sha256=payload["input_sha256"],
                      relay_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
    assert dataset.aggregate([evidence], [tid])["train"]["score"] == 1
    assert dataset.aggregate([], [tid])["train"]["score"] is None
    with pytest.raises(ValueError, match="duplicate"):
        dataset.aggregate([evidence, evidence], [tid])


@pytest.fixture
def unequal_episode_results(monkeypatch):
    # Authored question scores: the train episode mean is 1/2, but its MCQ mean is 1/4.
    pool, records = {}, []
    for split, persona, scores in (("train", 1, [1]), ("train", 2, [0, 0, 0]),
                                   ("benchmark", 1, [1, 1])):
        tid = storage.task_id(split, persona)
        excluded = ([{"split": split, "persona_id": persona, "row_index": 7,
                      "row_sha256": "a" * 64, "reason": "missing_mcq_distractors"}]
                    if persona == 2 else [])
        pool[tid] = {"task_id": tid, "split": split, "persona_id": persona,
                     "input_sha256": digest(tid), "question_count": len(scores),
                     "original_question_count": len(scores) + len(excluded),
                     "excluded_questions": excluded}
        records.append({"task_id": tid, "split": split, "metric": METRIC, "measured": True,
                        "artifacts": {"input_sha256": pool[tid]["input_sha256"]},
                        "expected_questions": len(scores),
                        "original_questions": len(scores) + len(excluded),
                        "excluded_question_count": len(excluded), "excluded_questions": excluded,
                        "questions": [{"score": score} for score in scores],
                        "score": sum(scores) / len(scores)})
    monkeypatch.setattr(dataset, "rows", lambda: pool)
    return list(pool), records


def test_reporting_uses_eligible_question_rows_per_native_split(unequal_episode_results):
    from eval.metrics import mean

    ids, records = unequal_episode_results
    report = dataset.aggregate(records, ids)
    assert set(report) == {"train", "benchmark"}
    train = report["train"]
    assert train["score"] == 0.25
    assert mean([r["score"] for r in records[:2]], dataset="personamem_v2") == 0.5
    assert train["selected_episodes"] == train["measured_episodes"] == 2
    assert train["expected_questions"] == train["measured_questions"] == 4
    assert train["original_questions"] == 5 and train["excluded_question_count"] == 1
    assert report["benchmark"]["score"] == 1
    assert report["benchmark"]["expected_questions"] == 2
    assert dataset.aggregate(records[:1], ids[:1])["train"]["score"] == 1
    assert dataset.aggregate(list(reversed(records)), ids) == report


@pytest.mark.parametrize("missing", [True, False], ids=["absent", "unmeasured"])
def test_reporting_keeps_selected_denominator_when_episode_is_incomplete(unequal_episode_results, missing):
    ids, records = unequal_episode_results
    if missing:
        records.pop(1)
    else:
        records[1].update(measured=False, questions=[{"score": 1}], score=None)
    report = dataset.aggregate(records, ids)
    train = report["train"]
    assert train["score"] is None
    assert train["expected_questions"] == 4 and train["measured_questions"] == 1
    assert train["original_questions"] == 5 and train["excluded_question_count"] == 1
    assert train["unmeasured_task_ids"] == [ids[1]]
    assert report["benchmark"]["score"] == 1


def saved_episode(tmp_path, tid, verdicts):
    from harnessgrad.records import _save_verdicts

    scores = [v.get("score") for v in verdicts]
    result = {"verdicts": {tid: verdicts[-1]},
              "per_task": {} if None in scores else {tid: sum(scores) / len(scores)}}
    if len(verdicts) > 1:
        result["trial_verdicts"] = {tid: verdicts}
    _save_verdicts(tmp_path, 0, result, [tid])
    return json.loads((tmp_path / "verdicts/round-0" / f"{tid}.json").read_text())


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_reporting_rejects_saved_trial_histories(prepared, tmp_path, reverse, wrapped):
    tid, payload, gold = prepared
    verdicts = []
    for trial, answer in enumerate(("", r"\boxed{c}"), 1):
        record = completed_record(payload, answer)
        record["binding"]["trial_id"] = f"trial-{trial}"
        evidence = verify(gold, record, task_id=tid, input_sha256=payload["input_sha256"],
                          relay_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
        verdicts.append({"kind": "command", "score": evidence["score"],
                         "evidence": evidence, "trial": trial})
    if reverse:
        verdicts.reverse()
    saved = saved_episode(tmp_path, tid, verdicts)
    assert saved["score"] == 0.5
    assert saved["trial_scores"] == ([1.0, 0.0] if reverse else [0.0, 1.0])
    with pytest.raises(ValueError, match="single-trial.*trial histories"):
        dataset.aggregate([{"verdict": saved} if wrapped else saved], [tid])


@pytest.mark.parametrize("history", [
    {"trials": []}, {"trials": None}, {"trials": {}}, {"trials": [{}]},
    {"trial_scores": [0, 1]}, {"trial_scores": []}, {"trial_scores": None},
])
def test_reporting_rejects_partial_trial_histories(unequal_episode_results, history):
    ids, records = unequal_episode_results
    with pytest.raises(ValueError, match="single-trial.*trial histories"):
        dataset.aggregate([{"evidence": records[0], **history}], ids[:1])


@pytest.mark.parametrize("answer,score", [("", 0), (r"\boxed{c}", 1)])
def test_reporting_accepts_saved_single_trial(prepared, tmp_path, answer, score):
    tid, payload, gold = prepared
    evidence = verify(gold, completed_record(payload, answer), task_id=tid,
                      input_sha256=payload["input_sha256"],
                      relay_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
    saved = saved_episode(tmp_path, tid, [{"kind": "command", "score": score, "evidence": evidence}])
    assert "trials" not in saved and "trial_scores" not in saved
    expected = dataset.aggregate([evidence], [tid])
    assert expected["train"]["score"] == score
    assert dataset.aggregate([saved], [tid]) == expected
    assert dataset.aggregate([{"verdict": saved}], [tid]) == expected


@pytest.fixture
def scorer_error_record(unequal_episode_results, tmp_path):
    tid = unequal_episode_results[0][1]
    output, reward = tmp_path / "error.json", tmp_path / "reward.txt"
    result = subprocess.run([
        sys.executable, str(dataset.ASSETS / "verify/check.py"),
        "--gold", str(tmp_path / "missing-gold.json"), "--gold-sha256", "0" * 64,
        "--task-id", tid, "--input-sha256", digest(tid), "--relay-sha256", "0" * 64,
        "--reward-file", str(reward), "--evidence-file", str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 3, result.stderr
    assert not reward.exists()
    evidence = json.loads(output.read_text())
    assert evidence["measured"] is False and evidence["reason"] == "scorer_error"
    return evidence


@pytest.mark.parametrize("form", ["evidence", "verdict", "saved"])
def test_reporting_retains_real_scorer_errors(unequal_episode_results, scorer_error_record, tmp_path, form):
    ids, records = unequal_episode_results
    error = scorer_error_record
    verdict = {"kind": "command", "score": None, "unmeasured": error["detail"], "evidence": error}
    failed = {"evidence": error, "verdict": {"verdict": verdict},
              "saved": saved_episode(tmp_path, ids[1], [verdict])}[form]
    records[1] = failed
    original = copy.deepcopy(records)
    report = dataset.aggregate(records, ids)
    train = report["train"]
    assert train["score"] is None
    assert train["selected_episodes"] == 2 and train["measured_episodes"] == 1
    assert train["expected_questions"] == 4 and train["measured_questions"] == 1
    assert train["original_questions"] == 5 and train["excluded_question_count"] == 1
    assert train["unmeasured_task_ids"] == [ids[1]]
    assert train["scorer_errors"] == {ids[1]: error["detail"]}
    assert report["benchmark"]["score"] == 1
    assert "scorer_errors" not in report["benchmark"]
    without_error = dataset.aggregate([records[0], records[2]], ids)
    assert {k: v for k, v in train.items() if k != "scorer_errors"} == without_error["train"]
    assert records == original
    with pytest.raises(ValueError, match="duplicate"):
        dataset.aggregate(records + [failed], ids)
    with pytest.raises(ValueError, match="outside selected pool"):
        dataset.aggregate([failed], ids[:1])
    with pytest.raises(ValueError, match="task identity mismatch"):
        dataset.aggregate([{"task_id": ids[0], "evidence": error}], ids)


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"measured": True}, {"measured": 0}, {"measured": "false"},
    {"detail": None}, {"detail": ""}, {"metric": "other"}, {"reason": "incomplete_episode"},
])
def test_reporting_rejects_malformed_scorer_errors(unequal_episode_results, scorer_error_record, change):
    with pytest.raises(ValueError):
        dataset.aggregate([{**scorer_error_record, **change}], unequal_episode_results[0])


@pytest.mark.parametrize("fault", ["gold-checksum", "missing-record"])
def test_platform_scorer_error_survives_sandbox_save_and_aggregate(prepared, tmp_path, monkeypatch, fault):
    from eval.verification.verifier import _verify

    tid, _, _ = prepared
    spec = dataset.verifier(tid)
    # Bind the test interpreter explicitly; the scorer still uses the adapter's
    # real command, immutable code inputs, gold pin and strict exit contract.
    spec["argv"][0] = sys.executable
    spec["runtime_paths"] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                               str(Path(sys._base_executable).parent.parent)]))
    if fault == "gold-checksum":
        # Corrupt only this synthetic pool, after the adapter admitted its pin.
        (dataset.PREPARED_ROOT / f"gold/{tid}.json").write_text("{}")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work))
    verdict = _verify(work, spec, "", sandbox=True, timeout_s=30)
    emitted = json.loads((work / "verifier/evidence.json").read_text())
    assert emitted["reason"] == "scorer_error" and emitted["measured"] is False
    assert ("gold checksum mismatch" if fault == "gold-checksum" else "FileNotFoundError") in emitted["detail"]
    assert verdict["exit_code"] == 3 and verdict["score"] is None
    assert verdict["outcome"] == "unmeasured" and not verdict["passed"]
    assert verdict["evidence"] == emitted
    assert not (work / "verifier/reward.txt").exists()
    saved = saved_episode(tmp_path / "run", tid, [verdict])
    assert saved["score"] is None and saved["evidence"] == emitted
    report = dataset.aggregate([saved], [tid])["train"]
    assert report["score"] is None
    assert report["selected_episodes"] == report["expected_questions"] == 1
    assert report["measured_episodes"] == report["measured_questions"] == 0
    assert report["unmeasured_task_ids"] == [tid]
    assert report["scorer_errors"] == {tid: emitted["detail"]}


@pytest.fixture
def batch_episodes(prepared):
    """Five authored episodes: both measured scores and two distinct nonmeasurements."""
    _, template_input, template_gold = prepared
    rows, artifacts, modes = [], {}, {}
    for split, persona, mode in (("train", 78, "oracle"), ("train", 79, "wrong"),
                                 ("train", 80, "noop"), ("train", 81, "scorer_error"),
                                 ("benchmark", 78, "oracle")):
        tid = storage.task_id(split, persona)
        payload, gold = copy.deepcopy(template_input), copy.deepcopy(template_gold)
        payload["task_id"] = tid
        payload["questions"][0]["id"] = inputs.question_id(tid, payload["questions"][0]["text"])
        del payload["input_sha256"]
        payload["input_sha256"] = digest(payload)
        gold.update(task_id=tid, split=split, persona_id=persona, input_sha256=payload["input_sha256"])
        gold["questions"][0]["id"] = payload["questions"][0]["id"]
        rows.append({"task_id": tid, "split": split, "persona_id": persona,
                     "input_sha256": payload["input_sha256"], "question_count": 1,
                     "chunk_count": 2, "original_question_count": 1, "excluded_questions": []})
        artifacts.update({f"service/{tid}.json": payload, f"gold/{tid}.json": gold})
        modes[tid] = mode
    artifacts["pool.json"] = rows
    hashes = {}
    for name, value in artifacts.items():
        path = dataset.PREPARED_ROOT / name
        path.write_text(canonical(value) + "\n")
        hashes[name] = storage.file_sha(path)
    metadata = json.loads((dataset.PREPARED_ROOT / "manifest.json").read_text())
    metadata["artifacts"] = hashes
    (dataset.PREPARED_ROOT / "manifest.json").write_text(canonical(metadata))
    dataset.rows.cache_clear()
    return modes


@pytest.mark.parametrize("jobs", [1, 3])
@pytest.mark.parametrize("trials", [1, 2])
def test_evaluate_preserves_episode_evidence_through_saved_reports(batch_episodes, tmp_path, monkeypatch,
                                                                 jobs, trials):
    from eval.run.runner import evaluate
    from harnessgrad.records import _save_verdicts

    work, harness = tmp_path / "work", tmp_path / "harness"
    work.mkdir()
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work))
    shutil.copytree(dataset.ASSETS / "oracle_harness", harness)
    metadata = json.loads((harness / "harness.json").read_text())
    metadata["runtime_paths"] = list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                                  str(Path(sys._base_executable).parent.parent)]))
    (harness / "harness.json").write_text(json.dumps(metadata))
    setups, verifiers = {}, {}
    for tid, mode in batch_episodes.items():
        setup = copy.deepcopy(dataset.SETUP[tid])
        gold = storage.artifact(dataset.PREPARED_ROOT, f"gold/{tid}.json")
        answers = {q["id"]: r"\boxed{a}" if mode == "wrong" else r"\boxed{c}" for q in gold["questions"]}
        setup["files"].append({"path": ".control.json", "content": json.dumps({
            "mode": "noop" if mode == "noop" else "oracle", "answers": answers})})
        setups[tid] = setup
        verifiers[tid] = dataset.verifier(tid)
        verifiers[tid]["argv"][0] = sys.executable
        verifiers[tid]["runtime_paths"] = metadata["runtime_paths"]
        if mode == "scorer_error":
            (dataset.PREPARED_ROOT / f"gold/{tid}.json").write_text("{}")
    ids, cache = list(batch_episodes), {}
    result = evaluate(harness, [dataset.control_task(tid) for tid in ids], dict.fromkeys(ids, METRIC),
                      setups=setups, verifiers=verifiers, envs={tid: dataset.ENV[tid] for tid in ids},
                      sandbox=True, run_id="synthetic-batch", harness_sha="control", cache=cache,
                      trials=trials, jobs=jobs, recordings_root=tmp_path / "recordings",
                      progress_dir=tmp_path / "progress")
    assert result["per_task"] == {ids[0]: 1, ids[1]: 0, ids[4]: 1}
    assert set(result["invalid"]) == {ids[2], ids[3]}
    assert set(result["verdicts"]) == set(ids)
    assert cache == {} and result["cached_tasks"] == []
    run = tmp_path / "run"
    _save_verdicts(run, 0, result, ids)
    saved = [json.loads((run / "verdicts/round-0" / f"{tid}.json").read_text()) for tid in ids]
    for tid, entry in zip(ids, saved):
        assert entry["evidence"] == result["verdicts"][tid]["evidence"]
        assert result["runtime_diagnostics"][tid]["tool_relay"]["cleanup"] == {"complete": True, "errors": []}
        if tid in result["invalid"]:
            assert entry["score"] is None and entry["invalid"] == result["invalid"][tid]
        else:
            assert entry["score"] == result["per_task"][tid]
        if trials > 1:
            # Failed legacy trials stop early, but still must carry a history
            # marker so a one-item survivor cannot look like a single-trial run.
            assert len(entry["trials"]) == (1 if tid in result["invalid"] else trials)
    assert saved[2]["evidence"]["reason"] == "incomplete_episode"
    assert saved[3]["evidence"]["reason"] == "scorer_error"
    assert "gold checksum mismatch" in saved[3]["evidence"]["detail"]
    if trials > 1:
        with pytest.raises(ValueError, match="single-trial"):
            dataset.aggregate(saved, ids)
    else:
        report = dataset.aggregate(saved, ids)
        assert report["train"]["score"] is None
        assert report["train"]["expected_questions"] == report["train"]["selected_episodes"] == 4
        assert report["train"]["measured_questions"] == report["train"]["measured_episodes"] == 2
        assert report["train"]["unmeasured_task_ids"] == ids[2:4]
        assert report["train"]["scorer_errors"] == {ids[3]: saved[3]["evidence"]["detail"]}
        assert report["benchmark"]["score"] == 1
        assert dataset.aggregate(saved[:2], ids[:2])["train"]["score"] == 0.5


def test_driver_persists_incomplete_episode_evidence(batch_episodes, tmp_path):
    complete, tid = list(batch_episodes)[:2]
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "harness.json").write_text(json.dumps({
        "name": "synthetic-mixed", "version": "1", "entrypoint": "agent.py", "backend": "cli",
        "runtime_paths": list(dict.fromkeys([sys.prefix, sys.base_prefix,
                                             str(Path(sys._base_executable).parent.parent)]))}))
    # One complete synthetic task keeps the CLI run scoreable, while the other
    # exercises a genuine incomplete relay episode in the same saved round.
    (harness / "agent.py").write_text(textwrap.dedent(f"""\
        import argparse, json, sys
        from pathlib import Path
        p = argparse.ArgumentParser()
        p.add_argument('--task'); p.add_argument('--workdir')
        a = p.parse_args()
        sys.path.insert(0, a.workdir)
        from personamem_transport import call
        if json.loads(Path(a.task).read_text())['task_id'] == {complete!r}:
            while True:
                item = call('/run/harnessgrad-tool/tool.sock', 'next')
                if item['kind'] == 'done':
                    break
                if item['kind'] == 'chunk':
                    op, key = 'ack', 'chunk_id'
                    fields = {{key: item[key]}}
                else:
                    op, key = 'submit', 'question_id'
                    fields = {{key: item[key], 'answer': r'\\boxed{{c}}'}}
                fields['request_id'] = op + '-' + item[key]
                assert call('/run/harnessgrad-tool/tool.sock', op, fields)['accepted']
        Path(a.workdir, 'trace.jsonl').write_text(json.dumps(
            {{'usage': {{'input_tokens': 0, 'output_tokens': 0, 'calls': 0}}}}) + '\\n')
        """))
    runs = tmp_path / "runs"
    proc = subprocess.run([sys.executable, "driver.py", "--dataset", "personamem_v2",
                           "--harness", str(harness), "--sampling", "all", "--tasks", complete + "," + tid,
                           "--rounds", "0", "--trials", "1", "--run-id", "synthetic-incomplete",
                           "--runs-root", str(runs), "--work-root", str(tmp_path / "work")],
                          cwd=dataset.ASSETS.parents[2], capture_output=True, text=True, timeout=90,
                          env={**os.environ, "HG_PERSONAMEM_V2_DATA": str(dataset.PREPARED_ROOT.parent)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    success = json.loads((runs / "synthetic-incomplete/verdicts/round-0" / f"{complete}.json").read_text())
    assert success["score"] == 1 and success["evidence"]["measured"] is True
    saved = json.loads((runs / "synthetic-incomplete/verdicts/round-0" / f"{tid}.json").read_text())
    assert saved["score"] is None and saved["evidence"]["reason"] == "incomplete_episode"
    summary = dataset.aggregate([saved], [tid])["train"]
    assert summary["score"] is None and summary["unmeasured_task_ids"] == [tid]


@pytest.mark.parametrize("field", ["metric", "artifacts", "split", "expected_questions", "original_questions"])
@pytest.mark.parametrize("measured", [True, False])
def test_reporting_rejects_damaged_evidence(unequal_episode_results, field, measured):
    ids, records = unequal_episode_results
    records[0]["measured"] = measured
    del records[0][field]
    with pytest.raises(ValueError):
        dataset.aggregate(records, ids)


@pytest.mark.parametrize("field", ["invalid", "unmeasured"])
@pytest.mark.parametrize("layer", ["record", "verdict", "evidence"])
def test_reporting_rejects_measured_evidence_on_excluded_records(unequal_episode_results, field, layer):
    ids, evidence = unequal_episode_results
    record = {"task_id": ids[0], "score": None, "verdict": {"evidence": evidence[0]}}
    target = {"record": record, "verdict": record["verdict"], "evidence": evidence[0]}[layer]
    target[field] = "platform rejected this measurement"
    with pytest.raises(ValueError, match="measured evidence.*invalid or unmeasured"):
        dataset.aggregate([record], ids[:1])


@pytest.mark.parametrize("field", ["invalid", "unmeasured"])
def test_evaluate_saved_platform_exclusion_cannot_become_a_score(
        unequal_episode_results, tmp_path, monkeypatch, field):
    from eval.run import runner
    from harnessgrad.records import _save_verdicts

    ids, evidence = unequal_episode_results
    tid = ids[0]
    attempt = {"verdict": {"passed": True, "score": 1.0, "evidence": evidence[0]}}
    if field == "invalid":
        attempt["invalid"] = {"stage": "model call", "detail": "platform rejected this measurement"}
    else:
        attempt["verdict"]["unmeasured"] = "platform rejected this measurement"
    monkeypatch.setattr(runner, "run_one", lambda *_args, **_kwargs: attempt)
    result = runner.evaluate(tmp_path, [{"task_id": tid}], {tid: METRIC})
    assert result["per_task"] == {} and tid in result["invalid"]
    _save_verdicts(tmp_path, 0, result, [tid])
    saved = json.loads((tmp_path / "verdicts/round-0" / f"{tid}.json").read_text())
    assert saved["score"] is None and saved["evidence"] == evidence[0]
    with pytest.raises(ValueError, match="measured evidence.*invalid or unmeasured"):
        dataset.aggregate([saved], [tid])


@pytest.mark.parametrize("change,message", [
    ({"denominator": "measured_tasks"}, "measured_tasks needs"),
    ({"aggregation": "ordered_macro_mean"}, "requires arithmetic_mean"),
])
def test_corpus_aggregation_declaration_is_live(unequal_episode_results, monkeypatch, tmp_path,
                                             change, message):
    from data.scaffolds import aggregation

    policies = json.loads(aggregation.POLICIES.read_text())
    policies["personamem_v2"]["corpus_metrics"].update(change)
    path = tmp_path / "aggregation_policies.json"
    path.write_text(json.dumps(policies))
    monkeypatch.setattr(aggregation, "POLICIES", path)
    with pytest.raises(ValueError, match=message):
        dataset.aggregate(unequal_episode_results[1], unequal_episode_results[0])


def test_prepare_keeps_native_membership_and_checks_source_bytes(tmp_path, monkeypatch):
    from data.datasets.personamem_v2 import prepare
    raw = tmp_path / "raw"
    history_name = "data/chat_history_32k/history.json"
    history = raw / history_name
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({"metadata": {"persona_id": 78}, "chat_history": [
        {"role": "system", "content": "PRIVATE PERSONA"}, {"role": "user", "content": "old"},
        {"role": "user", "content": "new"}]}))
    row = {"persona_id": "78", "chat_history_32k_link": history_name,
           "user_query": "{'content':'Which?'}", "correct_answer": "new",
           "incorrect_answers": "['old','other','none']", "pref_type": "neutral_preferences",
           "updated": "True", "sensitive_info": "False"}
    names = [history_name]
    bad = dict(row, user_query="{'content':'Broken?'}", incorrect_answers="")
    after = dict(row, user_query="{'content':'Later?'}")
    for split in ("train", "val", "benchmark"):
        name = f"benchmark/text/{split}.csv"
        path = raw / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
            if split == "train":
                writer.writerow(bad)
                writer.writerow(after)
        names.append(name)
    exclusion = {"split": "train", "row_index": 1, "persona_id": 78,
                 "row_sha256": digest(bad), "reason": "missing_mcq_distractors"}
    pins = {"files": {n: {"sha256": storage.file_sha(raw / n)} for n in names},
            "excluded_questions": [exclusion]}
    monkeypatch.setattr(prepare, "source_pins", lambda: (pins, "a" * 64))
    monkeypatch.setattr(prepare, "COUNTS", {"train": 3, "val": 1, "benchmark": 1})
    output = tmp_path / "prepared"
    result = prepare.prepare(raw, output)
    assert result["episodes"] == 3 and result["questions"] == 4
    assert result["original_questions"] == 5 and result["excluded_questions"] == 1
    for tid, r in storage.pool(output).items():
        payload = storage.artifact(output, f"service/{tid}.json")
        assert payload["questions"][0]["id"] == inputs.question_id(tid, row["user_query"])
        assert set(payload["questions"][0]) == {"id", "text"}
        assert "row_index" not in canonical(payload)
        assert "PRIVATE PERSONA" not in canonical(payload)
        gold = storage.artifact(output, f"gold/{tid}.json")
        if r["split"] == "train":
            assert r["question_count"] == 2 and r["original_question_count"] == 3
            assert r["excluded_questions"] == gold["excluded_questions"] == [exclusion]
            assert [q["row_index"] for q in gold["questions"]] == [0, 2]
            assert payload["questions"][1]["text"] == inputs.question(after, 2)[0]
            assert gold["questions"][1]["correct_letter"] == inputs.question(after, 2)[1]
            assert all(q["id"] != inputs.question_id(tid, bad["user_query"]) for q in payload["questions"])
        else:
            assert r["excluded_questions"] == []
    assert storage.manifest(output)["excluded_questions"] == [exclusion]
    with pytest.raises(FileExistsError):
        prepare.prepare(raw, output)
    exclusion["row_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="exclusion identity"):
        prepare.prepare(raw, tmp_path / "wrong-exclusion")
    exclusion["row_sha256"] = digest(bad)
    pins["excluded_questions"] = []
    with pytest.raises(ValueError, match="missing MCQ distractors"):
        prepare.prepare(raw, tmp_path / "unknown-bad-row")
    pins["excluded_questions"] = [exclusion]
    history.write_text("{}")
    with pytest.raises(ValueError, match="history digest"):
        prepare.prepare(raw, tmp_path / "broken")
    assert not (tmp_path / "broken").exists()
    assert not list(tmp_path.glob(".personamem-prepare-*"))


def test_complete_release_inventory_native_overlap_and_gold_parity():
    if not (dataset.PREPARED_ROOT / "manifest.json").is_file():
        pytest.skip("provision the pinned release to audit full native divisions")
    rows = dataset.rows()
    assert len(rows) == 1735 and sum(r["question_count"] for r in rows.values()) == 25609
    assert sum(r["original_question_count"] for r in rows.values()) == 25610
    assert {s: len(ids) for s, ids in dataset.NATIVE_SPLITS.items()} == {
        "train": 800, "val": 735, "benchmark": 200}
    for split, count in (("train", 20), ("val", 2), ("benchmark", 6)):
        assert rows[storage.task_id(split, 78)]["question_count"] == count
    personas = {s: {rows[tid]["persona_id"] for tid in ids} for s, ids in dataset.NATIVE_SPLITS.items()}
    assert personas["benchmark"] & personas["train"] == {78}
    assert personas["benchmark"] & personas["val"] == {78}
    normalized = sum(len(r["normalized_json_distractors"]) for r in rows.values())
    excluded = [r for r in rows.values() if r["excluded_questions"]]
    assert normalized == 10
    assert len(excluded) == 1 and excluded[0]["task_id"] == storage.task_id("train", 592)
    assert excluded[0]["original_question_count"] == 29 and excluded[0]["question_count"] == 28
    assert len(excluded[0]["excluded_questions"]) == 1
    assert excluded[0]["excluded_questions"][0]["row_index"] == 949
    # Every runnable released reference must reach the actual MCQ scoring path.
    scored = 0
    native_queries = {}
    for split in ("train", "val", "benchmark"):
        with (dataset.DATASET_ROOT / "raw/benchmark/text" / f"{split}.csv").open(newline="") as stream:
            native_queries[split] = list(csv.DictReader(stream))
    for tid in rows:
        gold = storage.artifact(dataset.PREPARED_ROOT, f"gold/{tid}.json")
        for q in gold["questions"]:
            native_row = native_queries[gold["split"]][q["row_index"]]
            raw_query = native_row["user_query"]
            assert q["id"] == inputs.question_id(tid, raw_query)
            assert q["correct_letter"] == inputs.question(native_row, q["row_index"])[1]
            assert score_answer("\\boxed{" + q["correct_letter"] + "}", q["correct_letter"]) == 1
            scored += 1
    assert scored == 25609
