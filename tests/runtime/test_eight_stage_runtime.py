from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad.runtime import (  # noqa: E402
    ArtifactKind, EventRecorder, ImplementationMode, Owner, ParentRef, Scope, Stage, StageID,
    artifact, validate_events,
)
from harnessgrad.runtime.compat import LegacyCompatibilityWrapper  # noqa: E402
from harnessgrad.runtime.validator import ProtocolError  # noqa: E402

H = "sha256:" + "a" * 64


def boot():
    return {"protocol_version": "1.0", "run_id": "run-1", "event_id": "boot-1",
        "event_kind": "protocol_bootstrap", "producer": {"role": "framework", "id": "framework-1"},
        "bootstrap": {"profile_opt_in": "test.v1", "stage_modes": {},
            "task_plan": {"task_set_sha": H}, "evidence_mode": "trusted_events",
            "budget": {"unit": "generations", "limit": 2}, "delegates": []}}


def ev(eid, stage, payload, *, producer="framework", lifecycle="completed", evidence="measured",
       mode="custom", inputs=None, outputs=None, artifacts=None, **kwargs):
    return {"protocol_version": "1.0", "run_id": "run-1", "event_id": eid,
        "event_kind": "stage_completed" if lifecycle == "completed" else "stage_failed",
        "scope": {"run_id": "run-1", "session_id": "s-1", "iteration_id": "i-1", "branch_id": "b-1"},
        "stage": {"id": stage, "invocation_id": "inv-" + eid, "mode": mode},
        "lifecycle": lifecycle, "evidence": evidence,
        "owner": {"role": producer, "id": producer + "-1"},
        "producer": {"role": producer, "id": producer + "-1"},
        "implementation": {"module": "test.module", "compliance": "declared", "fidelity": "unknown"},
        "artifacts": artifacts or [], "input_artifacts": inputs or [], "output_artifacts": outputs or [],
        "result_kind": "artifact" if outputs else "empty", "attempt_id": "attempt-" + eid,
        "attempt": 1, "idempotency_key": "idem-" + eid,
        "started_at": "2026-10-09T00:00:00Z", "finished_at": "2026-10-09T00:00:01Z",
        "payload": payload, **kwargs}


def art(aid, kind, *, producer="framework", **kwargs):
    return {"artifact_id": aid, "kind": kind, "sha256": H, "visibility": "public_record",
        "producer": {"role": producer, "id": producer + "-1"}, **kwargs}


def test_multi_parent_dgm_archive_and_typed_crossover():
    p1, p2 = art("parent-1", "parent_state", root=True), art("parent-2", "parent_state", root=True)
    child = art("candidate-1", "candidate", parents=["parent-1", "parent-2"],
                lineage=[{"parent_id": "parent-1", "role": "base"},
                         {"parent_id": "parent-2", "role": "crossover"}], content_kind="tree")
    events = [boot(), ev("ctx-1", "select_context", {"payload_kind": "context_selection", "task_set_sha": H,
        "parent_refs": [{"parent_id": "parent-1", "role": "base"}, {"parent_id": "parent-2", "role": "crossover"}],
        "selection_basis": "archive sampling"}, artifacts=[p1, p2]),
        ev("proposal-1", "propose_changes", {"payload_kind": "proposal", "candidate_ids": ["candidate-1"],
            "artifact_kind": "tree", "hypothesis": "combine"}, artifacts=[child]),
        ev("archive-1", "select_context", {"payload_kind": "archive", "member_ids": ["parent-1", "parent-2", "candidate-1"],
            "retention_rule": "keep_all"})]
    assert validate_events(events)


def test_dream_replay_is_not_new_execution():
    events = [boot(), ev("replay-1", "execute", {"payload_kind": "execution", "execution_mode": "replay",
        "replay_kind": "stored_counterfactual", "source_trace_ids": ["trace-1"], "new_execution": False},
        inputs=[art("trace-1", "trace")], outputs=[art("trace-2", "experience")])]
    assert validate_events(events)
    events[1]["payload"]["new_execution"] = True
    try: validate_events(events)
    except ProtocolError as exc: assert "replay_execution_contract" in str(exc)
    else: raise AssertionError("replay claimed new execution")


