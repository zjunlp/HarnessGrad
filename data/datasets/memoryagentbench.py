"""MemoryAgentBench: all 146 episodes / 3,671 questions.

400 questions use verifier-only model judging; the other 3,271 score locally.
One ordered material sequence and its questions form each interactive task.

Provision using ``python -m data.datasets.memoryagentbench.prepare``; set HG_MAB_DATA or
HG_MAB_PREPARED for another external location. Import reads no data or gold.
No study/evaluation division is declared. Four upstream splits are capability
partitions, and OFFICIAL_EVAL=None identifies the complete provisioned pool.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path


# Real released benchmark adapter. Publication awaits the memory taxonomy policy;
# explicit registry.load/run_one remain available for adapter controls.
KIND = "fixture"
ORACLE_SOURCE = "released"
ORACLE_COVERAGE = "Released answers; model-judge controls require explicit judge configuration"
OFFICIAL_EVAL = None
ASSETS = Path(__file__).resolve().parent / "memoryagentbench"
# Keep dataset-private imports available beside the registry's flat declaration.
__path__ = [str(ASSETS)]
DATASET_ROOT = Path(os.environ.get("HG_MAB_DATA", Path(__file__).resolve().parents[3]
                                 / "datasets" / "memoryagentbench"))
PREPARED_ROOT = Path(os.environ.get("HG_MAB_PREPARED", DATASET_ROOT / "prepared"))
REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"


@lru_cache(maxsize=1)
def _rows():
    from data.datasets.memoryagentbench.lib.storage import load_pool
    pool = load_pool(PREPARED_ROOT)
    return {row["task_id"]: row for row in pool["tasks"]}


def _instruction(row):
    return ("Read the supplied material in order, then answer every question using that material. "
            "Use ./mab through your shell tool. ./mab next writes the current full text to a file; "
            "read that file, then acknowledge its chunk ID. After all chunks, submit each answer "
            "from a UTF-8 file. Keep one request ID per operation and reuse it on retries. "
            "Follow each question's answer format. You may organize and retrieve material as you choose. "
            f"This episode has {row['chunk_count']} material chunks and {row['question_count']} questions. "
            "Continue until next reports done, then finish your harness run. Your final harness answer "
            "does not submit the individual question answers.")


def load():
    rows = _rows()
    tasks = [{"task_id": tid, "goal": _instruction(row), "inputs": []} for tid, row in rows.items()]
    from data.datasets.memoryagentbench.lib.scoring import PRIMARY_METRICS
    return tasks, {tid: PRIMARY_METRICS[row["source"]] for tid, row in rows.items()}


def _service_content(row):
    from data.datasets.memoryagentbench.lib.storage import load_service
    payload = load_service(PREPARED_ROOT, row["task_id"])
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _setup(row):
    return {"files": [
        {"path": "mab", "from": str(ASSETS / "cli/mab"), "mode": "0755"},
        {"path": "mab_transport.py", "from": str(ASSETS.parents[2] / "eval/service/tool_transport.py")},
    ], "tool_relay": {
        "program": str(ASSETS / "relay.py"), "content_flag": "--episode",
        "content": _service_content(row), "filename": "episode-input.json",
        "max_calls": None, "deadline_s": None, "max_line_bytes": None,
        "record": {"path": "trusted_episode", "max_bytes": None},
    }}


def _verify(row):
    from data.datasets.memoryagentbench.lib.storage import load_gold, load_manifest
    load_gold(PREPARED_ROOT, row["task_id"])
    manifest = load_manifest(PREPARED_ROOT)
    gold_path = f"gold/{row['task_id']}.json"
    digest = hashlib.sha256(_service_content(row).encode()).hexdigest()
    result = {"kind": "command", "argv": [
        "python3", "-I", "mab_scorer/verify/check.py", "--gold", "mab_verify/gold.json",
        "--task-id", row["task_id"], "--input-sha256", row["input_sha256"],
        "--relay-sha256", digest, "--gold-sha256", manifest["artifacts"][gold_path],
        "--entity2id-sha256", manifest["artifacts"]["entity2id.json"]],
        "inputs": [
            {"path": "mab_verify/gold.json", "from": str(PREPARED_ROOT / gold_path)},
            {"path": "mab_verify/entity2id.json", "from": str(PREPARED_ROOT / "entity2id.json")},
            {"path": "mab_scorer", "from": str(ASSETS), "immutable": True}],
        "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
        "result_contract": {"version": 1, "score_range": [0, 1],
                            "require_exit_zero": True}}
    from data.datasets.memoryagentbench.lib.scoring import JUDGE_SOURCES
    if row['source'] in JUDGE_SOURCES:
        from data.datasets.memoryagentbench.lib.judge import profile
        from harnessgrad.config import current
        cfg = current().judge
        model = cfg.resolve_model('memoryagentbench', default=profile(row['source'])['model'])
        output_tokens = cfg.memoryagentbench_max_output_tokens
        selected = profile(row['source'], model, max_output_tokens=output_tokens)
        result['judge'] = {key: selected[key] for key in ('model', 'max_output_tokens', 'parameters')}
        result['judge'].update(max_calls=row['question_count'] * selected['calls_per_question'],
                               timeout_s=None, max_retries=0, max_attempts=None,
                               response_max_bytes=None, max_line_bytes=None)
        result['argv'] += ['--judge-model', selected['model'],
                           '--judge-max-output-tokens', str(selected['max_output_tokens'])]
        result['inputs'].append({'path': 'mab_judge_transport.py',
            'from': str(ASSETS.parents[2] / 'eval/service/tool_transport.py')})
    return result


def _env(row):
    return {"kind": "files"}


class _TaskMapping(Mapping):
    """Construct declarations on demand; no import-time reading of the task pool."""
    def __init__(self, factory):
        self.factory = factory

    def __iter__(self):
        return iter(_rows())

    def __len__(self):
        return len(_rows())

    def __getitem__(self, task_id):
        return self.factory(_rows()[task_id])


SETUP = _TaskMapping(_setup)
VERIFY = _TaskMapping(_verify)
ENV = _TaskMapping(_env)


def reporting_manifest(task_ids):
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("duplicate selected task IDs")
    return [dict(_rows()[tid]) for tid in task_ids]


def excluded_tasks():
    """All provisioned sources now have a scoring path."""
    return []


def provision_status():
    try:
        rows = _rows()
        return {"root": str(DATASET_ROOT), "prepared": str(PREPARED_ROOT),
                "present": bool(rows), "tasks": len(rows), "questions": sum(row["question_count"] for row in rows.values()),
                "excluded_tasks": len(excluded_tasks()),
                "excluded_questions": sum(row["question_count"] for row in excluded_tasks())}
    except (OSError, ValueError) as exc:
        return {"root": str(DATASET_ROOT), "prepared": str(PREPARED_ROOT), "present": False,
                "detail": str(exc)}


def downloads():
    pins = json.loads((ASSETS / "specs" / "source.json").read_text())
    resources = json.loads((ASSETS / "specs" / "resources.lock.json").read_text())
    encoding = resources["files"][-1]
    return [{"kind": "hf-dataset", "name": "MemoryAgentBench fixed data", "repo": "ai-hyz/MemoryAgentBench",
             "version": REVISION, "dst": "raw", "files": list(pins["files"]),
             "sha256": {name: item["sha256"] for name, item in pins["files"].items()},
             "size": str(sum(item["bytes"] for item in pins["files"].values())) + " bytes",
             "note": "Fixed official data; prepare.py validates all bytes and produces isolated service/gold artifacts. Adapter offers local and model-judge primary scoring. No study/eval division."},
            {"kind": "http", "name": "MemoryAgentBench English sentence tables archive",
             "version": resources["nltk_data_revision"], "dst": "resources/downloads/punkt_tab.zip",
             "url": resources["nltk_archive_url"], "sha256": resources["nltk_archive_sha256"],
             "size": "pinned punkt_tab archive; extract only four English tables",
             "note": "Run prepare_resources with --archive to extract and verify the four locked English tables offline."},
            {"kind": "http", "name": "MemoryAgentBench o200k_base encoding",
             "version": resources["tiktoken_version"], "dst": encoding["path"],
             "url": encoding["source_url"], "sha256": encoding["sha256"],
             "size": str(encoding["bytes"]) + " bytes",
             "note": "The fixed encoding is loaded locally after checksum verification, without tokenizer downloads."}]


def aggregate(records, selected_task_ids=None):
    """Report per-source question means; missing selected tasks remain visible."""
    from data.datasets.memoryagentbench.lib.reporting import summarize
    ids = list(_rows()) if selected_task_ids is None else selected_task_ids
    return summarize(records, reporting_manifest(ids))
