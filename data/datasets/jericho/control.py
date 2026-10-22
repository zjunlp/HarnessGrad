#!/usr/bin/env python3
"""The Jericho controls: does the check actually reach 1, and actually reach 0?

    python3 data/datasets/jericho/control.py --oracle --task-id GAME
    python3 data/datasets/jericho/control.py --noop --task-id GAME
    python3 data/datasets/jericho/control.py --oracle --platform --task-id GAME
    python3 data/datasets/jericho/control.py --noop --platform --task-id GAME

A dataset whose oracle scores 0 has an unreachable check, and every harness score on it is
meaningless. A dataset whose do-nothing control scores 1 has a check that cannot fail. Both
failures are quiet -- a plausible number lands in the right place -- so both are run and both
are asserted.

The oracle is Jericho's own: `FrotzEnv.get_walkthrough()` at the game's official seed, which is
the reference solution the benchmark's `game_info.py` ships. Controls require an explicit
`--task-id` so that a run never implicitly selects an evaluation game.

A note on what "reward 1" means here. The reward is `score / get_max_score()`, so 1 means the
game was finished. Measured over this suite, the walkthroughs reach the maximum for most games
but **not all** -- a few stop short -- and when the oracle picks an eval-side game whose
walkthrough does not reach the maximum, this control will fail. That is the control doing its
job: it is reporting that the reference solution does not finish that game, which is a fact
about the benchmark rather than about the check. `--task-id` exists for that case, and the
suite's own walkthrough scores are recorded by `tests/quality/test_jericho_dataset.py`.
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
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import data.datasets.jericho as jericho  # noqa: E402
import jer_env  # noqa: E402


def _record(task_id: str | None) -> dict:
    if task_id is None:
        raise SystemExit("--task-id is required; controls must explicitly select approved games")
    records = jericho._RECORDS
    if not records:
        raise SystemExit("no Jericho games; provision them with "
                         "`python3 tools/datasets.py --fetch --dataset jericho`")
    for record in records:
        if record["task_id"] == task_id:
            return record
    raise SystemExit(f"unknown task id {task_id!r}")


def _copy_entry(entry: dict, workdir: Path) -> None:
    """One SETUP/VERIFY entry, the way the platform's `_materialize` does it."""
    target = workdir / entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if "content" in entry:
        target.write_text(entry["content"])
    else:
        shutil.copy2(entry["from"], target)
        if entry["path"] == "jer":
            target.chmod(0o755)


def materialize(record: dict, workdir: Path) -> None:
    """Put the task where `SETUP` would: the game identity, the ROM, the CLI, its library."""
    workdir.mkdir(parents=True, exist_ok=True)
    # --keep preserves the directory, not an earlier episode or a log symlink.
    (workdir / jericho.LOG_FILE).unlink(missing_ok=True)
    for entry in jericho.SETUP[record["task_id"]]["files"]:
        _copy_entry(entry, workdir)


def drive(workdir: Path, actions: list[str], *, timeout_s: float = 300) -> int:
    """Act through the CLI exactly as an agent would. Returns how many commands ran."""
    from eval.policy.runtime_policy import phase_timeout, stage_agent_runtime
    from eval.containers import sandbox

    workdir = workdir.resolve()
    control_root = workdir / ".control"
    control_root.mkdir(exist_ok=True)
    child_env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": "/tmp",
                 "TMPDIR": "/tmp", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                 "PYTHONDONTWRITEBYTECODE": "1"}
    deadline = time.monotonic() + phase_timeout(timeout_s)
    with tempfile.TemporaryDirectory(prefix="jericho-agent-runtime-") as runtime:
        package = stage_agent_runtime(jericho._env()["agent_runtime"], Path(runtime))
        for args in [*[['do', action] for action in actions], ['done']]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("Jericho agent phase", timeout_s)
            proc = sandbox.run([sys.executable, "jer", *args], cwd=workdir,
                               platform=ROOT, work_root=control_root, repo=control_root,
                               task_dir=workdir, env=child_env, timeout_s=remaining,
                               network="none", package_overlays=(package,))
            if proc.returncode != 0:
                raise RuntimeError(f"Jericho CLI exited {proc.returncode}: {proc.stderr[-400:]}")
    return len(actions)