def test_executed_replay_kinds_are_distinct_from_stored_counterfactual():
    for replay_kind in ("executed_smoke", "executed_benchmark"):
        events = [boot(), ev("replay-" + replay_kind, "execute", {"payload_kind": "execution",
            "execution_mode": "replay", "replay_kind": replay_kind,
            "source_trace_ids": ["trace-1"], "new_execution": True},
            inputs=[art("trace-1", "trace")], outputs=[art("trace-out-" + replay_kind, "experience")])]
        assert validate_events(events)


def test_full_file_sica_artifact_kind_and_root_lineage():
    candidate = art("candidate-1", "candidate", root=True, content_kind="full_file")
    events = [boot(), ev("proposal-1", "propose_changes", {"payload_kind": "proposal", "candidate_ids": ["candidate-1"],
        "artifact_kind": "full_file", "hypothesis": "rewrite whole source"}, artifacts=[candidate])]
    assert validate_events(events)


def test_nested_proposer_training_and_feedback_fanout():
    parent_scope = {"run_id": "run-1", "session_id": "s-1", "iteration_id": "train-1", "branch_id": "proposer",
                   "parent_scope": "outer-1"}
    event_value = ev("train-1", "execute", {"payload_kind": "execution", "execution_mode": "online",
        "new_execution": True, "training_scope": "proposer"})
    event_value["scope"] = parent_scope
    fb = ev("fb-1", "organize_feedback", {"payload_kind": "feedback", "source_artifact_ids": [],
        "consumer_scope": "outer-1", "feedback_consumers": ["proposer-trainer", "outer-policy"]})
    assert validate_events([boot(), event_value, fb])


def test_async_late_events_idempotent_retry_and_reconciliation(tmp_path):
    rec = EventRecorder("run-1", "s-1", stream=tmp_path / "events.jsonl")
    rec.bootstrap(task_set_sha=H)
    scope = Scope("run-1", "s-1", "i-1", "b-1")
    kwargs = dict(scope=scope, stage=Stage(StageID.EVALUATE_CANDIDATES, "inv-1", ImplementationMode.CUSTOM),
        payload={"payload_kind": "study_evaluation", "score_kind": "study", "candidate_ids": [],
                 "scorer_revision": "v1", "evaluation_completeness": "partial"},
        implementation={"module": "evaluator.v1", "compliance": "verified", "fidelity": "measured"},
        event_id="late-1", idempotency_key="trial-attempt-1", attempt_id="attempt-1", attempt=1)
    first = rec.record(**kwargs)
    assert first == rec.record(**kwargs)
    rec.record(**{**kwargs, "event_id": "late-2", "idempotency_key": "late-result-2", "attempt_id": "attempt-2", "attempt": 2})
    rec.reconcile(scope=scope, stage=Stage(StageID.EVALUATE_CANDIDATES, "reconcile-1", "custom"),
                  event_ids=["late-1", "late-2"], reason="late attempt arrived")
    assert [e["event_id"] for e in rec.events][1:3] == ["late-1", "late-2"]
    assert validate_events(list(rec.events))
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 4
    try: rec.record(**{**kwargs, "claims": {"different": True}})
    except ValueError as exc: assert "idempotency_conflict" in str(exc)
    else: raise AssertionError("conflicting idempotent retry accepted")


def test_selection_and_deployment_are_distinct_facts():
    candidates = [art("candidate-a", "candidate", root=True), art("candidate-b", "candidate", root=True)]
    events = [boot(), ev("select-1", "select_deploy", {"payload_kind": "selection_result",
        "selected_candidate_ids": ["candidate-a", "candidate-b"], "basis": ["study-a"], "tentative": True},
        producer="method", inputs=[art("study-a", "study_score", producer="evaluator",
            visibility="method", score_kind="study")], artifacts=candidates), ev("deploy-1", "select_deploy", {"payload_kind": "deployment_fact",
        "deployment_id": "deploy-1", "deployed_candidate_ids": [], "status": "none",
        "from_snapshot": H, "to_snapshot": H}, producer="deployer")]
    assert validate_events(events)


