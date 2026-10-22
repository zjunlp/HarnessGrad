
"""The last mile: a sandboxed `alf` reaches the host environment, and cannot read the game.

Measured before this test existed (2026-10-17): the same sandbox plan, run by hand, printed
"You are in the middle of a room. Looking quickly around you, you see a bed 1, a desk 2, ..."
while `cat game.tw-pddl` in the same directory said the file does not exist and the relay
receipt recorded one call. This test is that measurement, so the property cannot regress
silently.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from eval.containers import sandbox as SB
from eval.run import tool_relay as TR

PLATFORM = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed")
def test_a_sandboxed_alf_asks_the_host_and_cannot_read_the_game():
    alf = importlib.import_module("data.datasets.alfworld")
    setup = alf._setup(alf._RECORDS[0])

    root = Path(tempfile.mkdtemp(prefix="hg-relaywire-"))
    task = root / "task"
    task.mkdir()
    for entry in setup["files"]:
        (task / entry["path"]).write_text(Path(entry["from"]).read_text())
    state = root / "state"
    state.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(root)}
    relay = TR.start({"tool_relay": setup["tool_relay"]})
    try:
        plan = SB.command(["python3", "alf", "look"], platform=PLATFORM, work_root=root,
                          task_dir=task, env=env, repo=task, state_dirs=(state,),
                          tool_socket=relay.path)
        assert any("tool.sock" in arg for arg in plan), "the sandbox plan has no tool socket"
        proc = subprocess.run(plan, capture_output=True, text=True, timeout=300, cwd=task)
        assert proc.returncode == 0, proc.stderr[-800:]
        assert "Welcome to TextWorld" in proc.stdout or "You are in" in proc.stdout, proc.stdout[:400]
        assert "Traceback" not in proc.stderr

        # The same directory, from inside: the game is absent, not merely unreadable. The
        # check is locale-independent on purpose -- `cat`'s message is translated, so an
        # English assertion passes here and fails on a machine with LANG=zh_CN.UTF-8.
        probe = SB.command(["sh", "-c", "test -e game.tw-pddl; echo \"exists=$?\"; "
                                        "cat game.tw-pddl 2>/dev/null | head -c 200"],
                           platform=PLATFORM, work_root=root, task_dir=task, env=env,
                           repo=task, state_dirs=(state,), tool_socket=relay.path)
        seen = subprocess.run(probe, capture_output=True, text=True, timeout=120, cwd=task)
        assert "exists=1" in seen.stdout, seen.stdout
        for token in (":objects", "inReceptacle", "pddl_problem"):
            assert token not in seen.stdout, seen.stdout[:400]
    finally:
        relay.stop()
        assert relay.fault() is None, relay.fault()
        assert relay.receipt["calls"] >= 1, relay.receipt
        relay.cleanup()
        shutil.rmtree(root, ignore_errors=True)
