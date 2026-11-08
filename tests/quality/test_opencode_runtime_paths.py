"""Offline admission controls for the packaged OpenCode runtime."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from base_harness.opencode import opencode_runtime as runtime
from eval.containers import external_runtime
from tools import configure_opencode_harness as prepare

SOURCE = Path(__file__).resolve().parents[2] / "base_harness/opencode"
needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")


@pytest.fixture
def installed(tmp_path):
    with tempfile.TemporaryDirectory(prefix="hg-opencode-release-", dir="/var/tmp") as directory:
        package = Path(directory) / "package"
        binary = package / "bin/opencode"
        binary.parent.mkdir(parents=True)
        shutil.copyfile("/bin/true", binary)
        binary.chmod(0o755)
        (package / "package.json").write_text(json.dumps({"name": "@opencode/cli-linux-x64", "version": "2.2.6"}))
        harness = tmp_path / "candidate"
        shutil.copytree(SOURCE, harness)
        yield harness, binary


@pytest.fixture
def version_probe(monkeypatch):
    def report(version):
        def run(argv, **kwargs):
            result = subprocess.run(argv, **kwargs)
            if result.returncode == 0:
                result.stdout = f"opencode v{version}\n"
            return result

        monkeypatch.setattr(prepare, "subprocess", SimpleNamespace(run=run))

    report("2.2.6")
    return report


def test_unprepared_template_is_rejected(installed):
    harness, _ = installed
    with pytest.raises(ValueError, match="configure_opencode_harness.py"):
        runtime.resolve(harness, {})


@needs_bwrap
def test_preparation_pins_native_release_and_does_not_import_candidate_code(installed, version_probe):
    harness, binary = installed
    (harness / "opencode_runtime.py").write_text("raise AssertionError('candidate imported')\n")
    selected = prepare.configure(harness, binary, expected_version="2.2.6")
    assert selected["profile"] == "2"
    assert selected["argv"] == [str(binary)]
    assert selected["runtime_paths"] == [str(binary), str(binary.parent.parent / "package.json")]
    assert runtime.resolve(harness, {"PATH": "/does-not-exist"}) == selected
    assert external_runtime.identities(harness, {})["opencode"]["cacheable"]


@needs_bwrap
def test_opencode_identity_is_required_for_results_trials_and_checkpoints(installed, version_probe, tmp_path):
    harness, binary = installed
    prepare.configure(harness, binary)
    current = external_runtime.identities(harness, {})
    result = external_runtime.stamp({"n_trials": 1}, current)
    changed = {"opencode": {**current["opencode"], "installation_sha256": "0" * 64}}
    for services in (False, True):
        external_runtime.require_records(current, result, services=services)
        with pytest.raises(external_runtime.RuntimeUnavailable, match="missing or changed"):
            external_runtime.require_records(changed, result, services=services)
    point = {"round": 0, "env": external_runtime.fields(current)}
    assert external_runtime.admit_checkpoint(tmp_path, point, fallback=harness, env={}) == current
    with pytest.raises(external_runtime.RuntimeUnavailable, match="checkpoint source is unavailable"):
        external_runtime.admit_checkpoint(tmp_path, point, env={})


def test_shell_wrapper_is_refused_at_preparation_and_readmission(installed, monkeypatch):
    harness, binary = installed
    record = runtime.installation(binary)
    binary.write_text("#!/bin/sh\nprintf 'opencode v2.2.6\\n'\n")
    path = harness / "harness.json"
    original = path.read_bytes()

    def unexpected_probe(*args, **kwargs):
        pytest.fail("wrapper reached the sandbox version probe")

    monkeypatch.setattr(prepare.sandbox, "command", unexpected_probe)
    with pytest.raises(ValueError, match="Linux x86-64 ELF"):
        prepare.configure(harness, binary)
    assert path.read_bytes() == original

    # Reproduce a wrapper receipt that the earlier preparation accepted.
    record["sha256"][0] = runtime.digest(binary)
    manifest = json.loads(original)
    manifest.update(runtime_paths=record["runtime_paths"], _opencode_runtime=record)
    path.write_text(json.dumps(manifest))
    with pytest.raises(external_runtime.RuntimeUnavailable, match="Linux x86-64 ELF"):
        external_runtime.identities(harness, {})


@needs_bwrap
@pytest.mark.parametrize("change", ["binary", "package", "mode", "paths"])
def test_changed_runtime_is_refused_before_cache_lookup(installed, version_probe, change):
    harness, binary = installed
    prepare.configure(harness, binary)
    if change == "binary":
        with binary.open("ab") as stream:
            stream.write(b"changed")
    elif change == "package":
        metadata = binary.parent.parent / "package.json"
        metadata.write_text(metadata.read_text() + "\n")
    elif change == "mode":
        binary.chmod(0o700)
    else:
        manifest = harness / "harness.json"
        data = json.loads(manifest.read_text())
        data["runtime_paths"] = []
        manifest.write_text(json.dumps(data))
    with pytest.raises(external_runtime.RuntimeUnavailable, match="changed|invalid"):
        external_runtime.identities(harness, {})


@needs_bwrap
@pytest.mark.parametrize("requested", ["1.19.5", "2.2.6"])
def test_version_mismatch_leaves_manifest_unprepared(installed, version_probe, requested):
    harness, binary = installed
    manifest = harness / "harness.json"
    original = manifest.read_bytes()
    if requested == "2.2.6":
        version_probe("2.2.7")
    with pytest.raises(ValueError, match="version"):
        prepare.configure(harness, binary, expected_version=requested)
    assert manifest.read_bytes() == original


def test_symlinked_package_metadata_cannot_escape_the_binding(installed, tmp_path):
    harness, binary = installed
    metadata = binary.parent.parent / "package.json"
    other = tmp_path / "outside.json"
    other.write_bytes(metadata.read_bytes())
    metadata.unlink()
    metadata.symlink_to(other)
    with pytest.raises(ValueError, match="overlaps|npm native package|hidden /tmp"):
        runtime.installation(binary, forbidden=(tmp_path,))
