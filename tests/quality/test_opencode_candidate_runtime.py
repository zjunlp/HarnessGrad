"""Candidate preparation controls; no model, training or released dataset."""
import json
import os
from pathlib import Path

import pytest
from ckpt.git_state import stage_workspace
from ckpt.git_state import commit_state, content_sha
from methods.apply import apply


def source(root):
    root.mkdir()
    (root / "harness.json").write_text(json.dumps({
        "name": "opencode-agent", "version": "0.1.0", "path": ".",
        "entrypoint": "agent.py", "backend": "cli", "env_kinds": ["files"],
        "runtime": {"kind": "bun", "source": "opencode", "entrypoint": "packages/cli/src/index.ts", "package": "@opencode/cli"},
    }))
    (root / "agent.py").write_text("print('owned')\n")
    for name, text in [
        ("opencode/packages/cli/src/index.ts", "export const value = 1"),
        ("opencode/packages/core/src/example.ts", 'export const value = "parent"'),
        ("opencode/node_modules/dep/index.js", "third party"),
        ("opencode/packages/core/node_modules/dep/index.js", "third party"),
        (".state/session.txt", "old session"),
    ]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_formal_staging_copies_source_without_dependencies_or_sessions(tmp_path):
    parent = source(tmp_path / "parent")
    child = tmp_path / "child"
    stage_workspace(parent, child)
    assert not list(child.rglob("node_modules"))
    assert not (child / ".state/session.txt").exists()
    target = child / "opencode/packages/core/src/example.ts"
    target.write_text('export const value = "child"')
    assert '"parent"' in (parent / target.relative_to(child)).read_text()


def test_method_apply_does_not_carry_parent_dependencies(tmp_path):
    parent = source(tmp_path / "parent")
    child = tmp_path / "child"
    apply(parent, child, [])
    assert not list(child.rglob("node_modules"))
    assert not (child / ".state/session.txt").exists()


def test_preparing_dependencies_cannot_change_candidate_source_identity(tmp_path):
    parent = source(tmp_path / "parent")
    child = tmp_path / "child"
    stage_workspace(parent, child)
    before = content_sha(child)
    dependency = child / "opencode/packages/core/node_modules/dep/index.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("installed package")
    assert commit_state(child, "prepared runtime") == before


def test_dependency_identity_includes_manifest_runtime_and_lock(tmp_path):
    from eval.containers.bun_runtime import dependency_identity
    root = source(tmp_path / "parent") / "opencode"
    (root / "package.json").write_text('{"name":"fixture"}')
    (root / "bun.lock").write_text("lock A")
    original = dependency_identity(root, "1.4.2", "Linux", "x86_64")
    # Source edits reuse dependencies; package/lock/runtime edits do not.
    (root / "packages/core/src/example.ts").write_text("export const value = 2")
    assert dependency_identity(root, "1.4.2", "Linux", "x86_64") == original
    for version, system, arch in [("1.4.3", "Linux", "x86_64"),
                                  ("1.4.2", "Darwin", "x86_64"),
                                  ("1.4.2", "Linux", "arm64")]:
        assert dependency_identity(root, version, system, arch) != original
    (root / "packages/core/package.json").write_text('{"name":"changed"}')
    assert dependency_identity(root, "1.4.2", "Linux", "x86_64") != original
    (root / "packages/core/package.json").unlink()
    (root / "bun.lock").write_text("lock B")
    assert dependency_identity(root, "1.4.2", "Linux", "x86_64") != original


def test_reusing_layout_leaves_live_links_untouched(tmp_path):
    from eval.containers.bun_runtime import attach_layout
    store, candidate = tmp_path / "store", tmp_path / "candidate"
    layout = store / "layout/packages/core/node_modules"
    layout.mkdir(parents=True)
    (store / "packages").mkdir()
    (layout / "dep").symlink_to("../../../node_modules/.bun/dep")
    (store / "layout.json").write_text(json.dumps(["packages/core/node_modules"]))
    candidate.mkdir()
    attach_layout(candidate, store)
    link = candidate / "packages/core/node_modules/dep"
    inode = link.lstat().st_ino
    attach_layout(candidate, store)
    assert link.lstat().st_ino == inode
    # A stale workspace link is repaired without following it into another source.
    link.unlink()
    link.symlink_to(tmp_path / "foreign")
    attach_layout(candidate, store)
    assert str(link.readlink()) == "../../../node_modules/.bun/dep"


def test_patch_bytes_change_dependency_identity(tmp_path):
    from eval.containers.bun_runtime import dependency_identity
    root = source(tmp_path / "parent") / "opencode"
    (root / "package.json").write_text(json.dumps({
        "patchedDependencies": {"example@1.0.0": "patches/example.patch"}}))
    patch = root / "patches/example.patch"
    patch.parent.mkdir()
    patch.write_text("first patch")
    first = dependency_identity(root, "1.4.2", "Linux", "x86_64")
    patch.write_text("second patch")
    assert dependency_identity(root, "1.4.2", "Linux", "x86_64") != first


