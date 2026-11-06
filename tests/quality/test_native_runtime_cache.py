"""Measurement-cache identity controls; all tasks and admission receipts are authored."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
from types import SimpleNamespace

import pytest

from eval.run import runner
from eval.verification import native_runtime


@pytest.fixture
def control(tmp_path, monkeypatch):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"name": "authored-cache", "entrypoint": "agent.py"}))
    state = SimpleNamespace(repo=repo, task={"task_id": "t", "goal": "authored cache control"},
                            score=1.0, calls=[], admissions=[], refuse=False, override=None,
                            host={"system": "Linux", "machine": platform.machine(),
                                  "kernel": platform.release(), "admission_policy": "linux-capabilities-v1"})

    def profile(label):
        path = tmp_path / (label + ".json")
        raw = json.dumps({"dataset": "bird_minidev", "revision": label}).encode()
        path.write_bytes(raw)
        return {"dataset": "bird_minidev", "manifest": str(path),
                "sha256": hashlib.sha256(raw).hexdigest()}

    def receipt(binding):
        return {"verified": True, "dataset": binding["dataset"], "sha256": binding["sha256"],
                "mechanism": "sealed-memfd-private-root-v1", "files": 3,
                "identity": {"kernel": "recorded-build-kernel", "machine": platform.machine()},
                "host": deepcopy(state.host)}

    @contextmanager
    def admit(binding):
        state.admissions.append(deepcopy(binding))
        if state.refuse:
            raise native_runtime.NativeRuntimeUnavailable("authored unavailable runtime")
        assert hashlib.sha256(Path(binding["manifest"]).read_bytes()).hexdigest() == binding["sha256"]
        yield SimpleNamespace(receipt=receipt(binding))

    def run(repo, task, **kwargs):
        state.calls.append(task["task_id"])
        verdict = {"kind": "command", "score": state.score, "passed": state.score == 1.0}
        binding = (kwargs.get("verifier") or {}).get("native_runtime")
        if binding is not None:
            verdict["native_runtime"] = receipt(binding)
            if state.override is not None:
                state.override(verdict)
        return {"task_id": task["task_id"], "answer": "authored", "trace": "", "exit_code": 0,
                "stderr": "", "verdict": verdict}

    def spec(binding):
        return {"kind": "command", "argv": ["python3", "check.py"], "native_runtime": binding,
                "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json"}

    def evaluate(binding, cache, trials=1):
        verifiers = {"t": spec(binding)} if binding is not None else {}
        return runner._evaluate(repo, [state.task], {}, cache=cache, harness_sha="h", trials=trials,
                                verifiers=verifiers, persist_trial_report=False)

    state.a, state.b = profile("A"), profile("B")
    state.receipt, state.spec, state.evaluate = receipt, spec, evaluate
    monkeypatch.setattr(native_runtime, "admit", admit)
    monkeypatch.setattr(runner, "run_one", run)
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    return state


def test_changed_runtime_remeasures_and_keeps_both_cache_entries(control):
    cache = {}
    first = control.evaluate(control.a, cache)
    assert first["per_task"] == {"t": 1.0} and not first["cached_tasks"]
    old_key = next(iter(cache))
    control.score = 0.0
    second = control.evaluate(control.b, cache)
    assert second["per_task"] == {"t": 0.0} and not second["cached_tasks"]
    assert control.calls == ["t", "t"] and len(cache) == 2
    assert cache[old_key]["verdict"]["native_runtime"]["sha256"] == control.a["sha256"]
    third = control.evaluate(control.b, cache)
    assert third["cached_tasks"] == ["t"] and third["per_task"] == {"t": 0.0}
    assert control.calls == ["t", "t"]


def test_same_identity_reuses_cache_after_manifest_relocation(control, tmp_path):
    cache = {}
    control.evaluate(control.a, cache)
    relocated = dict(control.a)
    path = tmp_path / "relocated.json"
    path.write_bytes(Path(control.a["manifest"]).read_bytes())
    relocated["manifest"] = str(path)
    result = control.evaluate(relocated, cache)
    assert result["cached_tasks"] == ["t"] and control.calls == ["t"]
    assert len(cache) == 1 and len(control.admissions) == 2


@pytest.mark.parametrize("field", ["missing", "verified", "sha256", "dataset", "identity", "host", "mechanism"])
def test_inconsistent_receipt_under_correct_key_is_remeasured(control, field):
    cache = {}
    control.evaluate(control.a, cache)
    verdict = next(iter(cache.values()))["verdict"]
    if field == "missing":
        verdict.pop("native_runtime")
    elif field == "verified":
        verdict["native_runtime"][field] = False
    else:
        verdict["native_runtime"][field] = "another identity"
    control.score = 0.0
    result = control.evaluate(control.a, cache)
    assert not result["cached_tasks"] and result["per_task"] == {"t": 0.0}
    assert control.calls == ["t", "t"]


@pytest.mark.parametrize("field", ["trial", "missing-trials", "last-verdict"])
def test_every_trial_and_the_last_verdict_must_match(control, field):
    cache = {}
    control.evaluate(control.a, cache, trials=2)
    entry = next(iter(cache.values()))
    if field == "trial":
        entry["trial_verdicts"][0]["native_runtime"] = control.receipt(control.b)
    elif field == "missing-trials":
        entry.pop("trial_verdicts")
    else:
        entry["verdict"] = deepcopy(entry["verdict"])
        entry["verdict"]["native_runtime"] = control.receipt(control.b)
    control.score = 0.0
    result = control.evaluate(control.a, cache, trials=2)
    assert not result["cached_tasks"] and result["per_task"] == {"t": 0.0}
    assert control.calls == ["t"] * 4


def test_legacy_native_entry_is_not_reused(control):
    cache = {"h:t:1": {"score": 1.0, "trace": "", "verdict": {
        "score": 1.0, "native_runtime": control.receipt(control.a)}}}
    control.score = 0.0
    result = control.evaluate(control.a, cache)
    assert not result["cached_tasks"] and result["per_task"] == {"t": 0.0}
    assert control.calls == ["t"]


def test_non_native_request_rejects_legacy_native_receipt(control):
    cache = {"h:t:1": {"score": 1.0, "trace": "", "verdict": {
        "score": 1.0, "native_runtime": control.receipt(control.a)}}}
    control.score = 0.0
    result = control.evaluate(None, cache)
    assert not result["cached_tasks"] and result["per_task"] == {"t": 0.0}
    assert control.evaluate(None, cache)["cached_tasks"] == ["t"]
    assert control.calls == ["t"]


def test_unavailable_runtime_fails_before_cache_or_execution(control):
    class NoCache(dict):
        def get(self, *args):
            pytest.fail("unavailable native runtime read the measurement cache")

        def __setitem__(self, *args):
            pytest.fail("unavailable native runtime populated the measurement cache")

    control.refuse = True
    result = control.evaluate(control.a, NoCache({"h:t:1": {"score": 1.0, "trace": ""}}))
    assert not result["per_task"] and not result["cached_tasks"]
    assert result["invalid"]["t"]["stage"] == "native runtime preparation"
    assert control.calls == []


@pytest.mark.parametrize('failure', ['sandbox', 'argv', 'backend', 'services', 'dataset', 'declaration'])
def test_cache_never_bypasses_native_route_validation(control, failure):
    class NoRead(dict):
        def get(self, *args):
            pytest.fail('invalid native declaration reached the measurement cache')

    cache = {}
    control.evaluate(control.a, cache)
    spec = control.spec(dict(control.a))
    env = {'kind': 'files'}
    if failure == 'argv':
        spec['argv'] = ['python3', 'actor.py']
    elif failure == 'backend':
        env['kind'] = 'exec'
    elif failure == 'services':
        env['services'] = [{'name': 'authored-unsupported-service'}]
    elif failure == 'dataset':
        spec['native_runtime']['dataset'] = 'unsupported'
    elif failure == 'declaration':
        spec['native_runtime'] = None
    result = runner._evaluate(control.repo, [control.task], {}, cache=NoRead(cache), harness_sha='h',
                              sandbox=failure != 'sandbox', verifiers={'t': spec}, envs={'t': env},
                              persist_trial_report=False)
    assert not result['cached_tasks'] and not result['per_task'] and result['invalid']
    assert control.calls == ['t']


@pytest.mark.parametrize("field", ["missing", "different", "unverified"])
def test_fresh_mismatched_runtime_is_not_scored_or_cached(control, field):
    def alter(verdict):
        if field == "missing":
            verdict.pop("native_runtime")
        elif field == "different":
            verdict["native_runtime"] = control.receipt(control.b)
        else:
            verdict["native_runtime"]["verified"] = False

    control.override = alter
    cache = {}
    result = control.evaluate(control.a, cache)
    assert not result["per_task"] and not result["cached_tasks"]
    assert result["invalid"]["t"]["stage"] == "native runtime identity"
    assert not cache


def test_failed_profile_does_not_block_non_native_task(control):
    control.refuse = True
    tasks = [control.task, {"task_id": "plain", "goal": "authored non-native control"}]
    result = runner._evaluate(control.repo, tasks, {}, cache={}, harness_sha="h",
                              verifiers={"t": control.spec(control.a)}, persist_trial_report=False)
    assert result["per_task"] == {"plain": 1.0} and set(result["invalid"]) == {"t"}
    assert control.calls == ["plain"]


def test_shared_profile_is_admitted_once_per_evaluation(control):
    tasks = [control.task, {"task_id": "other", "goal": "authored shared-profile control"}]
    result = runner._evaluate(control.repo, tasks, {}, cache={}, harness_sha="h",
                              verifiers={t["task_id"]: control.spec(control.a) for t in tasks},
                              persist_trial_report=False)
    assert result["per_task"] == {"t": 1.0, "other": 1.0}
    assert len(control.admissions) == 1


def test_compatible_host_change_remeasures(control):
    cache = {}
    control.evaluate(control.a, cache)
    control.host["kernel"] = "another-compatible-kernel"
    control.score = 0.0
    result = control.evaluate(control.a, cache)
    assert not result["cached_tasks"] and result["per_task"] == {"t": 0.0}
    assert not result["invalid"] and len(cache) == 2
