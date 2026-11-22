"""Authored content-admission controls with no live model or benchmark calls."""
import hashlib
import json

import pytest

from harnessgrad import config
from harnessgrad.provenance import h0_content, scoring_identity, verification_context
from harnessgrad.provenance.runtime_content import canonical


def serving_receipt(path, model, route, *, weights="a" * 64, temperature=0):
    path.write_bytes(canonical({
        "schema": h0_content.AGENT_SCHEMA, "model": model,
        "endpoint_sha256": hashlib.sha256(route.encode()).hexdigest(),
        "content": {"weights_sha256": weights, "backend": "mock",
                    "decoding": {"temperature": temperature, "max_tokens": 16},
                    "harness_config": {"max_steps": 8}},
    }))
    return path


@pytest.fixture
def portable_run(tmp_path, monkeypatch):
    import driver

    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.setenv("HG_AGENT_MODEL", "job-original")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "")
    config.reset()
    calls = []

    def evaluate(repo, tasks, scorable, cache, **kwargs):
        calls.append(kwargs["harness_sha"])
        ids = [task["task_id"] for task in tasks]
        return {"per_task": {tid: .5 for tid in ids},
                "identity_declared": {"config": {"max_steps": 8}},
                "verification_context": kwargs["verification_context"], "trials": 1,
                "traces": {tid: '{"event":"authored"}\n' for tid in ids}}

    def run(name, *extra, tasks="t01"):
        argv = ["driver.py", "--harness", "loop_plain", "--dataset", "demo", "--sampling", "all",
                "--rounds", "0", "--tasks", tasks, "--improver", "none", "--no-summary",
                "--no-sandbox", "--run-id", name, "--runs-root", str(tmp_path / "runs"),
                "--work-root", str(tmp_path / "work"), *map(str, extra)]
        monkeypatch.setattr("sys.argv", argv)
        return driver.main()

    monkeypatch.setattr(driver, "evaluate", evaluate)
    donor = tmp_path / "runs" / "donor"
    original = serving_receipt(tmp_path / "serving-original.json", "job-original", "")
    assert run("donor", "--h0-agent-content", original) == 0
    pin = hashlib.sha256((donor / h0_content.FILENAME).read_bytes()).hexdigest()
    monkeypatch.setenv("HG_AGENT_MODEL", "job-other")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "http://localhost:39099/v1")
    config.reset()
    recipient = serving_receipt(tmp_path / "serving-recipient.json", "job-other", "http://localhost:39099/v1")

    def reuse(*args, **kwargs):
        return run(*args, "--h0-from", "donor", "--h0-comparability", "content",
                   "--h0-agent-content", recipient, "--h0-attestation-sha256", pin, **kwargs)

    yield run, reuse, donor, recipient, calls
    config.reset()


@pytest.mark.parametrize("curve_present", [True, False])
def test_content_h0_ignores_deployment_but_does_not_execute(portable_run, curve_present, capsys):
    run, reuse, donor, _, calls = portable_run
    assert run("strict", "--h0-from", "donor") == 2
    assert "agent_model differs" in capsys.readouterr().err
    (donor / "run_meta.json").unlink()
    (donor / "h0.json").unlink()
    if not curve_present:
        (donor / "curve.jsonl").unlink()
    before = {p.relative_to(donor): p.read_bytes() for p in donor.rglob("*") if p.is_file()}
    assert reuse("recipient") == 0
    point = json.loads((donor.parent / "recipient" / "curve.jsonl").read_text())
    assert len(calls) == 1 and point["score"] == .5
    assert point["current_run_cost"]["evaluation_trials"] == 0
    assert point["shared_from"]["comparability"] == "content"
    assert set(point["shared_from"]["deployment_difference"]) == {"agent_model", "endpoint_sha256"}
    assert (donor.parent / "recipient" / "round0_traces" / "t01.jsonl").is_file()
    assert before == {p.relative_to(donor): p.read_bytes() for p in donor.rglob("*") if p.is_file()}
    assert run("resume", "--h0-from", "recipient") == 2


