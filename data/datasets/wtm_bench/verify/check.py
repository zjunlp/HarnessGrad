#!/usr/bin/env python3
"""Score a submitted workbook with the untouched released WTM-Bench grader."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from zipfile import BadZipFile, ZipFile

HERE = Path(__file__).resolve().parent
METRIC = "wtm_bench.workbook_cell_grade"
INPUT_NAMES = ("_wtm_check.py", "_wtm_scorer.py", "_wtm_initial.xlsx", "_wtm_expected.xlsx")


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stage_submission(target):
    output = Path.cwd() / "output.xlsx"
    try:
        mode = output.lstat().st_mode
    except FileNotFoundError:
        return "missing_workbook"
    if not stat.S_ISREG(mode):
        return "invalid_workbook"
    fd = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "invalid_workbook"
        with target.open("wb") as staged:
            shutil.copyfileobj(source, staged)
    try:
        with ZipFile(target) as workbook:
            workbook.getinfo("[Content_Types].xml")
    except (BadZipFile, KeyError):
        return "invalid_workbook"
    return None


def workbook_grade(scorer, submission, expected, initial):
    result = scorer.compute_reward(
        generated_path=str(submission), ground_truth_path=str(expected),
        initial_path=str(initial), mode="values_only",
    )
    raw = result.total_reward
    if not math.isfinite(raw) or not 0 <= raw <= 1:
        raise ValueError("invalid WTM-Bench workbook grade")
    # Match benchmark.py's emitted cell_grade field.
    return round(min(1.0, raw), 4), raw


def verify(paths, receipt, evidence_path):
    evidence = {"measured": False, "metric": METRIC, "scorer_input": receipt}
    try:
        with tempfile.TemporaryDirectory(prefix="hg-wtm-grade-") as temp:
            submission = Path(temp) / "output.xlsx"
            status = stage_submission(submission)
            if status is None:
                # Formula compilation and parser diagnostics can include workbook data.
                with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                    scorer = load_source("_wtm_official", paths["_wtm_scorer.py"])
                    score, raw = workbook_grade(scorer, submission, paths["_wtm_expected.xlsx"],
                                                paths["_wtm_initial.xlsx"])
                status = "graded_workbook"
            else:
                score = raw = 0.0
        Path("verifier/reward.txt").write_text(str(score), encoding="utf-8")
        evidence.update(measured=True, score=score, raw_grade=raw, submission=status)
    except Exception as exc:
        evidence["error"] = type(exc).__name__
        print(f"WTM-Bench workbook scoring failed ({type(exc).__name__})", file=sys.stderr)
    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if evidence["measured"] else 3


def main():
    helper = load_source("_wtm_scorer_input", HERE / "data/scaffolds/scorer_input.py")
    parser = argparse.ArgumentParser(description=__doc__)
    helper.add_arguments(parser)
    args = parser.parse_args()
    if not (args.task_id and args.scorer_input_sha256 and args.scorer_input_files):
        parser.error("WTM-Bench requires bound scorer inputs")
    args.evidence_file = "verifier/evidence.json"
    paths = {name: HERE / name for name in INPUT_NAMES}
    return helper.run_bound(args, paths, lambda copies, receipt:
                            verify(copies, receipt, Path(args.evidence_file)))


if __name__ == "__main__":
    raise SystemExit(main())
