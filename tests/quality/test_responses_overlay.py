"""Prepared transport identity and task-owned receipts; no model client."""
import json
from pathlib import Path

import pytest

from eval.containers import responses_overlay as overlay
from eval.containers import bun_runtime
from test_bun_runtime_contract import harness, fake_runtime


def configured(tmp_path, *, mode="repair", route="https://coding.tu-zi.com/v1/responses"):
    bun=tmp_path/"runtime/bun"
    bun.parent.mkdir(exist_ok=True)
    bun.write_bytes(b"offline fixture binary")
    bun.chmod(0o755)
    result=overlay.configure(bun, tmp_path/"overlays", route=route, mode=mode)
    return bun,result


def test_setup_lookup_identity_and_distinct_real_binary(tmp_path):
    bun,result=configured(tmp_path)
    found=overlay.lookup(Path(result["path"]),bun)
    assert found==result
    assert Path(result["launcher"]) != bun
    assert result["identity"]["mode"]=="repair"
    assert result["identity"]["repair_events"]==["response.output_text.delta"]
    assert result["identity"]["binary_sha256"]==bun_runtime.file_digest(bun)


def test_setup_refuses_to_write_inside_bun_installation(tmp_path):
    bun,_=configured(tmp_path)
    destination=bun.parent/"responses"
    with pytest.raises(ValueError,match="Bun installation"):
        overlay.configure(bun,destination,route="https://coding.tu-zi.com/v1/responses")
    assert not destination.exists()


def test_launcher_preserves_task_directory_with_distinct_source_cwd(tmp_path):
    import os
    import subprocess
    import sys
    bun=tmp_path/"runtime/bun"
    bun.parent.mkdir()
    bun.write_text(f"#!{sys.executable}\nimport json,os\nprint(json.dumps({{'cwd':os.getcwd(),'pwd':os.environ.get('PWD')}}))\n")
    bun.chmod(0o755)
    task=tmp_path/"task with spaces";task.mkdir()
    source=tmp_path/"source";source.mkdir()
    prepared=overlay.configure(bun,tmp_path/"overlays",route="https://coding.tu-zi.com/v1/responses")
    result=subprocess.run([prepared["launcher"],"entry.ts"],cwd=source,
        env={"PATH":"/usr/bin:/bin","PWD":str(task),"HG_RESPONSES_ARTIFACT_DIR":str(task/".responses")},
        capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)=={"cwd":str(source),"pwd":str(task)}


@pytest.mark.parametrize("file", ["launcher","responses_preload.mjs","responses_transport.mjs","responses_capture.mjs","manifest.json"])
def test_tampered_prepared_overlay_fails_closed(tmp_path,file):
    bun,result=configured(tmp_path)
    p=Path(result["path"])/file
    p.chmod(0o644)
    p.write_text(p.read_text()+"\nmutation")
    with pytest.raises(ValueError,match="overlay"):
        overlay.lookup(Path(result["path"]),bun)


def test_changed_mode_route_or_bun_changes_overlay_identity(tmp_path):
    bun,a=configured(tmp_path)
    b=overlay.configure(bun,tmp_path/"overlays",route=a["identity"]["route"],mode="observe")
    c=overlay.configure(bun,tmp_path/"overlays",route="http://127.0.0.1:8123/v1/responses",mode="repair",allow_loopback=True)
    assert len({a["identity"]["sha256"],b["identity"]["sha256"],c["identity"]["sha256"]})==3
    bun.write_bytes(b"changed binary")
    with pytest.raises(ValueError,match="overlay"):
        overlay.lookup(Path(a["path"]),bun)


@pytest.mark.parametrize("route", ["https://other.example/v1/responses","https://coding.tu-zi.com/v1/responses?key=x",
                                  "https://user:secret@coding.tu-zi.com/v1/responses","http://127.0.0.1:8123/v1/responses"])
def test_setup_rejects_unapproved_route(tmp_path,route):
    with pytest.raises(ValueError,match="route"):
        configured(tmp_path,route=route)


def test_formal_prepare_never_builds_missing_overlay(tmp_path,monkeypatch):
    repo=harness(tmp_path); bun,_,calls=fake_runtime(tmp_path,monkeypatch)
    first=bun_runtime.configure(repo)
    assert first["identity"]["responses_overlay"] is None
    monkeypatch.setenv("HG_RESPONSES_OVERLAY",str(tmp_path/"absent"))
    calls.clear()
    with pytest.raises(bun_runtime.RuntimeUnavailable,match="overlay"):
        bun_runtime.prepare(repo)
    assert not any("install" in c for c in calls)


