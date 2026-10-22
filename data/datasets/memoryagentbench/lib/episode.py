"""In-memory protocol core for the trusted MemoryAgentBench relay.

This object enforces ordering and immutable submissions, not process isolation.
It belongs on the trusted service side; putting it in the harness process would
not prevent that harness from reading private attributes or future inputs.
There is deliberately no gold, scorer, persistence, HTTP or container code here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from threading import Lock


class ProtocolError(ValueError):
    """A rejected operation; no progress or successful-request record is changed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ProtocolError("invalid_argument", f"{name} must be a nonblank string")
    return value


class Episode:
    """One ordered material sequence followed by one ordered question sequence.

    Question IDs must be unique stable adapter IDs within this episode. Raw
    upstream qa_pair_id values are not globally unique. Chunk IDs are positional
    (``chunk:0`` etc.) and scoped by episode_id. Successful request IDs are global
    within the episode across ack and submit, and remain replayable after seal.
    """

    def __init__(self, episode_id: str, chunks: list[str], questions: list[dict]):
        self._episode_id = _identifier(episode_id, "episode_id")
        if type(chunks) is not list or any(type(chunk) is not str for chunk in chunks):
            raise ProtocolError("invalid_argument", "chunks must be a list of strings")
        if type(questions) is not list or not questions:
            raise ProtocolError("invalid_argument", "questions must be a nonempty list")
        parsed = []
        for question in questions:
            if type(question) is not dict or set(question) != {"id", "text"}:
                raise ProtocolError("invalid_argument", "question requires only id and text")
            qid = _identifier(question["id"], "question id")
            if type(question["text"]) is not str or not question["text"].strip():
                raise ProtocolError("invalid_argument", "question text must be nonblank")
            parsed.append((qid, question["text"]))
        if len({qid for qid, _ in parsed}) != len(parsed):
            raise ProtocolError("invalid_argument", "question IDs must be unique")
        self._chunks = tuple(chunks)
        self._questions = tuple(parsed)
        self._chunk_ids = tuple(f"chunk:{i}" for i in range(len(chunks)))
        self._chunk_index = 0
        self._question_index = 0
        self._issued = False
        self._answers: list[tuple[str, str]] = []
        self._requests: dict[str, tuple[tuple[str, ...], dict]] = {}
        self._sealed: dict | None = None
        self._lock = Lock()

    def _phase(self) -> str:
        if self._chunk_index < len(self._chunks):
            return "materials"
        if self._question_index < len(self._questions):
            return "questions"
        return "done"

    def next(self) -> dict:
        """Return only the current item, without advancing; repeated reads agree."""
        with self._lock:
            if self._sealed is not None:
                return {"episode_id": self._episode_id, "phase": "sealed",
                        "kind": "done", "status": self._sealed["status"]}
            phase = self._phase()
            result = {"episode_id": self._episode_id, "phase": phase}
            if phase == "materials":
                result.update(kind="chunk", chunk_id=self._chunk_ids[self._chunk_index],
                              text=self._chunks[self._chunk_index])
                self._issued = True
            elif phase == "questions":
                qid, text = self._questions[self._question_index]
                result.update(kind="question", question_id=qid, text=text)
                self._issued = True
            else:
                result["kind"] = "done"
            return result

    def _replay(self, request_id: str, payload: tuple[str, ...]) -> dict | None:
        if request_id not in self._requests:
            return None
        recorded, response = self._requests[request_id]
        if payload != recorded:
            raise ProtocolError("request_conflict", "request ID is bound to another operation or payload")
        return deepcopy(response)

    def _writable(self, phase: str, current_id: str | None, supplied_id: str) -> None:
        if self._sealed is not None:
            raise ProtocolError("sealed", "episode is sealed")
        if self._phase() != phase:
            raise ProtocolError("wrong_phase", f"operation requires {phase} phase")
        if supplied_id != current_id:
            raise ProtocolError("unexpected_id", "only the current item can be accepted")
        if not self._issued:
            raise ProtocolError("not_issued", "call next before accepting this item")

    def _accept(self, request_id: str, payload: tuple[str, ...], item_key: str) -> dict:
        response = {"episode_id": self._episode_id, "accepted": True,
                    "operation": payload[0], item_key: payload[1], "phase": self._phase()}
        self._issued = False
        self._requests[request_id] = (payload, deepcopy(response))
        return response

    def ack(self, chunk_id: str, request_id: str) -> dict:
        """Confirm a previously issued current chunk, or replay an identical ack."""
        _identifier(chunk_id, "chunk_id")
        _identifier(request_id, "request_id")
        payload = ("ack", chunk_id)
        with self._lock:
            replay = self._replay(request_id, payload)
            if replay is not None:
                return replay
            current = (self._chunk_ids[self._chunk_index]
                       if self._chunk_index < len(self._chunks) else None)
            self._writable("materials", current, chunk_id)
            self._chunk_index += 1
            return self._accept(request_id, payload, "chunk_id")

    def submit(self, question_id: str, answer: str, request_id: str) -> dict:
        """Accept the verbatim answer once; empty strings are valid submissions."""
        _identifier(question_id, "question_id")
        _identifier(request_id, "request_id")
        if type(answer) is not str:
            raise ProtocolError("invalid_argument", "answer must be a string")
        payload = ("submit", question_id, answer)
        with self._lock:
            replay = self._replay(request_id, payload)
            if replay is not None:
                return replay
            current = (self._questions[self._question_index][0]
                       if self._question_index < len(self._questions) else None)
            self._writable("questions", current, question_id)
            self._answers.append((question_id, answer))
            self._question_index += 1
            return self._accept(request_id, payload, "question_id")

    def seal(self) -> dict:
        """Trusted controller operation: stop writes and freeze a scoring record.

        SHA256 covers UTF-8 JSON without the sha256 field, using sorted keys,
        compact separators and ensure_ascii=False. This digest identifies content;
        it is not a signature or proof of isolation. No score is generated here.
        """
        with self._lock:
            if self._sealed is None:
                qids = [qid for qid, _ in self._questions]
                record = {
                    "schema": "hg-mab-episode-seal-v1", "episode_id": self._episode_id,
                    "status": "complete" if self._phase() == "done" else "incomplete",
                    "expected": {"chunk_ids": list(self._chunk_ids), "question_ids": qids},
                    "acked_chunk_ids": list(self._chunk_ids[:self._chunk_index]),
                    "submitted_question_ids": qids[:self._question_index],
                    "missing_chunk_ids": list(self._chunk_ids[self._chunk_index:]),
                    "missing_question_ids": qids[self._question_index:],
                    "answers": [{"question_id": qid, "answer": answer}
                                for qid, answer in self._answers],
                }
                encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")
                record["sha256"] = hashlib.sha256(encoded).hexdigest()
                self._sealed = record
            return deepcopy(self._sealed)
