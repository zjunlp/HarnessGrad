"""Regression cases for the audited live pristine failure (no daemon required).

The Docker double runs the sealed script against the actual bounded projection.
Live DAC access and Docker enforcement are separately checked by the sealed control.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from eval.admission import scorer_bundle as bundles
from eval.verification import verifier
from test_scorer_isolation_option_b import actor, docker_double, scorer


def replace_script(spec, body):
    declaration = spec["scorer_bundle"]
    root = Path(declaration["root"])
    manifest = json.loads(Path(declaration["manifest"]).read_text())
    (root / "check.py").write_text(body)
    for row in manifest["scorer"]["files"]:
        if row["path"] == "check.py":
            raw = (root / "check.py").read_bytes()
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    raw, pin = bundles.seal_bundle(manifest)
    Path(declaration["manifest"]).write_bytes(raw)
    declaration["manifest_sha256"] = pin
    contract_path = Path(declaration["artifact_contract"])
    contract = json.loads(contract_path.read_text())
    contract["scorer"] = manifest["scorer"]
    raw = json.dumps(contract).encode()
    contract_path.write_bytes(raw)
    declaration["artifact_contract_sha256"] = hashlib.sha256(raw).hexdigest()


def test_d1_projection_is_readable_by_separate_scorer_identity(tmp_path, scorer, docker_double):
    observed = {}

    def observe(info):
        observed["user"] = info["Config"]["User"]
        root = Path(next(m["Source"] for m in info["Mounts"]
                         if m["Destination"] == "/control-input"))
        observed["metadata"] = [(stat.S_IMODE(p.stat().st_mode), p.stat().st_gid)
                                for p in (root, root / "answer.txt")]
    docker_double.mutate = observe
    verdict = verifier._verify(actor(tmp_path, "wrong"), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert observed["user"] == f"65534:{os.getegid()}"
    assert int(observed["user"].split(":")[0]) != os.geteuid()
    assert observed["metadata"] == [(0o750, os.getegid()), (0o640, os.getegid())]
    assert verdict["score"] == 0.0 and not verdict.get("unmeasured")


@pytest.mark.parametrize("user", ["", "0:0", f"65534:{os.getegid() + 1}"])
def test_d1_unexpected_scorer_identity_refuses(tmp_path, scorer, docker_double, user):
    docker_double.mutate = lambda info: info["Config"].update(User=user)
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and verdict.get("unmeasured")
    assert not docker_double.started


def test_d1_private_closure_cannot_start_unreadable(tmp_path, scorer, docker_double):
    Path(scorer["scorer_bundle"]["root"], "check.py").chmod(0o600)
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and "readable" in verdict["unmeasured"]
    assert not docker_double.started


@pytest.mark.parametrize("body,reason", [
    ("import sys; print('Permission denied', file=sys.stderr); sys.exit(1)", "completion"),
    ("import sys; sys.exit(0)", "completion"),
    ("import sys; print('completed_check=fail'); sys.exit(2)", "exit"),
    ("import sys; print('completed_check=pass'); sys.exit(1)", "exit"),
    ("print('completed_check=fail')", "exit"),
    ("print('completed_check=pass\\ncompleted_check=pass')", "completion"),
    ("print('completed_check=pass\\ncompleted_check=fail')", "completion"),
    ("print('completed_check=unknown')", "completion"),
])
def test_d2_incomplete_or_inconsistent_check_is_unmeasured(
        tmp_path, scorer, docker_double, body, reason):
    replace_script(scorer, body + "\n")
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert docker_double.started
    assert verdict["score"] is None and verdict["passed"] is False
    assert verdict["outcome"] == "unmeasured"
    assert reason in verdict["unmeasured"]
    if "Permission denied" in body:
        assert "Permission denied" in verdict["unmeasured"]


@pytest.mark.parametrize("answer,score", [("correct", 1.0), ("wrong", 0.0)])
def test_d2_completed_checks_remain_measurements(tmp_path, scorer, docker_double, answer, score):
    verdict = verifier._verify(actor(tmp_path, answer), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] == score
    assert verdict["outcome"] == "measured"
    assert not verdict.get("unmeasured")


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_d3_native_verdict_retains_stopped_and_cleanup_results(
        tmp_path, scorer, docker_double, cleanup_failure):
    docker_double.cleanup_failure = cleanup_failure
    verdict = verifier._verify(actor(tmp_path, "wrong"), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    # Round-trip the native verdict; no external observer supplies these facts.
    evidence = json.loads(json.dumps(verdict))["scorer_isolation_evidence"]
    assert evidence["stopped_result"]["state"] == docker_double.info["State"]
    assert evidence["stopped_result"]["returncode"] == 0
    assert evidence["start_result"]["returncode"] == 1
    assert evidence["start_result"]["stdout"] == "completed_check=fail\n"
    cleanup = evidence["cleanup"]
    assert cleanup["removed"] is (not cleanup_failure)
    assert cleanup["returncode"] == int(cleanup_failure)
    assert cleanup["stderr"] == ("synthetic cleanup failure" if cleanup_failure else "")
    assert "stdout" in cleanup
    if cleanup_failure:
        assert verdict["score"] is None and verdict["outcome"] == "unmeasured"


def test_d1_nested_projection_modes_survive_restrictive_umask(tmp_path, scorer):
    from eval.admission import artifact_admission as artifacts
    source = actor(tmp_path)
    (source / "one/two").mkdir(parents=True)
    (source / "one/two/answer.txt").write_text("wrong")
    contract = json.loads(Path(scorer["scorer_bundle"]["artifact_contract"]).read_text())
    contract["export"] = dict(files=[dict(path="one/two/answer.txt", max_bytes=16)],
                              max_bytes=16, max_inodes=4)
    raw = json.dumps(contract).encode()
    destination = tmp_path / "input"
    previous = os.umask(0o077)
    try:
        receipt = artifacts.export_state(source, destination, raw,
                                         hashlib.sha256(raw).hexdigest(), run_id="nested")
    finally:
        os.umask(previous)
    for path in (destination, destination / "one", destination / "one/two"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o750
        assert path.stat().st_gid == os.getegid()
    assert stat.S_IMODE((destination / "one/two/answer.txt").stat().st_mode) == 0o640
    assert receipt["bytes"] == 5 and receipt["inodes"] == 4
    artifacts.verify_state_export(destination, receipt, raw,
                                  hashlib.sha256(raw).hexdigest(), run_id="nested")


@pytest.mark.parametrize("failure", ["oom", "runtime-error", "attach-error", "timeout", "inspect-error"])
def test_d2_infrastructure_failure_with_marker_is_unmeasured_and_retained(
        tmp_path, scorer, docker_double, monkeypatch, failure):
    import subprocess
    from eval.containers import container
    original = container.docker

    def docker(*args, **kwargs):
        if args[0] == "start" and failure == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        result = original(*args, **kwargs)
        if args[0] == "start":
            if failure == "oom":
                docker_double.info["State"]["OOMKilled"] = True
            elif failure == "runtime-error":
                docker_double.info["State"]["Error"] = "synthetic runtime error"
            elif failure == "attach-error":
                return subprocess.CompletedProcess(args, 125, result.stdout, "synthetic attach error")
        if args[0] == "inspect" and docker_double.started and failure == "inspect-error":
            return subprocess.CompletedProcess(args, 1, "", "synthetic inspect error")
        return result
    monkeypatch.setattr(container, "docker", docker)
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and verdict["passed"] is False
    assert verdict["outcome"] == ("timeout" if failure == "timeout" else "unmeasured")
    reason = verdict["unmeasured"]
    if failure == "oom":
        assert "OOMKilled" in reason
    elif failure == "runtime-error":
        assert "synthetic runtime error" in reason
    elif failure == "attach-error":
        assert "attach 125" in reason
    elif failure == "timeout":
        assert "timed out" in reason
    else:
        assert "result inspection failed" in reason
    evidence = verdict["scorer_isolation_evidence"]
    assert evidence["cleanup"]["removed"]
    if failure in ("oom", "runtime-error"):
        assert evidence["stopped_result"]["state"] == docker_double.info["State"]
    if failure == "inspect-error":
        assert evidence["stopped_result"]["stderr"] == "synthetic inspect error"
        assert evidence["stopped_result"]["returncode"] == 1


def test_d3_cleanup_exception_retains_native_error(tmp_path, scorer, docker_double, monkeypatch):
    from eval.containers import container
    original = container.docker

    def docker(*args, **kwargs):
        if args[0] == "rm":
            raise OSError("synthetic remove unavailable")
        return original(*args, **kwargs)
    monkeypatch.setattr(container, "docker", docker)
    verdict = verifier._verify(actor(tmp_path), scorer, "", sandbox=False, timeout_s=5, task_id="t", run_id="binding-control", trial_id="round-0.harness-.trial-1")
    assert verdict["score"] is None and verdict["outcome"] == "unmeasured"
    cleanup = verdict["scorer_isolation_evidence"]["cleanup"]
    assert cleanup["removed"] is False
    assert "synthetic remove unavailable" in cleanup["error"]
