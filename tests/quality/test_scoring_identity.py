"""Opt-in scoring identities; legacy records and unopted adapters stay unchanged."""
import copy
import json
import shlex
import sys
from types import SimpleNamespace

import pytest

from harnessgrad import policies, scoring_identity as scoring
from harnessgrad.records import _curve_point
from harnessgrad.loops.eval_side import _baseline_block, _resume_mismatch
from tools import prepare_h0


def identity(model="judge-a"):
    return scoring.create({"metric": "synthetic.defense.v1", "judge": {"model": model}})


def point(scoring_identity=None):
    return _curve_point(run_id="test", round_index=0, identity={},
        sampling={"task_ids": ["t"]}, scores=[.5], task_ids=["t"], cost={}, cumulative={},
        scoring_identity=scoring_identity)


@pytest.mark.parametrize("name", ["gsm8k", "appworld", "memoryagentbench", "personamem_v2", "tau3_bench", "demo"])
def test_unopted_adapters_are_not_imported(name, monkeypatch):
    def forbidden(*args):
        pytest.fail("unopted adapters must not import a scoring provider or their data")
    monkeypatch.setattr(scoring.importlib.util, "spec_from_file_location", forbidden)
    assert scoring.resolve(name) is None


def test_provider_is_explicit_and_profiles_can_describe_mixed_verifiers(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring, "PROVIDERS", tmp_path)
    assets = tmp_path / "synthetic"
    assets.mkdir()
    (assets / "scoring_profile.py").write_text(
        "def profile():\n return {'metric':'mixed.v1','verifiers':[{'kind':'answer'},"
        "{'kind':'command','judge':{'model':'a'}},{'kind':'command','judge':{'model':'b'}}]}\n")
    got = scoring.resolve("synthetic")
    assert len(got["profile"]["verifiers"]) == 3
    assert scoring.validate(got) == got


def test_a_broken_opt_in_cannot_silently_disable_identity_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring, "PROVIDERS", tmp_path)
    assets = tmp_path / "synthetic"
    assets.mkdir()
    (assets / "scoring_profile.py").write_text("# missing profile factory\n")
    with pytest.raises(ValueError, match="requires profile"):
        scoring.resolve("synthetic")


def test_identity_is_canonical_and_detached_from_its_input():
    profile = {"metric": "metric", "parameters": {"temperature": 0, "max_tokens": None}}
    got = scoring.create(profile)
    assert got == scoring.create({"parameters": {"max_tokens": None, "temperature": 0}, "metric": "metric"})
    profile["parameters"]["temperature"] = 1
    assert got != scoring.create(profile)
    assert got["profile"]["parameters"]["temperature"] == 0


@pytest.mark.parametrize("change", ["profile", "digest", "version", "missing"])
def test_corrupt_profiles_cannot_be_reused(change):
    bad = copy.deepcopy(identity())
    if change == "profile":
        bad["profile"]["judge"]["model"] = "other"
    elif change == "digest":
        bad["sha256"] = "0" * 64
    elif change == "version":
        bad["version"] = True
    else:
        del bad["profile"]
    assert scoring.problems(bad, identity())
    with pytest.raises(ValueError):
        scoring.require_same(bad, identity())


@pytest.mark.parametrize("reference,current,expected", [
    (None, None, False), (None, "a", True), ("a", None, True),
    ("a", "a", False), ("a", "b", True)])
def test_disabled_enabled_and_mixed_comparisons(reference, current, expected):
    reference = identity(reference) if reference else None
    current = identity(current) if current else None
    assert bool(scoring.problems(reference, current)) is expected


def test_points_keep_legacy_shape_and_record_enabled_profiles():
    assert "scoring_identity" not in point()
    got = point(identity())
    assert got["scoring_identity"] == identity()
    assert policies.reference_problems(point(), point()) == []
    assert policies.reference_problems(got, point(identity("judge-b")))
    assert policies.reference_problems(point(), got)


def test_frozen_reference_marks_scoring_mismatch_incomparable(tmp_path):
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(point(identity())))
    got = _baseline_block(str(path), point(identity("judge-b")))
    assert got["comparable"] is False
    assert any("scoring_identity" in p for p in got["problems"])
    missing = _baseline_block(str(tmp_path / "absent.json"), point(identity()))
    assert missing["comparable"] is False and missing["problems"]


def test_eval_resume_checks_identity_without_rewriting_metadata(tmp_path):
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"evaluated_from": {"run_id": "train", "round": 0},
                               **scoring.fields(identity())}))
    original = meta.read_bytes()
    args = SimpleNamespace(from_run="train", from_round=0, scoring_identity=identity("judge-b"))
    assert "scoring_identity" in _resume_mismatch(tmp_path, args, "", "sha")
    assert meta.read_bytes() == original
    args.scoring_identity = identity()
    assert _resume_mismatch(tmp_path, args, "", "sha") is None