def test_healthy_measured_empty_and_unobserved_legacy_claim():
    empty = ev("empty-1", "propose_changes", {"payload_kind": "proposal", "candidate_ids": [],
        "artifact_kind": "none", "hypothesis": "no change"})
    assert validate_events([boot(), empty])


def test_heldout_taint_rejected_through_indirect_lineage():
    hidden = art("hidden-score", "heldout_score", producer="evaluator", visibility="heldout_private", score_kind="heldout")
    summary = art("summary", "feedback", parents=["hidden-score"])
    feedback = ev("feedback-1", "organize_feedback", {"payload_kind": "feedback",
        "source_artifact_ids": ["summary"], "consumer_scope": "s-1"}, inputs=[summary], artifacts=[summary])
    with_hidden = ev("score-1", "evaluate_candidates", {"payload_kind": "study_evaluation", "score_kind": "study",
        "candidate_ids": [], "scorer_revision": "v1", "evaluation_completeness": "partial"},
        producer="evaluator", outputs=[hidden])
    try: validate_events([boot(), with_hidden, feedback])
    except ProtocolError as exc: assert "heldout_taint_to_adaptation" in str(exc)
    else: raise AssertionError("heldout indirect taint accepted")


def test_compatibility_wrapper_preserves_one_json_stdout_and_marks_unknown(tmp_path):
    method = tmp_path / "legacy.py"
    method.write_text("import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({'steps': 1, 'changed': True}))\n")
    stream = tmp_path / "events.jsonl"
    result = LegacyCompatibilityWrapper([sys.executable, str(method)], event_stream=stream).run({
        "run_id": "run-1", "session_id": "s-1", "task_ids": ["task-1"], "trajectory_out": str(tmp_path / "trajectory.json")})
    assert result.response == {"steps": 1, "changed": True}
    assert result.returncode == 0
    assert len(result.events) == 9  # bootstrap + one legacy proposal + seven unobserved phases
    proposal = next(e for e in result.events if e.get("stage", {}).get("id") == "propose_changes")
    assert proposal["evidence"] == "unobserved"
    assert proposal["implementation"]["compliance"] == "unverified"
    assert proposal["claims"]["legacy_changed"] is True
    assert validate_events(list(result.events))
    assert len(stream.read_text().splitlines()) == 9


def test_validator_rejects_forged_platform_deployment():
    forged = ev("deploy-1", "select_deploy", {"payload_kind": "deployment_fact", "deployment_id": "d-1",
        "deployed_candidate_ids": ["candidate-1"], "status": "deployed", "from_snapshot": H, "to_snapshot": H}, producer="method")
    try: validate_events([boot(), forged])
    except ProtocolError as exc: assert "method_forged_deployment" in str(exc)
    else: raise AssertionError("method claim promoted to deployment fact")


def test_root_parent_marker_is_typed_and_exclusive():
    good = ev("ctx-root", "select_context", {"payload_kind": "context_selection", "task_set_sha": H,
        "parent_refs": [{"role": "root"}], "selection_basis": "initial state"})
    assert validate_events([boot(), good])
    bad = copy.deepcopy(good)
    bad["event_id"] = "ctx-bad"
    bad["idempotency_key"] = "idem-ctx-bad"
    bad["payload"]["parent_refs"] = [{"role": "root"}, {"parent_id": "parent-1", "role": "base"}]
    try: validate_events([boot(), bad])
    except ProtocolError as exc: assert "root_parent_ref_must_be_exclusive" in str(exc)
    else: raise AssertionError("root marker mixed with a parent")


def test_missing_logger_is_unobserved_and_subject_failure_is_measured():
    infra = ev("logger-1", "execute", {"payload_kind": "empty_result", "reason": "logger unavailable"},
        lifecycle="failed", evidence="unobserved", kwargs_unused=True,
        failure={"class": "recorder_error", "subject_or_infrastructure": "infrastructure"})
    # The helper's extra argument is intentionally rejected by schema, so build
    # the exact event shape without relying on a method claim.
    infra.pop("kwargs_unused")
    assert validate_events([boot(), infra])
    measured = copy.deepcopy(infra)
    measured["event_id"] = "logger-2"; measured["idempotency_key"] = "idem-logger-2"
    measured["evidence"] = "measured"
    try: validate_events([boot(), measured])
    except ProtocolError as exc: assert "infrastructure_failure_must_be_unobserved" in str(exc)
    else: raise AssertionError("logger outage became measured")


