"""Synthetic boundary regressions; never import the release game/reference table."""
from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds.registry import _check_env, _normalize_env
from eval.containers import sandbox
from eval.run.runner import _refuse_unobserved_policies, run_one
from eval.policy.runtime_policy import phase_timeout, stage_agent_runtime
from eval.verification.verifier import _verify, _verify_container_state


def load_asset(name):
    spec = importlib.util.spec_from_file_location(f"jericho_{name}_test", ROOT / "data/datasets/jericho" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_import_does_not_read_release_payload(monkeypatch):
    original = __import__("builtins").__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith("jericho"), name
        return original(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    spec = importlib.util.spec_from_file_location("jericho_lazy_test", ROOT / "data/datasets/jericho.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "_RECORDS" not in vars(module)
    assert module.DEFAULT_ENV["network"] == "none"


def test_projection_reads_only_code_and_excludes_docs_data_and_bytecode(tmp_path, monkeypatch):
    runtime = load_asset("runtime")
    package = tmp_path / "jericho"
    package.mkdir()
    for name in runtime.CODE_FILES:
        (package / name).write_text('"""synthetic reference example: SECRET"""\n')
    (package / "defines.py").write_text(
        'from .game_info import *\nBINDINGS_DICT = {"digest": fictional}\n')
    (package / "jericho.py").write_text(
        'class FrotzEnv:\n'
        '    """SECRET"""\n'
        '    def seed(self, seed=None):\n'
        '        seed = seed or self.bindings.get("seed", -1)\n'
        '        self._seed = seed\n'
        '        return seed\n'
        '    def get_walkthrough(self):\n'
        '        return self.bindings["walkthrough"].split("/")\n')
    (package / "libfrotz.so").write_bytes(b"synthetic native library")
    for name in ("game_info.py", "game_info.pyc", "clubfloyd_verb_counts.json", "reference.zip"):
        (package / name).write_text("SECRET")
    read = Path.read_text
    opened = []

    def guarded(path, *args, **kwargs):
        if path.parent == package:
            opened.append(path.name)
            assert path.name in runtime.CODE_FILES
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    declaration = runtime.agent_runtime(str(package))
    assert set(opened) == set(runtime.CODE_FILES)
    projection, _ = stage_agent_runtime(declaration, tmp_path / "projection")
    assert sorted(p.name for p in projection.iterdir()) == sorted((*runtime.CODE_FILES, "libfrotz.so"))
    text = (projection / "jericho.py").read_text()
    assert "SECRET" not in text
    namespace = {}
    exec(text, namespace)
    env = namespace["FrotzEnv"]()
    assert env.seed(0) == 0 and env.seed(4) == 4 and env.seed(None) == -1
    with pytest.raises(RuntimeError, match="unavailable"):
        env.get_walkthrough()
    assert "BINDINGS_DICT = {}" in (projection / "defines.py").read_text()
    assert (package / "game_info.py").read_bytes() == b"SECRET"


@pytest.mark.parametrize("bad", [0, -1, True, "300", float("nan"), float("inf")])
def test_invalid_timeouts_refused(bad):
    with pytest.raises(ValueError, match="timeout"):
        phase_timeout(bad)


def test_limits_cannot_be_loosened_and_files_policy_survives_normalization():
    assert phase_timeout() == 3600
    assert phase_timeout(900, 300, 100) == 100
    env = {"kind": "files", "network": "none", "agent_timeout_s": 300, "verify_timeout_s": 120}
    _check_env(env, "synthetic", "jericho")
    assert _normalize_env(env) == env
    with pytest.raises(ValueError, match="network"):
        _check_env({"kind": "files", "network": "host"}, "synthetic", "jericho")


@pytest.mark.parametrize("mode", ["host", "sandbox", "exec", "snapshot"])
def test_verifier_declaration_reaches_every_launch_path(tmp_path, monkeypatch, mode):
    captured = []
    spec = {"kind": "command", "argv": ["synthetic"], "timeout_s": 0.02}

    def timeout(*args, **kwargs):
        captured.append(kwargs.get("timeout_s", kwargs.get("timeout")))
        raise subprocess.TimeoutExpired("synthetic", captured[-1])

    if mode == "snapshot":
        snapshot_path = ROOT / "tests/fixtures/hg_wsnap/raw/015.stdout.txt"
        if not snapshot_path.is_file():
            pytest.skip("tests/fixtures/hg_wsnap is internal (see .gitignore) and absent from this clone")
        snapshot = snapshot_path.read_text().strip()
        monkeypatch.setattr("eval.verification.verifier.container_mod.docker",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, snapshot, ""))
        monkeypatch.setattr("eval.verification.verifier.container_mod.create", lambda *a, **k: "fixture")
        monkeypatch.setattr("eval.verification.verifier.container_mod.exec_in", timeout)
        monkeypatch.setattr("eval.verification.verifier.container_mod.start", lambda *a, **k: None)
        monkeypatch.setattr("eval.verification.verifier.container_mod.remove_container", lambda *a, **k: None)
        verdict = _verify_container_state(snapshot, spec, expected_snapshot=snapshot, task_mount="/app", staging=tmp_path,
                                          timeout_s=900, run_id="synthetic", task_id="synthetic")
    else:
        target = {"host": "subprocess.run", "sandbox": "sandbox_mod.run", "exec": "container_mod.run"}[mode]
        monkeypatch.setattr("eval.verification.verifier." + target, timeout)
        verdict = _verify(tmp_path, spec, "", sandbox=mode != "host", timeout_s=900,
                          env={"kind": "exec", "image": "synthetic"} if mode == "exec" else {"kind": "files"})
    assert captured == [0.02]
    assert "timed out after 0.02s" in verdict["detail"]
    assert "score" not in verdict


def test_real_host_sleep_obeys_declared_timeout(tmp_path):
    result = _verify(tmp_path, {"kind": "command", "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
                               "timeout_s": 0.04}, "", sandbox=False, timeout_s=30)
    assert "timed out after 0.04s" in result["detail"]


def harness(path, body):
    path.mkdir(parents=True)
    (path / "harness.json").write_text(json.dumps({"entrypoint": "agent.py", "name": "synthetic", "version": "1"}))
    (path / "agent.py").write_text(body)
    return path


def test_runner_refuses_unsandboxed_declared_isolation(tmp_path):
    repo = harness(tmp_path / "harness", "raise AssertionError('must not execute')")
    with pytest.raises(ValueError, match="isolation requires sandbox=True"):
        run_one(repo, {"task_id": "synthetic", "goal": "synthetic"}, sandbox=False,
                env={"kind": "files", "network": "none"})
    assert list(tmp_path.iterdir()) == [repo]


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_unobserved_admission_and_synthetic_projection_backend_boundaries(tmp_path, monkeypatch):
    # A separate synthetic platform tree preserves the production staging rule.
    # All test writes remain in the user-authorized worktree.
    platform = tmp_path / "platform"
    platform.mkdir()
    monkeypatch.setattr("eval.run.runner.PLATFORM_ROOT", platform)
    monkeypatch.setattr("eval.verification.verifier.PLATFORM_ROOT", platform)
    work = tmp_path / "work"
    package = work / "packages" / "boundary_fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('REFERENCE = "SYNTHETIC_SECRET"\n')
    (package / "game_info.py").write_text('REFERENCE = "SYNTHETIC_SECRET"\n')
    (package / "__pycache__").mkdir()
    (package / "__pycache__/game_info.pyc").write_bytes(b"SYNTHETIC_SECRET")
    runtime = {"package_dir": str(package), "files": [{"path": "__init__.py", "content": "PUBLIC = True\n"}]}
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(work))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host_port = listener.getsockname()[1]
        body = f'''import argparse, json, os, pathlib, socket, sys
ap = argparse.ArgumentParser(); ap.add_argument('--task'); ap.add_argument('--workdir'); a = ap.parse_args()
assert os.environ['HG_AGENT_DEADLINE_S'] == '3'
sys.path.insert(0, {str(package.parent)!r})
import boundary_fixture as pkg
seen = {{'public': pkg.PUBLIC, 'reference': hasattr(pkg, 'REFERENCE'),
        'data': pathlib.Path(pkg.__file__).with_name('game_info.py').exists(),
        'bytecode': pathlib.Path(pkg.__file__).with_name('__pycache__').exists()}}
try:
    pathlib.Path(pkg.__file__).write_text('rewritten'); seen['writable'] = True
except OSError:
    seen['writable'] = False
for label, target in [('egress', ('192.0.2.1', 9)), ('host_loopback', ('127.0.0.1', {host_port}))]:
    with socket.socket() as sock:
        sock.settimeout(.2)
        try:
            sock.connect(target); seen[label] = True
        except OSError:
            seen[label] = False
pathlib.Path(a.workdir, 'answer.txt').write_text(json.dumps(seen))
'''
        repo = harness(work / "run/workspace", body)
        verifier = {"kind": "command", "timeout_s": 2, "argv": [sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(package.parent)!r}); import boundary_fixture as p; "
            "assert p.REFERENCE == 'SYNTHETIC_SECRET'; print('trusted package retained')"]}
        env = {"kind": "files", "network": "none", "agent_timeout_s": 3,
               "verify_timeout_s": 5, "agent_runtime": runtime}
        assert _refuse_unobserved_policies({"task_id": "synthetic"}, env, verifier) is None
        assert not list(work.glob("task-*"))
        assert (package / "__init__.py").read_text() == 'REFERENCE = "SYNTHETIC_SECRET"\n'
        # Exercise the real admission hook: the platform applies these deadlines,
        # the package projection and the private network without an authorization.
        result = run_one(repo, {"task_id": "synthetic", "goal": "synthetic"},
                         env=env, verifier=verifier)
    assert result["exit_code"] == 0, result["stderr"]
    assert json.loads(result["answer"]) == {"public": True, "reference": False, "data": False,
                                           "bytecode": False, "writable": False,
                                           "egress": False, "host_loopback": False}
    assert result["verdict"]["passed"], result["verdict"]
    assert result["runtime_boundary"] == {"agent_timeout_s": 3, "verifier_timeout_s": 2,
        "agent_network": "none", "agent_package_projected": True, "verifier_package": "trusted"}


def test_control_materialization_resets_actions_and_requires_explicit_id(tmp_path, monkeypatch):
    import data.datasets.jericho as adapter
    control = load_asset("control")
    rom = tmp_path / "synthetic.z5"
    rom.write_bytes(b"synthetic ROM")
    record = {"task_id": "synthetic", "rom": rom.name, "path": rom, "seed": 0, "walkthrough": []}
    monkeypatch.setitem(adapter.__dict__, "_RECORDS", [record])
    work = tmp_path / "task"
    control.materialize(record, work)
    (work / adapter.LOG_FILE).write_text('{"kind":"action","action":"synthetic old action"}\n')
    control.materialize(record, work)
    assert (work / adapter.LOG_FILE).read_text() == ""
    assert all(e["path"] != adapter.LOG_FILE for e in adapter.VERIFY["synthetic"]["inputs"])
    with pytest.raises(SystemExit, match="task-id"):
        control._record(None)