@pytest.mark.parametrize("spec", ["file:./local", "link:./local", "../outside", "/outside"])
def test_local_dependency_forms_refuse_instead_of_reusing_stale_store(tmp_path, spec):
    from eval.containers.bun_runtime import dependency_identity, RuntimeUnavailable
    root = source(tmp_path / "parent") / "opencode"
    (root / "package.json").write_text(json.dumps({"dependencies": {"example": spec}}))
    with pytest.raises(RuntimeUnavailable, match="local dependency"):
        dependency_identity(root, "1.4.2", "Linux", "x86_64")


@pytest.mark.parametrize("patch", ["../outside.patch", "missing.patch"])
def test_patch_inputs_must_exist_inside_source(tmp_path, patch):
    from eval.containers.bun_runtime import dependency_identity, RuntimeUnavailable
    root = source(tmp_path / "parent") / "opencode"
    (root.parent / "outside.patch").write_text("outside")
    (root / "package.json").write_text(json.dumps({"patchedDependencies": {"example": patch}}))
    with pytest.raises(RuntimeUnavailable, match="patch"):
        dependency_identity(root, "1.4.2", "Linux", "x86_64")


def test_trusted_parser_does_not_execute_candidate_preload(tmp_path, monkeypatch):
    from eval.containers.bun_runtime import validate_source
    bun = os.environ.get("HG_AGENT_OPENCODE_BUN")
    if not bun:
        pytest.skip("Set HG_AGENT_OPENCODE_BUN for the real Bun boundary control")
    repo = source(tmp_path / "candidate")
    entry = repo / "opencode/packages/cli/src/index.ts"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("export const value = 1")
    marker = tmp_path / "preload-executed"
    (repo / "opencode/preload.ts").write_text(
        "require('fs').writeFileSync(" + json.dumps(str(marker)) + ", 'executed')")
    (repo / "opencode/bunfig.toml").write_text('preload = ["./preload.ts"]\n')
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("BUN_INSTALL_CACHE_DIR", str(cache))
    monkeypatch.delenv("HG_OPENCODE_DEPENDENCY_CACHE", raising=False)
    assert validate_source(repo) == []
    assert not marker.exists(), "Trusted parsing executed candidate code outside its sandbox"


@pytest.mark.parametrize("failure", ["filesystem", "corrupt_layout", "missing_layout"])
def test_preparation_errors_are_unmeasured_in_formal_runner(tmp_path, monkeypatch, failure):
    from eval.containers import bun_runtime as runtime
    from eval.run.runner import run_one
    repo = source(tmp_path / "candidate")
    (repo / "opencode/bun.lock").write_text("fixture")
    cache = tmp_path / "cache"
    cache.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (store / "layout.json").write_text("[" if failure == "corrupt_layout" else '["node_modules"]')
    monkeypatch.setattr(runtime, "validate_source", lambda repo: [])
    monkeypatch.setattr(runtime, "runtime_settings", lambda: (tmp_path / "bun", cache))
    monkeypatch.setattr(runtime, "_run", lambda *a, **kw: "1.4.2")
    (tmp_path / "bun").write_bytes(b"fixture")
    def layout(*args, **kwargs):
        if failure == "filesystem":
            raise PermissionError("cache is read-only")
        return store, "fixture-store-hash"
    monkeypatch.setattr(runtime, "lookup_store", layout)
    result = run_one(repo, {"task_id": "prep-control", "prompt": "unused"})
    assert result["invalid"]["stage"] == "harness runtime preparation"
    assert "verdict" not in result and result["exit_code"] is None


def test_observed_workers_refuse_opencode_before_launch(tmp_path, monkeypatch):
    from eval.run import cooperative
    repo = source(tmp_path / "candidate")
    workers = [{"id": "peer", "goal": "unused", "artifacts": ["answer.txt"],
                "env": {"kind": "exec", "backend": "observed-v1",
                        "image": "sha256:" + "a" * 64, "workdir": "/workspace",
                        "image_workspace": "/workspace", "cpus": 1, "memory_mb": 256,
                        "pids": 64, "filesystems": {
                            "/workspace": {"bytes": 64 << 20, "inodes": 4096}}}}]
    workers.append({**workers[0], "id": "peer2"})
    # Admission must finish before any worker state or container is created.
    def no_launch(*args, **kwargs):
        pytest.fail("OpenCode observed worker reached runtime creation")
    monkeypatch.setattr(cooperative.tempfile, "mkdtemp", no_launch)
    result = cooperative.run(repo, {"task_id": "parent"}, setup={"workers": workers},
                             verifier={"kind": "command", "command": ["true"]}, env=None,
                             timeout_s=1, verify_timeout_s=1, sandbox=True,
                             run_id="control", run_seed="1", trial_id="1", recordings_root=None)
    assert result["invalid"]["stage"] == "workers admission"
    assert "Bun" in result["invalid"]["detail"]
    assert "verdict" not in result