def test_build_validation_and_score_authority_are_platform_facts():
    validation = ev("validation-1", "build_validate", {"payload_kind": "validation", "candidate_ids": [],
        "valid": True, "checks": ["isolated"]}, producer="method")
    try: validate_events([boot(), validation])
    except ProtocolError as exc: assert "method_forged_validation" in str(exc)
    else: raise AssertionError("method forged build validation")
    study = art("study-1", "study_score", producer="method", visibility="method", score_kind="study")
    score = ev("score-1", "evaluate_candidates", {"payload_kind": "study_evaluation", "score_kind": "study",
        "candidate_ids": [], "scorer_revision": "v1", "evaluation_completeness": "full"},
        producer="method", outputs=[study])
    try: validate_events([boot(), score])
    except ProtocolError as exc: assert "study_score_authority" in str(exc) or "study_visibility_or_authority" in str(exc)
    else: raise AssertionError("method forged study score")


def test_scope_stream_batch_and_deployment_epoch_are_schema_typed():
    e = ev("batch-1", "execute", {"payload_kind": "execution", "execution_mode": "online", "new_execution": True})
    e["scope"].update({"stream_id": "stream-1", "batch_id": "batch-1", "deployment_epoch": "epoch-1",
                        "candidate_ids": ["candidate-a", "candidate-b"]})
    e["parallel_group"] = "parallel-1"
    e["concurrency_group"] = "concurrent-1"
    assert validate_events([boot(), e])


def test_reconciliation_must_reference_existing_events():
    rec = ev("reconcile-1", "evaluate_candidates", {"payload_kind": "reconciliation", "reconciles_event_ids": ["missing"]})
    try: validate_events([boot(), rec])
    except ProtocolError as exc: assert "reconciliation_reference_missing" in str(exc)
    else: raise AssertionError("reconciliation accepted missing event")


def test_method_cannot_hide_platform_fact_artifacts_in_claim_payload():
    forged = art("validation-1", "validation", producer="method")
    proposal = ev("proposal-fact-1", "propose_changes", {"payload_kind": "proposal",
        "candidate_ids": [], "artifact_kind": "none", "hypothesis": "claim"},
        producer="method", outputs=[forged])
    try: validate_events([boot(), proposal])
    except ProtocolError as exc: assert "artifact_producer_authority" in str(exc)
    else: raise AssertionError("method smuggled a validation fact")


def test_delegate_score_authority_requires_declared_capability():
    score = art("study-delegate-1", "study_score", producer="delegate",
        visibility="method", score_kind="study")
    evaluation = ev("delegate-score-1", "evaluate_candidates", {"payload_kind": "study_evaluation",
        "score_kind": "study", "candidate_ids": [], "scorer_revision": "delegate-v1",
        "evaluation_completeness": "partial"}, producer="delegate", outputs=[score])
    evaluation["producer"]["id"] = "delegate-1"
    try: validate_events([boot(), evaluation])
    except ProtocolError as exc: assert "delegate_authority" in str(exc)
    else: raise AssertionError("undeclared delegate capability accepted")
    declared = copy.deepcopy(evaluation)
    declared["event_id"] = "delegate-score-2"; declared["idempotency_key"] = "idem-delegate-score-2"
    declared["implementation"] = {"module": "delegate.v1", "compliance": "declared", "fidelity": "unknown"}
    b = boot()
    b["bootstrap"]["delegates"] = [{"delegate_id": "delegate-1", "capabilities": ["evaluate"], "timeout_ms": 1000}]
    assert validate_events([b, declared])


def test_top_level_and_scope_run_identity_is_mandatory():
    e = ev("identity-1", "execute", {"payload_kind": "execution", "execution_mode": "online", "new_execution": True})
    e["scope"]["run_id"] = "another-run"
    try: validate_events([boot(), e])
    except ProtocolError as exc: assert "scope_run_id_mismatch" in str(exc)
    else: raise AssertionError("scope run identity mismatch accepted")


