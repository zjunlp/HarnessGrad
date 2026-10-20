"""PersonaMem-v2 text-32k: sequential private history, then official MemAgent MCQs."""
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

KIND = "dataset"
ORACLE_SOURCE = "released"
ORACLE_COVERAGE = "Every provisioned official MCQ; reference letters remain verifier-only."
SPLIT_SOURCE = "declared"
ASSETS = Path(__file__).resolve().parent / "personamem_v2"
__path__ = [str(ASSETS)]
DATASET_ROOT = Path(os.environ.get(
    "HG_PERSONAMEM_V2_DATA",
    Path(__file__).resolve().parents[3] / "datasets/personamem_v2"))
PREPARED_ROOT = DATASET_ROOT / "prepared"


@lru_cache(maxsize=1)
def rows():
    from data.datasets.personamem_v2.lib.storage import pool
    return pool(PREPARED_ROOT)


def instruction(row):
    return (
        "Maintain memory of this user's conversation history as it arrives in order, then answer "
        "the user's questions. Use ./personamem next to obtain the current message as a UTF-8 file. "
        "The current input file is overwritten by next and removed after acknowledgement. "
        "Copy or summarize material into your own storage if you will need it later. "
        "Then call ./personamem ack --chunk-id ID --request-id ID "
        "to advance. Future history and questions are unavailable until their turn. After history, "
        "next supplies one MCQ at a time. Submit a UTF-8 answer file with "
        "./personamem submit --question-id ID --answer-file PATH --request-id ID. "
        "Use a unique request ID per operation; reuse it only to retry identical input. "
        "Every answer is final. The required MCQ answer is \\boxed{a}, \\boxed{b}, \\boxed{c}, or \\boxed{d}. "
        "Continue until next reports done. The final harness answer does not submit the MCQs. "
        "Choose your own memory, retrieval and context strategy. "
        f"There are {row['chunk_count']} history messages and {row['question_count']} questions."
    )


def load():
    from data.datasets.personamem_v2.lib.protocol import METRIC
    return ([{"task_id": tid, "goal": instruction(row), "inputs": []} for tid, row in rows().items()],
            {tid: METRIC for tid in rows()})


def service_content(tid):
    from data.datasets.personamem_v2.lib.storage import artifact
    from data.datasets.personamem_v2.lib.protocol import canonical
    return canonical(artifact(PREPARED_ROOT, f"service/{tid}.json"))


def setup(tid):
    return {"files": [
        {"path": "personamem", "from": str(ASSETS / "cli/personamem"), "mode": "0755"},
        {"path": "personamem_transport.py", "from": str(ASSETS.parents[2] / "eval/service/tool_transport.py")},
    ], "tool_relay": {
        "program": str(ASSETS / "relay.py"), "content_flag": "--episode",
        "content": service_content(tid), "filename": "episode-input.json",
        "max_calls": None, "deadline_s": None, "max_line_bytes": None,
        "record": {"path": "trusted_episode", "max_bytes": None},
    }}


