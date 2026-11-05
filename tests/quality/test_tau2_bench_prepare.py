"""Read-only preparation gates and deterministic/transactional publication; no model calls.

Synthetic tests need no dataset storage or Python 3.13. The optional real gate only reads
an already provisioned payload; absent storage skips rather than fetching/copying it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("tau2_prepare", ROOT / "data/datasets/tau2_bench/prepare.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


@pytest.fixture
def payload(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    boot = prepare._bootstrap_module()
    monkeypatch.setattr(prepare, "_bootstrap_module", lambda: boot)
    monkeypatch.setattr(prepare.dataset, "DOMAINS", ("airline",))
    monkeypatch.setattr(prepare.dataset, "N_TASKS", {"airline": 1})
    prepare.write(root / "bundle/LICENSE", "Official MIT license")
    prepare.write(root / "bundle/src/tau2/example.py", "official source")
    files = {p.relative_to(root / "bundle").as_posix(): prepare.digest(p)
             for p in (root / "bundle").rglob("*") if p.is_file()}
    prepare.write(root / "bundle/manifest.json", {"release": "v0.1.0", "files": files})
    monkeypatch.setattr(boot, "BUNDLE_SHA256", prepare.digest(root / "bundle/manifest.json"))
    tid = "tau2_bench/airline/0"
    relative = "cases/airline/0"
    prepare.write(root / relative / "public.json", {"task_id": tid, "domain": "airline",
                                                   "policy": "official policy", "tools": [], "user_tools": []})
    prepare.write(root / relative / "private.json", {
        "task_id": tid, "domain": "airline", "release": "v0.1.0", "max_steps": 200,
        "max_errors": 10, "task": {"id": "0", "evaluation_criteria": {"actions": [], "nl_assertions": []}}})
    row = {"task_id": tid, "domain": "airline", "path": relative,
           "needs_judge": False, "user_actions": False,
           **{half + "_sha256": prepare.digest(root / relative / (half + ".json"))
              for half in ("public", "private")}}
    manifest = {"schema": "harnessgrad.tau2_cases.v1", "release": "v0.1.0",
                "archive_sha256": prepare.dataset.TARBALL_SHA256, "counts": {"airline": 1},
                "user_action_tasks": {"airline": 0}, "max_steps": 200, "max_errors": 10, "tasks": [row]}
    prepare.write(root / "manifest.json", manifest)
    monkeypatch.setattr(prepare.dataset, "MANIFEST_SHA256", prepare.digest(root / "manifest.json"))
    return root, boot, manifest


def repin(root, manifest, monkeypatch):
    prepare.write(root / "manifest.json", manifest)
    monkeypatch.setattr(prepare.dataset, "MANIFEST_SHA256", prepare.digest(root / "manifest.json"))


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_check_only_is_read_only_and_does_not_import_official_code(payload, monkeypatch):
    root, _, _ = payload
    before = snapshot(root)
    monkeypatch.setattr(prepare, "check_runtime", lambda runtime: {"version": [3, 13]})
    monkeypatch.setattr(prepare, "write", lambda *a: pytest.fail("check-only wrote a file"))
    monkeypatch.setattr(prepare, "_constructor", lambda *a: pytest.fail("check-only initialized tau2"))
    assert prepare.check_only(root)["tasks"] == 1
    assert snapshot(root) == before


@pytest.mark.parametrize("half", ["public", "private"])
def test_both_halves_are_checked(payload, half):
    root, _, _ = payload
    path = root / "cases/airline/0" / (half + ".json")
    path.write_text("{}")
    with pytest.raises(ValueError, match=half + " task digest mismatch"):
        prepare.validate_cases(root)


def test_expected_manifest_digest_is_not_self_trusted(payload):
    root, _, _ = payload
    with (root / "manifest.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="case manifest digest mismatch"):
        prepare.validate_cases(root)


@pytest.mark.parametrize("failure", ["missing", "changed", "unlisted", "pin", "symlink"])
def test_bundle_complete_files_and_pin(payload, failure):
    root, boot, _ = payload
    asset = root / "bundle/src/tau2/example.py"
    if failure == "missing":
        asset.unlink()
    elif failure == "changed":
        asset.write_text("tampered")
    elif failure == "unlisted":
        (asset.parent / "injected.py").write_text("extra")
    elif failure == "pin":
        (root / "bundle/manifest.json").write_text("{}")
    else:
        asset.unlink()
        asset.symlink_to(root / "bundle/LICENSE")
    with pytest.raises(ValueError, match="missing|digest mismatch|unlisted|symlink"):
        boot.validate_bundle(root / "bundle")


@pytest.mark.parametrize("failure", ["counts", "identity", "flags", "path", "limits"])
def test_inventory_semantics(payload, monkeypatch, failure):
    root, _, manifest = payload
    row = manifest["tasks"][0]
    if failure == "counts":
        manifest["counts"]["airline"] = 2
    elif failure == "identity":
        row["task_id"] = "tau2_bench/airline/other"
    elif failure == "flags":
        row["needs_judge"] = True
    elif failure == "path":
        row["path"] = "../outside"
    else:
        manifest["max_steps"] = 1
    repin(root, manifest, monkeypatch)
    with pytest.raises(ValueError):
        prepare.validate_cases(root)


def test_case_identity_checked_even_with_matching_file_digest(payload, monkeypatch):
    root, _, manifest = payload
    path = root / "cases/airline/0/public.json"
    value = json.loads(path.read_text())
    value["task_id"] = "wrong"
    prepare.write(path, value)
    manifest["tasks"][0]["public_sha256"] = prepare.digest(path)
    repin(root, manifest, monkeypatch)
    with pytest.raises(ValueError, match="public task identity"):
        prepare.validate_cases(root)


@pytest.mark.parametrize("info, message", [
    ({"version": [3, 12], "missing": []}, "Python 3.13"),
    ({"version": [3, 13], "missing": ["litellm"]}, "missing dependencies: litellm"),
])
def test_runtime_readiness_failures(tmp_path, monkeypatch, info, message):
    info["prefix"] = str(tmp_path)
    def run(argv, **kwargs):
        assert "-B" in argv and "-I" in argv
        assert "import tau2" not in argv[-1] and "import litellm" not in argv[-1]
        return SimpleNamespace(stdout=json.dumps(info))
    monkeypatch.setattr(prepare.subprocess, "run", run)
    with pytest.raises(ValueError, match=message):
        prepare.check_runtime(tmp_path)


def test_preparation_failure_never_publishes_partial_payload(tmp_path, monkeypatch):
    target = tmp_path / "new"
    monkeypatch.setattr(prepare.sys, "version_info", (3, 13))
    monkeypatch.setattr(prepare, "bundle", lambda source, staged: staged / "bundle")
    def fail(source, staged, total):
        prepare.write(staged / "cases/partial.json", {"partial": True})
        raise ValueError("injected generation failure")
    monkeypatch.setattr(prepare, "_generate", fail)
    with pytest.raises(ValueError, match="injected"):
        prepare.prepare(tmp_path / "source", target)
    assert not target.exists()
    assert not list(tmp_path.glob(".new-prepare-*"))


def test_reuse_stages_cases_without_copying_or_overwriting_existing(payload, monkeypatch):
    root, _, manifest = payload
    before = snapshot(root)
    monkeypatch.setattr(prepare.sys, "version_info", (3, 13))
    monkeypatch.setattr(prepare, "bundle", lambda *a: pytest.fail("reuse copied bundle"))
    def generate(source, staged, total):
        assert total == root / "bundle"
        assert staged != root
        # For this synthetic transaction test, the generated cases are the known fixture.
        import shutil
        shutil.copytree(root / "cases", staged / "cases")
        prepare.write(staged / "manifest.json", manifest)
        return manifest
    monkeypatch.setattr(prepare, "_generate", generate)
    assert prepare.prepare(root.parent / "source", root, reuse=True) == manifest
    assert snapshot(root) == before


def test_source_only_bundle_pin_is_accepted(payload, monkeypatch):
    root, boot, _ = payload
    monkeypatch.setattr(boot, "SOURCE_BUNDLE_SHA256", boot.BUNDLE_SHA256)
    monkeypatch.setattr(boot, "BUNDLE_SHA256", "legacy-other-digest")
    assert boot.validate_bundle(root / "bundle")["release"] == "v0.1.0"


def test_bundle_does_not_trust_unlisted_symlink_directories(payload):
    root, boot, _ = payload
    (root / "bundle/src/injected").symlink_to(root / "cases", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        boot.validate_bundle(root / "bundle")


def test_preparation_publishes_only_after_expected_digest_validates(payload, monkeypatch):
    root, _, manifest = payload
    target = root.parent / "new"
    monkeypatch.setattr(prepare.sys, "version_info", (3, 13))
    monkeypatch.setattr(prepare, "bundle", lambda source, staged: staged / "bundle")
    def generate(source, staged, total):
        import shutil
        shutil.copytree(root / "cases", staged / "cases")
        prepare.write(staged / "manifest.json", manifest)
        assert not target.exists()
        return manifest
    monkeypatch.setattr(prepare, "_generate", generate)
    assert prepare.prepare(root.parent / "source", target) == manifest
    assert prepare.validate_cases(target) == manifest


def test_failed_reproduction_never_overwrites_existing(payload, monkeypatch):
    root, _, _ = payload
    before = snapshot(root)
    monkeypatch.setattr(prepare.sys, "version_info", (3, 13))
    def generate(source, staged, total):
        prepare.write(staged / "manifest.json", {"wrong": "generation"})
        return {}
    monkeypatch.setattr(prepare, "_generate", generate)
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        prepare.prepare(root.parent / "source", root, reuse=True)
    assert snapshot(root) == before
    assert not list(root.parent.glob(".payload-prepare-*"))


def test_check_only_cli_requires_no_source_and_reports_failures(tmp_path):
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "data/datasets/tau2_bench/prepare.py"),
                           "--check-only", "--target", str(tmp_path / "absent")],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 1
    assert "tau2 preparation failed" in proc.stderr and "manifest.json" in proc.stderr
    assert not (tmp_path / "absent").exists()


def test_real_prepared_payload_is_read_only_when_available():
    try:
        root = Path(prepare.dataset.DATASET_ROOT)
        runtime = Path(prepare.dataset.RUNTIME)
    except Exception:
        pytest.skip("dataset storage is not configured")
    if not (root / "bundle/manifest.json").is_file() or not (runtime / "bin/python").is_file():
        pytest.skip("tau2 payload/runtime are not provisioned")
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "data/datasets/tau2_bench/prepare.py"),
                           "--check-only", "--target", str(root), "--runtime", str(runtime)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["tasks"] == 278 and result["check_only"] is True
