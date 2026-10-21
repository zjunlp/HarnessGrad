"""Verified Bun inputs reach container-state execution; no Docker or provider runs."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from eval.containers import bun_runtime, container_state
from eval.run import runner


@pytest.mark.parametrize("legacy_bun", [None, "/ambient/other-bun"])
@pytest.mark.parametrize("transport", [False, True])
def test_container_state_exec_receives_verified_bun_environment(tmp_path, monkeypatch, legacy_bun, transport):
    repo = tmp_path / "candidate"
    source = repo / "engine"
    source.mkdir(parents=True)
    entry = source / "main.ts"
    entry.write_text("export const fixture = true")
    (repo / "agent.py").write_text("raise AssertionError('must not execute')")
    (repo / "harness.json").write_text(json.dumps({
        "name": "renamed-bun-adapter", "entrypoint": "agent.py",
        "runtime": {"kind": "bun", "source": "engine", "entrypoint": "main.ts",
                    "package": "fixture"}}))
    binary = tmp_path / "runtime/bun"
    binary.parent.mkdir()
    binary.write_text("synthetic binary; must not execute")
    store = tmp_path / "prepared-store"
    store.mkdir()
    preparation = {"binary": str(binary), "identity": {"binary_sha256": "verified-fixture"},
                   "runtime_paths": [str(binary.parent), str(store)]}
    if transport:
        overlay = tmp_path / "transport"
        overlay.mkdir()
        preparation.update(execution_binary=str(overlay / "launcher"),
                           responses_overlay={"identity": {"sha256": "fixture-overlay"}})
        preparation["runtime_paths"].append(str(overlay))
    monkeypatch.setattr(bun_runtime, "prepare", lambda _: preparation)
    monkeypatch.setenv("HG_BUN_BINARY", str(binary))
    monkeypatch.delenv("HG_HARNESS_BUN", raising=False)
    monkeypatch.delenv("HG_HARNESS_SOURCE", raising=False)
    monkeypatch.delenv("HG_HARNESS_ENTRYPOINT", raising=False)
    monkeypatch.delenv("HG_AGENT_OPENCODE_SOURCE", raising=False)
    monkeypatch.delenv("HG_AGENT_OPENCODE_EXTERNAL_SOURCE", raising=False)
    if legacy_bun:
        monkeypatch.setenv("HG_AGENT_OPENCODE_BUN", legacy_bun)
    else:
        monkeypatch.delenv("HG_AGENT_OPENCODE_BUN", raising=False)
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected process"))

    service = container_state.services_mod
    monkeypatch.setattr(service, "create_network", lambda *a: "fixture-network")
    monkeypatch.setattr(service, "remove_network", lambda *a: None)
    docker = container_state.container_mod
    monkeypatch.setattr(docker, "image_python", lambda *a: "/usr/bin/python3")
    monkeypatch.setattr(container_state.harness_runtime, "lookup", lambda *a, **k: None)
    observed = {}
    monkeypatch.setattr(docker, "create", lambda *a, **k: observed.update(mounts=k["extra_mounts"]))
    for name in ("cp_in", "start", "mkdir_in", "stop", "remove_image", "remove_container"):
        monkeypatch.setattr(docker, name, lambda *a, **k: None)
    if transport:
        from eval.containers import responses_overlay
        collect = responses_overlay.collect_capture
        monkeypatch.setattr(docker, "remove_container", lambda *a: observed.update(removed=True))
        def collect_after_teardown(path):
            assert observed.get("removed"), "capture must be stable after container teardown"
            return collect(path)
        monkeypatch.setattr(responses_overlay, "collect_capture", collect_after_teardown)
    monkeypatch.setattr(docker, "cp_out", lambda *a, **k: False)
    monkeypatch.setattr(docker, "commit", lambda *a, **k: "fixture-snapshot")
    monkeypatch.setattr(container_state, "_verify_container_state", lambda *a, **k: {"passed": True})

    def execute(name, argv, *, cwd, env, timeout_s):
        observed["env"] = dict(env)
        if transport:
            capture_mount = next(m for m in observed["mounts"] if f"dst={cwd}/.responses" in m)
            assert "readonly" not in capture_mount
            capture = Path(capture_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            assert env["HG_RESPONSES_ARTIFACT_DIR"] == f"{cwd}/.responses"
            (capture / "http.jsonl").write_text('{"kind":"request","request_id":"r"}\n')
            (capture / "status.json").write_text('{"truncated":false}')
            for filename in ("opencode.events.jsonl", "opencode.stderr.log", "opencode.session.json"):
                mount = next(m for m in observed["mounts"] if f"dst={cwd}/{filename}" in m)
                path = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
                assert path.is_file() and "readonly" not in mount
                path.write_text("retained diagnostic")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(docker, "exec_in", execute)
    receipt = {}
    # Use the internal seam to isolate runtime propagation from image policy admission.
    result = runner._run_one(repo, {"task_id": "offline-control", "prompt": "unused"},
                             env={"kind": "exec", "state": "container", "image": "not-launched"},
                             runtime_receipt=receipt)
    assert result["exit_code"] == 0
    assert receipt["bun"]["identity"] == preparation["identity"]
    assert observed["env"]["HG_HARNESS_BUN"] == preparation.get("execution_binary", str(binary))
    assert observed["env"]["HG_AGENT_OPENCODE_BUN"] == preparation.get("execution_binary", str(binary))
    assert observed["env"]["HG_HARNESS_SOURCE"] == str(source.resolve())
    assert observed["env"]["HG_HARNESS_ENTRYPOINT"] == str(entry.resolve())
    assert f"type=bind,src={binary.parent},dst={binary.parent},readonly" in observed["mounts"]
    assert f"type=bind,src={store},dst={store},readonly" in observed["mounts"]
    if transport:
        assert f"type=bind,src={overlay},dst={overlay},readonly" in observed["mounts"]
        assert receipt["responses_capture"]["artifacts"][".responses/http.jsonl"]["status"] == "submitted"
        assert receipt["responses_capture"]["complete"] is False
        assert receipt["responses_capture"]["artifacts"]["opencode.stderr.log"]["bytes"] == 19
