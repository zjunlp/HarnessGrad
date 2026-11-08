"""Pure record projections preserve allowlists, redaction and writer semantics."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path

import pytest

from harnessgrad import config, verification_context, records
from harnessgrad.config.areas import RunConfig
from harnessgrad.orchestration import run_metadata as meta


def inputs():
    run = RunConfig("authored", "fixture", "demo", "train", "all", 2, 3, 4, True, "unified",
                    seed="authored-seed", runs_root="./raw-runs", work_root="../raw-work")
    cfg = config.dump(config.load({"HG_AGENT_API_KEY": "authored-private-key"}), run=run)
    ctx = verification_context.capture([{"task_id": "t1"}], {"t1": "authored"})
    ids = meta.identity_fields(meta.IdentityRecords(None, None, ctx, None))
    return run, cfg, ids


def planned(run, cfg, ids, **changes):
    values = dict(identities=ids, diagnostic=None, enforcement=None,
        workspace=meta.WorkspaceRecord("/external/work/a/workspace", "/external/work", "/platform", {"requested": True}),
        config_record=cfg, budget_record={"calls": None, "known_zero": 0}, env_record={"kind": "files"},
        split=meta.SplitRecord(None, None, None, None),
        provenance=meta.ProvenanceRecord(None, None, None, "platform-original", "authored", None),
        attribution=meta.AttributionRecord("", "mock", "test-version", {}, None, ""), web_record={})
    values.update(changes)
    return meta.planned_run_fields(run, **values)


def test_projection_has_no_filesystem_or_environment_effects(monkeypatch):
    run, cfg, ids = inputs()
    before = dict(os.environ)
    def forbidden(*args, **kwargs): pytest.fail("pure projection performed filesystem IO")
    monkeypatch.setattr(Path, "open", forbidden)
    value = planned(run, cfg, ids)
    prepared = meta.prepared_run_fields(run, cfg, "test-version", None)
    assert dict(os.environ) == before
    assert value["config"] is prepared["config"] is cfg
    assert value["verification_context"] == ids["verification_context"]


def test_prepared_and_planned_have_different_explicit_allowlists_and_root_spelling():
    run, cfg, ids = inputs()
    prepared = meta.prepared_run_fields(run, cfg, "test-version", None)
    value = planned(run, cfg, ids)
    assert prepared["side"] == "train" and prepared["rounds"] == 2
    assert prepared["runs_root"] == "./raw-runs" and prepared["work_root"] == "../raw-work"
    assert prepared["sandbox"] == {"requested": True, "active": False}
    assert prepared["prepared_by"] == "--prepare-only" and prepared["launched"] is False
    assert not {"mode", "workspace", "platform_content_sha", "verification_context", "budget"} & prepared.keys()
    assert not {"side", "rounds", "runs_root", "prepared_by", "launched"} & value.keys()
    assert value["work_root"] == "/external/work" and cfg["run"]["work_root"] == "../raw-work"
    assert set(asdict(run)) - value.keys()  # RunConfig is not blindly splatted into the record.


@pytest.mark.parametrize("tool_calls", [None, False, True])
def test_none_zero_and_false_and_existing_detail_truncation_are_not_normalized(tool_calls):
    run, cfg, ids = inputs()
    value = planned(run, cfg, ids, attribution=meta.AttributionRecord(None, "mock", "v", {}, tool_calls, "a" * 400))
    assert value["agent_model"] is value["improver"] is None
    assert value["improver_tool_calls"] is tool_calls
    assert value["improver_tool_calls_detail"] == "a" * 300
    assert value["budget"] == {"calls": None, "known_zero": 0}
    assert value["oracle_coverage"] is None and "oracle_coverage" in value


def test_canonical_configuration_redaction_is_preserved():
    run, cfg, ids = inputs()
    for value in (planned(run, cfg, ids), meta.prepared_run_fields(run, cfg, "v", None)):
        assert "authored-private-key" not in json.dumps(value)
        assert value["config"]["agent"]["api_key"] == {"present": True}


def test_empty_split_is_declared_but_its_existing_hash_value_stays_none():
    run, cfg, ids = inputs()
    value = planned(run, cfg, ids, split=meta.SplitRecord({}, "declared", None, None))
    assert value["split_declared"] is True and value["split"] == {} and value["split_sha"] is None
    assert "split" not in meta.prepared_run_fields(run, cfg, "v", None)
    assert meta.prepared_run_fields(run, cfg, "v", meta.SplitRecord({}, "declared", None, None))["split"] == {}


def test_writer_still_owns_none_merge_and_platform_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "_platform_record", lambda: {"content_sha": "current"})
    run, cfg, ids = inputs()
    records._write_run_meta(tmp_path, platform_content_sha="first", oracle_coverage="retained", improver_tool_calls=True)
    value = planned(run, cfg, ids)
    records._write_run_meta(tmp_path, **value)
    saved = json.loads((tmp_path / "run_meta.json").read_text())
    assert saved["platform_content_sha"] == saved["platform_content_sha_started"] == "first"
    assert saved["platform"]["content_sha"] == "current"
    assert saved["oracle_coverage"] == "retained" and saved["improver_tool_calls"] is True
    assert value["oracle_coverage"] is None and value["improver_tool_calls"] is None


def test_identity_validation_keeps_canonical_order(monkeypatch):
    events = []
    for name, module in (("scoring", meta.scoring_identity), ("dataset", meta.dataset_runtime_identity),
                         ("verification", meta.verification_context), ("harness", meta.candidate_runtime)):
        monkeypatch.setattr(module, "fields", lambda value, name=name: events.append(name) or {name: value})
    value = meta.identity_fields(meta.IdentityRecords(None, None, None, None))
    assert events == ["scoring", "dataset", "verification", "harness"]
    assert list(value) == events