def run_check(module, record: dict, workdir: Path) -> tuple[float, str]:
    """Run the check the way the platform's verifier does, re-materializing its inputs."""
    from eval.verification.verifier import _verify
    verifier = module.VERIFY[record["task_id"]]
    workdir = workdir.resolve()
    control_root = workdir / ".control"
    control_root.mkdir(exist_ok=True)
    # A standalone /tmp/jericho-control-* has grandparent '/', the verifier's
    # fallback work root. Binding that read-only covers the private /tmp tmpfs.
    # Use the same dedicated root as drive(), preserving a writable private /tmp.
    old_work_root = os.environ.get("HARNESSGRAD_WORK_ROOT")
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(control_root)
    try:
        # `task_id` is required: since 63606f42 the verifier refuses a spec whose `scorer_input`
        # binding does not match the selected task, and omitting it made this control report
        # reward -1 / unmeasured for a task the oracle had actually completed.
        verdict = _verify(workdir, verifier, "", sandbox=True,
                          task_id=record["task_id"],
                          timeout_s=module.VERIFY_TIMEOUT_S, env=module.DEFAULT_ENV)
    finally:
        if old_work_root is None:
            os.environ.pop("HARNESSGRAD_WORK_ROOT", None)
        else:
            os.environ["HARNESSGRAD_WORK_ROOT"] = old_work_root
    evidence = verdict.get("evidence") or {}
    reward = (float(verdict["score"]) if verdict.get("score") is not None
              and evidence.get("measured") is True and not verdict.get("unmeasured") else -1.0)
    detail = {
        "passed": verdict.get("passed"),
        "reward": reward,
        "score": evidence.get("score"),
        "max_score": evidence.get("max_score"),
        "moves": evidence.get("moves"),
        "measured": evidence.get("measured"),
        "why": evidence.get("why") or evidence.get("error"),
        "detail": verdict.get("detail"),
    }
    return reward, json.dumps(detail, indent=1, sort_keys=True)


def run_local(module, record: dict, *, oracle: bool, workdir: Path) -> int:
    materialize(record, workdir)
    if oracle:
        drive(workdir, record["walkthrough"], timeout_s=module.AGENT_TIMEOUT_S)
    reward, detail = run_check(module, record, workdir)
    print(f"task    {record['task_id']}  ({len(record['walkthrough'])} reference commands)")
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
    """The same episode through `eval.run.runner.run_one`, with no Docker and no model."""
    from eval.run.runner import run_one

    tid = record["task_id"]
    task = {"task_id": tid, "goal": module._goal(record), "inputs": []}
    setup = {"files": list(module.SETUP[tid]["files"])}
    if oracle:
        setup["files"].append({"path": ".oracle/actions.json",
                               "content": json.dumps(record["walkthrough"])})
    work_root = workdir / "work"
    harness = work_root / "oracle_harness"
    shutil.copytree(Path(__file__).resolve().parent / "oracle_harness", harness,
                    dirs_exist_ok=True)
    old_work_root = os.environ.get("HARNESSGRAD_WORK_ROOT")
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work_root)
    started = time.time()
    try:
        result = run_one(
            repo=harness, task=task, setup=setup, verifier=module.VERIFY[tid],
            env=module.ENV[tid], sandbox=sandbox,
            timeout_s=module.AGENT_TIMEOUT_S, verify_timeout_s=module.VERIFY_TIMEOUT_S,
            run_id="jericho-control", run_seed="0")
    finally:
        if old_work_root is None:
            os.environ.pop("HARNESSGRAD_WORK_ROOT", None)
        else:
            os.environ["HARNESSGRAD_WORK_ROOT"] = old_work_root
    verdict = result.get("verdict") or {}
    print(f"task     {tid}")
    print(f"mode     {'oracle' if oracle else 'do-nothing'} (platform run_one, "
          f"sandbox={sandbox})")
    print(f"exit     {result.get('exit_code')}   wall {time.time() - started:.1f}s")
    print(f"score    {verdict.get('score')}  passed {verdict.get('passed')}")
    print(f"detail   {verdict.get('detail')}")
    if verdict.get("evidence"):
        print(f"evidence {json.dumps(verdict['evidence'].get('why'), ensure_ascii=False)}")
    expected = 1.0 if oracle else 0.0
    if result.get("exit_code") != 0 or verdict.get("score") is None \
            or not (verdict.get("evidence") or {}).get("measured") \
            or float(verdict["score"]) != expected:
        print(f"CONTROL FAILED: expected score {expected:g}, got {verdict.get('score')}",
              file=sys.stderr)
        return 1
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--oracle", action="store_true",
                      help="replay the game's own walkthrough; expect reward 1")
    mode.add_argument("--noop", action="store_true", help="do nothing; expect 0")
    parser.add_argument("--platform", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    record = _record(args.task_id)
    if args.workdir:
        workdir = Path(args.workdir)
        if workdir.exists() and not args.keep:
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="jericho-control-"))
        cleanup = True
    started = time.time()
    try:
        if args.platform:
            return run_platform(jericho, record, oracle=args.oracle, workdir=workdir,
                                sandbox=not args.no_sandbox)
        return run_local(jericho, record, oracle=args.oracle, workdir=workdir)
    finally:
        print(f"elapsed {time.time() - started:.1f}s")
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
