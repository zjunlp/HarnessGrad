"""Readiness regressions using synthetic adapters only: no data/model access."""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import dataset_lint  # noqa: E402
from harnessgrad.paths import DataRootNotConfigured  # noqa: E402


@pytest.fixture
def adapter(monkeypatch):
    tasks = [{"task_id": "synthetic", "goal": "A synthetic task"}]
    downloads = [{"kind": "pip", "name": "synthetic", "version": "1.0",
                  "size": "1 KB", "package": "synthetic==1.0",
                  "note": "Synthetic declaration; never downloaded."}]
    module = SimpleNamespace(KIND="dataset", ORACLE_SOURCE="released",
                             load=Mock(return_value=(tasks, {})),
                             downloads=Mock(return_value=downloads))
    importer = Mock(return_value=module)
    monkeypatch.setattr(dataset_lint.importlib, "import_module", importer)
    load = Mock(return_value=(tasks, {}))
    monkeypatch.setattr(dataset_lint.registry, "load", load)
    monkeypatch.setattr(dataset_lint.registry, "load_split_groups", Mock(return_value=None))
    monkeypatch.setattr(dataset_lint.registry, "load_tasks", Mock(return_value=(tasks, {}, None)))
    monkeypatch.setattr(dataset_lint.registry, "load_envs", Mock(return_value={}))
    return SimpleNamespace(module=module, importer=importer, load=load)


def lint(**kwargs):
    return dataset_lint.lint_dataset("synthetic", dataset_lint.load_schema(), **kwargs)


@pytest.mark.parametrize("snapshot,expected", [
    ({"present": False, "split_dir": "/external/split", "root": "/external"},
     "no data at /external/split"),
    ({"present": False, "root": "/external", "detail": "runtime digest mismatch"},
     "no data at /external: runtime digest mismatch"),
    ({"present": False, "root": None, "detail": "HG_DATA_ROOT is not configured"},
     "data not provisioned: HG_DATA_ROOT is not configured"),
    ({"present": False}, "data not provisioned"),
])
def test_absent_readiness_uses_exactly_one_snapshot(adapter, snapshot, expected):
    # A second read would see a different filesystem state, not just cost more.
    adapter.module.provision_status = Mock(side_effect=[snapshot, {"present": True}])
    result = lint()
    assert result.ok and result.unprovisioned == expected
    adapter.module.provision_status.assert_called_once_with()
    adapter.load.assert_not_called()


def test_present_readiness_loads_after_one_call(adapter):
    adapter.module.provision_status = Mock(return_value={"present": True})
    result = lint()
    assert result.ok and result.unprovisioned is None
    adapter.module.provision_status.assert_called_once_with()
    adapter.load.assert_called_once_with("synthetic")


def test_unprovisioned_status_does_not_erase_static_errors(adapter):
    adapter.module.KIND = "broken"
    adapter.module.provision_status = Mock(return_value={
        "present": False, "detail": "runtime missing"})
    result = lint(require_data=False)
    assert not result.ok
    assert "KIND must be one of" in result.errors[0]
    assert "runtime missing" in result.unprovisioned
    adapter.module.provision_status.assert_called_once_with()
    adapter.load.assert_not_called()


def test_missing_optional_readiness_still_loads(adapter):
    assert lint().ok
    adapter.load.assert_called_once_with("synthetic")


@pytest.mark.parametrize("require_data", [True, False])
@pytest.mark.parametrize("snapshot", [None, [], "ready", True, {},
                                      {"present": None}, {"present": 0},
                                      {"present": 1}, {"present": "false"},
                                      {"present": "true"}])
def test_malformed_readiness_fails_closed(adapter, snapshot, require_data):
    adapter.module.provision_status = Mock(return_value=snapshot)
    result = lint(require_data=require_data)
    assert not result.ok and result.unprovisioned is None
    assert result.errors == ["provision_status() must return a dict with boolean `present`"]
    adapter.module.provision_status.assert_called_once_with()
    adapter.load.assert_not_called()


