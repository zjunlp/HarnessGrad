"""Synthetic protocol controls, without model, dataset, service or container."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Barrier

import pytest

from data.datasets.memoryagentbench.lib.episode import Episode, ProtocolError


def episode(chunks=None):
    return Episode("synthetic-A", ["first", "second"] if chunks is None else chunks,
                   [{"id": "q:0", "text": "first question"},
                    {"id": "q:1", "text": "future question"}])


def rejected(code, function, *args):
    with pytest.raises(ProtocolError) as caught:
        function(*args)
    assert caught.value.code == code


def test_ordered_delivery_requires_issuance_and_never_returns_future_text():
    run = episode()
    rejected("not_issued", run.ack, "chunk:0", "early")
    rejected("wrong_phase", run.submit, "q:0", "answer", "early-q")
    first = run.next()
    assert first == run.next()
    assert first["text"] == "first"
    assert "future question" not in json.dumps(first)
    rejected("unexpected_id", run.ack, "chunk:1", "future")
    assert run.next() == first
    run.ack("chunk:0", "a0")
    rejected("not_issued", run.ack, "chunk:1", "a1")
    assert run.next()["text"] == "second"
    run.ack("chunk:1", "a1")
    rejected("not_issued", run.submit, "q:0", "answer", "s0")
    current = run.next()
    assert current["text"] == "first question"
    rejected("unexpected_id", run.submit, "q:1", "answer", "future-q")
    assert run.next() == current
    assert run.submit("q:0", "  exact\n答案  ", "s0")["phase"] == "questions"
    assert run.next()["question_id"] == "q:1"
    response = run.submit("q:1", "", "s1")
    assert response == {"episode_id": "synthetic-A", "operation": "submit",
                        "question_id": "q:1", "accepted": True, "phase": "done"}
    assert run.next() == {"episode_id": "synthetic-A", "kind": "done", "phase": "done"}
    record = run.seal()
    assert record["status"] == "complete"
    assert record["missing_chunk_ids"] == record["missing_question_ids"] == []
    assert record["answers"] == [{"question_id": "q:0", "answer": "  exact\n答案  "},
                                 {"question_id": "q:1", "answer": ""}]


def test_successful_requests_are_bound_globally_and_replay_after_done_and_seal():
    run = episode([""])
    assert run.next()["text"] == ""
    ack = run.ack("chunk:0", "global")
    assert run.ack("chunk:0", "global") == ack
    rejected("request_conflict", run.ack, "chunk:1", "global")
    run.next()
    rejected("request_conflict", run.submit, "q:0", "a", "global")
    answer = run.submit("q:0", "a", "s0")
    rejected("request_conflict", run.submit, "q:0", "changed", "s0")
    rejected("unexpected_id", run.submit, "q:0", "changed", "new")
    assert run.submit("q:0", "a", "s0") == answer
    assert run.next()["question_id"] == "q:1"
    run.submit("q:1", "b", "s1")
    assert run.submit("q:0", "a", "s0") == answer
    sealed = run.seal()
    assert run.ack("chunk:0", "global") == ack
    assert run.submit("q:0", "a", "s0") == answer
    rejected("sealed", run.submit, "q:1", "b", "new-after-seal")
    rejected("request_conflict", run.submit, "q:0", "changed", "s0")
    assert run.seal() == sealed


@pytest.mark.parametrize("progress", [0, 1, 2, 3])
def test_early_seal_reports_missing_work_and_cannot_be_reopened(progress):
    run = episode()
    if progress >= 1:
        run.next()
        run.ack("chunk:0", "a0")
    if progress >= 2:
        run.next()
        run.ack("chunk:1", "a1")
    if progress >= 3:
        run.next()
        run.submit("q:0", "partial", "s0")
    record = run.seal()
    assert record["status"] == "incomplete"
    assert record["missing_chunk_ids"] == ["chunk:0", "chunk:1"][min(progress, 2):]
    assert record["missing_question_ids"] == ["q:0", "q:1"][max(progress - 2, 0):]
    assert run.next() == {"episode_id": "synthetic-A", "kind": "done",
                          "phase": "sealed", "status": "incomplete"}
    rejected("sealed", run.ack, "chunk:0", "new-a")
    rejected("sealed", run.submit, "q:1", "new", "new-s")
    assert run.seal() == record


def test_caller_mutation_cannot_change_inputs_ack_replays_or_sealed_answers():
    chunks = ["original"]
    questions = [{"id": "q", "text": "original question"}]
    run = Episode("immutable", chunks, questions)
    chunks[0] = "tampered"
    questions[0].update(id="tampered", text="tampered")
    response = run.next()
    response["text"] = "tampered"
    assert run.next()["text"] == "original"
    ack = run.ack("chunk:0", "a")
    ack["phase"] = "tampered"
    assert run.ack("chunk:0", "a")["phase"] == "questions"
    assert run.next()["text"] == "original question"
    accepted = run.submit("q", "answer", "s")
    accepted["accepted"] = False
    assert run.submit("q", "answer", "s")["accepted"] is True
    record = run.seal()
    original_hash = record["sha256"]
    record["answers"][0]["answer"] = "tampered"
    record["expected"]["question_ids"].append("extra")
    assert run.seal()["answers"] == [{"question_id": "q", "answer": "answer"}]
    stable = run.seal()
    assert stable.pop("sha256") == original_hash
    assert hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest() == original_hash


def test_two_episodes_with_same_local_ids_do_not_share_progress_or_requests():
    left, right = episode([]), Episode("synthetic-B", [], [{"id": "q:0", "text": "other"}])
    left.next()
    right.next()
    left.submit("q:0", "left", "shared-request-id")
    right.submit("q:0", "right", "shared-request-id")
    assert left.next()["question_id"] == "q:1"
    assert right.next()["phase"] == "done"
    assert left.seal()["answers"][0]["answer"] == "left"
    assert right.seal()["answers"][0]["answer"] == "right"


def test_identical_transcripts_hash_identically_despite_reads_retries_and_rejections():
    records = []
    for noisy in (False, True):
        run = episode(["material"])
        run.next()
        if noisy:
            run.next()
            rejected("unexpected_id", run.ack, "chunk:99", "bad")
        run.ack("chunk:0", "a")
        if noisy:
            run.ack("chunk:0", "a")
        for index, answer in enumerate(["答案", ""]):
            run.next()
            run.submit(f"q:{index}", answer, f"s{index}")
            if noisy:
                run.submit(f"q:{index}", answer, f"s{index}")
        records.append(run.seal())
    assert records[0] == records[1]


def test_concurrent_same_request_commits_one_answer_and_returns_identical_receipts():
    run = episode([])
    run.next()
    barrier = Barrier(12)

    def submit_once(_):
        barrier.wait()
        return run.submit("q:0", "one answer", "same")

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(submit_once, range(12)))
    assert all(result == results[0] for result in results)
    assert run.next()["question_id"] == "q:1"
    assert run.seal()["answers"] == [{"question_id": "q:0", "answer": "one answer"}]


def test_concurrent_conflicting_requests_accept_exactly_one_answer():
    run = episode([])
    run.next()
    barrier = Barrier(2)

    def competing(answer):
        barrier.wait()
        try:
            run.submit("q:0", answer, "same")
            return answer
        except ProtocolError as error:
            assert error.code == "request_conflict"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(competing, ["left", "right"]))
    winners = [answer for answer in results if answer is not None]
    assert len(winners) == 1
    assert run.seal()["answers"] == [{"question_id": "q:0", "answer": winners[0]}]


@pytest.mark.parametrize("bad", [None, 1, True, [], {}, "", "  \n"])
def test_invalid_ids_are_rejected_without_consuming_current_item(bad):
    run = episode([])
    current = run.next()
    rejected("invalid_argument", run.submit, bad, "answer", "s")
    rejected("invalid_argument", run.submit, "q:0", "answer", bad)
    rejected("invalid_argument", run.ack, bad, "a")
    rejected("invalid_argument", run.ack, "chunk:0", bad)
    assert run.next() == current


@pytest.mark.parametrize("bad", [None, 1, True, [], {}, b"bytes"])
def test_missing_or_nontext_answers_are_not_silently_converted(bad):
    run = episode([])
    current = run.next()
    rejected("invalid_argument", run.submit, "q:0", bad, "s")
    assert run.next() == current


@pytest.mark.parametrize("chunks,questions", [
    (None, [{"id": "q", "text": "question"}]),
    ([None], [{"id": "q", "text": "question"}]),
    ([], []),
    ([], [{"id": "q", "text": "question", "gold": "forbidden"}]),
    ([], [{"id": "q", "text": "a"}, {"id": "q", "text": "b"}]),
    ([], [{"id": "", "text": "question"}]),
    ([], [{"id": "q", "text": "  "}]),
])
def test_invalid_initial_state_is_rejected(chunks, questions):
    rejected("invalid_argument", Episode, "synthetic", chunks, questions)
