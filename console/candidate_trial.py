"""Candidate trial request controller with the existing double reservation contract."""
from __future__ import annotations
import json
import os
import pathlib
import subprocess
import sys
if __package__:
    from .candidates import CANDIDATE_NAME
else:
    from candidates import CANDIDATE_NAME

def reserve(runs, run_id):
    # Walk from / with no symlink following. The administrator must own and
    # protect this existing root and its ancestors from untrusted writers.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = work_fd = None
    reserved = False
    identity = None
    try:
        root_fd = os.open("/", flags)
        for part in runs.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = next_fd
        try:
            os.mkdir("work", 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        work_fd = os.open("work", flags, dir_fd=root_fd)
        os.mkdir(run_id, 0o700, dir_fd=root_fd)
        reserved = True
        identity = os.stat(run_id, dir_fd=root_fd, follow_symlinks=False)
        os.mkdir(run_id, 0o700, dir_fd=work_fd)
    except FileExistsError:
        # Only remove our still-empty run reservation before handoff; never
        # remove a work reservation or anything created by the driver.
        if reserved:
            try:
                current = os.stat(run_id, dir_fd=root_fd, follow_symlinks=False)
                if identity and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.rmdir(run_id, dir_fd=root_fd)
            except OSError:
                pass
        return 409, {"error": "trial-run-exists", "run_id": run_id}
    except OSError:
        if reserved:
            try:
                current = os.stat(run_id, dir_fd=root_fd, follow_symlinks=False)
                if identity and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.rmdir(run_id, dir_fd=root_fd)
            except OSError:
                pass
        return 503, {"error": "unsafe-trial-root"}
    finally:
        if work_fd is not None:
            os.close(work_fd)
        if root_fd is not None:
            os.close(root_fd)
    return None


def trial(name: str, dataset: str, run_id: str = "", *, candidates_root, platform_root, environment, run_driver, parse_json) -> tuple[int, dict]:
    """Run round zero over the selected training side; task count is dataset-dependent."""
    if (not isinstance(name, str) or name in (".", "..")
            or not CANDIDATE_NAME.fullmatch(name)):
        return 404, {"error": "candidate-not-found", "detail": str(name)}
    target = candidates_root / name
    if not (target / "harness.json").is_file():
        return 404, {"error": "candidate-not-found", "detail": name}
    run_id = run_id or f"manual-{name[:24]}-{os.urandom(8).hex()}"
    if (not isinstance(run_id, str) or run_id in (".", "..", "work")
            or not CANDIDATE_NAME.fullmatch(run_id)):
        return 400, {"error": "bad-run-id"}
    configured = environment.get("HG_CONSOLE_TRIAL_RUNS")
    if not configured or not pathlib.Path(configured).is_absolute():
        return 503, {"error": "trial-root-not-configured"}
    runs = pathlib.Path(configured)
    if ".." in runs.parts:
        return 503, {"error": "unsafe-trial-root"}
    platform = platform_root.resolve()
    if runs == platform or runs in platform.parents or platform in runs.parents:
        return 503, {"error": "unsafe-trial-root"}

    problem = reserve(runs, run_id)
    if problem:
        return problem
    argv = [sys.executable, str(platform_root / "driver.py"), "--harness-path", str(target),
            "--dataset", str(dataset), "--sampling", "all", "--rounds", "0",
            "--run-id", run_id, "--runs-root", str(runs), "--work-root", str(runs / "work")]
    try:
        done = run_driver(argv, cwd=str(platform_root), capture_output=True, text=True,
                              timeout=1800, env=dict(environment))
    except subprocess.TimeoutExpired:
        return 504, {"error": "trial-timeout", "run_id": run_id}
    except OSError:
        # Preserve both identities even if launching the driver fails.
        return 503, {"error": "trial-launch-failed", "run_id": run_id}
    curve = runs / run_id / "curve.jsonl"
    score = None
    if curve.is_file():
        first = curve.read_text(encoding="utf-8").splitlines()
        if first:
            score = parse_json(first[0]).get("score")
    return (200 if done.returncode == 0 else 422), {
        "run_id": run_id, "exit": done.returncode, "score": score,
        "stdout_tail": (done.stdout or "")[-1200:], "stderr_tail": (done.stderr or "")[-1200:]}
