"""Candidate preparation before CLI launch; all providers are replaced by local probes."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from methods import cli_improver as ci
from eval.containers import bun_runtime as br
from tests.quality.test_bun_runtime_contract import harness, fake_runtime


def prepared(tmp_path, monkeypatch):
    parent = harness(tmp_path)
    bun, _, calls = fake_runtime(tmp_path, monkeypatch)
    receipt = br.configure(parent)
    # A workspace dependency must resolve into the new candidate, not its parent.
    store = Path(receipt["dependency_store"])
    (store / "layout/node_modules/workspace").symlink_to("../app")
    (store / "provenance.json").write_text(json.dumps({
        "dependency_key": receipt["dependency_key"], "store_sha256": br.store_digest(store)}))
    receipt = br.prepare(parent)
    calls.clear()
    return parent, receipt, calls


def test_cli_launch_has_owned_dependencies_and_public_selftest(tmp_path, monkeypatch):
    parent, receipt, calls = prepared(tmp_path, monkeypatch)
    ws = tmp_path / "workspace"
    ws.mkdir()
    candidate = ws / "candidate"
    seen = []
    def run(argv, **kw):
        if argv[0] != "git":
            seen.append(argv)
            assert (candidate / "engine/node_modules/sample/index.js").read_text() == "third party"
            assert (candidate / "engine/node_modules/workspace").resolve() == candidate / "engine/app"
            assert (candidate / "evidence/SELF_TEST.md").is_file()
            assert (candidate / ".state/self-test").is_file()
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(ci.subprocess, "run", run)
    monkeypatch.setattr(ci, "_improver_command", lambda req: ["local-probe"])
    result = ci.propose({"workspace": str(ws), "harness_runtime": receipt}, parent)
    assert seen and not calls
    assert not result["changed"]


def test_declared_bun_candidate_without_preparation_cannot_launch(tmp_path, monkeypatch):
    parent = harness(tmp_path)
    monkeypatch.setattr(ci.subprocess, "run", lambda *a, **k: pytest.fail("CLI launched without dependencies"))
    with pytest.raises(ValueError, match="prepared Bun"):
        ci.propose({"workspace": str(tmp_path / "ws")}, parent)


def test_formal_entrypoint_projects_harness_runtime_without_changing_improver(tmp_path, monkeypatch):
    from harnessgrad import methods
    parent, receipt, calls = prepared(tmp_path, monkeypatch)
    improver = {"name": "dsh", "home": "/unchanged/home", "model": "unchanged"}
    request = {"base_harness": str(parent), "improver": dict(improver)}
    def run(argv, **kw):
        payload = json.loads(kw["input_text"])
        assert payload["harness_runtime"]["dependency_key"] == receipt["dependency_key"]
        assert payload["improver"] == improver
        assert Path(receipt["dependency_store"]) in kw["visible_paths"]
        return subprocess.CompletedProcess(argv, 0, '{}', '')
    monkeypatch.setattr(methods.sandbox, "run", run)
    assert methods._call_entrypoint(parent, [sys.executable, str(Path(ci.__file__))], request) == {}
    assert "harness_runtime" not in request
    assert not calls or not any("install" in c for c in calls)


def test_selftest_rejects_changed_dependency_inputs(tmp_path, monkeypatch):
    from methods.bun_workspace import stage_candidate
    parent, receipt, _ = prepared(tmp_path, monkeypatch)
    (parent / "engine/bun.lock").write_text("changed")
    with pytest.raises(ValueError, match="dependency"):
        stage_candidate(parent, receipt)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux self-test isolation")
def test_selftest_hides_credentials_private_files_and_network(tmp_path, monkeypatch):
    from methods.bun_selftest import command
    from methods.bun_workspace import stage_candidate
    from eval.ckpt.source_tree import copy_harness
    parent, receipt, _ = prepared(tmp_path, monkeypatch)
    candidate = tmp_path / "fresh"
    copy_harness(parent, candidate)
    stage_candidate(candidate, receipt)
    (candidate / "engine/test.ts").write_text("fixture")
    private = tmp_path / "private-reference.patch"
    private.write_text("private")
    # An edited informational receipt must never select mounts for the entry.
    forged = {**receipt, "dependency_store": str(private.parent)}
    (candidate / ".state/candidate-runtime.json").write_text(json.dumps(forged))
    bun = Path(receipt["binary"])
    bun.write_text("#!/usr/bin/python3\n" + "\n".join([
        "import os,socket", "from pathlib import Path",
        "assert not any('KEY' in k or 'TOKEN' in k for k in os.environ)",
        f"assert not Path({str(private)!r}).exists()",
        f"assert not Path({str(parent)!r}).exists()",
        "assert socket.if_nameindex() == [(1, 'lo')]",
        f"p=Path({str(Path(receipt['dependency_store']) / 'layout.json')!r})",
        "try: p.open('a'); raise AssertionError('store writable')",
        "except OSError: pass",
        f"state=Path({str(candidate / '.state')!r})",
        "for name in ('self-test', 'candidate-runtime.json'):",
        " try: (state/name).write_text('replaced'); raise AssertionError('self-test launch state writable')",
        " except OSError: pass",
        "print('isolated self-test ran')",
    ]))
    bun.chmod(0o755)
    argv = command(candidate, receipt, "unit", ".", ["test.ts"])
    p = subprocess.run(argv, env={"PATH":"/usr/bin:/bin", "HG_METHOD_API_KEY":"must-not-leak"},
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    assert "isolated self-test ran" in p.stdout
    p = subprocess.run([str(candidate / ".state/self-test"), "unit", ".", "test.ts"],
                       env={"PATH":"/usr/bin:/bin", "HG_METHOD_API_KEY":"must-not-leak"},
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    assert "isolated self-test ran" in p.stdout


def test_selftest_refuses_dependency_change_after_staging(tmp_path, monkeypatch):
    from methods.bun_selftest import command
    from methods.bun_workspace import stage_candidate
    from eval.ckpt.source_tree import copy_harness
    parent, receipt, _ = prepared(tmp_path, monkeypatch)
    candidate = tmp_path / "fresh"
    copy_harness(parent, candidate)
    stage_candidate(candidate, receipt)
    (candidate / "engine/bun.lock").write_text("new dependency")
    with pytest.raises(ValueError, match="dependency"):
        command(candidate, receipt, "unit", ".", ["test.ts"])


def test_editor_cli_bridge_reuses_runtime_for_source_only_archive_parent(tmp_path, monkeypatch):
    import cli_improver as bridge
    from methods import editor
    from eval.ckpt.source_tree import copy_harness
    base, _, calls = prepared(tmp_path, monkeypatch)
    parent = tmp_path / "archive-parent"
    copy_harness(base, parent)
    (parent / "engine/app/main.ts").write_text("export const n = 42")
    seen = []
    def run(argv, **kw):
        if argv[0] != "git":
            candidate = Path(kw["cwd"])
            assert (candidate / "engine/node_modules/sample/index.js").is_file()
            assert "42" in (candidate / "engine/app/main.ts").read_text()
            seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "_improver_command", lambda req: ["local-probe"])
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setattr(editor, "cli_improver_identity", lambda _: {"kind":"cli", "argv_style":"codex"})
    editor.ask("offline only", base=base, parent=parent)
    assert seen and not calls