def test_artifact_cycle_and_cross_scope_reference_rejected():
    a = art("artifact-a", "feedback", parents=["artifact-b"])
    b = art("artifact-b", "feedback", parents=["artifact-a"])
    e = ev("cycle-1", "organize_feedback", {"payload_kind": "feedback", "source_artifact_ids": [], "consumer_scope": "s-1"}, artifacts=[a, b])
    try: validate_events([boot(), e])
    except ProtocolError as exc: assert "artifact_cycle" in str(exc)
    else: raise AssertionError("artifact cycle accepted")
    scoped = art("scoped-feedback", "feedback", scope={"run_id": "run-1", "session_id": "other-session", "iteration_id": "i-1", "branch_id": "b-1"})
    cross = ev("scope-1", "organize_feedback", {"payload_kind": "feedback", "source_artifact_ids": ["scoped-feedback"], "consumer_scope": "s-1"}, inputs=[scoped], artifacts=[scoped])
    try: validate_events([boot(), cross])
    except ProtocolError as exc: assert "artifact_scope_session_id_mismatch" in str(exc)
    else: raise AssertionError("cross-scope artifact accepted")


def test_replay_source_must_resolve_and_be_trace_like():
    missing = ev("replay-missing", "execute", {"payload_kind": "execution", "execution_mode": "replay",
        "replay_kind": "stored_counterfactual", "source_trace_ids": ["missing-trace"], "new_execution": False})
    try: validate_events([boot(), missing])
    except ProtocolError as exc: assert "replay_source_unresolved" in str(exc)
    else: raise AssertionError("missing replay source accepted")
    wrong = ev("replay-wrong", "execute", {"payload_kind": "execution", "execution_mode": "replay",
        "replay_kind": "stored_counterfactual", "source_trace_ids": ["study-1"], "new_execution": False},
        inputs=[art("study-1", "study_score", producer="evaluator", visibility="method", score_kind="study")])
    try: validate_events([boot(), wrong])
    except ProtocolError as exc: assert "replay_source_wrong_kind" in str(exc)
    else: raise AssertionError("score used as replay source")


def test_validator_structural_gate_survives_missing_jsonschema(monkeypatch):
    import harnessgrad.runtime.validator as runtime_validator
    malformed = {"protocol_version": "1.0", "run_id": "run-1", "event_id": "bad-1", "event_kind": "stage_completed"}
    monkeypatch.setattr(runtime_validator, "Draft202012Validator", None)
    valid = [boot(), ev("no-schema-1", "execute", {"payload_kind": "execution", "execution_mode": "online", "new_execution": True})]
    assert runtime_validator.validate_events(valid)
    try: runtime_validator.validate_events([malformed])
    except ProtocolError as exc: assert "missing_scope" in str(exc)
    else: raise AssertionError("structural gate failed open without jsonschema")


def test_stdlib_structural_gate_rejects_schema_type_bypasses(monkeypatch):
    import harnessgrad.runtime.validator as runtime_validator
    monkeypatch.setattr(runtime_validator, "Draft202012Validator", None)

    bad_budget = boot()
    bad_budget["bootstrap"]["budget"]["limit"] = True
    try: runtime_validator.validate_events([bad_budget])
    except ProtocolError as exc: assert "budget_limit" in str(exc)
    else: raise AssertionError("boolean budget limit accepted")

    for value in (float("nan"), float("inf"), float("-inf")):
        bad = boot(); bad["bootstrap"]["budget"]["limit"] = value
        try: runtime_validator.validate_events([bad])
        except ProtocolError as exc: assert "budget_limit" in str(exc)
        else: raise AssertionError("non-finite budget limit accepted")

    bad_delegate = boot()
    bad_delegate["bootstrap"]["delegates"] = [{"delegate_id": "delegate-1", "capabilities": [], "timeout_ms": 10},
                                                {"delegate_id": "delegate-1", "capabilities": [], "timeout_ms": 10}]
    try: runtime_validator.validate_events([bad_delegate])
    except ProtocolError as exc: assert "delegate" in str(exc)
    else: raise AssertionError("duplicate delegate accepted")

    malformed = ev("stdlib-types-1", "execute", {"payload_kind": "execution", "execution_mode": "online",
        "new_execution": "false"})
    try: runtime_validator.validate_events([boot(), malformed])
    except ProtocolError as exc: assert "new_execution" in str(exc)
    else: raise AssertionError("string boolean accepted")

    for key in ("candidate_ids", "source_trace_ids", "reconciles_event_ids"):
        payload = {"payload_kind": "execution", "execution_mode": "online", "new_execution": True}
        if key == "candidate_ids":
            payload = {"payload_kind": "proposal", "candidate_ids": "candidate-1", "artifact_kind": "none", "hypothesis": "bad"}
            bad = ev("stdlib-array-" + key, "propose_changes", payload)
        else:
            payload[key] = "not-an-array"
            payload["execution_mode"] = "online"; payload["new_execution"] = True
            bad = ev("stdlib-array-" + key, "execute", payload)
        try: runtime_validator.validate_events([boot(), bad])
        except ProtocolError as exc: assert key in str(exc) or "payload" in str(exc)
        else: raise AssertionError(f"scalar {key} accepted")


