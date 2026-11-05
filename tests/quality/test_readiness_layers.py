"""Scoped inspection tests: synthetic adapters, no host configuration or data."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harnessgrad.provisioning import normalize_provision_status
from tools import dataset_lint as lint


@pytest.fixture
def adapter(monkeypatch):
    tasks = [{"task_id": "synthetic", "goal": "synthetic"}]
    module = SimpleNamespace(KIND="dataset", ORACLE_SOURCE="released",
                             load=Mock(), downloads=Mock(return_value=[{
                                 "kind": "pip", "name": "synthetic", "version": "1.0",
                                 "size": "1 KB", "package": "synthetic==1.0",
                                 "note": "Synthetic declaration, never downloaded."}]),
                             provision_status=Mock(return_value={"present": True}))
    monkeypatch.setattr(lint.importlib, "import_module", Mock(return_value=module))
    for name, value in [("load", (tasks, {})), ("load_tasks", (tasks, {}, None)),
                        ("load_split_groups", None), ("load_envs", {})]:
        monkeypatch.setattr(lint.registry, name, Mock(return_value=value))
    return module


def test_inspection_skips_dynamic_calls(adapter):
    result = lint.lint_dataset("synthetic", lint.load_schema(), inspect_only=True)
    assert result.status == "ready"
    assert result.as_dict()["scope"] == "imported-adapter-declarations"
    assert result.provision_snapshot is None
    adapter.downloads.assert_not_called()
    adapter.provision_status.assert_not_called()
    lint.registry.load.assert_not_called()
    lint.registry.load_tasks.assert_not_called()


def test_new_operation_rechecks_corruption(adapter):
    adapter.provision_status.side_effect = [
        {"present": True},
        {"present": False, "status": "invalid", "detail": "digest mismatch"}]
    first = lint.lint_dataset("synthetic", lint.load_schema())
    second = lint.lint_dataset("synthetic", lint.load_schema())
    assert first.status == "ready" and first.readiness.scope == "full-adapter-contract"
    assert second.status == "invalid" and not second.ok
    assert adapter.provision_status.call_count == 2
    assert second.provision_snapshot["present"] is False


def test_inspect_cli_reports_scope(adapter, capsys):
    import json
    assert lint.main(["--dataset", "synthetic", "--inspect", "--json"]) == 0
    result, = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready" and result["scope"] == "imported-adapter-declarations"
    adapter.provision_status.assert_not_called()


@pytest.mark.parametrize("stage", ["import", "provision", "load"])
def test_typed_missing_config(adapter, monkeypatch, stage):
    from harnessgrad.config import ConfigMissing
    target = {"import": lint.importlib.import_module,
              "provision": adapter.provision_status, "load": lint.registry.load}[stage]
    target.side_effect = ConfigMissing("synthetic configuration absent")
    result = lint.lint_dataset("synthetic", lint.load_schema())
    assert result.ok and result.status == "config_missing"


def test_legacy_false_does_not_infer_reason():
    for detail in ["missing data", "digest mismatch", "needs configuration"]:
        fact = normalize_provision_status({"present": False, "detail": detail})
        assert fact.status == "unavailable"
        assert fact.scope == "adapter-provision-status"


@pytest.mark.parametrize("status", ["data_missing", "config_missing", "invalid", "refused"])
def test_explicit_reasons_are_preserved(status):
    fact = normalize_provision_status({"present": False, "status": status, "scope": "runtime"})
    assert fact.status == status and fact.scope == "runtime"


@pytest.mark.parametrize("snapshot", [{"present": True, "status": "invalid"},
    {"present": False, "status": "ready"}, {"present": True, "scope": ""}])
def test_contradictions_fail(snapshot):
    with pytest.raises(ValueError):
        normalize_provision_status(snapshot)


def test_dataset_inventory_never_calls_full_status(monkeypatch):
    from harnessgrad import readiness
    monkeypatch.setattr(readiness.datasets, "published", lambda: ["synthetic"])
    monkeypatch.setattr(readiness.datasets, "load", lambda name: ([{"task_id": "a"}], {}))
    status = Mock(side_effect=AssertionError("full status must not run"))
    monkeypatch.setattr(readiness.datasets, "provision_status", status)
    check = readiness._dataset_check(SimpleNamespace(dataset="synthetic"))
    assert check.ok and check.readiness.status == "ready"
    assert check.readiness.scope == "public-task-inventory"
    status.assert_not_called()
