"""Provider tools keep optional dependencies out of the platform interpreter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data.scaffolds import provider_worker
from data.scaffolds.qa.base import QASpec
from harnessgrad import dataset_runtime
from harnessgrad.paths import DataRootNotConfigured
from tools import dataset_lint, datasets, prepare_dataset


@pytest.mark.parametrize('name', ['gsm8k', 'triviaqa', 'hotpotqa'])
def test_documentation_reads_provider_metadata_without_importing_adapter(monkeypatch, name):
    from tools import datasets_doc
    original = datasets_doc.importlib.import_module

    def forbid_adapter(module_name, *args, **kwargs):
        assert module_name != f"data.datasets.{name}", "documentation imported the dataset loader"
        return original(module_name, *args, **kwargs)

    monkeypatch.setattr(datasets_doc.importlib, "import_module", forbid_adapter)
    module = datasets_doc._import_adapter(name)
    command = datasets_doc._provision(name, module, refused=False)
    assert module.KIND == "dataset"
    assert f"tools/datasets.py --fetch --dataset {name}" in command
    assert f"tools/prepare_dataset.py --dataset {name}" in command


@pytest.fixture
def opted_in(tmp_path, monkeypatch):
    root = tmp_path / "source"
    adapters = root / "data/datasets"
    adapters.mkdir(parents=True)
    folder = adapters / "synthetic"
    folder.mkdir()
    (folder / "runtime.json").write_text("{}")
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.json").write_text("rows")
    entries = [dict(kind="http", name="rows", version="1.0", size="1 KB",
                    note="Synthetic data bytes; no download is needed.",
                    url="https://example.invalid/rows", dst="rows.json"),
               dict(kind="pip", name="marker", version="1.0", size="1 KB",
                    note="Synthetic provider dependency, never installed here.", package="marker")]
    source = adapters / "synthetic.py"
    source.write_text(
        "from harnessgrad.paths import dataset_root\n"
        "KIND='dataset'\nORACLE_SOURCE='released'\n"
        "DATASET_ROOT=dataset_root('synthetic')\n"
        "def load(): raise AssertionError('adapter imported in platform')\n"
        f"def downloads(): return {entries!r}\n"
        "raise AssertionError('adapter imported in platform')\n")
    monkeypatch.setenv("HG_DATA_SYNTHETIC", str(data))
    monkeypatch.setattr(datasets, "ROOT", root)
    monkeypatch.setattr(dataset_lint, "ROOT", root)
    descriptor = Mock(return_value={"dataset": "synthetic", "provider": "qa"})
    monkeypatch.setattr(dataset_runtime, "descriptor", descriptor)
    return SimpleNamespace(root=root, data=data, source=source, entries=entries,
                           descriptor=descriptor)


def _runtime(present=True):
    return {"present": present, "status": "ready" if present else "unavailable",
            "scope": "dataset-provider-runtime", "detail": "verified" if present else "prepare first",
            **({"runtime": {"python": "/selected/runtime/bin/python", "path": "/selected/runtime"}}
               if present else {})}


def test_download_discovery_never_imports_an_opted_in_adapter(opted_in, monkeypatch):
    importer = Mock(side_effect=AssertionError("no adapter import"))
    monkeypatch.setattr(datasets.importlib, "import_module", importer)
    monkeypatch.setattr(datasets.registry, "published", lambda: ["synthetic"])
    assert datasets.datasets_with_downloads() == [("synthetic", opted_in.entries)]
    module = datasets._download_adapter("synthetic")
    assert datasets.dataset_root(module) == opted_in.data
    importer.assert_not_called()


def test_fetch_only_downloads_and_names_the_explicit_prepare_step(opted_in, monkeypatch, capsys):
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda **_: [("synthetic", opted_in.entries)])
    install = Mock(side_effect=AssertionError("fetch must not install"))
    monkeypatch.setattr(dataset_runtime, "prepare", install)
    assert datasets.main(["--fetch", "--dataset", "synthetic"]) == 0
    assert "prepare_dataset.py --dataset synthetic" in capsys.readouterr().out
    install.assert_not_called()


@pytest.mark.parametrize("installed,expected", [("1.0", True), ("2.0", False), (None, False)])
def test_pip_check_uses_only_the_target_runtime(opted_in, monkeypatch, installed, expected):
    monkeypatch.setattr(dataset_runtime, "check", lambda _: _runtime())
    platform = Mock(side_effect=AssertionError("do not inspect platform packages"))
    monkeypatch.setattr(datasets.importlib.metadata, "version", platform)
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"marker": installed}), ""))
    monkeypatch.setattr(datasets.subprocess, "run", launch)
    result = datasets.check_dataset("synthetic", opted_in.entries)
    assert result["present"] is expected and result["data_present"] is True
    assert result["entries"][1]["present"] is expected
    command = launch.call_args.args[0]
    assert command[:3] == ["/selected/runtime/bin/python", "-B", "-I"]
    platform.assert_not_called()


def test_missing_runtime_and_present_data_are_reported_separately(opted_in, monkeypatch):
    monkeypatch.setattr(dataset_runtime, "check", lambda _: _runtime(False))
    launch = Mock(side_effect=AssertionError("no target interpreter"))
    monkeypatch.setattr(datasets.subprocess, "run", launch)
    result = datasets.check_dataset("synthetic", opted_in.entries)
    assert not result["present"] and result["data_present"]
    assert result["runtime"]["status"] == "unavailable"
    assert result["entries"][1]["detail"] == "prepare first"
    launch.assert_not_called()


def test_json_check_has_the_same_failure_exit_status(opted_in, monkeypatch, capsys):
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda **_: [("synthetic", opted_in.entries)])
    monkeypatch.setattr(dataset_runtime, "check", lambda _: _runtime(False))
    assert datasets.main(["--check", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)["datasets"][0]
    assert result["data_present"] and not result["present"]


@pytest.mark.parametrize("present", [False, True])
def test_legacy_json_check_reports_missing_bytes_with_nonzero_exit(tmp_path, monkeypatch, capsys, present):
    entry = {"kind": "http", "name": "authored legacy bytes", "version": "1.0",
             "url": "https://example.invalid/data", "dst": "rows.txt"}
    if present:
        (tmp_path / "rows.txt").write_text("authored legacy fixture")
    monkeypatch.setattr(dataset_runtime, "descriptor", lambda name: None)
    monkeypatch.setattr(datasets, "_download_adapter", lambda name: SimpleNamespace(DATASET_ROOT=tmp_path))
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda **kwargs: [("legacy", [entry])])
    assert datasets.main(["--check", "--json"]) == (0 if present else 1)
    result = json.loads(capsys.readouterr().out)["datasets"][0]
    assert result["present"] is present


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_prepare_cli_checks_loading_and_json_errors_are_nonzero(opted_in, monkeypatch, capsys, check, ready):
    prepared = Mock(return_value={"python": "/selected/bin/python", "path": "/selected", "reused": False})
    monkeypatch.setattr(dataset_runtime, "prepare", prepared)
    snapshot = {"present": ready, "status": "ready" if ready else "data_missing",
                "scope": "dataset-provider-load", "detail": "load result", "tasks": 2 if ready else 0}
    provider = Mock()
    provider.check.return_value = snapshot
    monkeypatch.setattr(prepare_dataset, "DatasetProvider", Mock(return_value=provider))
    argv = ["--dataset", "synthetic", "--json"] + (["--check"] if check else [])
    assert prepare_dataset.main(argv) == (0 if ready else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "dataset-provider-load" and result["present"] is ready
    assert prepared.call_count == (0 if check else 1)
    provider.check.assert_called_once_with()


def test_prepare_cli_runtime_root_applies_to_provider_and_is_restored(opted_in, monkeypatch, capsys):
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", "/previous")
    root = opted_in.data / "runtimes"
    seen = []
    def prepare(*args, **kwargs):
        seen.append((os.environ["HG_DATASET_RUNTIME_ROOT"], kwargs["root"]))
        return {"path": str(root), "python": str(root / "bin/python")}
    class Provider:
        def __init__(self, *args, **kwargs):
            pass
        def check(self):
            seen.append(os.environ["HG_DATASET_RUNTIME_ROOT"])
            return dict(present=True, status="ready", scope="dataset-provider-load", detail="loadable")
    monkeypatch.setattr(dataset_runtime, "prepare", prepare)
    monkeypatch.setattr(prepare_dataset, "DatasetProvider", Provider)
    assert prepare_dataset.main(["--dataset", "synthetic", "--runtime-root", str(root), "--json"]) == 0
    assert seen == [(str(root), root), str(root)]
    assert os.environ["HG_DATASET_RUNTIME_ROOT"] == "/previous"


def test_prepare_cli_reports_malformed_descriptor_without_installing(opted_in, monkeypatch, capsys):
    opted_in.descriptor.side_effect = ValueError("bad descriptor")
    prepare = Mock()
    monkeypatch.setattr(dataset_runtime, "prepare", prepare)
    assert prepare_dataset.main(["--dataset", "synthetic", "--check", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
    prepare.assert_not_called()


@pytest.fixture
def execution_cli(monkeypatch):
    receipt = {"dataset": "costbench", "profile": "execution", "python": "/execution/bin/python",
               "path": "/execution", "runtime_id": "verified-artifact",
               "runtime_paths": ["/execution", "/base-python"], "reused": False}
    descriptor = Mock(return_value={"dataset": "costbench", "profile": "execution"})
    prepare = Mock(return_value=receipt)
    check = Mock(return_value=dict(present=True, status="ready", scope="dataset-execution-runtime",
                                   detail="execution artifact verified", runtime=receipt))
    monkeypatch.setattr(dataset_runtime, "execution_descriptor", descriptor)
    monkeypatch.setattr(dataset_runtime, "prepare_execution", prepare)
    monkeypatch.setattr(dataset_runtime, "check_execution", check)
    provider = Mock(side_effect=AssertionError("execution artifacts must not load the QA provider"))
    monkeypatch.setattr(prepare_dataset, "DatasetProvider", provider)
    for name in ("descriptor", "prepare", "resolve", "check"):
        monkeypatch.setattr(dataset_runtime, name, Mock(side_effect=AssertionError("provider profile invoked")))
    return SimpleNamespace(descriptor=descriptor, prepare=prepare, check=check,
                           provider=provider, receipt=receipt)


def test_execution_prepare_only_prepares_and_verifies_the_artifact(execution_cli, tmp_path, capsys):
    root = tmp_path / "runtime-store"
    assert prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--python", "/base/python",
                                 "--runtime-root", str(root), "--timeout", "12", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profile"] == "execution" and result["scope"] == "dataset-execution-runtime"
    assert result["runtime"] == execution_cli.receipt and "tasks" not in result
    execution_cli.descriptor.assert_called_once_with("costbench")
    execution_cli.prepare.assert_called_once_with("costbench", python="/base/python", root=root, timeout_s=12)
    execution_cli.check.assert_called_once_with("costbench", root=root)
    execution_cli.provider.assert_not_called()


def test_execution_check_never_prepares_or_loads_the_provider(execution_cli, capsys):
    assert prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--check", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "check" and result["runtime"] == execution_cli.receipt
    execution_cli.prepare.assert_not_called()
    execution_cli.provider.assert_not_called()
    execution_cli.check.assert_called_once_with("costbench", root=None)


@pytest.mark.parametrize("check_only", [False, True])
def test_execution_missing_declaration_is_invalid_without_fallback(execution_cli, capsys, check_only):
    execution_cli.descriptor.return_value = None
    args = ["--dataset", "costbench", "--runtime", "execution", "--json"]
    assert prepare_dataset.main(args + (["--check"] if check_only else [])) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid" and result["scope"] == "dataset-execution-runtime"
    assert "no dataset execution runtime declaration" in result["detail"]
    execution_cli.prepare.assert_not_called()
    execution_cli.check.assert_not_called()
    execution_cli.provider.assert_not_called()


@pytest.mark.parametrize("status", ["invalid", "unavailable"])
@pytest.mark.parametrize("check_only", [False, True])
def test_execution_readiness_failure_keeps_status_and_json_exit_code(
        execution_cli, capsys, status, check_only):
    execution_cli.check.return_value = dict(present=False, status=status,
                                           scope="dataset-execution-runtime", detail="artifact changed")
    args = ["--dataset", "costbench", "--runtime", "execution", "--json"]
    assert prepare_dataset.main(args + (["--check"] if check_only else [])) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and not result["present"]
    assert result["profile"] == "execution" and result["scope"] == "dataset-execution-runtime"
    assert execution_cli.prepare.call_count == (0 if check_only else 1)
    execution_cli.provider.assert_not_called()


@pytest.mark.parametrize("status", ["invalid", "unavailable"])
def test_execution_prepare_exception_preserves_runtime_failure_class(execution_cli, capsys, status):
    execution_cli.prepare.side_effect = dataset_runtime.DatasetRuntimeUnavailable("damaged artifact", status=status)
    assert prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["scope"] == "dataset-execution-runtime"
    execution_cli.check.assert_not_called()
    execution_cli.provider.assert_not_called()


@pytest.mark.parametrize("previous", [None, "/previous-store"])
def test_execution_root_is_visible_during_check_and_restored_after_failure(
        execution_cli, tmp_path, monkeypatch, capsys, previous):
    if previous is None:
        monkeypatch.delenv("HG_DATASET_RUNTIME_ROOT", raising=False)
    else:
        monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", previous)
    root = tmp_path / "execution-store"
    seen = []
    def check(name, **kwargs):
        seen.append((name, kwargs["root"], os.environ["HG_DATASET_RUNTIME_ROOT"]))
        raise dataset_runtime.DatasetRuntimeUnavailable("runtime inventory mismatch", status="invalid")
    execution_cli.check.side_effect = check
    assert prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--check",
                                 "--runtime-root", str(root), "--json"]) == 1
    assert seen == [("costbench", root, str(root))]
    assert os.environ.get("HG_DATASET_RUNTIME_ROOT") == previous
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
    execution_cli.prepare.assert_not_called()


def test_execution_check_rejects_preparation_only_python_parameter(execution_cli, capsys):
    with pytest.raises(SystemExit) as exc:
        prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--check", "--python", "/base/python"])
    assert exc.value.code == 2
    execution_cli.descriptor.assert_not_called()
    execution_cli.prepare.assert_not_called()
    execution_cli.check.assert_not_called()


def test_execution_timeout_is_a_nonzero_artifact_check_result(execution_cli, capsys):
    execution_cli.prepare.side_effect = subprocess.TimeoutExpired("runtime preparation", 1)
    assert prepare_dataset.main(["--dataset", "costbench", "--runtime", "execution", "--timeout", "1", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable" and result["scope"] == "dataset-execution-runtime"
    assert result["profile"] == "execution"
    execution_cli.check.assert_not_called()


def test_explicit_provider_profile_preserves_loading_behavior(opted_in, monkeypatch, capsys):
    client = Mock()
    client.check.return_value = dict(present=True, status="ready", scope="dataset-provider-load", detail="loadable")
    factory = Mock(return_value=client)
    monkeypatch.setattr(prepare_dataset, "DatasetProvider", factory)
    execution_check = Mock(side_effect=AssertionError("execution profile selected"))
    monkeypatch.setattr(dataset_runtime, "check_execution", execution_check)
    assert prepare_dataset.main(["--dataset", "synthetic", "--runtime", "provider", "--check", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["profile"] == "provider" and result["scope"] == "dataset-provider-load"
    factory.assert_called_once_with("synthetic", timeout_s=None)
    client.check.assert_called_once_with()
    execution_check.assert_not_called()


@pytest.fixture
def provider(opted_in, monkeypatch):
    tasks = [{"task_id": name, "goal": "Synthetic task"} for name in ("a", "b")]
    metadata = {"KIND": "dataset", "ORACLE_SOURCE": "released", "SPLIT": None,
                "DOWNLOADS": opted_in.entries}
    client = Mock()
    client.check.return_value = dict(present=True, status="ready", scope="dataset-provider-load", detail="loadable")
    client.catalog.return_value = dict(tasks=tasks, scorable={}, metadata=metadata, runtime={})
    client.resolve.return_value = dict(tasks=tasks, setups={},
                                     verifiers={tid: dict(kind="answer", expected="yes") for tid in ("a", "b")},
                                     envs={tid: dict(kind="files") for tid in ("a", "b")}, runtime={})
    factory = Mock(return_value=client)
    monkeypatch.setattr(dataset_lint, "DatasetProvider", factory)
    return SimpleNamespace(client=client, factory=factory, metadata=metadata)


def test_provider_inspection_is_static_and_needs_no_prepared_environment(opted_in, provider, monkeypatch):
    monkeypatch.setattr(dataset_lint.importlib, "import_module", Mock(side_effect=AssertionError("adapter import")))
    result = dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema(), inspect_only=True)
    assert result.ok and result.readiness.scope == "static-provider-declarations", result.errors
    assert result.unprovisioned is None
    provider.factory.assert_not_called()


def test_provider_lint_checks_explicit_catalog_selection_without_adapter_import(opted_in, provider, monkeypatch):
    monkeypatch.setattr(dataset_lint.importlib, "import_module", Mock(side_effect=AssertionError("adapter import")))
    result = dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema())
    assert result.ok and result.unprovisioned is None, result.errors
    assert result.readiness.scope == "provider-catalog-and-execution-declarations"
    provider.client.resolve.assert_called_once_with(["a", "b"])
    assert provider.metadata["SPLIT"] is None and "DEFAULT_ENV" not in provider.metadata


def test_provider_lint_preserves_missing_runtime_status(opted_in, provider):
    provider.client.check.return_value = _runtime(False)
    result = dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema())
    assert result.ok and result.unprovisioned == "prepare first"
    assert result.readiness.scope == "dataset-provider-runtime"
    provider.client.catalog.assert_not_called()
    provider.client.resolve.assert_not_called()


@pytest.mark.parametrize("operation", ["catalog", "resolve"])
@pytest.mark.parametrize("status,exit_code", [("unavailable", 0), ("invalid", 1)])
def test_provider_lint_keeps_runtime_failure_status_after_readiness(
        opted_in, provider, capsys, operation, status, exit_code):
    getattr(provider.client, operation).side_effect = dataset_runtime.DatasetRuntimeUnavailable(
        "runtime changed after readiness", status=status)
    assert dataset_lint.main(["--dataset", "synthetic", "--json", "--allow-unprovisioned"]) == exit_code
    result, = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["scope"] == "dataset-provider-runtime"
    assert result["ok"] is (status != "invalid")
    if status == "invalid":
        assert result["errors"] and result["unprovisioned"] is None
    else:
        assert result["errors"] == [] and result["unprovisioned"]


@pytest.mark.parametrize("allow_unprovisioned", [False, True])
@pytest.mark.parametrize("failure,status,exit_code", [
    (NameError("missing_goal_renderer"), "invalid", 1),
    (RuntimeError("goal rendering failed"), "invalid", 1),
    (ModuleNotFoundError("undeclared loader dependency"), "invalid", 1),
    (FileNotFoundError("missing task data"), "data_missing", 0),
    (DataRootNotConfigured("dataset storage is not configured"), "config_missing", 0),
])
def test_provider_lint_distinguishes_qa_errors_from_missing_prerequisites(
        opted_in, provider, monkeypatch, capsys, allow_unprovisioned, failure, status, exit_code):
    def render_goal(question, gold):
        raise failure

    spec = QASpec(
        name="synthetic", root=opted_in.data,
        rows=lambda: {"test": [{"question": "Authored question", "answer": "yes"}]},
        question=lambda row: row["question"], gold=lambda row: {"answer": row["answer"]},
        goal=render_goal, score=lambda prediction, gold: {}, downloads=lambda: [],
        split="test", source="authored loader error control",
    )
    monkeypatch.setattr(provider_worker.registry, "load", lambda name: spec.load())
    provider.client.check.side_effect = lambda: provider_worker.dispatch({
        "dataset": "synthetic", "operation": "check", "task_ids": None})

    argv = ["--dataset", "synthetic", "--json"]
    if allow_unprovisioned:
        argv.append("--allow-unprovisioned")
    assert dataset_lint.main(argv) == exit_code
    result, = json.loads(capsys.readouterr().out)
    assert result["status"] == result["provision_status"]["status"] == status
    assert result["provision_status"]["present"] is False
    assert str(failure) in result["provision_status"]["detail"]
    if status == "invalid":
        assert result["ok"] is False and result["errors"]
        assert result["unprovisioned"] is None
    else:
        assert result["ok"] is True and result["errors"] == []
        assert str(failure) in result["unprovisioned"]
    provider.client.catalog.assert_not_called()
    provider.client.resolve.assert_not_called()


@pytest.mark.parametrize("target", ["verifier", "setup", "env", "split"])
def test_provider_lint_reuses_platform_contract_validators(opted_in, provider, target):
    if target == "verifier":
        provider.client.resolve.return_value["verifiers"]["a"] = {"kind": "command"}
    elif target == "setup":
        provider.client.resolve.return_value["setups"]["a"] = {"files": [{"path": "../escape", "content": "bad"}]}
    elif target == "env":
        provider.client.resolve.return_value["envs"]["a"] = {"kind": "unsupported"}
    else:
        provider.metadata["SPLIT"] = {"train": ["a", "b"], "eval": ["b"]}
    result = dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema())
    assert not result.ok and result.errors


def test_static_error_is_not_hidden_by_an_unprepared_runtime(opted_in, provider):
    opted_in.source.write_text(opted_in.source.read_text().replace("KIND='dataset'", "KIND='broken'"))
    provider.client.check.return_value = _runtime(False)
    result = dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema())
    assert not result.ok and any("KIND" in error for error in result.errors)
    provider.factory.assert_not_called()
