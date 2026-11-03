"""H0 reuse binds a recipient to the donor's recorded and measured agent identity."""
import json

import pytest


def _donor(tmp_path):
    agent = {"model": "agent-a", "backend": "openai", "base_url": "http://local-a/v1"}
    metadata = {"config": {"agent": dict(agent)}}
    point = {"measured_by_platform": True,
             "identity": {"agent_model": "agent-a", "agent_backend": "openai"}}
    (tmp_path / "run_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "curve.jsonl").write_text(json.dumps(point) + "\n", encoding="utf-8")
    return agent, metadata, point


def _problems(run, agent):
    from harnessgrad.h0_identity import agent_problems
    point = json.loads((run / "curve.jsonl").read_text(encoding="utf-8"))
    return agent_problems(run, point, model=agent["model"], backend=agent["backend"],
                          endpoint=agent["base_url"])


def test_matching_recorded_and_measured_agent_identity_can_be_reused(tmp_path):
    agent, _, _ = _donor(tmp_path)
    assert _problems(tmp_path, agent) == []


@pytest.mark.parametrize("field,value", [("model", "agent-b"), ("backend", "mock"),
                                         ("base_url", "http://local-b/v1")])
def test_recipient_agent_must_match_donor_measurement(tmp_path, field, value):
    agent, _, _ = _donor(tmp_path)
    agent[field] = value
    assert _problems(tmp_path, agent)


@pytest.mark.parametrize("missing", ["metadata", "agent", "model", "backend", "base_url"])
def test_unknown_legacy_agent_identity_is_not_assumed_comparable(tmp_path, missing):
    agent, metadata, _ = _donor(tmp_path)
    if missing == "metadata":
        (tmp_path / "run_meta.json").unlink()
    else:
        if missing == "agent":
            metadata["config"].pop("agent")
        else:
            metadata["config"]["agent"].pop(missing)
        (tmp_path / "run_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert _problems(tmp_path, agent)


@pytest.mark.parametrize("change", ["model", "backend", "missing", "unverified"])
def test_curve_must_verify_the_recorded_agent_identity(tmp_path, change):
    agent, _, point = _donor(tmp_path)
    if change == "missing":
        point.pop("identity")
    elif change == "unverified":
        point["measured_by_platform"] = False
    else:
        point["identity"]["agent_" + change] = "different"
    (tmp_path / "curve.jsonl").write_text(json.dumps(point), encoding="utf-8")
    assert _problems(tmp_path, agent)


def test_explicit_mock_model_and_endpoint_absences_are_valid_recorded_facts(tmp_path):
    agent, metadata, point = _donor(tmp_path)
    agent.update(model=None, backend="mock", base_url="")
    metadata["config"]["agent"] = dict(agent)
    point["identity"].update(agent_model=None, agent_backend="mock")
    (tmp_path / "run_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "curve.jsonl").write_text(json.dumps(point), encoding="utf-8")
    assert _problems(tmp_path, agent) == []


def test_reused_point_requires_the_original_donor_endpoint_evidence(tmp_path):
    agent, metadata, point = _donor(tmp_path)
    # A historical consumer may have inherited a point from a different endpoint.
    agent["base_url"] = "http://recipient-b/v1"
    metadata["config"]["agent"] = dict(agent)
    point["shared_from"] = {"run_id": "original-donor-a"}
    (tmp_path / "run_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "curve.jsonl").write_text(json.dumps(point), encoding="utf-8")
    assert any("original donor" in problem for problem in _problems(tmp_path, agent))