@pytest.mark.parametrize("dataset", ["appworld", "gsm8k", "memoryagentbench", "personamem_v2", "tau3_bench"])
def test_unopted_h0_configuration_keeps_scorer_fields_absent(dataset, monkeypatch):
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    # Runtime preparation is tested separately; this case isolates scoring opt-in.
    monkeypatch.setattr(prepare_h0.dataset_runtime_identity, "resolve_h0", lambda *a, **k: None)
    args = SimpleNamespace(dataset=dataset, side="train", study_ids=None,
                           model="agent", endpoint="endpoint", trials=1)
    assert prepare_h0.configuration(args) == {
        "dataset": dataset, "side": "train", "n_study_ids": 0, "study_ids_sha": None,
        "agent_model": "agent", "agent_backend": "mock", "endpoint": "endpoint", "trials": 1,
        "sampling": "all"}


def test_h0_lookup_requires_consistent_payload_metadata_and_curve(tmp_path):
    config = {"dataset": "synthetic", "agent_model": "agent", "agent_backend": "mock",
              "endpoint": "", "sampling": "all", **scoring.fields(identity())}
    run = tmp_path / "h0-synthetic-test"
    run.mkdir()
    payload = {"h0_sha": "sha", "base": {"per_task": {"t": .5}, "invalid": {}, "score": .5},
               **scoring.fields(identity())}
    (run / "h0.json").write_text(json.dumps(payload))
    (run / "run_meta.json").write_text(json.dumps({"h0_configuration": config,
        "sampling_policy": "all", "config": {"agent": {"model": "agent", "backend": "mock", "base_url": ""},
                                              "run": {"sampling": "all"}}, **scoring.fields(identity())}))
    measured = point(identity())
    measured.update(measured_by_platform=True, identity={"agent_model": "agent", "agent_backend": "mock"})
    measured["sampling"]["policy"] = "all"
    (run / "curve.jsonl").write_text(json.dumps(measured) + "\n")
    assert prepare_h0.find_existing(config, tmp_path)[0] == run
    assert prepare_h0.verify(run, None, 1, scoring=identity())["n_tasks"] == 1
    assert prepare_h0.find_existing({"dataset": "synthetic", **scoring.fields(identity("b"))}, tmp_path) is None
    del payload["scoring_identity"]
    (run / "h0.json").write_text(json.dumps(payload))
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match="scoring_identity"):
        prepare_h0.verify(run, None, 1, scoring=identity())


def test_unopted_driver_can_still_share_h0_after_judge_changes(tmp_path, monkeypatch):
    from test_shared_h0 import _run, _first_point
    method = shlex.join([sys.executable, str(prepare_h0.ROOT / "methods/noop/run.py")])
    monkeypatch.setenv("HG_JUDGE_MODEL", "unused-judge-a")
    first = _run(tmp_path, "donor", "--method-entrypoint", method)
    assert first.returncode == 0, first.stdout + first.stderr
    monkeypatch.setenv("HG_JUDGE_MODEL", "unused-judge-b")
    monkeypatch.setenv("HG_JUDGE_BASE_URL", "http://127.0.0.1:9/v1")
    same = _run(tmp_path, "consumer", "--h0-from", "donor", "--method-entrypoint", method)
    assert same.returncode == 0, same.stdout + same.stderr
    assert _first_point(tmp_path, "consumer")["per_task"] == _first_point(tmp_path, "donor")["per_task"]
    for run_id in ("donor", "consumer"):
        assert "scoring_identity" not in _first_point(tmp_path, run_id)
        assert "scoring_identity" not in json.loads((tmp_path / "runs" / run_id / "run_meta.json").read_text())
    assert "scoring_identity" not in json.loads((tmp_path / "runs/donor/h0.json").read_text())
    assert not (tmp_path / "runs/consumer/h0.json").exists()


@pytest.mark.parametrize("variable,value,field", [
    ("HG_AGENT_MODEL", "recipient-only-model", "agent_model"),
    ("HG_AGENT_BASE_URL", "http://127.0.0.1:9/v1", "endpoint"),
])
def test_driver_refuses_h0_from_a_different_recipient_agent(tmp_path, monkeypatch, variable, value, field):
    from test_shared_h0 import _run
    method = shlex.join([sys.executable, str(prepare_h0.ROOT / "methods/noop/run.py")])
    monkeypatch.delenv("HG_AGENT_MODEL", raising=False)
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    donor = _run(tmp_path, "donor", "--improver", "none", "--method-entrypoint", method)
    assert donor.returncode == 0, donor.stdout + donor.stderr
    monkeypatch.setenv(variable, value)
    consumer = _run(tmp_path, "consumer", "--h0-from", "donor", "--improver", "none",
                    "--method-entrypoint", method)
    assert consumer.returncode == 2, consumer.stdout + consumer.stderr
    assert "refusing --h0-from" in consumer.stdout + consumer.stderr
    assert field in consumer.stdout + consumer.stderr


def test_record_readers_validate_profiles_without_new_legacy_errors_or_notes():
    from harnessgrad.store.schema import check_point, check_run_meta
    record = point()
    for check in (check_point, check_run_meta):
        before = check(record)
        assert check({**record, **scoring.fields(identity())}) == before
        bad = identity()
        bad["sha256"] = "0" * 64
        problems, _ = check({**record, "scoring_identity": bad})
        assert any("scoring_identity digest mismatch" in problem for problem in problems)