@pytest.mark.parametrize("require_data", [True, False])
@pytest.mark.parametrize("stage", ["import", "provision", "load", "path"])
def test_explicit_missing_configuration_is_unprovisioned(adapter, stage, require_data):
    missing = DataRootNotConfigured("Set HG_DATA_ROOT for synthetic storage")
    if stage == "import":
        adapter.importer.side_effect = missing
    elif stage == "provision":
        adapter.module.provision_status = Mock(side_effect=missing)
    elif stage == "load":
        adapter.load.side_effect = missing
    else:
        class MissingPath:
            def __fspath__(self):
                raise missing
        adapter.module.DATASET_ROOT = MissingPath()
    result = lint(require_data=require_data)
    assert result.ok and result.errors == []
    assert result.unprovisioned == str(missing)
    if stage != "load":
        adapter.load.assert_not_called()


@pytest.mark.parametrize("require_data", [True, False])
@pytest.mark.parametrize("stage,prefix", [("import", "will not import:"),
                                          ("provision", "provision_status() raised"),
                                          ("load", "load() raised")])
def test_unexpected_valueerror_remains_error(adapter, stage, prefix, require_data):
    broken = ValueError("synthetic adapter defect")
    if stage == "import":
        adapter.importer.side_effect = broken
    elif stage == "provision":
        adapter.module.provision_status = Mock(side_effect=broken)
    else:
        adapter.load.side_effect = broken
    result = lint(require_data=require_data)
    assert not result.ok and result.unprovisioned is None
    assert result.errors == [f"{prefix} ValueError: synthetic adapter defect"]


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("exception,exit_code", [
    (DataRootNotConfigured("Set HG_DATA_ROOT"), 0),
    (ValueError("unexpected import defect"), 1),
])
def test_cli_keeps_configuration_distinct_from_defects(adapter, capsys, allow,
                                                       exception, exit_code):
    adapter.importer.side_effect = exception
    argv = ["--dataset", "synthetic", "--json"]
    if allow:
        argv.append("--allow-unprovisioned")
    assert dataset_lint.main(argv) == exit_code
    result, = json.loads(capsys.readouterr().out)
    assert result["ok"] is (exit_code == 0)
    if exit_code == 0:
        assert result["unprovisioned"] == str(exception)
    else:
        assert result["unprovisioned"] is None
        assert "ValueError" in result["errors"][0]


def test_cli_reports_contract_errors_even_when_storage_is_missing(monkeypatch, capsys):
    result = dataset_lint.Result("synthetic")
    result.error("broken declaration")
    result.unprovisioned = "missing external storage"
    monkeypatch.setattr(dataset_lint, "lint_dataset", lambda *args, **kwargs: result)
    assert dataset_lint.main(["--dataset", "synthetic", "--allow-unprovisioned"]) == 1
    output = capsys.readouterr().out
    assert "FAIL synthetic" in output and "broken declaration" in output
    assert "missing external storage" in output
    assert "SKIP synthetic" not in output


@pytest.mark.parametrize("field,error", [
    ("VERIFY", FileNotFoundError("selected tests.json is missing")),
    ("VERIFY", ValueError("selected tests.json does not match the pinned SHA-256")),
    ("SETUP", FileNotFoundError("selected setup input is missing")),
])
def test_cli_reports_lazy_declaration_failure_and_keeps_checking(adapter, capsys, field, error):
    class BrokenDeclaration(Mapping):
        def __iter__(self):
            return iter(("synthetic",))

        def __len__(self):
            return 1

        def __getitem__(self, key):
            raise error

    declaration = BrokenDeclaration()
    healthy = SimpleNamespace(KIND="dataset", ORACLE_SOURCE="released",
                              load=adapter.module.load, downloads=adapter.module.downloads)
    adapter.importer.side_effect = {
        "data.datasets.synthetic": adapter.module,
        "data.datasets.healthy": healthy,
    }.__getitem__
    setattr(adapter.module, field, declaration)

    def load_tasks(name):
        if name == "synthetic":
            declaration["synthetic"]
        return adapter.load(name)[0], {}, None

    dataset_lint.registry.load_tasks.side_effect = load_tasks
    assert dataset_lint.main([
        "--dataset", "synthetic", "--dataset", "healthy", "--json", "--allow-unprovisioned",
    ]) == 1
    output = capsys.readouterr()
    broken, following = json.loads(output.out)
    assert not output.err
    assert broken["dataset"] == "synthetic" and broken["status"] == "invalid"
    assert f"registry.load_tasks: {type(error).__name__}: {error}" in broken["errors"]
    assert following["dataset"] == "healthy" and following["ok"]