def test_transport_change_invalidates_measurement_not_dependency_store(tmp_path,monkeypatch):
    repo=harness(tmp_path); bun,_,calls=fake_runtime(tmp_path,monkeypatch)
    before=bun_runtime.configure(repo)
    prepared=overlay.configure(bun,tmp_path/"overlays",route="https://coding.tu-zi.com/v1/responses",mode="repair")
    monkeypatch.setenv("HG_RESPONSES_OVERLAY",prepared["path"])
    calls.clear()
    after=bun_runtime.prepare(repo)
    assert after["binary"]==str(bun)
    assert after["execution_binary"]==prepared["launcher"]
    assert after["identity"]!=before["identity"]
    assert after["dependency_key"]==before["dependency_key"]
    assert not after["installed"] and not any("install" in c for c in calls)
    assert prepared["path"] in after["runtime_paths"]


def test_mixed_or_missing_overlay_identities_refuse_comparison(tmp_path):
    _,a=configured(tmp_path)
    same={"bun":{"responses_overlay":a["identity"]}}
    overlay.require_comparable([same],[same])
    for other in [{}, {"bun":{}}, {"bun":{"responses_overlay":None}}]:
        with pytest.raises(ValueError,match="overlay"):
            overlay.require_comparable([same],[other])


def test_capture_collection_keeps_failure_evidence_and_bounds_untrusted_files(tmp_path):
    out=tmp_path/".responses"; out.mkdir()
    (out/"http.jsonl").write_text('{"kind":"fetch_error","message":"offline failure"}\n')
    (tmp_path/"opencode.stderr.log").write_text("provider failed")
    result=overlay.collect_capture(tmp_path)
    assert result["artifacts"][".responses/http.jsonl"]["status"]=="submitted"
    assert result["complete"] is False
    (out/"http.jsonl").unlink()
    (out/"http.jsonl").symlink_to(tmp_path/"opencode.stderr.log")
    result=overlay.collect_capture(tmp_path)
    assert result["artifacts"][".responses/http.jsonl"]["status"]=="invalid"
    assert result["artifacts"]["opencode.stderr.log"]["status"]=="submitted"


@pytest.mark.parametrize("log,status", [("null\n", '{"truncated":false}'), ('{"kind":"request","request_id":"r"}\n', "[]")])
def test_malformed_capture_does_not_erase_original_failure(tmp_path,log,status):
    out=tmp_path/".responses";out.mkdir()
    (out/"http.jsonl").write_text(log)
    (out/"status.json").write_text(status)
    result=overlay.collect_capture(tmp_path)
    assert result["complete"] is False
    assert result["artifacts"][".responses/http.jsonl"]["status"] == "submitted"


@pytest.mark.parametrize("timeout", [False,True])
def test_formal_failure_keeps_capture_after_task_cleanup(tmp_path,monkeypatch,timeout):
    import subprocess
    from types import SimpleNamespace
    from eval.run import runner
    repo=harness(tmp_path); bun,_,_=fake_runtime(tmp_path,monkeypatch)
    bun_runtime.configure(repo)
    prepared=overlay.configure(bun,tmp_path/"overlays",route="https://coding.tu-zi.com/v1/responses",mode="repair")
    monkeypatch.setenv("HG_RESPONSES_OVERLAY",prepared["path"])
    seen={}
    def execute(argv,*,cwd,env,timeout_s):
        seen.update(env=env,work=Path(cwd))
        capture=Path(env["HG_RESPONSES_ARTIFACT_DIR"])
        (capture/"http.jsonl").write_text('{"kind":"request","request_id":"r"}\n')
        (capture/"status.json").write_text('{"truncated":false}')
        (Path(cwd)/"opencode.stderr.log").write_text("preserved failure")
        if timeout: raise subprocess.TimeoutExpired(argv,timeout_s)
        return SimpleNamespace(returncode=1,stderr="provider failure")
    monkeypatch.setattr(runner,"run_process",execute)
    result=runner.run_one(repo,{"task_id":"offline-failure","prompt":"unused"},sandbox=False,timeout_s=1)
    assert seen["env"]["HG_HARNESS_BUN"]==prepared["launcher"]
    assert result["exit_code"]==(-1 if timeout else 1)
    assert not seen["work"].exists()
    capture=result["runtime_services"]["responses_capture"]
    assert capture["artifacts"]["opencode.stderr.log"]["status"]=="submitted"
    assert capture["complete"] is False
    assert result["harness_runtime"]["bun"]["responses_overlay"]==prepared["identity"]


