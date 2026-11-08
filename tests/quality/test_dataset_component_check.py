"""Cold CLI checks distinguish data, admitted dependencies and loader readiness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad import dataset_runtime as runtime
from test_dataset_execution_runtime import execution
from test_dataset_runtime import environment
from tools import datasets

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def component(execution):
    source, folder, store, calls = execution
    data = source.parent / "data"
    data.mkdir()
    (data / "rows.txt").write_text("synthetic rows")
    entries = [dict(kind="http", name="rows", version="1", url="https://example.invalid/rows",
                    dst="rows.txt"),
               dict(kind="pip", name="marker", package="runtime-marker", version="1.0")]
    (folder.parent / "control.py").write_text(
        "from harnessgrad.paths import dataset_root\n"
        "KIND='dataset'\nDATASET_ROOT=dataset_root('control')\n"
        f"def downloads(): return {entries!r}\n"
        "def provision_status(): raise AssertionError('must not contact judge or load data')\n")
    # This fixture exercises execution-only declarations, not the provider route.
    (folder / "runtime.json").unlink()
    return source, folder, store, calls, data


def cold_check(component, *, json_output=True):
    source, _, store, _, data = component
    env = {"PATH": os.defpath, "HOME": str(source.parent / "home"),
           "HG_REVIEWED_ENVIRONMENT_ONLY": "1", "HG_DATA_CONTROL": str(data),
           "HG_DATASET_RUNTIME_ROOT": str(store),
           "OPENAI_API_KEY": "synthetic-never-forward", "HG_MODEL_API_KEY": "synthetic-never-forward"}
    # The subprocess uses the real CLI and admission logic against a trusted synthetic
    # source tree. No hook changes checking, subprocess launch or package metadata.
    code = f"""
