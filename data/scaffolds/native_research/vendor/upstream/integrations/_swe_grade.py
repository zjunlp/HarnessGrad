"""Trusted subprocess seam to SWE-bench's pinned native script and scorer.

Upstream: SWE-bench/SWE-bench at 02e7a74ffd0b707aab73d203fe87bdc7c76afc8e.
Only host-owned inputs enter this process; candidate code runs in another container.
"""

import argparse
import contextlib
import json
import os
import sys
from importlib.metadata import distribution
from pathlib import Path

REVISION = "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e"


def check():
    try:
        origin = json.loads(
            distribution("swebench").read_text("direct_url.json") or "{}"
        )
    except Exception:
        raise ValueError(
            "Install flashrsi-lite[all] for official evaluation"
        ) from None
    if origin.get("vcs_info", {}).get("commit_id") != REVISION:
        raise ValueError(f"Official SWE-bench evaluator requires revision {REVISION}")


def evaluate(action, root):
    check()
    from swebench.harness.constants import APPLY_PATCH_FAIL
    from swebench.harness.grading import get_eval_report
    from swebench.harness.utils import make_test_spec

    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    spec = make_test_spec(request["row"])
    if action == "prepare":
        from swebench.harness.run_evaluation import GIT_APPLY_CMDS

        # This file executes in Linux even when the host uses CRLF.
        (root / "eval.sh").write_text(spec.eval_script, encoding="utf-8", newline="\n")
        return {"apply_patch_fail": APPLY_PATCH_FAIL, "apply_commands": GIT_APPLY_CMDS}
    report = get_eval_report(
        test_spec=spec,
        prediction={
            "instance_id": spec.instance_id,
            "model_name_or_path": "flashrsi",
            "model_patch": (root / "model.patch").read_text(encoding="utf-8"),
        },
        test_log_path=root / "test_output.txt",
        include_tests_status=True,
    )[spec.instance_id]
    if type(report.get("resolved")) is not bool:
        raise ValueError("Invalid official resolved result")
    if report.get("infra_failure") or report.get("infrastructure_error"):
        raise RuntimeError(
            "Official SWE-bench evaluator reported infrastructure failure"
        )
    counts = {
        group: {
            "passed": len(
                report.get("tests_status", {}).get(group, {}).get("success", [])
            ),
            "total": len(request["row"][group]),
        }
        for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
    }
    return {
        "resolved": report["resolved"],
        "patch_successfully_applied": report["patch_successfully_applied"],
        "tests": counts,
    }


def main(action, root):
    try:
        # Hidden test names and expected answers must not enter model feedback.
        with open(os.devnull, "w") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                result = evaluate(action, root)
    except Exception as exc:
        print(
            f"Official SWE-bench evaluator failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "grade"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    raise SystemExit(main(args.action, args.root))
