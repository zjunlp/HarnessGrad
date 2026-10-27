"""Declared runtime/lifecycle controls. No model or network access."""
import json
from pathlib import Path

import pytest

from eval.ckpt.source_tree import copy_harness
from eval.containers import bun_runtime as runtime


def harness(tmp_path, *, name="renamed-agent"):
    repo = tmp_path / "candidate"
    repo.mkdir()
    spec = {"kind": "bun", "source": "engine", "entrypoint": "app/main.ts", "package": "@sample/app"}
    (repo / "harness.json").write_text(json.dumps({
        "name": name, "version": "1", "entrypoint": "agent.py", "runtime": spec}))
    (repo / "agent.py").write_text("print('unused')")
    source = repo / "engine"
    (source / "app").mkdir(parents=True)
    (source / "app/main.ts").write_text("export const n = 1")
    (source / "bun.lock").write_text("fixture lock")
    (source / "package.json").write_text('{"name":"fixture"}')
    return repo


def fake_runtime(tmp_path, monkeypatch):
    bun = tmp_path / "bin/bun"
    bun.parent.mkdir()
    bun.write_bytes(b"fixture runtime")
    cache = tmp_path / "cache/bun"
    cache.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(runtime, "runtime_settings", lambda: (bun, cache))
    def run(argv, *, cwd, **kwargs):
        calls.append(argv)
        if "--version" in argv:
            return "fixture-1"
        if "install" in argv:
            modules = Path(cwd) / "node_modules"
            package = modules / ".bun/sample/node_modules/sample"
            package.mkdir(parents=True)
            (package / "index.js").write_text("third party")
            (modules / "sample").symlink_to(".bun/sample/node_modules/sample")
            return ""
        return '{"ok":true}'
    monkeypatch.setattr(runtime, "_run", run)
    return bun, cache, calls


def test_declared_runtime_copy_does_not_depend_on_harness_name(tmp_path):
    repo = harness(tmp_path)
    (repo / "engine/node_modules").mkdir()
    (repo / "engine/node_modules/old").write_text("old")
    copy_harness(repo, tmp_path / "copy")
    assert (tmp_path / "copy/engine/app/main.ts").is_file()
    assert not (tmp_path / "copy/engine/node_modules").exists()


