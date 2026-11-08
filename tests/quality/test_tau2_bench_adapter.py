"""Adapter capability declarations without dataset storage, providers or the 3.13 runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.datasets import tau2_bench as dataset
from harnessgrad import config


@pytest.fixture
def selected_case(tmp_path, monkeypatch):
    tid = "tau2_bench/telecom/synthetic"
    public = {"task_id": tid, "domain": "telecom", "policy": "public policy",
              "tools": [], "user_tools": [{"type": "function", "function": {
                  "name": "toggle_data", "description": "fixture schema", "parameters": {
                      "type": "object", "properties": {}}}}]}
    folder = tmp_path / "cases/telecom/synthetic"
    folder.mkdir(parents=True)
    raw = json.dumps(public).encode()
    (folder / "public.json").write_bytes(raw)
    private = {"user_scenario": "PRIVATE-SCENARIO-SENTINEL", "evaluation_criteria": {
        "actions": ["PRIVATE-GOLD-SENTINEL"]}}
    private_raw = json.dumps(private).encode()
    (folder / "private.json").write_bytes(private_raw)
    row = {"domain": "telecom", "path": "cases/telecom/synthetic", "task_id": tid,
           "public_sha256": hashlib.sha256(raw).hexdigest(),
           "private_sha256": hashlib.sha256(private_raw).hexdigest(), "needs_judge": False}
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    monkeypatch.setattr(dataset, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(dataset, "roster", lambda: {tid: row})
    monkeypatch.setattr(config, "current", lambda: config.load({
        "HG_ENV_MODEL": "fixture-user", "HG_JUDGE_MODEL": "fixture-judge"}))
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(dataset, "_execution_runtime", lambda: {
        "dataset": "tau2_bench", "profile": "execution", "runtime_id": "a" * 64,
        "manifest_path": str(tmp_path / "artifact.json"), "path": str(runtime),
        "python": str(runtime / "bin/python"), "runtime_paths": [str(runtime)]})
    return SimpleNamespace(tid=tid, row=row, folder=folder, public=public)


def test_actor_materialization_contains_no_private_case(selected_case):
    case = selected_case
    task = dataset.control_task(case.tid)
    setup = dataset.SETUP[case.tid]
    assert task["inputs"] == []
    assert {f["path"] for f in setup["files"]} == {
        "tau2.py", "tau2_transport.py", "tau2_public.json"}
    actor_bytes = json.dumps({"task": task, "files": setup["files"]})
    assert "PRIVATE-SCENARIO-SENTINEL" not in actor_bytes
    assert "PRIVATE-GOLD-SENTINEL" not in actor_bytes
    assert "private.json" not in actor_bytes
    staged_public = json.loads(next(item["content"] for item in setup["files"]
                                    if item["path"] == "tau2_public.json"))
    assert "user_tools" not in staged_public
    assert "toggle_data" not in json.dumps(staged_public)
    assert staged_public["policy"] == case.public["policy"]
    # The private path belongs to the host relay declaration, never SETUP.files.
    host = json.loads(setup["tool_relay"]["content"])
    assert host["case"] == str(case.folder / "private.json")
    assert host["sha256"] == case.row["private_sha256"]
    model = setup["tool_relay"]["model"]
    assert model["model"] == "fixture-user"
    assert model["tools"] == case.public["user_tools"]
    assert model["seed_source"] == "trial"
    assert dataset.ENV[case.tid] == {"kind": "files", "network": "none"}


@pytest.mark.parametrize("needs_judge", [False, True])
def test_judge_capability_matches_the_manifest_flag(selected_case, needs_judge):
    selected_case.row["needs_judge"] = needs_judge
    declaration = dataset.VERIFY[selected_case.tid]
    assert ("judge" in declaration) is needs_judge
    if needs_judge:
        assert declaration["judge"]["model"] == "fixture-judge"
        assert declaration["judge"]["max_calls"] == 1
        assert "seed_source" not in declaration["judge"]
    assert declaration["argv"][1] == "-I"
    assert declaration["argv"][-2:] == ["--case-sha256", selected_case.row["private_sha256"]]
    assert declaration["result_contract"]["require_exit_zero"] is True
    assert any(item["path"] == "tau2_private.json" for item in declaration["inputs"])


def test_tampered_public_half_is_refused_before_model_resolution(selected_case, monkeypatch):
    (selected_case.folder / "public.json").write_text("tampered")
    monkeypatch.setattr(dataset, "model_spec", lambda *args: pytest.fail("model resolved before pin"))
    with pytest.raises(ValueError, match="public task digest mismatch"):
        dataset.SETUP[selected_case.tid]


def test_directories_alone_do_not_claim_provisioning(tmp_path, monkeypatch):
    (tmp_path / "runtime/bin").mkdir(parents=True)
    (tmp_path / "runtime/bin/python").write_text("not an interpreter")
    (tmp_path / "bundle").mkdir()
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    monkeypatch.setattr(dataset, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(dataset, "_execution_runtime", lambda: {
        "path": str(tmp_path / "runtime"), "runtime_id": "a" * 64})
    status = dataset.provision_status()
    assert status["present"] is False
    assert status["tasks"] == 0
    assert "manifest" in status["detail"]


def test_provisioning_requires_valid_gate_receipt(selected_case, monkeypatch):
    receipt = {"tasks": 278, "manifest_sha256": dataset.MANIFEST_SHA256}
    def checked(argv, **kwargs):
        assert "--check-only" in argv and "-B" in argv
        assert "--runtime" in argv
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt), stderr="")
    monkeypatch.setattr(dataset.subprocess, "run", checked)
    status = dataset.provision_status()
    assert status["present"] is True
    assert status["model_health_checked"] is False
    receipt["manifest_sha256"] = "different"
    assert dataset.provision_status()["present"] is False
