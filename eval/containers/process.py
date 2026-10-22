"""Captured native launches with one deadline and process-group cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def termination_signal(code: int) -> signal.Signals | None:
    """Direct fatal signals and conventional shell/bwrap termination exits."""
    return next((sig for sig in (signal.SIGKILL, signal.SIGTERM)
                 if code in (-sig, 128 + sig)), None)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(argv: list[str], *, timeout_s: float, cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run in a new session; on timeout/interruption kill its whole process group.

    Keep communicate's original deadline and partial TimeoutExpired output. Do not
    communicate again after killing: an escaped session could still hold a pipe
    open. Popen's context closes our pipes and reaps the direct child, without an
    unbounded drain. Descendants that deliberately leave the group require a
    stronger lifecycle boundary (for example a PID namespace).

    The defect this closes is a **leaked descendant**, not a hang. `subprocess.run`
    reaps only the direct child, so a daemon the harness started outlived the task -
    measured: a `time.sleep(30)` grandchild was still alive after `run_one` returned
    0.464 s after a 0.3 s timeout (`reports/w-banhung-evidence/reproduction-wait-before.log`).
    `start_new_session` plus `killpg` is what removes it.
    """
    with subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          errors="replace", start_new_session=True, cwd=cwd, env=env) as proc:
        try:
            stdout, stderr = proc.communicate(input_text, timeout=timeout_s)
        except BaseException:
            # Even if the direct child already exited, its orphaned descendants
            # can still belong to this group and retain the captured pipe ends.
            _kill_group(proc.pid)
            raise
        if termination_signal(proc.returncode) is not None:
            # A watchdog may have killed the launcher while descendants that
            # closed their pipes are still alive in its session.
            _kill_group(proc.pid)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
