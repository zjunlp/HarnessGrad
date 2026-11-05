"""Provider-backed registry access never imports its adapter in the driver."""
import copy

import pytest

from data.scaffolds import registry


@pytest.fixture
def remote(monkeypatch):
    tasks = [{"task_id": "gsm8k/train/0", "goal": "public train question", "inputs": []},
             {"task_id": "gsm8k/test/0", "goal": "public test question", "inputs": []}]
    calls = []

    class Provider:
        def catalog(self):
            return copy.deepcopy({"tasks": tasks, "scorable": {t["task_id"]: "private" for t in tasks},
                "metadata": {"SPLIT": {"train": [tasks[0]["task_id"]], "eval": [tasks[1]["task_id"]]},
                             "OFFICIAL_EVAL": [tasks[1]["task_id"]], "ORACLE_SOURCE": "released"}})

        def resolve(self, task_ids):
            calls.append(task_ids)
            selected = registry._selected_tasks("gsm8k", tasks, task_ids)
            return {"tasks": selected,
                    "setups": {t["task_id"]: {"files": []} for t in selected},
                    "verifiers": {t["task_id"]: {"kind": "answer", "expected": "private"} for t in selected},
                    "envs": {t["task_id"]: {"kind": "files"} for t in selected}}

        def check(self):
            return {"present": False, "status": "unavailable", "scope": "dataset-provider-runtime",
                    "detail": "prepare the selected runtime"}

    provider = Provider()
    monkeypatch.setattr(registry, "provider", lambda name: provider)
    original = registry.importlib.import_module

    def forbid_adapter(name, *args, **kwargs):
        assert not name.startswith("data.datasets."), "the platform imported a provider-owned adapter"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(registry.importlib, "import_module", forbid_adapter)
    return tasks, calls


def test_catalog_split_oracle_and_status_stay_behind_provider(remote):
    tasks, calls = remote
    loaded, scorable, split = registry.load_split("gsm8k")
    assert loaded == tasks
    assert set(scorable) == {t["task_id"] for t in tasks}
    assert split == {"train": [tasks[0]["task_id"]], "eval": [tasks[1]["task_id"]]}
    assert registry.load("gsm8k")[0] == tasks
    assert registry.load_split_groups("gsm8k", tasks) is None
    assert registry.official_split("gsm8k", tasks) == split
    assert registry.oracle_source("gsm8k") == ("released", None)
    assert registry.provision_status("gsm8k")["scope"] == "dataset-provider-runtime"
    assert calls == [], "catalog/status must not resolve execution declarations"


@pytest.mark.parametrize("selection", [None, [], ["gsm8k/train/0"]])
def test_selected_declarations_and_private_answers_use_provider(remote, selection):
    tasks, calls = remote
    chosen, setups, verifiers = registry.load_tasks("gsm8k", task_ids=selection)
    envs = registry.load_envs("gsm8k", task_ids=selection)
    wanted = {t["task_id"] for t in tasks} if selection is None else set(selection)
    assert {t["task_id"] for t in chosen} == wanted
    assert set(setups) == set(verifiers) == set(envs) == wanted
    assert all(set(t) == {"task_id", "goal", "inputs"} for t in chosen)
    assert all(verifiers[tid]["expected"] == "private" for tid in wanted)
    assert calls == [selection, selection]


def test_provider_tasks_still_use_platform_privacy_validation(remote, monkeypatch):
    original = registry.provider("gsm8k")
    resolve = original.resolve

    def leaking(selection):
        result = resolve(selection)
        result["tasks"][0] = {**result["tasks"][0], "expected": "private"}
        return result

    monkeypatch.setattr(original, "resolve", leaking)
    with pytest.raises(ValueError, match="expected"):
        registry.load_tasks("gsm8k", task_ids=["gsm8k/train/0"])
