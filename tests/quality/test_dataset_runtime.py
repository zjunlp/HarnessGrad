"""Provider dependency preparation with real isolated Python and no package index."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from harnessgrad import dataset_runtime as runtime


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source = tmp_path / "platform"
    folder = source / "data/datasets/control"
    folder.mkdir(parents=True)
    (folder.parent / "control.py").write_text("raise AssertionError('adapter was imported')\n")
    (folder / "runtime.json").write_text(json.dumps({
        "schema": runtime.SCHEMA, "dataset": "control", "provider": "qa",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements": "requirements-load.txt"}))
    (folder / "requirements-load.txt").write_text("runtime-marker==1.0\n")
    monkeypatch.setattr(runtime, "ROOT", source)
    original_which = shutil.which
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/mock/uv" if name == "uv" else original_which(name))
    original_run = runtime._run
    calls = []

    def run(argv, *, timeout_s=None):
        args = [str(a) for a in argv]
        if args[0] != "/mock/uv":
            return original_run(argv, timeout_s=timeout_s)
        calls.append(args)
        if args[1] == "venv":
            # The manager must build at the eventual physical path, without exposing
            # an index a second reader could mistake for a finished environment.
            assert not list((tmp_path / "store").glob("*/ready.json"))
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", args[-1]],
                           check=True, capture_output=True, text=True)
            return ""
        if args[1] == "pip":
            python = Path(args[args.index("--python") + 1])
            site = python.parent.parent / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            site.mkdir(parents=True, exist_ok=True)
            (site / "runtime_marker.py").write_text("VALUE = 'from-provider-runtime'\n")
            dist = site / "runtime_marker-1.0.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: runtime-marker\nVersion: 1.0\n")
            return ""
        assert args[1:] == ["--version"]
        return "uv offline-test\n"

    monkeypatch.setattr(runtime, "_run", run)
    yield source, folder, tmp_path / "store", calls
    # Published trees are read-only; restore only these tests' directories so
    # pytest can remove its own temporary work after the test session.
    for path in tmp_path.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o700)


def test_static_discovery_does_not_import_adapter(environment):
    source, folder, _, _ = environment
    declaration = runtime.descriptor("control")
    assert declaration["pins"] == {"runtime-marker": "1.0"}
    assert declaration["source_root"] == str(source)
    assert declaration["requirements_path"] == str(folder / "requirements-load.txt")
    assert runtime.descriptor("not_declared") is None


@pytest.mark.parametrize("requirements", ["../outside.txt", "/outside.txt", "sub/../requirements-load.txt"])
def test_descriptor_rejects_requirements_outside_owned_directory(environment, requirements):
    _, folder, _, _ = environment
    declaration = json.loads((folder / "runtime.json").read_text())
    declaration["requirements"] = requirements
    (folder / "runtime.json").write_text(json.dumps(declaration))
    with pytest.raises(ValueError, match="owned normalized relative"):
        runtime.descriptor("control")


@pytest.mark.parametrize("requirements", ["runtime-marker>=1\n", "runtime-marker==1\nruntime_marker==1\n", "-r more.txt\n"])
def test_provider_lock_requires_unique_exact_pins(environment, requirements):
    _, folder, _, _ = environment
    (folder / "requirements-load.txt").write_text(requirements)
    with pytest.raises(ValueError, match="unique exact package pins"):
        runtime.descriptor("control")


def test_check_missing_runtime_is_read_only(environment):
    _, _, store, calls = environment
    state = runtime.check("control", root=store)
    assert state["status"] == "unavailable" and not state["present"]
    assert state["scope"] == runtime.SCOPE
    assert "prepare_dataset.py" in state["detail"]
    assert not store.exists() and calls == []


def test_prepare_uses_final_path_and_publishes_only_verified_runtime(environment):
    _, _, store, calls = environment
    selected = runtime.prepare("control", root=store)
    assert selected["reused"] is False
    assert calls[0][-1] == selected["path"]
    assert runtime.resolve("control", root=store)["runtime_id"] == selected["runtime_id"]
    probe = subprocess.run([selected["python"], "-B", "-I", "-c",
        "import runtime_marker,sys;print(runtime_marker.VALUE);print(sys.prefix)"],
        check=True, capture_output=True, text=True)
    assert probe.stdout.splitlines() == ["from-provider-runtime", selected["path"]]
    assert not Path(selected["path"]).stat().st_mode & 0o222
    assert runtime.check("control", root=store)["present"] is True


def test_cached_preparation_does_not_require_installer(environment, monkeypatch):
    _, _, store, calls = environment
    first = runtime.prepare("control", root=store)
    before = list(calls)
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    second = runtime.prepare("control", root=store)
    assert first["runtime_id"] == second["runtime_id"] and second["reused"]
    assert calls == before


def test_changed_package_is_refused_without_reinstall(environment):
    _, _, store, calls = environment
    selected = runtime.prepare("control", root=store)
    marker = next(Path(selected["path"]).rglob("runtime_marker.py"))
    marker.chmod(0o644)
    marker.write_text("VALUE = 'tampered'\n")
    before = list(calls)
    assert runtime.check("control", root=store)["status"] == "invalid"
    with pytest.raises(runtime.DatasetRuntimeUnavailable, match="inventory changed"):
        runtime.prepare("control", root=store)
    assert calls == before


def test_changed_base_interpreter_is_refused(environment, monkeypatch):
    _, _, store, _ = environment
    runtime.prepare("control", root=store)
    original = runtime._base
    monkeypatch.setattr(runtime, "_base", lambda probe: {**original(probe), "sha256": "f" * 64})
    with pytest.raises(runtime.DatasetRuntimeUnavailable, match="base interpreter changed"):
        runtime.resolve("control", root=store)


def test_unpublished_instance_is_never_selected(environment):
    _, _, store, _ = environment
    profile = store / runtime._key(runtime.descriptor("control"))
    partial = profile / "instances/interrupted"
    partial.mkdir(parents=True)
    (partial / "partial").write_text("not a runtime")
    assert runtime.check("control", root=store)["status"] == "unavailable"
    prepared = runtime.prepare("control", root=store)
    assert "interrupted" not in prepared["path"]
    assert (partial / "partial").read_text() == "not a runtime"


def test_failed_build_removes_only_its_instance(environment, monkeypatch):
    _, _, store, _ = environment
    original = runtime._run

    def fail(argv, **kwargs):
        if [str(a) for a in argv][:2] == ["/mock/uv", "pip"]:
            raise ValueError("authored installer failure")
        return original(argv, **kwargs)

    monkeypatch.setattr(runtime, "_run", fail)
    with pytest.raises(ValueError, match="installer failure"):
        runtime.prepare("control", root=store)
    assert not list(store.glob("*/ready.json"))
    assert not list(store.glob("*/instances/*"))


def test_foreign_import_path_is_rejected_before_publication(environment, monkeypatch, tmp_path):
    _, _, store, _ = environment
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "ambient_only.py").write_text("VALUE = 'ambient'\n")
    original = runtime._run

    def inject(argv, **kwargs):
        output = original(argv, **kwargs)
        args = [str(a) for a in argv]
        if args[:2] == ["/mock/uv", "pip"]:
            venv = Path(args[args.index("--python") + 1]).parent.parent
            site = next(venv.glob("lib/python*/site-packages"))
            (site / "ambient.pth").write_text(str(foreign) + "\n")
        return output

    monkeypatch.setattr(runtime, "_run", inject)
    with pytest.raises(ValueError, match="foreign package directory"):
        runtime.prepare("control", root=store)
    assert not list(store.glob("*/ready.json"))


def test_descriptor_change_does_not_reuse_old_environment(environment):
    _, folder, store, _ = environment
    original = runtime.prepare("control", root=store)
    (folder / "requirements-load.txt").write_text("runtime-marker==2.0\n")
    with pytest.raises(runtime.DatasetRuntimeUnavailable):
        runtime.resolve("control", root=store)
    assert Path(original["manifest_path"]).is_file()


def test_concurrent_preparations_publish_one_instance(environment):
    _, _, store, calls = environment
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.prepare, "control", root=store) for _ in range(2)]
        results = [future.result(timeout=45) for future in futures]
    assert results[0]["runtime_id"] == results[1]["runtime_id"]
    assert sorted(item["reused"] for item in results) == [False, True]
    assert sum(call[1] == "venv" for call in calls) == 1


def test_store_cannot_be_inside_platform(environment):
    source, _, _, _ = environment
    with pytest.raises(ValueError, match="outside platform"):
        runtime.prepare("control", root=source / "runtime")
