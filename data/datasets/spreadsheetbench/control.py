#!/usr/bin/env python3
"""The SpreadsheetBench controls: does the check reach 1, and can it reach 0?

    python3 data/datasets/spreadsheetbench/control.py --oracle
    python3 data/datasets/spreadsheetbench/control.py --noop
    python3 data/datasets/spreadsheetbench/control.py --oracle --platform
    python3 data/datasets/spreadsheetbench/control.py --noop   --platform

The oracle is the dataset's own reference: the **golden workbook**. Copying it into the
workdir and naming it is what a harness that solved the task would leave behind, and it exercises
the comparison end to end including the recalculation step (the golden files ship with cached
values, so the copy may not need recalculating -- `recalculate` is only called for a result that
holds formulas).

By default the control runs the **first eval-side task whose golden workbook needs no
recalculation**, because LibreOffice costs a few seconds per episode and the control's runtime is
a price the whole suite pays. `--task-id` runs any task, and `--formula-task` picks one whose
result *does* need recalculating, which is the interesting case for the LibreOffice path.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import data.datasets.spreadsheetbench as sb  # noqa: E402


def _record(task_id: str | None, *, formula_task: bool) -> dict:
    records = sb._RECORDS
    if not records:
        raise SystemExit("no SpreadsheetBench tasks; provision them with "
                         "`python3 tools/datasets.py --fetch --dataset spreadsheetbench`")
    if task_id is None:
        # The whole pool when there is no declared division (2026-10-24), otherwise its
        # eval side: the control exercises what a run is scored on.
        split = sb.SPLIT
        pool = set(split["eval"]) if split else {r["task_id"] for r in records}
        candidates = [r for r in records if r["task_id"] in pool]
        if formula_task:
            # A task whose *initial* workbook holds formulas is one where the golden has
            # computed values, so the check's comparison is over recalculated cells.
            import openpyxl
            with_formulas = []
            for record in candidates:
                # A workbook openpyxl cannot even parse must not abort the *control*: measured
                # on `36764`, whose `init.xlsx` holds invalid XML. The check reports that as
                # `measured: false`; the control just does not pick it.
                try:
                    wb = openpyxl.load_workbook(filename=record["init_path"], data_only=False)
                except Exception:  # noqa: BLE001
                    continue
                if _has_formula(wb):
                    with_formulas.append(record)
            candidates = with_formulas or candidates
        task_id = candidates[0]["task_id"]
    for record in records:
        if record["task_id"] == task_id:
            return record
    raise SystemExit(f"unknown task id {task_id!r}")


def _has_formula(wb) -> bool:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    return True
    return False


def materialize(record: dict, workdir: Path) -> None:
    """What `SETUP` writes: the starting workbook and the CLI."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in sb.SETUP[record["task_id"]]["files"]:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    (workdir / "qa" / "bin" / "qa").chmod(0o755)


def solve(record: dict, workdir: Path) -> None:
    """The oracle: leave the reference workbook where the contract says to leave it."""
    shutil.copy2(record["golden_path"], workdir / sb.RESULT_WORKBOOK)
    # Inherit both streams so a failed submission reports the CLI's actual error.
    subprocess.run([sys.executable, "qa/bin/qa", "submit", sb.RESULT_WORKBOOK],
                   cwd=workdir, check=True)


def run_check(module, record: dict, workdir: Path) -> tuple[float, str]:
    """Run the check the way `eval/verification/verifier.py` does, re-materializing its inputs."""
    verifier = module.VERIFY[record["task_id"]]
    for entry in verifier.get("inputs") or []:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True, errors="replace")
    reward_path = workdir / module.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / module.EVIDENCE_REL
    evidence = (json.loads(evidence_path.read_text())
                if evidence_path.is_file() else {})
    detail = {
        "returncode": proc.returncode,
        "reward": reward,
        "measured": evidence.get("measured"),
        "recalculated": evidence.get("recalculated"),
        "why": evidence.get("why") or evidence.get("error"),
        "answer_position": evidence.get("answer_position"),
        "stderr": proc.stderr[-400:],
    }
    return reward, json.dumps(detail, indent=1, sort_keys=True)


def run_local(module, record: dict, *, oracle: bool, workdir: Path) -> int:
    materialize(record, workdir)
    if oracle:
        solve(record, workdir)
    reward, detail = run_check(module, record, workdir)
    print(f"task    {record['task_id']}  ({record['instruction_type']})")
    print(f"mode    {'oracle' if oracle else 'do-nothing'}")
    print(f"reward  {reward:g}")
    print(detail)
    expected = 1.0 if oracle else 0.0
    if reward != expected:
        print(f"CONTROL FAILED: expected reward {expected:g}, got {reward:g}",
              file=sys.stderr)
        return 1
    return 0


def run_platform(module, record: dict, *, oracle: bool, workdir: Path,
                 sandbox: bool = True) -> int:
    from eval.run.runner import run_one

    tid = record["task_id"]
    task = next(t for t in module.load()[0] if t["task_id"] == tid)
    setup = {"files": list(module.SETUP[tid]["files"])}
    if oracle:
        # The control harness copies this into place and names it -- see oracle_harness/.
        setup["files"].append({"path": ".oracle/result.xlsx",
                               "from": str(record["golden_path"])})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(Path(__file__).resolve().parent / "oracle_harness", harness,
                    dirs_exist_ok=True)
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    started = time.time()
    result = run_one(
        repo=harness,
        task=task,
        setup=setup,
        verifier=module.VERIFY[tid],
        env=module.ENV[tid],
        sandbox=sandbox,
        run_id="spreadsheetbench-control",
        run_seed="0",
    )
    verdict = result.get("verdict") or {}
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}   wall {time.time() - started:.1f}s")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    expected = 1.0 if oracle else 0.0
    score = verdict.get("score")
    if score is None:
        # No score is not a zero. The platform refused the task, the scorer failed, or
        # the run produced nothing -- each is unmeasured, and an unmeasured do-nothing
        # arm must never be counted as a passing control. Treating `None` as 0.0 here
        # did exactly that: it turned a refusal into "the noop arm behaved".
        print(f"CONTROL UNMEASURED: no score for {tid}", file=sys.stderr)
        return 2
    if float(score) != expected:
        print(f"CONTROL FAILED: expected score {expected:g}, got {score}",
              file=sys.stderr)
        return 1
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true")
    mode.add_argument("--noop", action="store_true")
    parser.add_argument("--platform", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--formula-task", action="store_true",
                        help="pick a task whose workbook needs recalculating")
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    record = _record(args.task_id, formula_task=args.formula_task)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="spreadsheetbench-control-"))
        cleanup = True
    started = time.time()
    try:
        if args.platform:
            return run_platform(sb, record, oracle=args.oracle, workdir=workdir,
                                sandbox=not args.no_sandbox)
        return run_local(sb, record, oracle=args.oracle, workdir=workdir)
    finally:
        print(f"elapsed {time.time() - started:.1f}s")
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
