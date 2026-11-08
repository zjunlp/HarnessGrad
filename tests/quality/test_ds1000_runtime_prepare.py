"""Local authored transport fixtures for the CPU preparation CLI; no package install."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from harnessgrad import dataset_runtime
from tools import prepare_ds1000_runtime as prepare


@pytest.fixture
def outside_runtime():
    cache = Path.home() / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hg-ds1000-prepare-control-", dir=cache))
    try:
        yield root / "store"
    finally:
        shutil.rmtree(root)


def entry(tmp_path, raw=b"authored transport bytes"):
    source = tmp_path / "authored-source.whl"
    source.write_bytes(raw)
    return {"name": "authored-fixture", "version": "1.0",
            "filename": "authored_fixture-1.0-py3-none-any.whl",
            "url": source.as_uri(), "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}, source


def no_network(*args, **kwargs):
    pytest.fail("network or source fetch was not authorized")


def test_public_manifest_matches_the_complete_cpu_profile():
    entries = prepare._manifest()
    assert len(entries) == 29
    assert sum(row["size"] for row in entries) == 287334963
    torch = next(row for row in entries if row["name"] == "torch")
    assert torch["version"] == "2.5.1+cpu" and torch["size"] == 174642530
    assert not any(row["name"].startswith(("nvidia-", "triton")) for row in entries)


@pytest.mark.parametrize("present,code", [(True, 0), (False, 2)])
def test_default_check_is_read_only_and_never_fetches(tmp_path, monkeypatch, capsys, present, code, outside_runtime):
    root, wheels = outside_runtime, tmp_path / "wheels"
    monkeypatch.setattr(prepare, "urlopen", no_network)
    monkeypatch.setattr(dataset_runtime, "prepare_execution", no_network)
    monkeypatch.setattr(dataset_runtime, "check_execution", lambda name, root: {"present": present, "status": "ready" if present else "unavailable"})
    assert prepare.main(["--runtime-root", str(root), "--wheelhouse", str(wheels)]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "check" and result["downloaded_bytes"] == 0
    assert not root.exists() and not wheels.exists()


def test_other_architecture_is_diagnosed_before_download_or_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prepare.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(prepare, "urlopen", no_network)
    assert prepare.main(["--runtime-root", str(tmp_path / "store"), "--fetch"]) == 2
    assert "Linux x86_64 only" in json.loads(capsys.readouterr().out)["error"]
    assert list(tmp_path.iterdir()) == []


def test_wrong_python_target_is_refused_before_fetch(tmp_path, monkeypatch, capsys, outside_runtime):
    monkeypatch.setattr(prepare, "_python_target", lambda *args: (_ for _ in ()).throw(ValueError("CPython 3.12 target required")))
    monkeypatch.setattr(prepare, "urlopen", no_network)
    assert prepare.main(["--runtime-root", str(outside_runtime), "--fetch"]) == 2
    assert "CPython 3.12" in json.loads(capsys.readouterr().out)["error"]
    assert list(tmp_path.iterdir()) == []


def test_verified_cached_wheel_is_reused_without_opening_source(tmp_path, monkeypatch):
    row, _ = entry(tmp_path)
    folder = tmp_path / "cache"; folder.mkdir()
    first = prepare._wheel(row, folder, fetch=True, timeout=10)
    assert first == {"name": row["name"], "reused": False, "downloaded_bytes": row["size"]}
    monkeypatch.setattr(prepare, "urlopen", no_network)
    assert prepare._wheel(row, folder, fetch=True, timeout=10)["reused"] is True
    assert prepare._wheel(row, folder, fetch=False, timeout=10)["downloaded_bytes"] == 0


def test_source_sha_error_is_preserved_and_never_published(tmp_path):
    row, _ = entry(tmp_path)
    row["sha256"] = "0" * 64
    folder = tmp_path / "cache"; folder.mkdir()
    with pytest.raises(ValueError, match="SHA256"):
        prepare._wheel(row, folder, fetch=True, timeout=10)
    assert not (folder / row["filename"]).exists()
    assert [path.read_bytes() for path in folder.glob("*.part-*")] == [b"authored transport bytes"]


def test_partial_file_survives_a_successful_retry(tmp_path):
    row, source = entry(tmp_path, b"complete authored bytes")
    source.write_bytes(b"partial")
    folder = tmp_path / "cache"; folder.mkdir()
    with pytest.raises(ValueError, match="partial bytes retained"):
        prepare._wheel(row, folder, fetch=True, timeout=10)
    partial = next(folder.glob("*.part-*"))
    assert partial.read_bytes() == b"partial"
    source.write_bytes(b"complete authored bytes")
    assert prepare._wheel(row, folder, fetch=True, timeout=10)["reused"] is False
    assert partial.read_bytes() == b"partial"
    assert (folder / row["filename"]).read_bytes() == source.read_bytes()


def test_corrupt_cached_wheel_requires_explicit_fetch_and_preserves_old_bytes(tmp_path, monkeypatch):
    row, _ = entry(tmp_path)
    folder = tmp_path / "cache"; folder.mkdir()
    target = folder / row["filename"]
    target.write_bytes(b"old corrupt wheel")
    with pytest.raises(ValueError, match="use --fetch explicitly"):
        prepare._wheel(row, folder, fetch=False, timeout=10)
    assert target.read_bytes() == b"old corrupt wheel"
    prepare._wheel(row, folder, fetch=True, timeout=10)
    assert [path.read_bytes() for path in folder.glob("*.rejected-*")] == [b"old corrupt wheel"]


def test_offline_prepare_uses_canonical_manager_and_repeats_without_fetch(tmp_path, monkeypatch, capsys, outside_runtime):
    row, _ = entry(tmp_path)
    folder, root = tmp_path / "cache", outside_runtime
    folder.mkdir()
    prepare._wheel(row, folder, fetch=True, timeout=10)
    monkeypatch.setattr(prepare, "_manifest", lambda: [row])
    monkeypatch.setattr(prepare, "_python_target", lambda *args: None)
    monkeypatch.setattr(prepare, "urlopen", no_network)
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "https://ambient.invalid")
    monkeypatch.setenv("UV_INDEX_PRIVATE_TOKEN", "never-forward-this")
    calls = []
    def canonical(name, **kwargs):
        assert name == "ds1000" and kwargs["root"] == root
        assert os.environ["UV_INDEX_URL"] == (folder / "simple").as_uri()
        assert os.environ["UV_OFFLINE"] == "1" and os.environ["UV_PYTHON_DOWNLOADS"] == "never"
        assert "UV_EXTRA_INDEX_URL" not in os.environ and "UV_INDEX_PRIVATE_TOKEN" not in os.environ
        calls.append(kwargs)
        return {"runtime_id": "a" * 64}
    monkeypatch.setattr(dataset_runtime, "prepare_execution", canonical)
    monkeypatch.setattr(dataset_runtime, "check_execution", lambda *a, **k: {"present": True, "runtime": {"runtime_id": "a" * 64}})
    argv = ["--runtime-root", str(root), "--wheelhouse", str(folder), "--prepare"]
    for _ in range(2):
        assert prepare.main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["downloaded_bytes"] == 0 and result["runtime"]["runtime_id"] == "a" * 64
    assert len(calls) == 2
    assert os.environ["UV_EXTRA_INDEX_URL"] == "https://ambient.invalid"
    assert os.environ["UV_INDEX_PRIVATE_TOKEN"] == "never-forward-this"
    html = (folder / "simple/authored-fixture/index.html").read_text()
    assert "#sha256=" + row["sha256"] in html
    assert (folder / "prepare-receipt.json").is_file()


def test_failed_download_does_not_reach_managed_preparation(tmp_path, monkeypatch, capsys, outside_runtime):
    row, _ = entry(tmp_path)
    row["sha256"] = "0" * 64
    monkeypatch.setattr(prepare, "_manifest", lambda: [row])
    monkeypatch.setattr(prepare, "_python_target", lambda *args: None)
    monkeypatch.setattr(dataset_runtime, "prepare_execution", no_network)
    folder, root = tmp_path / "cache", outside_runtime
    assert prepare.main(["--runtime-root", str(root), "--wheelhouse", str(folder), "--fetch"]) == 2
    assert "SHA256" in json.loads(capsys.readouterr().out)["error"]
    assert list(folder.glob("*.part-*")) and not root.exists()


@pytest.mark.parametrize("target", ["simple", "simple/authored-fixture", "simple/authored-fixture/index.html"])
def test_local_index_refuses_symlink_destinations(tmp_path, target):
    row, _ = entry(tmp_path)
    folder = tmp_path / "cache"; folder.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    path = folder / target
    path.parent.mkdir(parents=True, exist_ok=True)
    if target.endswith(".html"):
        source = outside / "keep.txt"; source.write_text("unchanged")
        path.symlink_to(source)
    else:
        path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepare._index([row], folder)
    if target.endswith(".html"):
        assert source.read_text() == "unchanged"


@pytest.mark.parametrize("libc", ["musl 1.2.5", "glibc 2.17"])
def test_unsupported_libc_does_not_reach_fetch(monkeypatch, libc):
    from types import SimpleNamespace
    monkeypatch.setattr(prepare.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0,
        stdout=json.dumps(["cpython", [3, 12], "Linux", "x86_64", libc])))
    with pytest.raises(ValueError, match="glibc >= 2.28"):
        prepare._python_target("authored-python", 10)


def test_target_probe_receives_only_a_minimal_environment(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("HG_AGENT_API_KEY", "authored-not-a-secret")
    def run(argv, **kwargs):
        assert argv[1:4] == ["-I", "-S", "-B"]
        assert kwargs["env"] == {"PATH": os.defpath, "LANG": "C.UTF-8"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(["cpython", [3, 12], "Linux", "x86_64", "glibc 2.36"]))
    monkeypatch.setattr(prepare.subprocess, "run", run)
    prepare._python_target("authored-python", 10)


def test_target_probe_does_not_execute_venv_startup_files(tmp_path):
    import sys
    import venv
    environment = tmp_path / "probe-venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    site = environment / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    marker = tmp_path / "startup-executed"
    (site / "authored-startup.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('unexpected startup')\n")
    prepare._python_target(str(environment / "bin/python"), 10)
    assert not marker.exists(), "preparation target discovery executed package startup"


@pytest.mark.parametrize("path", ["/usr/local/forbidden-store", "/etc/forbidden-store"])
def test_system_tree_cannot_be_selected_as_a_cache_or_store(path):
    with pytest.raises(ValueError, match="outside system and interpreter trees"):
        prepare._external(path, "authored path")


@pytest.mark.parametrize("root", ["/tmp/authored-science-store", "/var/tmp/authored-science-store"])
def test_native_runtime_store_must_not_be_in_a_hidden_temporary_tree(root):
    with pytest.raises(ValueError, match="native sandbox isolation hides"):
        prepare._native_store(root)