@pytest.mark.parametrize("what", ["weights", "decoding", "harness_config", "tasks", "base_tree",
                                 "tampered_trace", "tampered_attestation", "missing_attestation"])
def test_content_mode_refuses_unproven_comparability(portable_run, what, capsys):
    _, reuse, donor, recipient, calls = portable_run
    extra = []
    tasks = "t01"
    if what in {"weights", "decoding", "harness_config"}:
        receipt = json.loads(recipient.read_text())
        if what == "weights":
            receipt["content"]["weights_sha256"] = "b" * 64
        elif what == "decoding":
            receipt["content"]["decoding"]["temperature"] = .5
        else:
            receipt["content"]["harness_config"]["max_steps"] = 10
        recipient.write_bytes(canonical(receipt))
    elif what == "tasks":
        tasks = "t02"
    elif what == "base_tree":
        extra.extend(("--harness", "oracle"))
    elif what == "tampered_trace":
        (donor / "round0_traces" / "t01.jsonl").write_text("different\n")
    elif what == "tampered_attestation":
        with (donor / h0_content.FILENAME).open("ab") as output:
            output.write(b"\n")
    elif what == "missing_attestation":
        (donor / h0_content.FILENAME).unlink()
    assert reuse("recipient", *extra, tasks=tasks) == 2
    assert len(calls) == 1
    assert not (donor.parent / "recipient" / "curve.jsonl").exists()


def test_content_verifier_binds_bytes_and_retains_unknown_fields(tmp_path):
    left, right = tmp_path / "old" / "check.py", tmp_path / "new" / "check.py"
    tasks, scorable = [{"task_id": "a"}], {}
    for path in (left, right):
        path.parent.mkdir()
        path.write_text("# stable source\n")

    def capture(fn, path, **extra):
        return fn(tasks, scorable, verifiers={"a": {"inputs": [
            {"from": str(path), "path": "check.py"}], "argv": ["python", "check.py"], **extra}})

    assert capture(verification_context.capture, left) != capture(verification_context.capture, right)
    before = capture(verification_context.capture_content, left)
    assert before == capture(verification_context.capture_content, right)
    assert before != capture(verification_context.capture_content, right, unknown="/tmp/opaque")
    right.write_text("# changed source\n")
    assert before != capture(verification_context.capture_content, right)


def test_uncommitted_bound_runtime_and_unknown_judge_are_refused(tmp_path):
    binding = {"schema": "harnessgrad.python-runtime-binding.v1",
               "dataset": "alfworld", "profile": "execution", "role": "verifier",
               "runtime_id": "1" * 64, "manifest_path": str(tmp_path / "artifact.json"),
               "python": str(tmp_path / "venv/bin/python"), "path": str(tmp_path / "venv"),
               "runtime_paths": [str(tmp_path / "venv")]}
    with pytest.raises(ValueError, match="verification context unavailable"):
        verification_context.capture_content([{"task_id": "a"}], {},
            verifiers={"a": {"python_runtime": binding, "argv": [binding["python"]]}})
    profile = {"metric": "authored", "endpoint_sha256": "a" * 64, "unknown_adapter_flag": 1}
    with pytest.raises(ValueError, match="pinned judge"):
        scoring_identity.content_key(scoring_identity.create(profile))
    profile["judge"] = {"weights_sha256": "c" * 64, "decoding": {"temperature": 0}}
    def key(**extra):
        return scoring_identity.content_key(scoring_identity.create({**profile, **extra}))
    assert key() == key(endpoint_sha256="b" * 64)
    assert key() != key(unknown_adapter_flag=2)


