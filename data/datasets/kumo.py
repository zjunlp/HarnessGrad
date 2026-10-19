"""KUMO: 5,290/5,300 released tasks, official consistency accuracy.

Ten unsatisfiable easy cases are excluded with repeat-audit evidence; 35 cases
with multiple accepted truths retain the official predicate. The protocol is
Harbor-style 50 action queries, not upstream VanillaAgent's 50 model turns.
Private cases are staged only for the selected task in a host-side tool relay.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

KIND = "dataset"
ORACLE_SOURCE = "released"
OFFICIAL_EVAL = None
ASSETS = Path(__file__).resolve().parent / "kumo"
DATASET_ROOT = Path(os.environ.get("HG_KUMO_DATA", Path(__file__).resolve().parents[3] / "datasets/kumo"))
PREPARED = DATASET_ROOT / "prepared-v1"
REVISION = "576748764f7d72634654d0784944a485eca9047c"
MANIFEST_SHA256 = "05e20d7a9e7e8cff348f77e36cfb0d2842b549c344086e71fe478458b3cd05fe"
METRIC = "kumo.official.consistency_accuracy.v1"
PROTOCOL = "kumo.query50.v1"
# Native mount isolation; network is inherited so the configured HTTPS model API
# is reachable. No egress isolation or contamination prevention is claimed.
# Harbor task-template deadlines; optional operator policy is recorded separately.
DEFAULT_ENV = {"kind": "files", "agent_timeout_s": 900, "verify_timeout_s": 120}
GOAL = """Identify the hidden truth in this KUMO reasoning game.
Read `python3 kumo.py show` for the candidate truths, available actions, domain
instructions and knowledge book. Query observations with:
  python3 kumo.py do 'EXACT ACTION'
Use `python3 kumo.py stats` to inspect your remaining query budget.
At most 50 accepted action queries are allowed; repeated queries also count.
Any additional service resource limits are declared by the run configuration.
Reading local materials and writing the answer do not use service calls.
Use the supplied task material and observations. The final answer.txt must
contain only one candidate verbatim, without an explanation. Finish through
your harness's normal completion procedure, ensuring it leaves that candidate
in answer.txt. The optional `python3 kumo.py submit 'EXACT CANDIDATE'` helper
only writes the file; it does not end the harness run.
"""


@lru_cache(maxsize=1)
def manifest():
    raw = (PREPARED / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("KUMO manifest digest mismatch; rerun pinned preparation")
    result = json.loads(raw)
    if result["source_revision"] != REVISION or len(result["tasks"]) != 5290:
        raise ValueError("KUMO manifest identity mismatch")
    return result


@lru_cache(maxsize=1)
def roster():
    return {row["task_id"]: row for row in manifest()["tasks"]}


def control_task(task_id):
    if task_id not in roster():
        raise ValueError(f"unknown KUMO task: {task_id}")
    return {"task_id": task_id, "goal": GOAL, "inputs": []}


def load():
    return [control_task(tid) for tid in roster()], {tid: METRIC for tid in roster()}


def _setup(tid, row):
    folder = PREPARED / row["path"]
    public = (folder / "public.json").read_bytes()
    if hashlib.sha256(public).hexdigest() != row["public_sha256"]:
        raise ValueError("KUMO public input digest mismatch")
    return {"files": [{"path": "kumo.py", "from": str(ASSETS / "cli/kumo.py")},
                      {"path": "kumo_public.json", "content": public.decode("utf-8")}],
            "tool_relay": {"program": str(ASSETS / "relay.py"), "content_flag": "--case-spec",
                           "content": json.dumps({"task_id": tid,
                                                  "path": str(folder / "private.json"),
                                                  "sha256": row["private_sha256"]}),
                           "filename": "case-spec.json",
                           "budget": 50,
                           "max_calls": None, "deadline_s": None, "max_line_bytes": None}}


def _verify(tid, row):
    case_path = "kumo_verify/private.json"
    return {"kind": "command", "argv": ["python3", "-I", "kumo_verify/check.py",
             "--task-id", tid, "--case-sha256", row["private_sha256"]],
            "inputs": [{"path": case_path, "from": str(PREPARED / row["path"] / "private.json")},
                       {"path": "kumo_verify/check.py", "from": str(ASSETS / "verify/check.py")},
                       {"path": "kumo_verify/scoring.py", "from": str(ASSETS / "lib/scoring.py")},
                       {"path": "kumo_verify/engine.py", "from": str(ASSETS / "vendor/engine.py")}],
            "scorer_input": {"task_id": tid, "path": case_path, "sha256": row["private_sha256"]},
            "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
            }


class _Tasks(Mapping):
    def __init__(self, build):
        self.build = build

    def __iter__(self):
        return iter(roster())

    def __len__(self):
        return len(roster())

    def __getitem__(self, tid):
        return self.build(tid, roster()[tid])


SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verify)
ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))


def oracle_files(task):
    row = roster()[task["task_id"]]
    private = json.loads((PREPARED / row["path"] / "private.json").read_text())
    return {"answer.txt": private["valid_truth"] + "\n"}


def downloads():
    files = json.loads((ASSETS / "source-files.json").read_text())
    return [{"kind": "http", "name": "KUMO pinned sparse source and released games",
             "version": REVISION, "url": f"https://raw.githubusercontent.com/linhaowei1/kumo/{REVISION}",
             "size": "127892007 bytes across 5846 files", "dst": "upstream",
             "files": list(files), "sha256": files,
             "note": "Prefer prepare.py --fetch for batched sparse Git download, then prepare.py audits 5300 rows twice and materializes 5290 eligible tasks. No model calls or pip dependencies; historical results omitted."}]


def provision_status():
    try:
        pool = roster()
        missing = sum(not (PREPARED / row["path"] / name).is_file()
                      for row in pool.values() for name in ("public.json", "private.json"))
        return {"root": str(DATASET_ROOT), "present": missing == 0,
                "tasks": len(pool), "source_tasks": 5300, "excluded": 10,
                "detail": f"{missing} missing prepared files"}
    except (OSError, ValueError, KeyError) as error:
        return {"root": str(DATASET_ROOT), "present": False, "detail": str(error)}


def __getattr__(name):
    if name == "SPLIT":
        # Adapter-chosen hash split, not an upstream benchmark split.
        from data.scaffolds.registry import default_split
        return default_split(load()[0])
    raise AttributeError(name)
