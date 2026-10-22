"""Test-only parent observer. Copy outside the platform before launching subjects.

The subject never supplies mount observations: bubblewrap reports its host PID over
a private inherited FD, and this parent reads that PID's kernel namespace directly
at a trusted post-setup, pre-exec handshake. The receipt directory is not mounted into
the subject. This is local test evidence, not an admission backend or signed receipt.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid


def observe(command: list[str], output: Path, *, timeout: float = 30,
            inspect_paths: tuple[str, ...] = ()) -> int:
    output.mkdir(parents=True, exist_ok=True)
    info_r, info_w = os.pipe()
    split = command.index("--")
    # --block-fd pauses *before* mount setup on bubblewrap 0.6.1. Instead,
    # a trusted inline trampoline handshakes after setup, before subject exec.
    gate = ("import os,sys; print('HG_B7_READY',flush=True); "
            "assert sys.stdin.buffer.read(1)==b'1'; os.execvp(sys.argv[1],sys.argv[1:])")
    actual = [command[0], "--info-fd", str(info_w), *command[1:split + 1],
              sys.executable, "-c", gate, *command[split + 1:]]
    proc = None
    receipt = {"command": command, "actual_command": actual, "cwd": os.getcwd(),
               "observer_pid": os.getpid(), "observation_source": "parent /proc; before subject exec"}
    try:
        proc = subprocess.Popen(actual, pass_fds=(info_w,), start_new_session=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        os.close(info_w)
        info_w = None
        payload = b""
        deadline = time.monotonic() + timeout
        while True:
            ready, _, _ = select.select([info_r], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError("bubblewrap did not supply independent PID information")
            chunk = os.read(info_r, 65536)
            if not chunk:
                raise RuntimeError(f"bubblewrap info pipe closed: {payload!r}")
            payload += chunk
            try:
                info = json.loads(payload)
                break
            except json.JSONDecodeError:
                continue
        pid = info["child-pid"]
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready or proc.stdout.readline() != b"HG_B7_READY\n":
            raise RuntimeError("trusted trampoline did not reach post-setup handshake")
        proc_root = Path(f"/proc/{pid}")
        receipt.update(bwrap_info=info, child_pid=pid,
                       mountinfo=(proc_root / "mountinfo").read_text(),
                       status=(proc_root / "status").read_text(),
                       namespaces={name: os.readlink(proc_root / "ns" / name)
                                   for name in ("mnt", "pid", "net", "user")},
                       observer_namespaces={name: os.readlink(f"/proc/self/ns/{name}")
                                            for name in ("mnt", "pid", "net", "user")})
        # Read selected source/library identities through the paused process's
        # actual root. These bytes and absence observations come from the parent,
        # independently of anything the eventual subject prints.
        views = {}
        targets = list(inspect_paths)
        for index, arg in enumerate(command):
            if arg not in ("--ro-bind", "--bind", "--tmpfs"):
                continue
            targets.append(command[index + (1 if arg == "--tmpfs" else 2)])
        for target in targets:
            for name in ("__init__.py", "libfrotz.so", "game_info.py", "agent.py", "run.py",
                         "eval/containers/sandbox.py", "driver.py"):
                absolute = str(Path(target) / name)
                path = proc_root / "root" / absolute.lstrip("/")
                if path.is_file():
                    stat = path.stat()
                    views[absolute] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "device": stat.st_dev, "inode": stat.st_ino}
        receipt["parent_source_views"] = views
        receipt["inspect_paths"] = list(inspect_paths)
        receipt["receipt_directory_visible"] = (
            proc_root / "root" / str(output.resolve()).lstrip("/")).exists()
        stdout, _ = proc.communicate(b"1", timeout=timeout)
        sys.stdout.buffer.write(stdout)
        sys.stdout.buffer.flush()
        receipt["exit_code"] = proc.returncode
        return receipt["exit_code"]
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        for fd in (info_r, info_w):
            if fd is not None:
                os.close(fd)
        child = Path(f"/proc/{receipt.get('child_pid', -1)}")
        # The monitor can exit just before the PID-namespace init is reaped.
        deadline = time.monotonic() + 2
        while child.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        receipt["cleanup"] = {"bwrap_reaped": proc is not None and proc.poll() is not None,
                              "child_absent": not child.exists()}
        (output / f"namespace-{uuid.uuid4().hex}.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n")
