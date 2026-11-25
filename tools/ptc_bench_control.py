#!/usr/bin/env python3
"""Exercise each PTC-Bench partition through the sandboxed platform runner."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.datasets import ptc_bench as adapter
from eval.run.runner import run_one

CASES = {
    "main": "ptc_yelp_highest_rated_filtered_0",
    "error_recovery": "ptc_intermediate_yelp_recent_drift_0",
    "multi_turn": "ptc_mt_yelp_top_k_refinement_0",
}


def run_control(partition, mode):
    task_id = f"ptc_bench/{partition}/{CASES[partition]}"
    task = adapter.control_task(task_id)
    setup = adapter.SETUP[task_id]
    setup["files"].append({"path": ".ptc-control", "content": mode})
    with tempfile.TemporaryDirectory(prefix="hg-ptc-control-") as temp:
        root = Path(temp)
        harness = root / "harness"
        harness.mkdir()
        shutil.copyfile(adapter.ASSETS / "control_actor.py", harness / "agent.py")
        (harness / "harness.json").write_text(json.dumps({
            "name": "ptc-control", "version": "1", "entrypoint": "agent.py",
            "backend": "cli", "env_kinds": ["files"], "install": None,
            "runtime_paths": [], "role": "control",
        }))
        previous = os.environ.get("HARNESSGRAD_WORK_ROOT")
        os.environ["HARNESSGRAD_WORK_ROOT"] = temp
        try:
            result = run_one(harness, task, timeout_s=60, sandbox=True, setup=setup,
                             verifier=adapter.VERIFY[task_id], verify_timeout_s=60,
                             env=adapter.ENV[task_id], run_id="ptc-bench-control", trial_id="0", run_seed="0")
        finally:
            if previous is None:
                os.environ.pop("HARNESSGRAD_WORK_ROOT", None)
            else:
                os.environ["HARNESSGRAD_WORK_ROOT"] = previous
    verdict = result.get("verdict") or {}
    evidence = verdict.get("evidence") or {}
    if (result.get("exit_code") != 0 or verdict.get("unmeasured")
            or evidence.get("measured") is not True):
        details = {key: result.get(key) for key in ("exit_code", "invalid", "error", "stderr")}
        raise RuntimeError(f"Unmeasured {partition} {mode} control: {details} {verdict}")
    expected = 1 if mode == "oracle" else 0
    if verdict["score"] != expected or evidence["official"]["passed"] != bool(expected):
        raise RuntimeError(f"{partition} {mode} expected {expected}, observed {verdict}")
    if mode == "oracle" and partition == "error_recovery" and evidence["api_errors"] != 1:
        raise RuntimeError("the official scheduled failure did not activate")
    if mode == "oracle" and partition == "multi_turn" and evidence["final_answer_channel"] != "artifact":
        raise RuntimeError("the final-turn answer artifact was not consumed")
    return {"partition": partition, "mode": mode, "score": verdict["score"], "evidence": evidence}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--oracle", action="store_true")
    group.add_argument("--noop", action="store_true")
    parser.add_argument("--platform", action="store_true", help="retained alias, controls always use run_one")
    parser.add_argument("--partition", choices=tuple(CASES))
    options = parser.parse_args()
    partitions = (options.partition,) if options.partition else CASES
    receipts = [run_control(partition, "oracle" if options.oracle else "noop") for partition in partitions]
    print(json.dumps({"score": int(options.oracle), "controls": receipts}))