def test_formal_cache_miss_never_installs(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    _, _, calls = fake_runtime(tmp_path, monkeypatch)
    with pytest.raises(runtime.RuntimeUnavailable, match="configure_bun_harness"):
        runtime.prepare(repo)
    assert not any("install" in call for call in calls)


def test_explicit_setup_then_source_rewrite_reuses_store(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    _, _, calls = fake_runtime(tmp_path, monkeypatch)
    first = runtime.configure(repo)
    assert first["installed"]
    assert any("@sample/app..." in c for c in calls)
    (repo / "engine/app/main.ts").write_text("export const n = 2")
    calls.clear()
    second = runtime.prepare(repo)
    assert second["cache_hit"] and not second["installed"]
    assert first["identity"] == second["identity"]
    assert not any("install" in call for call in calls)
    assert (repo / "engine/node_modules/sample/index.js").read_text() == "third party"


@pytest.mark.parametrize("field,value", [("source", "../other"), ("entrypoint", "/tmp/entry.ts"),
                                         ("kind", "unknown"), ("source", "engine\\other")])
def test_invalid_declaration_is_rejected(tmp_path, field, value):
    from eval.ckpt.source_tree import runtime_spec
    repo = harness(tmp_path)
    manifest = json.loads((repo / "harness.json").read_text())
    manifest["runtime"][field] = value
    (repo / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runtime_spec(repo)


def test_external_diagnostic_source_is_rejected_before_formal_preparation(tmp_path, monkeypatch):
    from eval.run.runner import run_one
    repo = harness(tmp_path)
    monkeypatch.setenv("HG_AGENT_OPENCODE_EXTERNAL_SOURCE", "1")
    monkeypatch.setenv("HG_AGENT_OPENCODE_SOURCE", str(tmp_path / "external"))
    monkeypatch.setattr(runtime, "prepare", lambda repo: pytest.fail("must reject before preparing"))
    result = run_one(repo, {"task_id": "ownership", "prompt": "unused"})
    assert result["invalid"]["stage"] == "harness runtime preparation"
    assert "diagnostic" in result["invalid"]["detail"].lower()


def test_changed_binary_cannot_reuse_runtime_identity(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    bun, _, _ = fake_runtime(tmp_path, monkeypatch)
    first = runtime.configure(repo)
    bun.write_bytes(b"changed bytes, same version")
    with pytest.raises(runtime.RuntimeUnavailable, match="configure_bun_harness"):
        runtime.prepare(repo)
    second = runtime.configure(repo)
    assert first["identity"] != second["identity"]


def test_changed_lock_requires_explicit_preparation(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    _, _, calls = fake_runtime(tmp_path, monkeypatch)
    runtime.configure(repo)
    (repo / "engine/bun.lock").write_text("changed lock")
    calls.clear()
    with pytest.raises(runtime.RuntimeUnavailable, match="configure_bun_harness"):
        runtime.prepare(repo)
    assert not any("install" in call for call in calls)


def test_source_symlink_escape_is_rejected(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    fake_runtime(tmp_path, monkeypatch)
    outside = tmp_path / "foreign.ts"
    outside.write_text("export const n = 9")
    (repo / "engine/app/main.ts").unlink()
    (repo / "engine/app/main.ts").symlink_to(outside)
    with pytest.raises(runtime.RuntimeUnavailable, match="candidate"):
        runtime.prepare(repo)


def test_runtime_identity_survives_result_cache_and_binary_change(tmp_path, monkeypatch):
    from eval.run import runner
    repo = harness(tmp_path)
    bun, _, _ = fake_runtime(tmp_path, monkeypatch)
    expected = runtime.configure(repo)["identity"]
    calls = []
    def run(repo, task, *args, runtime_receipt, **kwargs):
        calls.append(task["task_id"])
        runtime_receipt["bun"] = runtime.prepare(repo)
        return {"task_id": task["task_id"], "answer": "ok", "trace": "", "exit_code": 0,
                "stderr": "", "verdict": {"kind": "command", "passed": True},
                "harness_runtime": {"source": "platform", "python": None}}
    monkeypatch.setattr(runner, "_run_one", run)
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    tasks = [{"task_id": "t", "prompt": "offline control"}]
    cache = {}
    first = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha="same-source")
    assert first["harness_runtime"][0]["bun"] == expected
    second = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha="same-source")
    assert calls == ["t"]
    assert second["cached_tasks"] == ["t"]
    assert first["harness_runtime"] == second["harness_runtime"]
    # A cache entry lacking the verified runtime record is not usable.
    next(iter(cache.values()))["harness_runtime"].pop("bun")
    runner._evaluate(repo, tasks, {}, cache=cache, harness_sha="same-source")
    assert calls == ["t", "t"]
    bun.write_bytes(b"a different runtime build")
    changed = runtime.configure(repo)["identity"]
    third = runner._evaluate(repo, tasks, {}, cache=cache, harness_sha="same-source")
    assert calls == ["t", "t", "t"]
    assert not third["cached_tasks"]
    assert third["harness_runtime"][0]["bun"] == changed != expected


def test_missing_store_cannot_reuse_previous_measurement(tmp_path, monkeypatch):
    from eval.run import runner
    repo = harness(tmp_path)
    fake_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "run_one", lambda *a, **k: pytest.fail("missing runtime must not launch"))
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    result = runner._evaluate(repo, [{"task_id": "t", "prompt": "unused"}], {},
                              harness_sha="h", cache={"h:t:1": {"score": 1, "trace": ""}})
    assert not result["cached_tasks"]
    assert result["invalid"]


def test_validator_cli_works_without_repository_on_pythonpath(tmp_path):
    import os
    import subprocess
    import sys
    repo = harness(tmp_path)
    tool = Path(__file__).resolve().parents[2] / "tools/validate_harness.py"
    result = subprocess.run([sys.executable, str(tool), str(repo)], cwd=tmp_path,
                            env={"PATH": os.defpath}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_mutated_store_is_rejected_before_attachment(tmp_path, monkeypatch):
    repo = harness(tmp_path)
    fake_runtime(tmp_path, monkeypatch)
    configured = runtime.configure(repo)
    package = Path(configured["dependency_store"]) / "packages/sample/node_modules/sample/index.js"
    package.write_text("tampered dependency")
    monkeypatch.setattr(runtime, "attach_layout", lambda *a: pytest.fail("must reject before attaching"))
    with pytest.raises(runtime.RuntimeUnavailable, match="identity mismatch"):
        runtime.prepare(repo)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_store_permissions_are_part_of_verified_identity(tmp_path, monkeypatch, kind):
    repo = harness(tmp_path)
    fake_runtime(tmp_path, monkeypatch)
    configured = runtime.configure(repo)
    package = Path(configured["dependency_store"]) / "packages/sample/node_modules/sample/index.js"
    target = package if kind == "file" else package.parent
    target.chmod(target.stat().st_mode ^ (0o100 if kind == "file" else 0o020))
    with pytest.raises(runtime.RuntimeUnavailable, match="identity mismatch"):
        runtime.identity(repo)


def test_formal_legacy_manifest_cannot_use_external_diagnostic(tmp_path):
    repo = harness(tmp_path)
    manifest = json.loads((repo / "harness.json").read_text())
    manifest.pop("runtime")
    (repo / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(runtime.RuntimeUnavailable, match="diagnostic"):
        runtime.admit(repo, {"HG_AGENT_OPENCODE_EXTERNAL_SOURCE": "1"})


def test_container_state_receives_verified_bun_runtime(tmp_path, monkeypatch):
    from eval.run import runner
    repo = harness(tmp_path)
    bun, _, _ = fake_runtime(tmp_path, monkeypatch)
    configured = runtime.configure(repo)
    monkeypatch.setenv("HG_AGENT_OPENCODE_BUN", "/ambient/other-bun")
    observed = {}
    def dispatch(repo, task, **kwargs):
        observed.update(kwargs)
        return {"task_id": task["task_id"], "answer": "", "trace": "", "exit_code": 0, "stderr": "", "identity": None}
    monkeypatch.setattr(runner, "_run_one_container_state", dispatch)
    receipt = {}
    runner._run_one(repo, {"task_id": "container-control", "prompt": "unused"},
                    env={"kind": "exec", "image": "fixture", "state": "container"}, runtime_receipt=receipt)
    assert receipt["bun"]["identity"] == configured["identity"]
    assert observed["runtime_env"]["HG_HARNESS_BUN"] == str(bun)
    assert observed["runtime_env"]["HG_AGENT_OPENCODE_BUN"] == str(bun)
    assert observed["runtime_env"]["HG_HARNESS_SOURCE"] == str(repo / "engine")
