"""Pure policy/evidence controls. No model, H0, containers or official scorers."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.policy import diagnostic_policy as policy
from lifecycle_fixtures_codex177 import completed_lifecycle, summaries

ROOT = Path(__file__).resolve().parents[2]
ELIGIBLE = [f"task-{i}" for i in range(492)]


def attestation(status="passed"):
    return dict(status=status, receipt_sha256="a" * 64 if status == "passed" else None,
                detail="synthetic unit-test evidence; no runtime authorization")


@pytest.fixture
def context():
    return dict(dataset="demo", side="train",
                metric=dict(name="unit-test metric", scorer_sha256="b" * 64),
                hashes={k: "c" * 64 for k in policy.HASH_NAMES},
                endpoint=policy.ENDPOINTS[0], gates=attestation(),
                limits=dict(resource=dict(filesystem_bytes=1024),
                            budget=dict(model_calls=1, wall_seconds=1)),
                comparability_notes=["Synthetic fixture; not evidence for a production pilot."])


def receipt(context, status="measured", score=0.0):
    selected = policy.staged_task_set(ELIGIBLE, "diagnostic")
    outcomes = [dict(task_id=selected[0], trial=1, status=status,
                     score=score if status == "measured" else None,
                     reason=None if status == "measured" else "scorer unavailable")]
    lifecycle = (completed_lifecycle(context, ELIGIBLE, selected, outcomes)
                 if context["hashes"]["manifest"] is not None else None)
    cleanup, snapshot = summaries(lifecycle) if lifecycle else (attestation(), attestation())
    return policy.build_receipt(
        run_id="unit-diagnostic", stage="diagnostic", eligible=ELIGIBLE, selected=selected,
        context=context, cleanup=cleanup, snapshot=snapshot, outcomes=outcomes, lifecycle=lifecycle)


def reviewed(row):
    return dict(receipt=row, review=dict(approved=True, reviewer="unit-test reviewer",
                                        reviewed_at="2026-10-14T00:00:00Z",
                                        receipt_sha256=policy.receipt_digest(row)))


@pytest.mark.parametrize("stage,count", [("diagnostic", 1), ("pilot_10pct", 30)])
def test_staged_size_and_no_scores(stage, count):
    assert len(policy.staged_task_set(ELIGIBLE, stage)) == count
    assert policy.staged_task_set(ELIGIBLE, stage) == policy.staged_task_set(ELIGIBLE[::-1], stage)


@pytest.mark.parametrize("config", [dict(trials=3), dict(jobs=2), dict(rounds=1),
                                   dict(trials=True), dict(jobs=1.0), dict(rounds=False)])
def test_historical_or_parallel_protocol_is_refused(config):
    with pytest.raises(ValueError):
        policy.staged_task_set(ELIGIBLE, "pilot_10pct", **config)


def test_measured_zero_can_pass_separate_review(context):
    row = receipt(context)
    policy.validate_receipt(row)
    policy.require_review(reviewed(row), context=context, eligible=ELIGIBLE, run_id="fresh-unit-pilot")
    assert row["score"] == 0.0 and row["coverage"]["measured_trials"] == 1


@pytest.mark.parametrize("status", ["invalid", "unmeasured"])
def test_failure_is_null_and_never_authorizes_expansion(context, status):
    row = receipt(context, status)
    policy.validate_receipt(row)
    assert row["score"] is None and row["coverage"][f"{status}_tasks"] == 1
    with pytest.raises(ValueError, match="valid measured"):
        policy.require_review(reviewed(row), context=context, eligible=ELIGIBLE)


def test_missing_attempts_are_explicit_unmeasured(context):
    selected = policy.staged_task_set(ELIGIBLE, "pilot_10pct")
    row = policy.build_receipt(run_id="unit-incomplete", stage="pilot_10pct", eligible=ELIGIBLE,
                               selected=selected, context=context, outcomes=[],
                               cleanup=attestation("unknown"), snapshot=attestation("unknown"))
    policy.validate_receipt(row)
    assert row["coverage"]["unmeasured_trials"] == 30
    assert row["coverage"]["eligible_tasks"] == 492 and row["score"] is None
    assert all(r["score"] is None and r["reason"] for r in row["outcomes"])


def test_mixed_outcomes_average_only_measured_zeros_and_scores():
    selected = ["zero", "one", "invalid", "missing"]
    rows = [dict(task_id=t, trial=1, status=s, score=v, reason=r) for t, s, v, r in (
        ("zero", "measured", 0.0, None), ("one", "measured", 1.0, None),
        ("invalid", "invalid", None, "provider failed"))]
    summary = policy.measurement_summary(selected, rows)
    assert summary["score"] == 0.5
    assert summary["coverage"]["measured_tasks"] == 2
    assert summary["coverage"]["invalid_tasks"] == 1
    assert summary["coverage"]["unmeasured_tasks"] == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, None])
def test_nonfinite_or_non_numeric_measured_scores_are_refused(score):
    with pytest.raises(ValueError):
        policy.measurement_summary(["t"], [dict(task_id="t", trial=1, status="measured",
                                              score=score, reason=None)])


def test_metric_scale_is_not_assumed_to_be_unit_interval():
    summary = policy.measurement_summary(
        ["scaled"], [dict(task_id="scaled", trial=1, status="measured",
                          score=65.6, reason=None)])
    assert summary["score"] == 65.6


@pytest.mark.parametrize("change", [dict(task_id="outside"), dict(trial=2),
                                    dict(status="invalid", score=0.0, reason="timeout"),
                                    dict(status="unmeasured", score=None, reason=None)])
def test_invalid_outcome_contracts_are_refused(change):
    row = dict(task_id="t", trial=1, status="measured", score=0.0, reason=None)
    row.update(change)
    with pytest.raises(ValueError):
        policy.measurement_summary(["t"], [row])


def test_duplicate_trials_are_refused():
    row = dict(task_id="t", trial=1, status="measured", score=0.0, reason=None)
    with pytest.raises(ValueError, match="duplicate"):
        policy.measurement_summary(["t"], [row, row])


@pytest.mark.parametrize("field,value", [("measured_trials", 0), ("expected_trials", 3),
                                        ("eligible_tasks", 0), ("selected_tasks", True)])
def test_coverage_is_recomputed(context, field, value):
    row = receipt(context)
    row["coverage"][field] = value
    with pytest.raises(ValueError):
        policy.validate_receipt(row)


@pytest.mark.parametrize("field", ["cleanup", "snapshot"])
@pytest.mark.parametrize("status", ["unknown", "failed"])
def test_lifecycle_uncertainty_refuses_expansion(context, field, status):
    row = receipt(context)
    row[field] = attestation(status)
    with pytest.raises(ValueError, match="cleanup and snapshot"):
        policy.require_review(reviewed(row), context=context, eligible=ELIGIBLE)


@pytest.mark.parametrize("field", list(policy.HASH_NAMES))
def test_unknown_provenance_refuses_expansion(context, field):
    context["hashes"][field] = None
    row = receipt(context)
    policy.validate_receipt(row)
    with pytest.raises(ValueError, match="unknown provenance"):
        policy.require_review(reviewed(row), context=context, eligible=ELIGIBLE)


@pytest.mark.parametrize("field", ["source", "split", "manifest", "model", "harness"])
def test_changed_hash_context_refuses_old_diagnostic(context, field):
    bundle = reviewed(receipt(context))
    context["hashes"][field] = "d" * 64
    with pytest.raises(ValueError, match="context changed"):
        policy.require_review(bundle, context=context, eligible=ELIGIBLE)


@pytest.mark.parametrize("change", [dict(approved=False), dict(reviewer=""),
                                    dict(reviewed_at=""), dict(receipt_sha256="e" * 64)])
def test_separate_review_is_required(context, change):
    bundle = reviewed(receipt(context))
    bundle["review"].update(change)
    with pytest.raises(ValueError, match="separate approved review"):
        policy.require_review(bundle, context=context, eligible=ELIGIBLE)


def test_stale_or_changed_receipt_and_pool_are_refused(context):
    bundle = reviewed(receipt(context))
    with pytest.raises(ValueError, match="fresh run ID"):
        policy.require_review(bundle, context=context, eligible=ELIGIBLE, run_id="unit-diagnostic")
    with pytest.raises(ValueError, match="eligible pool"):
        policy.require_review(bundle, context=context, eligible=ELIGIBLE[:-1])
    changed_pool = list(ELIGIBLE)
    changed_index = next(i for i, t in enumerate(changed_pool)
                         if t != bundle["receipt"]["selected_task_ids"][0])
    changed_pool[changed_index] = "replacement-task"
    with pytest.raises(ValueError, match="eligible pool"):
        policy.require_review(bundle, context=context, eligible=changed_pool)
    bundle["receipt"]["context"]["comparability_notes"].append("changed after review")
    with pytest.raises(ValueError, match="scope/binding changed"):
        policy.require_review(bundle, context=context, eligible=ELIGIBLE)


def test_schema_matches_complete_and_incomplete_receipts(context):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "eval/diagnostic_receipt.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    for status in ("measured", "invalid", "unmeasured"):
        row = receipt(context, status)
        jsonschema.validate(row, schema)
        policy.validate_receipt(row)
    bad = receipt(context, "invalid")
    bad["outcomes"][0]["score"] = 0.0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_receipt_writes_and_run_reservations_preserve_old_evidence(context, tmp_path):
    root = policy.reserve_run_directory(tmp_path, "unit-fresh")
    path = root / "receipt.json"
    policy.write_receipt(path, receipt(context, "unmeasured"))
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        policy.write_receipt(path, receipt(context))
    with pytest.raises(FileExistsError):
        policy.reserve_run_directory(tmp_path, "unit-fresh")
    assert path.read_bytes() == before


@pytest.mark.parametrize("run_id", [".", "..", "../old", "/tmp/old", "a/b", ""])
def test_run_id_cannot_escape_reservation(tmp_path, run_id):
    with pytest.raises(ValueError):
        policy.reserve_run_directory(tmp_path, run_id)


@pytest.mark.parametrize("failure", [dict(invalid={"t": {"stage": "snapshot", "detail": "failed"}}),
    dict(invalid={"t": {"stage": "harness model call", "detail": "provider failure"}}),
    dict(verdicts={"t": {"unmeasured": "scorer dependencies unavailable"}}),
    dict(not_run=["t"]), dict(harness_failed={"t": {"reason": "timeout"}}),
    dict(cached_tasks=["t"]), dict(tampered={"eval": ["metrics.py"]})])
def test_runner_adapter_never_turns_failure_into_zero(failure):
    result = dict(trials=1, per_task={"t": 0.0}, **failure)
    summary = policy.measurement_summary(["t"], policy.evaluation_outcomes(result, ["t"]))
    assert summary["score"] is None and summary["coverage"]["measured_tasks"] == 0


def test_runner_adapter_retains_a_real_zero_and_missing_result():
    result = dict(trials=1, per_task={"zero": 0.0})
    rows = policy.evaluation_outcomes(result, ["zero", "missing"])
    assert [r["status"] for r in rows] == ["measured", "unmeasured"]
    assert policy.measurement_summary(["zero", "missing"], rows)["score"] == 0.0


def test_missing_score_is_not_an_explicit_null(context):
    row = receipt(context, "unmeasured")
    del row["score"]
    with pytest.raises(ValueError, match="score differs"):
        policy.validate_receipt(row)


@pytest.mark.parametrize("mutation", [lambda c: c.update(endpoint=None),
    lambda c: c["metric"].update(scorer_sha256=None),
    lambda c: c["limits"].update(resource=None),
    lambda c: c["limits"].update(budget=None)])
def test_unknown_metric_endpoint_or_limits_cannot_expand(context, mutation):
    mutation(context)
    with pytest.raises(ValueError, match="unknown provenance"):
        policy.require_review(reviewed(receipt(context)), context=context, eligible=ELIGIBLE)


@pytest.mark.parametrize("argv", [[], ["--trials", "3"], ["--jobs", "2"], ["--rounds", "1"],
                                   ["--sampling", "pilot_10pct"],
                                   ["--policy-evidence", "/nonexistent/policy.json"]])
def test_driver_refuses_staged_preflight_without_loading_or_running(monkeypatch, capsys, argv):
    import driver
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not load data, configuration, or call a model/H0")
    for name in ("load_env", "evaluate", "_probe_tool_calls", "_run_mode_a", "_run_eval_side"):
        monkeypatch.setattr(driver, name, forbidden)
    monkeypatch.setattr(driver.datasets, "load_split", forbidden)
    monkeypatch.setattr("sys.argv", ["driver.py", *argv])
    assert driver.main() == 2
    assert "refusing staged policy" in capsys.readouterr().err


@pytest.fixture
def prepared_driver(monkeypatch, tmp_path, context):
    import driver
    evidence = dict(context=context)
    evidence.update(reviewed(receipt(context)))
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(evidence))
    monkeypatch.setattr(driver, "load_env", lambda _: {})
    monkeypatch.setattr(driver, "describe_model", lambda: dict(model="unit-model", backend="openai", live=True))
    monkeypatch.setattr(driver.platform_config, "current", lambda: SimpleNamespace(
        agent=SimpleNamespace(base_url=context["endpoint"])))
    monkeypatch.setattr(driver, "forbidden_reason", lambda *a: None)
    monkeypatch.setattr(driver, "load_improvers", lambda: {})
    monkeypatch.setattr(driver.datasets, "load_split", lambda _: (
        [dict(task_id=t) for t in ELIGIBLE], {}, None))
    monkeypatch.setattr(driver.datasets, "oracle_source", lambda _: ("unit-test", 0))
    monkeypatch.setattr(driver.datasets, "load_tasks", lambda _, **kwargs: ([], {}, {}))
    monkeypatch.setattr(driver.datasets, "load_envs", lambda _, **kwargs: {})
    monkeypatch.setattr(driver, "evaluate", lambda *a, **k: pytest.fail("H0 is forbidden"))
    monkeypatch.setattr(driver, "_probe_tool_calls", lambda *a, **k: pytest.fail("model is forbidden"))
    return driver, path


@pytest.mark.parametrize("stage", ["diagnostic", "pilot_10pct"])
def test_driver_selects_before_runtime_with_frozen_eligible_pool(monkeypatch, prepared_driver, stage):
    driver, path = prepared_driver
    monkeypatch.setattr("sys.argv", ["driver.py", "--dataset", "demo", "--sampling", stage,
                                    "--policy-evidence", str(path)])
    class SelectionObserved(Exception):
        pass
    loaded = []
    def load_tasks(name, *, task_ids):
        assert task_ids == policy.staged_task_set(ELIGIBLE, stage)
        loaded.append("tasks")
        return [], {}, {}
    def load_envs(name, *, task_ids):
        assert task_ids == policy.staged_task_set(ELIGIBLE, stage)
        loaded.append("envs")
        return {}
    monkeypatch.setattr(driver.datasets, "load_tasks", load_tasks)
    monkeypatch.setattr(driver.datasets, "load_envs", load_envs)
    def stop_at_narrow(setups, verifiers, envs, side_ids):
        assert side_ids == policy.staged_task_set(ELIGIBLE, stage)
        assert loaded == ["tasks", "envs"]
        raise SelectionObserved
    monkeypatch.setattr(driver, "_narrow_to_run", stop_at_narrow)
    with pytest.raises(SelectionObserved):
        driver.main()


def test_driver_preserves_plan_on_interruption_and_refuses_resume(monkeypatch, prepared_driver, tmp_path):
    driver, path = prepared_driver
    runs = tmp_path / "runs"
    monkeypatch.setattr("sys.argv", ["driver.py", "--dataset", "demo", "--policy-evidence", str(path),
                                    "--run-id", "unit-preflight", "--runs-root", str(runs)])
    class BeforeRuntime(Exception):
        pass
    def stop(*a, **k):
        raise BeforeRuntime
    monkeypatch.setattr(driver.console, "attach", stop)
    with pytest.raises(BeforeRuntime):
        driver.main()
    plan_path = runs / "unit-preflight/staged_plan.json"
    original = plan_path.read_bytes()
    plan = json.loads(original)
    policy.validate_receipt(plan)
    assert plan["coverage"]["unmeasured_trials"] == 1 and plan["score"] is None
    assert plan["cleanup"]["status"] == plan["snapshot"]["status"] == "unknown"
    assert driver.main() == 2
    assert plan_path.read_bytes() == original


@pytest.mark.parametrize("failure", [True, False])
def test_eval_side_records_failure_or_pending_lifecycle_before_refusing_curve(
        monkeypatch, tmp_path, context, failure):
    from harnessgrad.loops import eval_side
    selected = policy.staged_task_set(ELIGIBLE, "diagnostic")
    result = dict(trials=1, per_task={selected[0]: 0.0},
                  invalid=({selected[0]: dict(stage="check", detail="scorer failed")} if failure else {}))
    monkeypatch.setattr(eval_side, "evaluate", lambda *a, **k: result)
    monkeypatch.setattr(eval_side, "commit_state", lambda *a, **k: "unit-commit")
    monkeypatch.setattr(eval_side, "_require_untampered", lambda *a: None)
    monkeypatch.setattr(eval_side, "_report_invalid", lambda *a: None)
    monkeypatch.setattr(eval_side, "_report_harness_failed", lambda *a: None)
    monkeypatch.setattr(eval_side, "_write_curve", lambda *a: pytest.fail("invalid result cannot become a curve"))
    monkeypatch.setenv("HARNESSGRAD_SEED", "unit-seed")
    def persist(observed):
        assert observed is result
        row = policy.build_receipt(run_id="unit-eval", stage="diagnostic", eligible=ELIGIBLE,
                                  selected=selected, context=context,
                                  outcomes=policy.evaluation_outcomes(observed, selected),
                                  cleanup=attestation("unknown"), snapshot=attestation("unknown"))
        policy.write_receipt(tmp_path / "diagnostic_receipt.json", row)
        return row["coverage"]["measured_trials"] == len(selected)
    code = eval_side._run_eval_side(
        work=tmp_path, man={}, run_id="unit-eval", run_dir=tmp_path,
        tasks=[dict(task_id=selected[0])], scorable={}, setups={}, verifiers={}, envs={},
        side_ids=selected, split=None, args=SimpleNamespace(
            from_run="unit-train", sandbox=True, trials=1, jobs=1, dataset="demo"),
        eval_from=None, after_evaluation=persist)
    assert code == 2
    row = json.loads((tmp_path / "diagnostic_receipt.json").read_text())
    assert row["score"] is None
    assert row["coverage"]["invalid_trials" if failure else "unmeasured_trials"] == 1
