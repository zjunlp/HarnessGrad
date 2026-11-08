"""Exact service/verifier Python artifacts, independent of discovery indices."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from harnessgrad import dataset_runtime as runtime
from test_dataset_runtime import environment  # shared offline venv/installer control


@pytest.fixture
def execution(environment, monkeypatch):
    source, folder, store, calls = environment
    (folder / "execution-runtime.json").write_text(json.dumps({
        "schema": runtime.EXECUTION_SCHEMA, "dataset": "control",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements": "requirements-load.txt"}))
    original_run = runtime._run

    def run(argv, **kwargs):
        args = [str(a) for a in argv]
        if args[:3] == ["/mock/uv", "python", "find"]:
            calls.append(args)
            return str(Path(sys.executable)) + "\n"
        return original_run(argv, **kwargs)

    monkeypatch.setattr(runtime, "_run", run)
    return source, folder, store, calls


def test_costbench_execution_descriptor_does_not_opt_in_to_qa_provider():
    declaration = runtime.execution_descriptor("costbench")
    assert declaration["schema"] == runtime.EXECUTION_SCHEMA
    assert declaration["profile"] == "execution"
    assert declaration["python"] == "3.12"
    assert declaration["requirements"] == "requirements.lock"
    assert runtime.descriptor("costbench") is None


def test_execution_check_does_not_prepare_a_missing_artifact(execution):
    _, _, store, calls = execution
    state = runtime.check_execution("control", root=store)
    assert state["status"] == "unavailable" and not state["present"]
    assert state["scope"] == runtime.EXECUTION_SCOPE
    assert "--runtime execution" in state["detail"]
    assert not store.exists() and not calls


def test_execution_build_uses_physical_base_and_records_all_prefixes(execution):
    _, _, store, calls = execution
    artifact = runtime.prepare_execution("control", root=store)
    build = next(args for args in calls if args[1] == "venv")
    assert build[build.index("--python") + 1] == str(Path(sys.executable).resolve())
    assert artifact["profile"] == "execution" and artifact["dataset"] == "control"
    assert artifact["runtime_paths"][0] == artifact["path"]
    assert all(Path(path).is_dir() and Path(path).resolve() == Path(path)
               for path in artifact["runtime_paths"])
    manifest = json.loads(Path(artifact["manifest_path"]).read_text())
    assert manifest["base_python"]["base_exec_prefix"] == str(Path(sys.base_exec_prefix).resolve())
    assert manifest["runtime_paths"] == artifact["runtime_paths"]
    assert runtime.check_execution("control", root=store)["present"]


def test_service_and_verifier_share_artifact_with_separate_roles(execution):
    _, _, store, _ = execution
    artifact = runtime.prepare_execution("control", root=store)
    service = runtime.bind(artifact, "service")
    verifier = runtime.bind(artifact, "verifier")
    assert service["runtime_id"] == verifier["runtime_id"]
    assert service["manifest_path"] == verifier["manifest_path"]
    assert service["role"] == "service" and verifier["role"] == "verifier"
    assert runtime.admit_binding(service, "service")["runtime_id"] == artifact["runtime_id"]
    assert runtime.admit_binding(verifier, "verifier", python=artifact["python"])["runtime_paths"] == artifact["runtime_paths"]
    with pytest.raises(ValueError, match="service or verifier"):
        runtime.bind(artifact, "provider")
    service["runtime_paths"].append("/not-a-shared-list")
    assert "/not-a-shared-list" not in verifier["runtime_paths"]
    assert "/not-a-shared-list" not in artifact["runtime_paths"]


def _unprepared_binding():
    return {"schema": runtime.BINDING_SCHEMA, "dataset": "control", "profile": "execution",
            "role": "service", "runtime_id": "a" * 64,
            "manifest_path": "/unprepared/instance/artifact.json",
            "path": "/unprepared/instance/venv", "python": "/unprepared/instance/venv/bin/python",
            "runtime_paths": ["/unprepared/instance/venv", "/opt/base-python"]}


def test_binding_structure_validation_neither_reads_nor_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("structure validation must not inspect an artifact")

    monkeypatch.setattr(runtime, "execution_descriptor", forbidden)
    monkeypatch.setattr(runtime, "_probe", forbidden)
    monkeypatch.setattr(runtime, "_verify_manifest", forbidden)
    binding = _unprepared_binding()
    assert runtime.validate_binding(binding, "service", binding["python"]) == binding
    validated = runtime.validate_binding(binding, "service")
    assert validated["runtime_paths"] is not binding["runtime_paths"]


@pytest.mark.parametrize("change", [
    {"runtime_paths": []}, {"runtime_paths": "/opt/python"},
    {"runtime_paths": ["/unprepared/instance/venv", "/unprepared/instance/venv"]},
    {"runtime_paths": ["/opt/base-python", "/unprepared/instance/venv"]},
    {"path": "/unprepared/../instance/venv"}, {"manifest_path": "/another/artifact.json"},
    {"python": "/unprepared/instance/venv/bin/python3"}, {"runtime_id": "not-a-digest"},
    {"dataset": "../control"}, {"runtime_paths": ["/unprepared/instance/venv", "/"]},
])
def test_binding_structure_rejects_ambiguous_role_paths(change):
    binding = {**_unprepared_binding(), **change}
    with pytest.raises(ValueError):
        runtime.validate_binding(binding, "service")


@pytest.mark.parametrize("change", [
    {"role": "service"}, {"schema": "wrong"}, {"profile": "provider"},
    {"runtime_id": "0" * 64}, {"python": "/usr/bin/python3"}, {"path": "/another/venv"},
    {"runtime_paths": ["/"]}, {"extra": "unrecognized"},
])
def test_binding_role_identity_and_mount_changes_are_refused(execution, change):
    _, _, store, _ = execution
    binding = runtime.bind(runtime.prepare_execution("control", root=store), "verifier")
    binding.update(change)
    with pytest.raises(runtime.DatasetRuntimeUnavailable) as caught:
        runtime.admit_binding(binding, "verifier")
    assert caught.value.status == "invalid"


def test_launch_python_must_preserve_venv_identity(execution):
    _, _, store, _ = execution
    artifact = runtime.prepare_execution("control", root=store)
    binding = runtime.bind(artifact, "service")
    # Even a binary resolving to the same base interpreter must not substitute
    # for the exact venv executable selected in the trusted declaration.
    with pytest.raises(runtime.DatasetRuntimeUnavailable, match="launch executable"):
        runtime.admit_binding(binding, "service", python=Path(artifact["python"]).resolve())


def test_artifact_tampering_is_refused_before_any_role_starts(execution):
    _, _, store, calls = execution
    binding = runtime.bind(runtime.prepare_execution("control", root=store), "verifier")
    marker = next(Path(binding["path"]).rglob("runtime_marker.py"))
    marker.chmod(0o644)
    marker.write_text("VALUE = 'changed-after-binding'\n")
    before = copy.deepcopy(calls)
    with pytest.raises(runtime.DatasetRuntimeUnavailable, match="inventory changed"):
        runtime.admit_binding(binding, "verifier")
    assert calls == before


def test_published_index_change_does_not_replace_a_bound_artifact(execution):
    _, _, store, _ = execution
    first = runtime.prepare_execution("control", root=store)
    binding = runtime.bind(first, "service")
    profile = Path(first["manifest_path"]).parents[2]
    (profile / "ready.json").unlink()
    second = runtime.prepare_execution("control", root=store)
    assert first["runtime_id"] != second["runtime_id"]
    assert runtime.resolve_execution("control", root=store)["runtime_id"] == second["runtime_id"]
    assert runtime.admit_binding(binding, "service")["runtime_id"] == first["runtime_id"]
    (profile / "ready.json").unlink()
    assert runtime.admit_binding(binding, "service")["path"] == first["path"]


def test_default_provider_manifest_and_api_remain_compatible(execution):
    _, _, store, _ = execution
    provider = runtime.prepare("control", root=store)
    manifest = json.loads(Path(provider["manifest_path"]).read_text())
    assert "profile" not in manifest and "runtime_paths" not in manifest
    assert "base_exec_prefix" not in manifest["base_python"]
    assert "profile" not in provider and "runtime_paths" not in provider
    with pytest.raises(ValueError, match="execution artifact"):
        runtime.bind(provider, "service")
    execution_artifact = runtime.prepare_execution("control", root=store)
    assert execution_artifact["runtime_id"] != provider["runtime_id"]
    assert runtime.resolve("control", root=store)["runtime_id"] == provider["runtime_id"]


def test_base_execution_prefix_drift_invalidates_exact_binding(execution, monkeypatch, tmp_path):
    _, _, store, _ = execution
    binding = runtime.bind(runtime.prepare_execution("control", root=store), "verifier")
    original = runtime._probe
    another = tmp_path / "another-base"
    another.mkdir()
    monkeypatch.setattr(runtime, "_probe", lambda *a, **kw: {
        **original(*a, **kw), "base_exec_prefix": str(another)})
    with pytest.raises(runtime.DatasetRuntimeUnavailable, match="base interpreter changed"):
        runtime.admit_binding(binding, "verifier")


@pytest.mark.skipif(shutil.which("uv") is None or shutil.which("bwrap") is None,
                    reason="real uv and bubblewrap are required")
def test_real_physical_runtime_starts_under_other_driver_interpreters(tmp_path, monkeypatch):
    """An execution venv must not rely on the launching driver's uv aliases."""
    platform_root = runtime.ROOT
    source = tmp_path / "source"
    folder = source / "data/datasets/physical_control"
    folder.mkdir(parents=True)
    (folder / "execution-runtime.json").write_text(json.dumps({
        "schema": runtime.EXECUTION_SCHEMA, "dataset": "physical_control",
        "python": "3.12", "requirements": "requirements.lock"}))
    (folder / "requirements.lock").write_text("")
    monkeypatch.setattr(runtime, "ROOT", source)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    # The verifier hides /tmp. Its runtime must be prepared outside that tree,
    # exactly like a real managed dependency environment.
    audit = Path(tempfile.mkdtemp(prefix="hg-execution-physical-", dir=Path.home() / ".cache"))
    try:
        artifact = runtime.prepare_execution("physical_control", root=audit / "store")
        check = """import json,os,sys
from pathlib import Path
Path('verifier/reward.txt').write_text('1')
Path('verifier/evidence.json').write_text(json.dumps({'measured':True,'python':sys.executable,
'prefix':sys.prefix,'readonly':bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY)}))
"""
        spec = {"kind": "command", "argv": [artifact["python"], "-B", "-I", "check.py"],
                "runtime_paths": artifact["runtime_paths"],
                "inputs": [{"path": "check.py", "content": check}],
                "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
                "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True}}
        launch = """import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from eval.verification.verifier import _verify
work=Path(sys.argv[2]);work.mkdir()
result=_verify(work,json.loads(sys.argv[3]),'',sandbox=True,timeout_s=20,env={'kind':'files'})
print(json.dumps(result))
"""
        drivers = [Path(sys.executable)]
        alternative = Path.home() / ".local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13"
        if alternative.is_file():
            drivers.append(alternative)
        for index, driver in enumerate(drivers):
            work = tmp_path / f"driver-{index}"
            environment = {"HOME": str(Path.home()), "PATH": os.defpath,
                           "HARNESSGRAD_WORK_ROOT": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
            process = subprocess.run([str(driver), "-B", "-I", "-c", launch,
                str(platform_root), str(work), json.dumps(spec)], env=environment,
                capture_output=True, text=True, timeout=45)
            assert process.returncode == 0, process.stdout + process.stderr
            result = json.loads(process.stdout)
            assert result.get("score") == 1, result
            assert result["evidence"]["python"] == artifact["python"]
            assert result["evidence"]["prefix"] == artifact["path"]
            assert result["evidence"]["readonly"] is True
    finally:
        runtime._remove_instance(audit)


def test_builder_normalizes_uv_reintroduced_alias_before_publication(execution, monkeypatch, tmp_path):
    _, _, store, _ = execution
    physical = Path(sys.executable).resolve(strict=True)
    alias = tmp_path / 'mutable-python-alias'
    alias.symlink_to(physical.parent, target_is_directory=True)
    original = runtime._run
    def run(argv, **kwargs):
        result = original(argv, **kwargs)
        args = [str(arg) for arg in argv]
        if args[:2] == ['/mock/uv', 'venv']:
            venv = Path(args[-1])
            link = venv / 'bin/python'
            link.unlink()
            link.symlink_to(alias / physical.name)
            config = venv / 'pyvenv.cfg'
            lines = config.read_text().splitlines()
            config.write_text('\n'.join('home = ' + str(alias) if line.startswith('home =')
                                        else line for line in lines) + '\n')
        return result
    monkeypatch.setattr(runtime, '_run', run)
    artifact = runtime.prepare_execution('control', root=store)
    assert os.readlink(Path(artifact['python'])) == str(physical)
    config = (Path(artifact['path']) / 'pyvenv.cfg').read_text()
    assert 'home = ' + str(physical.parent) in config
    assert str(alias) not in config
    alias.unlink()
    # The installed artifact stays valid even when uv's discovery alias disappears.
    assert runtime.admit_binding(runtime.bind(artifact, 'verifier'), 'verifier')['runtime_id'] == artifact['runtime_id']


def test_old_artifact_with_unmounted_interpreter_alias_is_rejected(tmp_path):
    physical = Path(sys.executable).resolve(strict=True)
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    alias = tmp_path / 'mutable-base-alias'
    alias.symlink_to(physical.parent, target_is_directory=True)
    (venv / 'bin/python').symlink_to(alias / physical.name)
    (venv / 'pyvenv.cfg').write_text('home = ' + str(physical.parent) + '\n')
    with pytest.raises(ValueError, match='unmounted alias.*fresh runtime store'):
        runtime._validate_execution_links(venv, [str(venv), str(physical.parent)])
    (venv / 'bin/python').unlink()
    (venv / 'bin/python').symlink_to(physical)
    runtime._validate_execution_links(venv, [str(venv), str(physical.parent)])
