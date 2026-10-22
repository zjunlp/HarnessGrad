"""The positive control's rule test: one edit, in one file, and it refuses when it cannot.

`methods/raise_step_cap` exists so the measurement chain can be tested before any method is
trusted. A method's edit may be arbitrary, so a null result from one leaves "the instrument
cannot see a real improvement" and "the method proposed nothing useful" indistinguishable --
and 101 of the corpus's 145 candidate rounds have no discordant pair at all, which is that
ambiguity piled up.

This test pins the three things that make it usable as a control:

* it makes **exactly one** change, to `agent.py`, so `editable_surface_touched` cannot be
  ambiguous about what was attributed;
* the change is the one its own `method.json` and docstring state, argued from
  `base_harness/loop/agent.py:54-57`'s criterion for when this lever is legitimate rather than
  from a hunch;
* it reports `changed: false` against a harness that does not carry the marker, because a
  control that cannot fail is not a control.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

METHOD = ROOT / "methods" / "raise_step_cap" / "run.py"
BASE = ROOT / "base_harness" / "loop"
API = "0.1.0"


def _invoke(tmp_path: Path, base: Path, mode: str = "B") -> tuple[dict, dict]:
    """Run the method the way the platform does: a request on stdin, JSON on stdout."""
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "trajectory.json"
    req = {
        "platform_api_version": API,
        "workspace": str(work),
        "base_harness": str(base),
        "candidate": str(work / "candidate"),
        "improver": None,
        "channel": str(base / "_harnessgrad"),
        "trajectory_out": str(out),
        "task_ids": [],
        "train_task_ids": [],
        "mode": mode,
    }
    proc = subprocess.run([sys.executable, str(METHOD)], input=json.dumps(req),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout), json.loads(out.read_text())


def test_it_makes_exactly_one_edit_and_it_is_the_one_it_declares(tmp_path):
    reply, traj = _invoke(tmp_path, BASE)
    assert reply["changed"] is True, reply
    steps = traj["steps"]
    assert len(steps) == 2 and steps[1]["edit_kind"] == "control_max_steps", steps

    # Exactly one file differs from the base. Anything else would make
    # `editable_surface_touched` ambiguous about what the round attributed.
    produced = Path(steps[1]["harness_dir"])
    differing = sorted(
        str(p.relative_to(produced))
        for p in produced.rglob("*") if p.is_file() and "__pycache__" not in p.parts
        and p.name not in (".gitignore",)
        and (not (BASE / p.relative_to(produced)).is_file()
             or (BASE / p.relative_to(produced)).read_bytes() != p.read_bytes()))
    assert differing == ["agent.py"], differing

    # The declared change, and the declaration still parses as Python.
    text = (produced / "agent.py").read_text()
    assert 'HG_AGENT_MAX_STEPS", "50"' in text, text[:400]
    assert 'HG_AGENT_MAX_STEPS", "32"' not in text
    compile(text, "agent.py", "exec")

    # Step 0 is the base verbatim: H0 has to be this run's own baseline, because the archived
    # alfworld figure predates the fidelity fixes.
    unmodified = Path(steps[0]["harness_dir"])
    assert (unmodified / "agent.py").read_bytes() == (BASE / "agent.py").read_bytes()


def test_it_refuses_a_harness_that_does_not_carry_the_marker(tmp_path):
    """**阴性方向是必须的。** 一条改不动的 harness 上它必须说"没改",而不是声称改了。

    没有这一条,这个控制就只会永远回答"我改了",而那样的控制什么也证明不了。
    """
    alien = tmp_path / "alien"
    alien.mkdir()
    (alien / "harness.json").write_text(json.dumps(
        {"name": "alien", "version": "1.0", "entrypoint": "agent.py"}))
    (alien / "agent.py").write_text("print('a harness with no such cap')\n")

    reply, traj = _invoke(tmp_path, alien)
    assert reply["changed"] is False, reply
    produced = Path(traj["steps"][1]["harness_dir"])
    assert (produced / "agent.py").read_bytes() == (alien / "agent.py").read_bytes(), (
        "it reported no change and changed the file anyway")


def test_it_is_declared_as_a_control_with_a_reason():
    """`kind` and `note` are what the chooser and the reader see. `kind: "other"` is the state
    `test_method_lint.py` exists to prevent, and a control with no stated reason is a method
    nobody can classify."""
    meta = json.loads((METHOD.parent / "method.json").read_text())
    assert meta["kind"] == "control", meta
    assert len(meta["note"]) > 200, "the note has to say why this edit and not another"
    assert "32" in meta["note"] and "50" in meta["note"], meta["note"]
