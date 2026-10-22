"""Ordered, query-blind history delivery. This object runs only in the trusted relay."""
from copy import deepcopy
import hashlib
import json
from threading import Lock

REVISION = "ed956dea41521fc4499acbc63f966e0fd3c053ba"
CODE_REVISION = "d29d91d016add354e459dfeb0d24af08bc402e2a"
METRIC = "personamem_v2.text32k.memagent_mcq_accuracy.valid_questions.v1"
SPLITS = ("train", "val", "benchmark")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def coverage(row, question_count):
    """Account for fixed source exclusions separately from execution failures."""
    original = row.get("original_question_count")
    excluded = row.get("excluded_questions")
    if (type(original) is not int or not isinstance(excluded, list)
            or original != question_count + len(excluded)):
        raise ValueError("source question coverage mismatch")
    identities = set()
    for item in excluded:
        if (not isinstance(item, dict)
                or set(item) != {"split", "row_index", "persona_id", "row_sha256", "reason"}
                or item["split"] != row["split"] or item["persona_id"] != row["persona_id"]
                or type(item["row_index"]) is not int or item["row_index"] < 0
                or not isinstance(item["row_sha256"], str) or len(item["row_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["row_sha256"])
                or item["reason"] != "missing_mcq_distractors"
                or item["row_index"] in identities):
            raise ValueError("invalid source exclusion")
        identities.add(item["row_index"])
    return {"original_questions": original, "excluded_question_count": len(excluded),
            "excluded_questions": deepcopy(excluded)}


class Session:
    """Only the issued item may be acknowledged; submitted answers are immutable."""

    def __init__(self, payload):
        if (set(payload) != {"schema", "task_id", "chunks", "questions", "input_sha256"}
                or payload["schema"] != "personamem-v2-input-v1"
                or not isinstance(payload["task_id"], str)
                or not isinstance(payload["chunks"], list) or not payload["chunks"]
                or any(not isinstance(x, str) or not x for x in payload["chunks"])
                or not isinstance(payload["questions"], list) or not payload["questions"]):
            raise ValueError("invalid episode input")
        questions = payload["questions"]
        if (any(set(q) != {"id", "text"} or not all(isinstance(v, str) and v for v in q.values())
                for q in questions)
                or len({q["id"] for q in questions}) != len(questions)
                or digest({k: v for k, v in payload.items() if k != "input_sha256"}) != payload["input_sha256"]):
            raise ValueError("invalid questions or input hash")
        self.payload = deepcopy(payload)
        self.chunk = self.question = self.rejected = 0
        self.issued = False
        self.answers = []
        self.requests = {}
        self.sealed = None
        self.lock = Lock()

    def handle(self, operation, args):
        with self.lock:
            try:
                return self._handle(operation, args)
            except (ValueError, KeyError, TypeError) as exc:
                self.rejected += 1
                return {"accepted": False, "error": str(exc)}

    def _handle(self, operation, args):
        fields = {"next": set(), "ack": {"chunk_id", "request_id"},
                  "submit": {"question_id", "answer", "request_id"}}
        if operation not in fields or set(args) != fields[operation]:
            raise ValueError("invalid_operation")
        if any(not isinstance(v, str) for v in args.values()):
            raise ValueError("arguments must be strings")
        if operation != "next":
            request_id = args["request_id"]
            if not request_id.strip():
                raise ValueError("request_id must not be blank")
            key = (operation, canonical(args))
            if request_id in self.requests:
                old, result = self.requests[request_id]
                if old != key:
                    raise ValueError("request_conflict")
                return deepcopy(result)
        if self.sealed is not None:
            raise ValueError("episode_sealed")
        if self.chunk < len(self.payload["chunks"]):
            item = {"kind": "chunk", "chunk_id": f"chunk:{self.chunk}",
                    "text": self.payload["chunks"][self.chunk]}
        elif self.question < len(self.payload["questions"]):
            question = self.payload["questions"][self.question]
            item = {"kind": "question", "question_id": question["id"], "text": question["text"]}
        else:
            item = {"kind": "done"}
        if operation == "next":
            self.issued = item["kind"] != "done"
            return item
        if not self.issued:
            raise ValueError("item_not_issued")
        if operation == "ack" and item["kind"] == "chunk" and args["chunk_id"] == item["chunk_id"]:
            self.chunk += 1
        elif operation == "submit" and item["kind"] == "question" and args["question_id"] == item["question_id"]:
            self.answers.append({"question_id": args["question_id"], "answer": args["answer"]})
            self.question += 1
        else:
            raise ValueError("unexpected_item_or_phase")
        self.issued = False
        result = {"accepted": True}
        self.requests[request_id] = (key, result)
        return dict(result)

    def record(self):
        """Trusted shutdown only; no seal/reset/get-all operation exists on the socket."""
        with self.lock:
            if self.sealed is None:
                self.sealed = {
                    "schema": "personamem-v2-record-v1", "task_id": self.payload["task_id"],
                    "input_sha256": self.payload["input_sha256"],
                    "chunk_count": len(self.payload["chunks"]), "acked_chunks": self.chunk,
                    "question_ids": [q["id"] for q in self.payload["questions"]],
                    "answers": deepcopy(self.answers),
                    "complete": self.chunk == len(self.payload["chunks"])
                    and self.question == len(self.payload["questions"]),
                }
            return deepcopy(self.sealed)