def test_compat_wrapper_invalid_json_nonzero_timeout_and_trajectory_errors(tmp_path):
    invalid = tmp_path / "invalid.py"
    invalid.write_text("import sys; sys.stderr.write('warn\\n'); print('not-json'); raise SystemExit(3)\n")
    result = LegacyCompatibilityWrapper([sys.executable, str(invalid)]).run({"run_id": "run-1", "session_id": "s-1"})
    assert result.returncode == 3 and result.response == "not-json\n" and "warn" in result.stderr
    proposal = next(e for e in result.events if e.get("stage", {}).get("id") == "propose_changes")
    assert proposal["lifecycle"] == "failed" and proposal["failure"]["class"] == "subject_error"
    timeout = tmp_path / "timeout.py"
    timeout.write_text("import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(1)\n")
    timed = LegacyCompatibilityWrapper([sys.executable, str(timeout)], timeout=0.05).run({"run_id": "run-2", "session_id": "s-2"})
    assert timed.returncode == 124 and timed.response == "partial"
    timed_proposal = next(e for e in timed.events if e.get("stage", {}).get("id") == "propose_changes")
    assert timed_proposal["failure"]["class"] == "subject_timeout"
    trajectory = tmp_path / "bad-trajectory.json"
    trajectory.write_text("{not-json")
    bad_traj = LegacyCompatibilityWrapper([sys.executable, "-c", "print('{}')"]).run({
        "run_id": "run-3", "session_id": "s-3", "trajectory_out": str(trajectory)})
    bad_proposal = next(e for e in bad_traj.events if e.get("stage", {}).get("id") == "propose_changes")
    assert "trajectory_json" in bad_proposal["claims"]["trajectory_error"]
    scalar = LegacyCompatibilityWrapper([sys.executable, "-c", "print('7')"]).run({"run_id": "run-4", "session_id": "s-4"})
    scalar_proposal = next(e for e in scalar.events if e.get("stage", {}).get("id") == "propose_changes")
    assert scalar.response == 7 and scalar_proposal["failure"]["class"] == "subject_error"


def test_heldout_evaluation_has_separate_typed_namespace():
    heldout = art("heldout-1", "heldout_score", producer="evaluator",
        visibility="heldout_private", score_kind="heldout", split_visibility="heldout_private")
    measured = ev("heldout-1", "evaluate_candidates", {"payload_kind": "heldout_evaluation",
        "score_kind": "heldout", "candidate_ids": [], "scorer_revision": "official-v1",
        "evaluation_completeness": "heldout"}, producer="evaluator", outputs=[heldout])
    assert validate_events([boot(), measured])
    forged = copy.deepcopy(measured)
    forged["event_id"] = "heldout-2"; forged["idempotency_key"] = "idem-heldout-2"
    forged["producer"] = {"role": "method", "id": "method-1"}
    try: validate_events([boot(), forged])
    except ProtocolError as exc: assert "heldout_score_authority" in str(exc)
    else: raise AssertionError("method forged heldout evaluation")
