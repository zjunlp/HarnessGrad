"""Fabricated AppWorld receipts only: no adapter import, data, Docker or model."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.policy.appworld_reporting import (
    TGC_METRIC, build_report, make_manifest, receipts_from_verdicts,
)
from harnessgrad.records import _save_verdicts

ROOT = Path(__file__).resolve().parents[2]


def rows(scenario="fabricated", split="test_normal", variants=(1, 2, 3)):
    return [{"task_id": f"{scenario}_{v}", "split": split,
             "instruction": "PRIVATE_INSTRUCTION", "answer": "PRIVATE_GOLD",
             "difficulty": "PRIVATE_DIFFICULTY"} for v in variants]


def receipt(tid, success=True, trial=1, measured=True):
    return {"task_id": tid, "trial": trial,
            "evidence": {"task_id": tid, "metric": TGC_METRIC,
                         "measured": measured, "success": success,
                         "passes": ["PRIVATE_TEST"], "failures": ["PRIVATE_GOLD"]}}


def test_task_tgc_and_scenario_sgc_keep_source_split_provenance():
    manifest = make_manifest(rows("normal") + rows("challenge", "test_challenge"))
    receipts = [receipt(row["task_id"], row["task_id"] != "normal_3") for row in manifest]
    report = build_report(manifest, receipts)
    assert report["TGC"] == pytest.approx(5 / 6)
    assert report["SGC"] == 0.5
    assert report["per_task"]["normal_3"]["TGC"] == 0.0
    assert report["per_scenario"]["normal"]["SGC"] == 0.0
    assert report["by_split"]["test_normal"]["label"] == "Test-N"
    assert report["by_split"]["test_normal"]["TGC"] == pytest.approx(2 / 3)
    assert report["by_split"]["test_challenge"]["label"] == "Test-C"
    assert report["by_split"]["test_challenge"]["SGC"] == 1.0
    assert report["coverage"]["measured_task_trials"] == 6
    assert report["coverage"]["measured_scenario_trials"] == 2
    assert "PRIVATE" not in json.dumps(report)
    assert "PRIVATE" not in json.dumps(manifest)
    assert len(report["manifest_sha256"]) == 64
    assert build_report(list(reversed(manifest)), receipts)["manifest_sha256"] == report["manifest_sha256"]


def test_partial_selection_and_unmeasured_are_never_scenario_zeroes():
    manifest = make_manifest(rows("partial", variants=(1,)) + rows("invalid") + rows("missing"))
    receipts = [receipt("partial_1"), receipt("invalid_1"), receipt("invalid_2"),
                receipt("invalid_3", measured=False), receipt("missing_1"), receipt("missing_2")]
    report = build_report(manifest, receipts)
    assert report["TGC"] == 1.0 and report["SGC"] is None
    assert all(s["SGC"] is None for s in report["per_scenario"].values())
    assert report["per_scenario"]["partial"]["trials"][0]["status"] == "incomplete_selection"
    assert report["coverage"]["invalid_task_trials"] == 0
    assert report["coverage"]["recorded_unmeasured_task_trials"] == 1
    assert report["coverage"]["missing_task_trials"] == 1
    assert report["coverage"]["unmeasured_task_trials"] == 2
    assert report["coverage"]["unmeasured_scenario_trials"] == 3
    assert build_report(manifest, [])["TGC"] is None
    assert build_report([], [])["SGC"] is None


def test_sgc_pairs_variants_within_trials_instead_of_combining_task_means():
    manifest = make_manifest(rows())
    receipts = [receipt(row["task_id"], not (row["variant"] == 3 and trial == 2), trial)
                for row in manifest for trial in (1, 2)]
    report = build_report(manifest, receipts, trials=2)
    assert report["TGC"] == pytest.approx(5 / 6)
    assert report["SGC"] == 0.5
    assert report["per_task"]["fabricated_3"]["TGC"] == 0.5
    assert [t["success"] for t in report["per_scenario"]["fabricated"]["trials"]] == [True, False]
    receipts[-1]["evidence"]["measured"] = False
    partial = build_report(manifest, receipts, trials=2)
    assert partial["SGC"] == 1.0
    assert partial["coverage"]["measured_scenario_trials"] == 1
    assert partial["coverage"]["unmeasured_scenario_trials"] == 1


@pytest.mark.parametrize("bad_rows", [
    rows() + rows(), rows(split="unknown"), [{"task_id": "unstructured", "split": "train"}],
    rows("mixed", "train", (1,)) + rows("mixed", "dev", (2,)),
])
def test_invalid_manifest_is_refused(bad_rows):
    with pytest.raises(ValueError):
        make_manifest(bad_rows)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(task_id="unselected_1"),
    lambda r: r.update(trial=2),
    lambda r: r.update(trial=True),
    lambda r: r["evidence"].update(task_id="other_1"),
    lambda r: r["evidence"].update(source_split="test_challenge"),
])
def test_contradictory_receipts_are_refused(mutate):
    r = receipt("fabricated_1")
    mutate(r)
    with pytest.raises(ValueError):
        build_report(make_manifest(rows()), [r])


def test_duplicates_and_fractional_generic_scores_cannot_fabricate_tgc():
    r = receipt("fabricated_1")
    with pytest.raises(ValueError, match="duplicate"):
        build_report(make_manifest(rows()), [r, r])
    with pytest.raises(ValueError, match="duplicate"):
        receipts_from_verdicts([{"task_id": "fabricated_1"}] * 2)
    generic = [{"task_id": "fabricated_1", "score": 0.5, "passed": True}]
    report = build_report(make_manifest(rows()), receipts_from_verdicts(generic))
    assert report["TGC"] is None and report["SGC"] is None
    assert report["coverage"]["invalid_task_trials"] == 0
    assert report["coverage"]["recorded_unmeasured_task_trials"] == 1
    r["evidence"]["success"] = 0.5
    assert build_report(make_manifest(rows()), [r])["TGC"] is None
    r["evidence"].update(success=True, metric="other.metric")
    assert build_report(make_manifest(rows()), [r])["TGC"] is None


def test_compacted_partial_trial_history_cannot_claim_same_trial_sgc():
    manifest = make_manifest(rows())
    verdicts = [{"task_id": row["task_id"], "trials": [
        {"evidence": receipt(row["task_id"])["evidence"]}
        for _ in range(1 if row["variant"] == 3 else 2)]} for row in manifest]
    report = build_report(manifest, receipts_from_verdicts(verdicts, trials=2), trials=2)
    assert report["TGC"] == 1.0 and report["SGC"] is None
    assert report["per_scenario"]["fabricated"]["trials"][0]["status"] == "unaligned_trials"
    assert report["coverage"]["measured_task_trials"] == 5
    assert report["coverage"]["missing_task_trials"] == 1


def test_saved_invalid_verdict_is_preserved_without_inventing_a_zero(tmp_path):
    manifest = make_manifest(rows())
    result = {"per_task": {"fabricated_1": 1.0, "fabricated_2": 1.0},
              "verdicts": {f"fabricated_{v}": {"passed": True,
                           "evidence": receipt(f"fabricated_{v}")["evidence"]} for v in (1, 2)},
              "invalid": {"fabricated_3": {"stage": "check", "detail": "fabricated failure"}}}
    _save_verdicts(tmp_path, 0, result, [row["task_id"] for row in manifest])
    saved = [json.loads((tmp_path / "verdicts/round-0" / (row["task_id"] + ".json")).read_text())
             for row in manifest]
    assert saved[2]["score"] is None and saved[2]["invalid"] == result["invalid"]["fabricated_3"]
    report = build_report(manifest, receipts_from_verdicts(saved))
    assert report["TGC"] == 1.0 and report["SGC"] is None
    assert report["coverage"]["invalid_task_trials"] == 1
    assert report["coverage"]["missing_task_trials"] == 0


def test_evaluate_keeps_appworld_evidence_without_scoring_a_platform_invalid_task(tmp_path, monkeypatch):
    from eval.run import runner

    tasks = rows()
    manifest = make_manifest(tasks)
    invalid = {"stage": "harness runtime identity", "detail": "runtime changed after admission"}

    def actor(_repo, task, **_kwargs):
        tid = task["task_id"]
        success = tid != "fabricated_2"
        result = {"verdict": {"kind": "command", "passed": success, "score": float(success),
                              "scorer_isolation_class": "actor-workdir",
                              "evidence": receipt(tid, success)["evidence"]},
                  "trace": "", "exit_code": 0, "stderr": ""}
        if tid == "fabricated_3":
            result["invalid"] = invalid
        return result

    monkeypatch.setattr(runner, "run_one", actor)
    result = runner.evaluate(tmp_path, tasks, {})
    assert result["per_task"] == {"fabricated_1": 1.0, "fabricated_2": 0.0}
    assert result["invalid"] == {"fabricated_3": invalid}
    evidence = receipt("fabricated_3", success=True)["evidence"]
    assert result["verdicts"]["fabricated_3"]["evidence"] == evidence

    _save_verdicts(tmp_path, 0, result, [row["task_id"] for row in manifest])
    saved = [json.loads((tmp_path / "verdicts/round-0" / (row["task_id"] + ".json")).read_text())
             for row in manifest]
    assert saved[2]["score"] is None and saved[2]["invalid"] == invalid
    assert saved[2]["passed"] is True and saved[2]["evidence"] == evidence
    converted = receipts_from_verdicts(saved)
    assert converted[2]["invalid"] is True and converted[2]["evidence"] == evidence

    report = build_report(manifest, converted)
    assert report["TGC"] == 0.5 and report["SGC"] is None
    assert report["per_task"]["fabricated_3"]["TGC"] is None
    assert report["per_task"]["fabricated_3"]["trials"][0]["status"] == "invalid"
    assert report["coverage"]["measured_task_trials"] == 2
    assert report["coverage"]["invalid_task_trials"] == 1
    assert report["coverage"]["missing_task_trials"] == 0


def test_trusted_adapter_exports_only_selected_public_facts_and_verify_split():
    # Never import data.datasets.appworld: it loads a mixed train/eval pool on import.
    tree = ast.parse((ROOT / "data/datasets/appworld.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "reporting_manifest")
    selected = rows("selected", "dev", (1,))
    namespace = {"make_manifest": make_manifest,
                 "_ROWS": {r["task_id"]: r for r in selected + rows("unused")}}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "fabricated_adapter", "exec"), namespace)
    exported = namespace["reporting_manifest"](["selected_1"])
    assert exported == make_manifest(selected)
    assert "PRIVATE" not in json.dumps(exported) and "unused" not in json.dumps(exported)
    declaration = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "VERIFY" for t in n.targets))
    # `VERIFY` is keyed by the run's ids (`_RUN_IDS`), not by the whole release: the dev
    # pool's selection is what a run can see. The fabricated namespace has to say so, and
    # that is the adapter fact this assertion is really about.
    namespace.update(ASSETS=ROOT / "data/datasets/appworld", VERIFIER_IMAGE="fabricated-image",
                     _RUN_IDS=[row["task_id"] for row in selected])
    specs = eval(compile(ast.Expression(declaration), "fabricated_verify", "eval"), namespace)
    argv = specs["selected_1"]["argv"]
    assert argv[argv.index("--source-split") + 1] == "dev"
    assert "reporting_manifest" not in ast.get_source_segment(
        (ROOT / "data/datasets/appworld.py").read_text(), next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load"))


def load_check():
    spec = importlib.util.spec_from_file_location("fabricated_check", ROOT / "data/datasets/appworld/verify/check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("success", [False, True])
def test_verifier_keeps_binary_tgc_and_split_in_fake_tracker_receipt(tmp_path, monkeypatch, success):
    check = load_check()
    seen = []

    class FakeWorld:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def evaluate(self):
            return SimpleNamespace(success=success, num_tests=2, passes=["fake"], failures=[])

    monkeypatch.setitem(sys.modules, "appworld", SimpleNamespace(AppWorld=FakeWorld))
    is_dir = check.Path.is_dir
    monkeypatch.setattr(check.Path, "is_dir", lambda path: False if str(path) == "/appworld/data" else is_dir(path))
    log, reward, evidence = [tmp_path / name for name in ("log.jsonl", "reward.txt", "evidence.json")]
    log.write_text("")
    monkeypatch.setattr(sys, "argv", ["check", "--task-id", "fabricated_1",
                                     "--source-split", "test_normal", "--log", str(log),
                                     "--reward-file", str(reward), "--evidence-file", str(evidence)])
    assert check.main() == 0
    assert float(reward.read_text()) == float(success)
    result = json.loads(evidence.read_text())
    assert result["success"] is success and result["measured"] is True
    assert result["metric"] == TGC_METRIC and result["source_split"] == "test_normal"
    assert seen[0]["ground_truth_mode"] == "minimal"


def test_missing_log_is_unmeasured_with_metric_provenance(tmp_path, monkeypatch):
    check = load_check()
    evidence, reward = tmp_path / "evidence.json", tmp_path / "reward.txt"
    monkeypatch.setattr(sys, "argv", ["check", "--task-id", "fabricated_1",
                                     "--source-split", "dev", "--log", str(tmp_path / "missing"),
                                     "--reward-file", str(reward), "--evidence-file", str(evidence)])
    with pytest.raises(SystemExit) as exc:
        check.main()
    assert exc.value.code == 3 and not reward.exists()
    result = json.loads(evidence.read_text())
    assert result["measured"] is False and result["source_split"] == "dev"
    assert result["metric"] == TGC_METRIC


def test_saved_evidence_supports_reusable_cli_without_dataset_access(tmp_path):
    manifest = make_manifest(rows())
    trial_verdicts = {row["task_id"]: [
        {"kind": "command", "passed": success, "score": float(success),
         "trial": trial, "evidence": receipt(row["task_id"], success)["evidence"]}
        for trial, success in enumerate((True, row["variant"] != 3), start=1)]
        for row in manifest}
    result = {"per_task": {tid: sum(t["score"] for t in ts) / 2 for tid, ts in trial_verdicts.items()},
              "verdicts": {tid: ts[-1] for tid, ts in trial_verdicts.items()},
              "trial_verdicts": trial_verdicts}
    _save_verdicts(tmp_path, 0, result, list(result["per_task"]))
    verdict_dir = tmp_path / "verdicts/round-0"
    (verdict_dir / "unselected_1.json").write_text("unreadable unselected receipt")
    selected = tmp_path / "manifest.json"
    selected.write_text(json.dumps(manifest))
    out = tmp_path / "report.json"
    command = [sys.executable, str(ROOT / "tools/report_appworld.py"),
               "--manifest", str(selected), "--verdicts", str(verdict_dir),
               "--trials", "2", "--out", str(out)]
    proc = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path, timeout=10)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text())
    assert report["TGC"] == pytest.approx(5 / 6) and report["SGC"] == 0.5
    assert "PRIVATE" not in json.dumps(report) and "unselected" not in json.dumps(report)
    stored = json.loads((verdict_dir / "fabricated_3.json").read_text())
    assert stored["evidence"]["success"] is False
    assert [t["evidence"]["success"] for t in stored["trials"]] == [True, False]
    before = out.read_bytes()
    proc = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path, timeout=10)
    assert proc.returncode != 0 and out.read_bytes() == before