def verifier(tid):
    from data.datasets.personamem_v2.lib.storage import artifact, manifest
    gold_name = f"gold/{tid}.json"
    gold = artifact(PREPARED_ROOT, gold_name)
    relay_sha = hashlib.sha256(service_content(tid).encode()).hexdigest()
    return {"kind": "command", "argv": [
        "python3", "-I", "personamem_scorer/verify/check.py",
        "--gold", "personamem_verify/gold.json", "--task-id", tid,
        "--gold-sha256", manifest(PREPARED_ROOT)["artifacts"][gold_name],
        "--input-sha256", gold["input_sha256"], "--relay-sha256", relay_sha],
        "inputs": [{"path": "personamem_verify/gold.json", "from": str(PREPARED_ROOT / gold_name)},
                   {"path": "personamem_scorer", "from": str(ASSETS), "immutable": True}],
        "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
        "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True}}


class TaskMapping(Mapping):
    def __init__(self, factory):
        self.factory = factory

    def __iter__(self):
        return iter(rows())

    def __len__(self):
        return len(rows())

    def __getitem__(self, tid):
        if tid not in rows():
            raise KeyError(tid)
        return self.factory(tid)


SETUP = TaskMapping(setup)
VERIFY = TaskMapping(verifier)
ENV = TaskMapping(lambda _: {"kind": "files"})


def __getattr__(name):
    if name == "NATIVE_SPLITS":
        from data.datasets.personamem_v2.lib.protocol import SPLITS
        return {split: [tid for tid, row in rows().items() if row["split"] == split] for split in SPLITS}
    if name == "OFFICIAL_EVAL":
        return __getattr__("NATIVE_SPLITS")["benchmark"]
    if name == "SPLIT":
        native = __getattr__("NATIVE_SPLITS")
        return {"train": native["train"] + native["val"], "eval": native["benchmark"]}
    raise AttributeError(name)


def provision_status():
    try:
        return {"present": True, "root": str(DATASET_ROOT), "tasks": len(rows()),
                "questions": sum(r["question_count"] for r in rows().values()),
                "original_questions": sum(r["original_question_count"] for r in rows().values()),
                "excluded_questions": sum(len(r["excluded_questions"]) for r in rows().values())}
    except (OSError, ValueError, KeyError) as exc:
        return {"present": False, "root": str(DATASET_ROOT), "detail": str(exc)}


def downloads():
    pins = json.loads((ASSETS / "specs/source.json").read_text())
    return [{"kind": "hf-dataset", "name": "PersonaMem-v2 text-32k CSVs and referenced histories",
             "repo": pins["dataset"], "version": pins["revision"], "dst": "raw",
             "files": list(pins["files"]),
             "sha256": {name: value["sha256"] for name, value in pins["files"].items()},
             "size": str(sum(v["bytes"] for v in pins["files"].values())) + " bytes",
             "note": "Pinned official native train/val/benchmark; offline preparation separates private gold and ordered history. Includes only text-32k inputs."}]


def control_task(tid):
    return {"task_id": tid, "goal": instruction(rows()[tid]), "inputs": []}


def aggregate(records, selected_task_ids=None):
    """Question micro-accuracy over the fixed eligible pool, with source coverage."""
    from data.datasets.personamem_v2.lib.protocol import METRIC, SPLITS, coverage
    ids = list(rows()) if selected_task_ids is None else list(selected_task_ids)
    if len(ids) != len(set(ids)) or set(ids) - rows().keys():
        raise ValueError("duplicate or unknown selected task")
    evidence = {}
    for record in records:
        row = record.get("verdict", record)
        row = row.get("evidence", row)
        tid = row.get("task_id")
        if tid not in ids:
            raise ValueError("record outside selected pool")
        if tid in evidence or row.get("metric") != METRIC:
            raise ValueError("duplicate evidence or incompatible metric")
        if (row.get("artifacts", {}).get("input_sha256") != rows()[tid]["input_sha256"]
                or row.get("split") != rows()[tid]["split"]):
            raise ValueError("evidence input or native split mismatch")
        expected_coverage = coverage(rows()[tid], rows()[tid]["question_count"])
        if (any(row.get(key) != value for key, value in expected_coverage.items())
                or row.get("expected_questions") != rows()[tid]["question_count"]):
            raise ValueError("evidence source coverage mismatch")
        evidence[tid] = row
    result = {}
    for split in SPLITS:
        selected = [tid for tid in ids if rows()[tid]["split"] == split]
        if not selected:
            continue
        expected = sum(rows()[tid]["question_count"] for tid in selected)
        complete = [evidence[tid] for tid in selected if tid in evidence and evidence[tid].get("measured")]
        for row in complete:
            if (row["expected_questions"] != rows()[row["task_id"]]["question_count"]
                    or len(row["questions"]) != row["expected_questions"]):
                raise ValueError("question coverage mismatch")
        missing = [tid for tid in selected if tid not in evidence or not evidence[tid].get("measured")]
        result[split] = {"metric": METRIC, "selected_episodes": len(selected),
                         "measured_episodes": len(complete), "expected_questions": expected,
                         "original_questions": sum(rows()[tid]["original_question_count"] for tid in selected),
                         "excluded_question_count": sum(len(rows()[tid]["excluded_questions"]) for tid in selected),
                         "excluded_questions": [e for tid in selected for e in rows()[tid]["excluded_questions"]],
                         "measured_questions": sum(len(e["questions"]) for e in complete),
                         "unmeasured_task_ids": missing,
                         "score": None if missing else sum(q["score"] for e in complete for q in e["questions"]) / expected}
    return result
