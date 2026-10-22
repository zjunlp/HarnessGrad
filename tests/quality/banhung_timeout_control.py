"""Local process supervisor for the timeout regression; no model or dataset."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    mode, root_arg, behavior = sys.argv[1:]
    root = Path(root_arg)
    root.mkdir(parents=True)
    # Reap our own orphaned control children on Linux rather than leave zombies
    # for the machine's init. Only this isolated supervisor becomes a subreaper.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    os.environ.update(TMPDIR=str(root), HG_AGENT_BACKEND="mock")
    import tempfile
    tempfile.tempdir = None
    from eval.containers import process
    from eval.run import runner

    repo = root / "work/run/workspace"
    repo.mkdir(parents=True)
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(root / "work")
    (repo / "harness.json").write_text(json.dumps({
        "entrypoint": "agent.py", "name": "synthetic", "version": "1"}))
    token = "banhung-" + uuid.uuid4().hex
    body = ("import argparse, os, pathlib, signal, subprocess, sys, time\n"
            "ap=argparse.ArgumentParser(); ap.add_argument('--task'); ap.add_argument('--workdir'); a=ap.parse_args()\n"
            "work=pathlib.Path(a.workdir)\n"
            "(work/'answer.txt').write_text('completed')\n"
            "(work/'trace.jsonl').write_text('partial trace\\n')\n")
    if behavior == "kill":
        body += "os.kill(os.getpid(), signal.SIGKILL)\n"
    elif behavior == "kill-child":
        body += (f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', {token!r}], "
                 "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                 "os.kill(os.getpid(), signal.SIGKILL)\n")
    elif behavior == "term":
        body += "os.kill(os.getpid(), signal.SIGTERM)\n"
    elif behavior == "crash":
        body += "sys.exit(1)\n"
    elif behavior == "zero":
        body += "(work/'answer.txt').write_text('wrong')\n"
    elif behavior == "kill-zero":
        # An outside kill with no passing artifact: nothing was graded, so this is
        # the one ending that must not become a score (`runner._stopped_by_signals`).
        body += ("(work/'answer.txt').write_text('wrong')\n"
                 "os.kill(os.getpid(), signal.SIGKILL)\n")
    else:
        # Inherit captured stdout AND stderr. The parent optionally exits first;
        # this is not just a sleep in the directly launched process.
        body += ("child = subprocess.Popen([sys.executable, '-c',"
                 f" 'import time; time.sleep(30)', {token!r}])\n")
        if behavior != "orphan":
            body += "time.sleep(30)\n"
    (repo / "agent.py").write_text(body)

    groups = []
    real_popen = process.subprocess.Popen

    def launch(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        groups.append(proc.pid)
        return proc

    process.subprocess.Popen = launch
    cache = {}
    start = time.monotonic()
    task = {"task_id": "synthetic", "goal": "synthetic"}
    verifier = {"kind": "answer", "expected": "completed"}
    timeout = 0.5
    try:
        result = runner.run_one(repo, task, sandbox=mode == "sandbox",
                                timeout_s=timeout, verifier=verifier)
        elapsed = time.monotonic() - start
        # Persistence uses the very result just observed, without a second launch.
        runner.run_one = lambda *a, **k: result
        evaluated = runner._evaluate(repo, [task], {}, cache=cache,
                                     progress_dir=root / "records", jobs=1)
    finally:
        process.subprocess.Popen = real_popen

    def reap():
        until = time.monotonic() + 1
        while time.monotonic() < until:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if not pid:
                time.sleep(0.01)

    reap()
    probes = []
    for group in groups:
        cmd = ["pgrep", "-a", "-g", str(group)]
        probe = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        probes.append({"command": cmd, "returncode": probe.returncode, "stdout": probe.stdout})
    token_cmd = ["pgrep", "-af", token]
    token_probe = subprocess.run(token_cmd, capture_output=True, text=True, timeout=2)
    progress = json.loads((root / "records/progress/round-0/synthetic.json").read_text())
    report = {"mode": mode, "behavior": behavior, "timeout_s": timeout,
              "elapsed_s": round(elapsed, 3), "result": result,
              "per_task": evaluated["per_task"], "invalid": evaluated["invalid"],
              "cache": cache, "progress": progress, "groups": probes,
              "token_probe": {"command": token_cmd, "returncode": token_probe.returncode,
                              "stdout": token_probe.stdout},
              "task_trees": [str(p) for p in root.glob("hg-task-*")]}
    # Clean only our own control groups if a regression left them alive.
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    reap()
    print(json.dumps(report))


if __name__ == "__main__":
    main()
