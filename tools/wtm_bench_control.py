#!/usr/bin/env python3
"""Exercise released WTM-Bench workbook controls through the sandboxed runner."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.datasets import wtm_bench as adapter
from eval.run.runner import run_one

CASES = {"566": 1.0, "1322": 1.0, "1334": 1.0, "2949": 1.0,
         "2939": 1.0, "2183": 0.8, "2284": 0.8}

ACTOR = """from pathlib import Path
import shutil

assert not Path('_wtm_expected.xlsx').exists()
assert not Path('_wtm_initial.xlsx').exists()
assert not Path('_wtm_scorer.py').exists()
"""


def run_control(case, mode):
    task_id = f"wtm_bench/Backward_EnronFuse.{case}"
    task = next(task for task in adapter.load()[0] if task["task_id"] == task_id)
    setup = adapter.SETUP[task_id]
    verifier = adapter.VERIFY[task_id]
    if mode == "oracle":
        # Only this test actor gets this extra, explicitly staged released reference.
        reference = next(entry["from"] for entry in verifier["inputs"]
                         if entry["path"] == "_wtm_expected.xlsx")
        setup["files"].append({"path": "control-reference.xlsx", "from": reference})
    with tempfile.TemporaryDirectory(prefix="hg-wtm-control-") as temp:
        harness = Path(temp)
        source = {"oracle": "control-reference.xlsx", "noop": "input.xlsx"}.get(mode)
        edit = f"shutil.copyfile({source!r}, 'output.xlsx')\n" if source else ""
        (harness / "agent.py").write_text(ACTOR + edit, encoding="utf-8")
        (harness / "harness.json").write_text(json.dumps({
            "name": "wtm-control", "version": "1", "entrypoint": "agent.py",
            "backend": "cli", "env_kinds": ["files"], "install": None,
            "runtime_paths": [], "role": "control",
        }), encoding="utf-8")
        previous = os.environ.get("HARNESSGRAD_WORK_ROOT")
        os.environ["HARNESSGRAD_WORK_ROOT"] = temp
        try:
            result = run_one(harness, task, timeout_s=60, sandbox=True, setup=setup,
                             verifier=verifier, env=adapter.ENV[task_id], run_id="wtm-bench-control",
                             trial_id="0", run_seed="0")
        finally:
            if previous is None:
                os.environ.pop("HARNESSGRAD_WORK_ROOT", None)
            else:
                os.environ["HARNESSGRAD_WORK_ROOT"] = previous
    verdict = result.get("verdict") or {}
    evidence = verdict.get("evidence") or {}
    want_score = CASES[case] if mode == "oracle" else 0
    want_status = "missing_workbook" if mode == "missing" else "graded_workbook"
    if (result.get("exit_code") != 0 or verdict.get("unmeasured")
            or verdict.get("score") != want_score or evidence.get("measured") is not True
            or evidence.get("metric") != adapter.METRIC or evidence.get("submission") != want_status):
        details = {key: result.get(key) for key in ("exit_code", "invalid", "error", "stderr")}
        raise RuntimeError(f"{task_id} {mode} expected {want_score}: {details} {verdict}")
    return {"task_id": task_id, "mode": mode, "score": verdict["score"],
            "raw_grade": evidence["raw_grade"], "submission": evidence["submission"],
            "scorer_input_verified": evidence["scorer_input"]["verified"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--oracle", action="store_true")
    group.add_argument("--noop", action="store_true", help="submit the unchanged initial workbook")
    group.add_argument("--missing", action="store_true")
    parser.add_argument("--case", choices=tuple(CASES), default="566")
    options = parser.parse_args()
    mode = "oracle" if options.oracle else ("noop" if options.noop else "missing")
    print(json.dumps(run_control(options.case, mode)))
