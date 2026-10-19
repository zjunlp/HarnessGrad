"""LoCoMo native file tasks, following Harbor's full-conversation QA protocol.

Source: snap-research/locomo@3eb6f2c5 data/locomo10.json -> Harbor@3e9f14a7
adapters/locomo -> this native adapter. All five categories are retained. The
Harbor scorer (including answer parsing and full-precision aggregation) is the
metric identity; this is not a claim of identical upstream model execution.
One task is a whole conversation and all its questions, available from the start.
No memory algorithm, study/evaluation division, or Docker service is introduced.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path

KIND = "dataset"
ORACLE_SOURCE = "released"
OFFICIAL_EVAL = None
ASSETS = Path(__file__).resolve().parent / "locomo"
#: Where the pinned `locomo10.json` lives. **Absolute, like every other adapter's.** The
#: first version defaulted to `parents[3]/datasets/locomo`, a path that depends on where the
#: checkout happens to sit: the same commit then reads different data (or none) on two
#: machines, and `tools/dataset_lint.py --dataset locomo` reported "not provisioned" from
#: every checkout except one. `HG_LOCOMO_DATA` still overrides it, and names the directory
#: that CONTAINS `raw/locomo10.json`.
DATASET_ROOT = Path(os.environ.get("HG_LOCOMO_DATA", "/mnt/20t/xubuqiang/datasets/locomo"))
DATA_FILE = DATASET_ROOT / "raw" / "locomo10.json"
SOURCE_COMMIT = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
DATA_SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
METRIC = "locomo.harbor.question_micro_mean.v1"
DEFAULT_ENV = {"kind": "files", "network": "none", "agent_timeout_s": 1800,
               "verify_timeout_s": 120}


@lru_cache(maxsize=None)
def _load_asset(relative_path):
    """Load a private helper by file path, as the ScienceWorld adapter does.

    These files also run outside the platform package in the verifier. Keep the
    adapter a normal module rather than turning it into a package via __path__.
    """
    path = ASSETS / relative_path
    spec = importlib.util.spec_from_file_location(f"locomo_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INPUTS = _load_asset("lib/inputs.py")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=1)
def rows():
    raw = DATA_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("LoCoMo source digest mismatch")
    source = json.loads(raw)
    if len(source) != 10 or sum(len(row["qa"]) for row in source) != 1986:
        raise ValueError("LoCoMo source inventory mismatch")
    ids = [row["sample_id"] for row in source]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i.startswith("conv-")
                                      or not i[5:].isdigit() for i in ids):
        raise ValueError("LoCoMo conversation IDs invalid")
    if {q["category"] for row in source for q in row["qa"]} != {1, 2, 3, 4, 5}:
        raise ValueError("LoCoMo categories invalid")
    return {f"locomo/{row['sample_id']}": row for row in source}


def load():
    return ([{"task_id": tid, "goal": _INPUTS.render_instruction(row), "inputs": []}
             for tid, row in rows().items()], {tid: METRIC for tid in rows()})


def _setup(tid, row):
    return {"files": [{"path": "conversation.md", "content": _INPUTS.render_conversation(row)}]}


def gold_record(tid):
    row = rows()[tid]
    return {"schema": "hg-locomo-gold-v1", "task_id": tid, "metric": METRIC,
            "source_sha256": DATA_SHA256,
            "conversation_sha256": hashlib.sha256(_INPUTS.render_conversation(row).encode()).hexdigest(),
            "questions": _INPUTS.gold_questions(row)}


def _verify(tid, row):
    content = canonical(gold_record(tid))
    digest = hashlib.sha256(content.encode()).hexdigest()
    gold_path = "locomo_verify/gold.json"
    return {"kind": "command", "argv": ["python3", "-I", "locomo_verify/check.py",
             "--task-id", tid, "--gold-sha256", digest, "--gold", gold_path,
             "--answers", "answers.json", "--reward-file", "verifier/reward.txt",
             "--evidence-file", "verifier/evidence.json"],
            "inputs": [{"path": gold_path, "content": content},
                       {"path": "locomo_verify/check.py", "from": str(ASSETS / "verify/check.py")},
                       {"path": "locomo_verify/scoring.py", "from": str(ASSETS / "lib/scoring.py")}],
            "scorer_input": {"task_id": tid, "path": gold_path, "sha256": digest},
            "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
            "timeout_s": 120}


class _Tasks(Mapping):
    def __init__(self, build):
        self.build = build

    def __iter__(self):
        return iter(rows())

    def __len__(self):
        return len(rows())

    def __getitem__(self, tid):
        return self.build(tid, rows()[tid])


SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verify)
ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))


def oracle_files(task):
    return {"answers.json": canonical(_INPUTS.oracle_answers(rows()[task["task_id"]]))}


def control_task(task_id):
    return {"task_id": task_id, "goal": _INPUTS.render_instruction(rows()[task_id]), "inputs": []}


def provision_status():
    try:
        pool = rows()
        return {"root": str(DATASET_ROOT), "present": True, "tasks": len(pool),
                "questions": sum(len(row["qa"]) for row in pool.values())}
    except (OSError, ValueError, KeyError) as error:
        return {"root": str(DATASET_ROOT), "present": False, "detail": str(error)}


def downloads():
    return [{"kind": "http", "name": "LoCoMo released conversations and all five QA categories",
             "version": SOURCE_COMMIT, "sha256": DATA_SHA256, "size": "2805274 bytes",
             "url": f"https://raw.githubusercontent.com/snap-research/locomo/{SOURCE_COMMIT}/data/locomo10.json",
             "dst": "raw/locomo10.json",
             "note": "Official fixed data; Harbor task delivery and scorer pinned separately. All 1986 questions retained."}]