def test_evaluation_runtime_drift_is_unmeasured_and_retains_evidence(tmp_path,monkeypatch):
    from eval.run import runner
    repo=harness(tmp_path); fake_runtime(tmp_path,monkeypatch)
    identity=bun_runtime.configure(repo)["identity"]
    different={**identity,"responses_overlay":{"sha256":"unexpected"}}
    monkeypatch.setattr(runner,"snapshot",lambda _: {})
    monkeypatch.setattr(runner,"run_one",lambda *a,**k:{"task_id":"t","answer":"ok","trace":"","exit_code":0,
        "stderr":"","verdict":{"passed":True},"harness_runtime":{"bun":different},
        "runtime_services":{"responses_capture":{"complete":False}}})
    cache={}
    result=runner._evaluate(repo,[{"task_id":"t","prompt":"unused"}],{},cache=cache,harness_sha="same")
    assert result["invalid"]["t"]
    assert not cache
    assert {"bun":different} in result["harness_runtime"]
    assert result["runtime_diagnostics"]["t"]["responses_capture"]["complete"] is False


def test_trusted_selection_rejects_differently_measured_overlays(tmp_path):
    from harnessgrad.trusted_runner import TrustedRunnerFacade,TrustedRunnerError
    facade=TrustedRunnerFacade(tmp_path/"facade")
    for cid,score,transport in [("a",0,None),("b",1,{"sha256":"new"})]:
        facade.evaluations[cid]={"candidate_id":cid,"aggregate_score":score,"measurement_status":"eligible",
            "task_outcomes":[{"trials":[{"harness_runtime":{"bun":{"responses_overlay":transport}}}]}]}
    with pytest.raises(TrustedRunnerError,match="overlay"):
        facade._select({"incumbent_id":"a","candidate_ids":["b"],"evaluation_ids":["a","b"]})


def test_trusted_selection_refuses_missing_trial_evidence(tmp_path):
    from harnessgrad.trusted_runner import TrustedRunnerFacade,TrustedRunnerError
    facade=TrustedRunnerFacade(tmp_path/"facade")
    facade.evaluations["a"]={"candidate_id":"a","aggregate_score":0,"measurement_status":"eligible",
        "task_outcomes":[{"trials":[{"harness_runtime":{"bun":{"responses_overlay":None}}}]}]}
    facade.evaluations["b"]={"candidate_id":"b","aggregate_score":1,"measurement_status":"eligible",
        "task_outcomes":[]}
    with pytest.raises(TrustedRunnerError,match="overlay"):
        facade._select({"incumbent_id":"a","candidate_ids":["b"],"evaluation_ids":["a","b"]})


def test_declared_bun_candidates_reject_two_legacy_evaluations(tmp_path):
    from harnessgrad.trusted_runner import TrustedRunnerFacade,TrustedRunnerError
    facade=TrustedRunnerFacade(tmp_path/"facade")
    repo=harness(tmp_path)
    for cid,score in [("a",0),("b",1)]:
        facade.candidates[cid]={"workspace":str(repo)}
        facade.evaluations[cid]={"candidate_id":cid,"aggregate_score":score,"measurement_status":"eligible",
                               "task_outcomes":[{"trials":[{}]}]}
    with pytest.raises(TrustedRunnerError,match="overlay"):
        facade._select({"incumbent_id":"a","candidate_ids":["b"],"evaluation_ids":["a","b"]})


def test_overlay_mode_changes_formal_cache_key_without_reinstall(tmp_path,monkeypatch):
    from eval.run import runner
    repo=harness(tmp_path); bun,_,installs=fake_runtime(tmp_path,monkeypatch)
    bun_runtime.configure(repo)
    installs.clear(); executed=[]; cache={}
    def run(repo,task,*a,runtime_receipt,**k):
        executed.append(task["task_id"])
        runtime_receipt["bun"]=bun_runtime.prepare(repo)
        runtime_receipt["responses_capture"]={"complete":True,"artifacts":{"fixture":"retained"}}
        return {"task_id":task["task_id"],"answer":"ok","trace":"","exit_code":0,
                "stderr":"","verdict":{"passed":True}}
    monkeypatch.setattr(runner,"_run_one",run)
    monkeypatch.setattr(runner,"snapshot",lambda _: {})
    task=[{"task_id":"t","prompt":"offline"}]
    identities=[]
    for mode in ["observe","observe","repair"]:
        prepared=overlay.configure(bun,tmp_path/"overlays",route="https://coding.tu-zi.com/v1/responses",mode=mode)
        monkeypatch.setenv("HG_RESPONSES_OVERLAY",prepared["path"])
        result=runner._evaluate(repo,task,{},cache=cache,harness_sha="same-source")
        identities.append(result["harness_runtime"][0]["bun"]["responses_overlay"])
        assert result["runtime_diagnostics"]["t"]["responses_capture"]["artifacts"]=={"fixture":"retained"}
    assert executed==["t","t"]
    assert identities[0]==identities[1]!=identities[2]
    assert not result["cached_tasks"]
    assert not any("install" in command for command in installs)
