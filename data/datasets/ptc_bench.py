"""PTC-Bench's official task verifiers behind an isolated simulated-tool relay."""
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import subprocess

from harnessgrad.paths import dataset_root

KIND = "dataset"
VERIFIER_KIND = "command"
OFFICIAL_EVAL = None
ORACLE_SOURCE = "authored"
ORACLE_VALIDATED_BY = "pinned upstream PTC-Bench task verifiers"
ORACLE_COVERAGE = "three authored positive controls, one in each official evaluation partition"
SPLIT = None
SPLIT_SOURCE = "none"
REVISION = "fa4462e5ad1ea3cde2011aaf6396de5d76da4489"
ARCHIVE_SHA256 = "6121aabf40f21c544d4cc3fce98c62afe0c2f3a38ef62ed7909441535f63e9ba"
CATALOG_SHA256 = "ed00d6db2f473d7a97e241e2c0cb3dfb074c4f680116965a739460a48c6c4671"
METRIC = "ptc_bench.official_task_pass"
DATASET_ROOT = dataset_root("ptc_bench")
ASSETS = Path(__file__).resolve().with_suffix("")
TRANSPORT = ASSETS.parents[2] / "eval/service/tool_transport.py"
GOAL = """Complete the PTC-Bench request using the simulated APIs.
Run `python3 ptc.py status` to read the current user request.
Run `python3 ptc.py tools` to list the documented tools.
Use `python3 ptc.py call TOOL '{"argument":"value"}'` to call a tool.
You may also import `ptc` in Python under the task directory and use
`ptc.call(name, **arguments)` for local programmatic tool orchestration.
Submit the current response with `python3 ptc.py answer 'RESPONSE'`.
A follow-up, when present, is only revealed after the preceding response.
Continue using the same simulated state and submit the response to each turn.
The last response may also be written to answer.txt after its request is revealed.
The scoring service uses tool effects and the responses you submitted.
"""


@lru_cache(maxsize=1)
def catalog():
    raw = (DATASET_ROOT / "prepared/catalog.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("PTC-Bench prepared catalogue digest mismatch")
    manifest = json.loads(raw)
    return {item["task_id"]: item for item in manifest["rows"]}


def control_task(task_id):
    if task_id not in catalog():
        raise KeyError(task_id)
    return {"task_id": task_id, "goal": GOAL, "inputs": []}


def load():
    return [control_task(task_id) for task_id in catalog()], {task_id: METRIC for task_id in catalog()}


def _runtime():
    from harnessgrad.datasets.runtime import resolve_execution
    return resolve_execution("ptc_bench")


def _case(row):
    return (DATASET_ROOT / "prepared" / row["case"]).resolve(strict=True)


def _bundle():
    return (DATASET_ROOT / "prepared/bundle").resolve(strict=True)


def _setup(task_id, row):
    from harnessgrad.datasets.runtime import bind
    runtime = _runtime()
    case, bundle = _case(row), _bundle()
    return {
        "files": [{"path": "ptc.py", "from": str(ASSETS / "cli/ptc.py")},
                  {"path": "ptc_transport.py", "from": str(TRANSPORT)}],
        "tool_relay": {
            "program": str(ASSETS / "relay.py"), "python": runtime["python"],
            "python_runtime": bind(runtime, "service"),
            "read_paths": [str(ASSETS), str(TRANSPORT), str(bundle), str(case)],
            "content_flag": "--case-spec", "filename": "case-spec.json",
            "content": json.dumps({"case": str(case), "sha256": row["sha256"], "bundle": str(bundle)}),
            "record": {"path": "ptc_trusted", "max_bytes": 32 * 1024 * 1024},
            "max_calls": 4096, "max_line_bytes": 8 * 1024 * 1024,
        },
    }


def _verifier(task_id, row):
    from harnessgrad.datasets.runtime import bind
    runtime = _runtime()
    return {
        "kind": "command",
        "python_runtime": bind(runtime, "verifier"),
        "argv": [runtime["python"], "-I", "-B", "ptc_check/check.py",
                 "--task-id", task_id, "--case-sha256", row["sha256"]],
        "runtime_paths": list(runtime["runtime_paths"]),
        "inputs": [{"path": "ptc_check", "from": str(ASSETS), "immutable": True},
                   {"path": "ptc_private_case.json", "from": str(_case(row))},
                   {"path": "ptc_bundle", "from": str(_bundle()), "immutable": True}],
        "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
        "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True},
        "trial_evidence": {"version": 1},
    }


class _Tasks(Mapping):
    def __init__(self, declaration):
        self.declaration = declaration

    def __iter__(self):
        return iter(catalog())

    def __len__(self):
        return len(catalog())

    def __getitem__(self, task_id):
        return self.declaration(task_id, catalog()[task_id])


ENV = _Tasks(lambda task_id, row: {"kind": "files", "network": "none"})
SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verifier)


def __getattr__(name):
    if name != "SPLIT_GROUPS":
        raise AttributeError(name)
    return {
        task_id: hashlib.sha256(re.sub(r"^ptc_(?:intermediate_|mt_)?", "",
                                       task_id.rsplit("/", 1)[-1]).encode()).hexdigest()
        for task_id in catalog()
    }


def downloads():
    return [{
        "kind": "http", "name": "Official I-PTC source and PTC-Bench evaluation partitions",
        "version": REVISION, "url": f"https://codeload.github.com/HuanzhiMao/I-PTC/tar.gz/{REVISION}",
        "sha256": ARCHIVE_SHA256, "size": "1,335,401 byte archive", "dst": "source.tar.gz",
        "note": "Main, error-recovery and multi-turn combined are 240 evaluation tasks. "
                "Prepare the private cases and a standard-library-only Python 3.12 execution runtime.",
    }]


def provision_status():
    """Check source and all cases without starting a simulator episode or model."""
    try:
        runtime = _runtime()
        catalog.cache_clear()
        rows = catalog()
        for row in rows.values():
            if hashlib.sha256(_case(row).read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("PTC-Bench prepared case digest mismatch")
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
                 "from source import bootstrap; bootstrap(sys.argv[2]); "
                 "from ptc_bench.eval_checker.verifiers import verify; assert callable(verify)")
        subprocess.run([runtime["python"], "-I", "-B", "-c", probe, str(ASSETS), str(_bundle())],
                       check=True, capture_output=True, timeout=30, env={})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"present": False, "detail": str(exc)}
    return {"present": True, "tasks": len(rows), "root": str(DATASET_ROOT),
            "runtime_id": runtime["runtime_id"]}