def test_release_packaging_preserves_unclaimed_platform_measurement(portable_run, tmp_path):
    from tools import prepare_h0_content

    run, _, donor, recipient, calls = portable_run
    envelope = json.loads((donor / h0_content.FILENAME).read_text())
    point = envelope["measurement_evidence"]["point"]
    point.pop("measured_by_platform")
    (donor / h0_content.FILENAME).unlink()
    (donor / "curve.jsonl").write_text(json.dumps(point) + "\n")
    review = tmp_path / "reviewed.json"
    from harnessgrad.provenance.runtime_content import digest
    review.write_bytes(canonical({
        **{key: envelope[key] for key in ("run_id", "content", "agent", "deployment")},
        "schema": prepare_h0_content.REVIEW_SCHEMA, "source": "synthetic-publication",
        "point_sha256": digest(point), "traces": envelope["measurement_evidence"]["traces"],
    }))
    digest_pin = hashlib.sha256(review.read_bytes()).hexdigest()
    pin = prepare_h0_content.package(donor, point, review, digest_pin)
    assert run("recipient", "--h0-from", "donor", "--h0-comparability", "content",
               "--h0-agent-content", recipient, "--h0-attestation-sha256", pin) == 0
    imported = json.loads((donor.parent / "recipient" / "curve.jsonl").read_text())
    assert "measured_by_platform" not in imported
    assert imported["shared_from"]["measurement_origin"] == "published-measurement"
    assert len(calls) == 1
    metadata = json.loads((donor.parent / "recipient" / "run_meta.json").read_text())
    assert metadata["completion"]["status"] == "complete"
    assert run("recipient", "--resume") == 0
    resumed = json.loads((donor.parent / "recipient" / "curve.jsonl").read_text())
    assert "measured_by_platform" not in resumed
    assert resumed["shared_from"]["attestation_sha256"] == pin and len(calls) == 1
    assert run("nested", "--h0-from", "recipient", "--h0-comparability", "content",
               "--h0-agent-content", recipient, "--h0-attestation-sha256", pin) == 2


def test_runtime_content_survives_a_verified_local_relocation(tmp_path, monkeypatch):
    from harnessgrad.datasets import runtime_identity
    from harnessgrad.execution.python_runtime.python_artifacts import canonical as runtime_json

    def binding(root, requirements="a" * 64):
        venv = root / "venv"
        executable = root / "cpython" / "bin" / "python"
        venv.mkdir(parents=True)
        config_file = venv / "pyvenv.cfg"
        config_file.write_text(f"home = {executable.parent}\nexecutable = {executable}\n")
        manifest = {"schema": "authored-v1", "dataset": "alfworld", "profile": "execution",
                    "descriptor_sha256": "b" * 64, "requirements_sha256": requirements,
                    "path": str(venv), "key": str(root), "recipe": {"version": 1, "uv": root.name},
                    "base_python": {"executable": str(executable), "version": "3.12",
                                    "sha256": "c" * 64, "base_prefix": str(executable.parent.parent)},
                    "files": {"pyvenv.cfg": {"kind": "file", "mode": 0o444,
                              "sha256": hashlib.sha256(config_file.read_bytes()).hexdigest()}}}
        manifest_path = root / "artifact.json"
        manifest_path.write_bytes(runtime_json(manifest))
        return {"schema": "harnessgrad.python-runtime-binding.v1", "dataset": "alfworld",
                "profile": "execution", "role": "service",
                "runtime_id": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "manifest_path": str(manifest_path), "python": str(venv / "bin" / "python"),
                "path": str(venv), "runtime_paths": [str(venv)]}

    def artifact(path, requirements="a" * 64):
        return runtime_identity.create(tasks={"t1": {"service": binding(tmp_path / path, requirements)}})

    monkeypatch.setattr(runtime_identity, "admit", lambda value: value)
    source, target = artifact("old"), artifact("new")
    assert source != target
    assert runtime_identity.content_key(source) == runtime_identity.content_key(target)
    assert runtime_identity.content_key(source) != runtime_identity.content_key(artifact("changed", "d" * 64))