import sys
sys.path.insert(0,{str(ROOT)!r})
from tools import datasets
from harnessgrad import dataset_runtime
from data.scaffolds import registry
from pathlib import Path
datasets.ROOT=Path({str(source)!r})
dataset_runtime.ROOT=datasets.ROOT
registry.__file__=str(datasets.ROOT/'data/scaffolds/registry.py')
raise SystemExit(datasets.main({['--check', '--json'] if json_output else ['--check']!r}))
"""
    child = subprocess.run([sys.executable, "-I", "-B", "-c", code], env=env,
                           capture_output=True, text=True, timeout=30)
    assert not child.stderr, child.stderr
    return child, json.loads(child.stdout)["datasets"][0] if json_output else child.stdout


def test_execution_only_missing_runtime_is_not_ready(component):
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["data_present"] is True and row["present"] is False
    assert row["execution_runtime"]["status"] == "unavailable"
    assert row["loader_ready"] is None
    assert "--runtime execution" in row["execution_runtime"]["detail"]
    assert not component[2].exists()
    assert not component[3], "checking must not invoke uv or install dependencies"


def test_execution_only_packages_use_the_admitted_python(component):
    _, _, store, _, _ = component
    runtime.prepare_execution("control", root=store)
    child, row = cold_check(component)
    assert child.returncode == 0, child.stdout
    assert row["data_present"] and row["present"]
    assert row["execution_runtime"]["present"]
    assert row["entries"][1]["present"]
    assert row["entries"][1]["scope"] == "dataset-execution-runtime"
    assert row["loader_ready"] is None
    child, output = cold_check(component, json_output=False)
    assert child.returncode == 0 and "execution_runtime:" in output
    assert "loader readiness: not checked" in output


def test_download_check_does_not_call_judge_or_adapter_status(component):
    # No pip entries, but the declared execution artifact is still required.
    _, folder, _, _, _ = component
    adapter = folder.parent / "control.py"
    adapter.write_text(adapter.read_text() + "\ndef downloads(): return " + repr([dict(
        kind="http", name="rows", version="1", url="https://example.invalid/rows", dst="rows.txt")]) + "\n")
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["status"]["scope"] == "declared-downloads-and-component-runtimes"
    assert "adapter_import_error" not in row["status"]


def test_metadata_probe_environment_excludes_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("HG_MODEL_API_KEY", "synthetic-secret")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    received = []
    real = subprocess.run

    def run(argv, **kwargs):
        received.append(kwargs["env"])
        return real(argv, **kwargs)

    monkeypatch.setattr(datasets.subprocess, "run", run)
    versions, error = datasets._runtime_package_versions(
        {"present": True, "runtime": {"python": sys.executable}}, ["missing-synthetic-package"])
    assert not error and versions == {"missing-synthetic-package": None}
    assert received and set(received[0]) <= {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"}


def test_ready_artifact_with_missing_data_stays_not_ready(component):
    runtime.prepare_execution("control", root=component[2])
    (component[4] / "rows.txt").unlink()
    child, row = cold_check(component)
    assert child.returncode == 1
    assert not row["data_present"] and not row["present"]
    assert row["execution_runtime"]["present"]
    assert row["entries"][1]["present"]


def test_invalid_execution_artifact_remains_invalid(component):
    artifact = runtime.prepare_execution("control", root=component[2])
    # Admission sees a changed locked file; checking must neither rebuild nor use
    # platform packages to turn the invalid artifact into a ready component.
    venv = Path(artifact["path"])
    venv.chmod(0o755)
    (venv / "unexpected-file").write_text("synthetic corruption")
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["data_present"] and not row["present"]
    assert row["execution_runtime"]["status"] == "invalid"
    assert "inventory changed" in row["execution_runtime"]["detail"]


def test_provider_and_execution_artifacts_are_both_required(component):
    _, folder, store, _, _ = component
    (folder / "runtime.json").write_text(json.dumps({
        "schema": runtime.SCHEMA, "dataset": "control", "provider": "qa",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements": "requirements-load.txt"}))
    runtime.prepare("control", root=store)
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["runtime"]["present"] and not row["execution_runtime"]["present"]
    assert row["entries"][1]["present"] and not row["present"]


def test_legacy_scope_does_not_claim_loader_or_judge_readiness(component):
    _, folder, _, _, _ = component
    (folder / "execution-runtime.json").unlink()
    # The legacy package declaration remains a host metadata requirement; this
    # test deliberately does not install it into the platform.
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["data_present"] and not row["present"]
    assert row["status"]["scope"] == "legacy-platform-declared-downloads"
    assert row["entries"][1]["scope"] == "legacy-platform-package-metadata"
    assert "execution_runtime" not in row and "runtime" not in row
    assert row["loader_ready"] is None


def test_agentdojo_declares_source_without_a_distribution_requirement():
    declarations = dict(datasets.datasets_with_downloads(source_fallback=True))["agentdojo"]
    assert not [entry for entry in declarations if entry.get("package") == "agentdojo"]
    source = declarations[0]
    assert source["version"] in source["url"] and source["sha256"]
    assert "0.1.35" in source["note"] and "v1.2.2" in source["note"]
    assert "execution-requirements.lock" in source["note"]


def test_malformed_execution_declaration_is_a_component_failure(component):
    (component[1] / "execution-runtime.json").write_text("{}")
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["data_present"] and not row["present"]
    assert row["execution_runtime"]["status"] == "invalid"
    assert not component[2].exists()


def test_named_execution_profile_alias_uses_its_own_artifact(component):
    _, folder, store, _, _ = component
    adapter = folder.parent / "control.py"
    adapter.write_text(adapter.read_text() + "\nMANAGED_EXECUTION_RUNTIME='engine'\n")
    engine = folder.with_name("engine")
    folder.rename(engine)
    declaration = json.loads((engine / "execution-runtime.json").read_text())
    declaration["dataset"] = "engine"
    (engine / "execution-runtime.json").write_text(json.dumps(declaration))
    runtime.prepare_execution("engine", root=store)
    child, row = cold_check(component)
    assert child.returncode == 0, child.stdout
    assert row["execution_runtime"]["runtime"]["dataset"] == "engine"
    assert row["data_present"] and row["present"]


@pytest.mark.parametrize("embedded_pin,ready", [("1.0", True), ("2.0", False)])
def test_exact_package_requirement_uses_distribution_metadata(component, embedded_pin, ready):
    _, folder, store, _, _ = component
    adapter = folder.parent / "control.py"
    adapter.write_text(adapter.read_text().replace(
        "'package': 'runtime-marker'", f"'package': 'runtime-marker=={embedded_pin}'"))
    runtime.prepare_execution("control", root=store)
    child, row = cold_check(component)
    assert child.returncode == (0 if ready else 1), child.stdout
    assert row["execution_runtime"]["present"] and row["data_present"]
    assert row["entries"][1]["present"] is ready
    if not ready:
        assert "conflicts with declared version" in row["entries"][1]["detail"]
        assert row["entries"][1]["status"] == "invalid"


@pytest.mark.parametrize("package", ["runtime-marker>=1", "runtime-marker[extra]", "https://example.invalid/pkg.whl"])
def test_unsupported_package_requirements_are_invalid_without_installing(component, package):
    _, folder, _, calls, _ = component
    adapter = folder.parent / "control.py"
    adapter.write_text(adapter.read_text().replace(
        "'package': 'runtime-marker'", f"'package': {package!r}"))
    child, row = cold_check(component)
    assert child.returncode == 1
    assert row["entries"][1]["status"] == "invalid"
    assert "unsupported pip package declaration" in row["entries"][1]["detail"]
    assert not calls
