"""QA declarations stay lazy through the registry's selected-task boundary."""
from collections.abc import Mapping
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from data.scaffolds import registry
from data.scaffolds.qa import base


def test_missing_source_retains_structured_cause_for_provider_readiness(tmp_path, monkeypatch):
    def rows():
        raise FileNotFoundError(tmp_path / "missing.parquet")

    spec = base.QASpec(name="missing_control", root=tmp_path, rows=rows,
                       question=lambda row: row["question"], gold=lambda row: row,
                       goal=lambda question, gold: question, score=lambda prediction, gold: {},
                       downloads=lambda: [], split="test", source="authored missing-data control")
    # Import-time discovery may have already observed the failure. Loading must
    # retain its type rather than force readiness to guess from free-form text.
    spec.build()
    from data.scaffolds.provider_worker import _check
    monkeypatch.setattr(registry, "load", lambda name: spec.load())
    assert _check("missing_control", None)["status"] == "data_missing"


@pytest.fixture
def qa(monkeypatch, tmp_path):
    rows = [{"question": f"Question {i}", "answer": str(i)} for i in range(3)]
    spec = base.QASpec(
        name="qa_selected_control", root=tmp_path,
        rows=lambda: {"test": rows}, question=lambda row: row["question"],
        gold=lambda row: {"answer": row["answer"]},
        goal=lambda question, gold: question,
        score=lambda prediction, gold: {"reward": float(prediction == gold["answer"])},
        downloads=lambda: [], split="test", source="authored selection control",
        extra_setup=[{"path": "public.txt", "content": "public fixture"}],
    )
    tasks, _ = spec.load()
    control = SimpleNamespace(spec=spec, ids=[task["task_id"] for task in tasks],
                              calls={"SETUP": [], "VERIFY": []}, lookups=[], allowed=None)

    def spy(name, build):
        def selected(record):
            task_id = record["task_id"]
            control.calls[name].append(task_id)
            if control.allowed is not None and task_id not in control.allowed:
                raise RuntimeError(f"unselected declaration was constructed: {task_id}")
            return build(record)
        return selected

    monkeypatch.setattr(spec, "setup", spy("SETUP", spec.setup))
    monkeypatch.setattr(spec, "verifier", spy("VERIFY", spec.verifier))
    module = ModuleType("data.demo")
    module.load = spec.load

    def declared(name):
        if name in ("SETUP", "VERIFY"):
            control.lookups.append((name, {key: list(value) for key, value in control.calls.items()}))
        return base.module_getattr(spec, name)

    module.__getattr__ = declared
    monkeypatch.setitem(sys.modules, "data.demo", module)
    control.module = module
    return control


@pytest.mark.parametrize("name", ["SETUP", "VERIFY"])
def test_mapping_keys_and_missing_lookups_never_construct_declarations(qa, name):
    declarations = getattr(qa.module, name)
    assert isinstance(declarations, Mapping)
    assert len(declarations) == len(qa.ids)
    assert list(declarations) == qa.ids
    assert list(declarations.keys()) == qa.ids
    assert set(declarations) == set(qa.ids)
    assert qa.ids[0] in declarations
    assert "unknown" not in declarations
    assert declarations.get("unknown") is None
    with pytest.raises(KeyError):
        declarations["unknown"]
    assert qa.calls == {"SETUP": [], "VERIFY": []}


@pytest.mark.parametrize("name", ["SETUP", "VERIFY"])
def test_one_lookup_constructs_only_that_qa_declaration(qa, name):
    chosen = qa.ids[1]
    qa.allowed = {chosen}
    declarations = getattr(qa.module, name)
    actual = declarations[chosen]
    record = qa.spec.ensure()[1]
    builder = base.QASpec.setup if name == "SETUP" else base.QASpec.verifier
    assert actual == builder(qa.spec, record)
    assert qa.calls[name] == [chosen]
    assert qa.calls["VERIFY" if name == "SETUP" else "SETUP"] == []


def test_registry_getattr_does_not_construct_unselected_qa_values(qa):
    chosen = qa.ids[1]
    qa.allowed = {chosen}
    tasks, setups, verifiers = registry.load_tasks("demo", task_ids=[chosen])
    assert [task["task_id"] for task in tasks] == [chosen]
    assert set(setups) == set(verifiers) == {chosen}
    assert qa.lookups == [("SETUP", {"SETUP": [], "VERIFY": []}),
                          ("VERIFY", {"SETUP": [], "VERIFY": []})]
    assert qa.calls == {"SETUP": [chosen], "VERIFY": [chosen]}
    gold = next(item for item in verifiers[chosen]["inputs"] if item["path"] == base.GOLD_FILE)
    assert json.loads(gold["content"]) == {"answer": "1"}
    assert all("gold" not in task and "expected" not in task for task in tasks)


def test_empty_registry_selection_constructs_no_qa_declarations(qa):
    qa.allowed = set()
    assert registry.load_tasks("demo", task_ids=[]) == ([], {}, {})
    assert [name for name, _ in qa.lookups] == ["SETUP", "VERIFY"]
    assert qa.calls == {"SETUP": [], "VERIFY": []}


@pytest.mark.parametrize("selection,reason", [(["unknown"], "unknown"),
                                              (["unknown", "unknown"], "duplicate")])
def test_invalid_registry_selection_fails_before_mapping_or_value_construction(qa, selection, reason):
    qa.allowed = set()
    with pytest.raises(ValueError, match=reason):
        registry.load_tasks("demo", task_ids=selection)
    assert qa.lookups == []
    assert qa.calls == {"SETUP": [], "VERIFY": []}


@pytest.mark.parametrize("name,builder", [("SETUP", base.QASpec.setup),
                                         ("VERIFY", base.QASpec.verifier)])
def test_explicit_items_iteration_preserves_full_pool_order_and_fields(qa, name, builder):
    expected = {record["task_id"]: builder(qa.spec, record) for record in qa.spec.ensure()}
    declarations = getattr(qa.module, name)
    actual = dict(declarations.items())
    assert list(actual) == qa.ids
    assert actual == expected
    assert qa.calls[name] == qa.ids


def test_registry_default_keeps_full_pool_declarations(qa):
    tasks, setups, verifiers = registry.load_tasks("demo")
    assert [task["task_id"] for task in tasks] == qa.ids
    assert list(setups) == list(verifiers) == qa.ids
    assert qa.calls == {"SETUP": qa.ids, "VERIFY": qa.ids}


@pytest.mark.parametrize("selection", [None, [], [1]])
def test_environment_values_are_built_only_for_selected_tasks(qa, monkeypatch, selection):
    selected = None if selection is None else [qa.ids[index] for index in selection]
    expected = qa.ids if selected is None else selected
    calls = []

    def environment(record):
        task_id = record["task_id"]
        assert task_id in expected, "an unselected environment was constructed"
        calls.append(task_id)
        return {"kind": "files"}

    monkeypatch.setattr(qa.spec, "environment", environment)
    qa.module.ENV = qa.spec.build()["ENV"]
    assert list(qa.module.ENV) == qa.ids
    assert len(qa.module.ENV) == len(qa.ids)
    assert qa.ids[0] in qa.module.ENV
    assert calls == [], "catalog keys must not construct execution environments"
    assert registry.load_envs("demo", task_ids=selected) == {
        task_id: {"kind": "files"} for task_id in expected}
    assert calls == expected
