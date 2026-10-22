"""Offline contract tests for the local trusted runner facade."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harnessgrad.trusted_runner import IdempotencyConflict, TrustedRunnerError, TrustedRunnerFacade


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value):
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else canon(value)).hexdigest()


def tree_sha(root):
    from harnessgrad.trusted_runner import _tree_sha
    return _tree_sha(root)


def harness(root: Path, text: str = "print('ok')\n"):
    root.mkdir()
    (root / "harness.json").write_text(json.dumps({"name": "h", "version": "1", "path": ".", "entrypoint": "agent.py", "env_kinds": ["files"], "role": "harness"}))
    (root / "agent.py").write_text(text)
    return root


def parent(snapshot, artifact_id="p", role="base"):
    return {"artifact_id": artifact_id, "role": role, "snapshot_sha": snapshot}


def proposal(cid, content, base, lineage=None, **extra):
    edits = [{"path": "agent.py", "content": content}]
    artifact = {"artifact_id": f"patch-{cid}", "kind": "patch", "content_sha": sha({"edits": edits}),
                "producer": {"role": "method", "id": "fake"}, "visibility": "framework"}
    return {"candidate_id": cid, "base_snapshot_sha": base,
            "lineage": lineage or [parent(base)], "artifact": artifact,
            "proposal_kind": "patch", "edits": edits, **extra}


class FakeRunner:
    def __init__(self, scores=None):
        self.scores = scores or {}

    def run(self, workspace, task, trial):
        value = self.scores.get((workspace.name, task["task_id"], trial), 0.5)
        return {"status": "measured", "score": value,
                "cost": {"accounting_revision": "fake-v1", "model_calls": 0, "known": True},
                "trace": f"{workspace.name}:{task['task_id']}:{trial}"}


def make_facade(tmp_path, *, runner=None, root_text="print('base')\n"):
    base = harness(tmp_path / "base", root_text)
    bsha = tree_sha(base)
    f = TrustedRunnerFacade(tmp_path / "run", parent_snapshots={bsha: base}, runner=runner,
                            current_snapshot_sha=bsha)
    return f, base, bsha


def build_request(f, bsha, proposals, parents):
    return {"operation": "build_validate", "run_id": "r", "iteration_id": "i",
            "parents": parents, "parent_set_sha": sha(sorted(parents, key=canon)),
            "proposals": proposals, "idempotency_key": "build-1"}


def study_cap(f):
    cap = {"task_set_id": "study-1", "task_set_sha": sha({"tasks": ["t1", "t2"]}),
           "visibility": "study", "adaptation_visible": True, "capability_id": "cap-study",
           "scorer_revision": "fake-v1", "task_ids": ["t1", "t2"]}
    f.issue_capability("cap-study", cap)
    return cap


def test_parent_fanout_and_candidate_isolation(tmp_path):
    f, base, bsha = make_facade(tmp_path)
    p = parent(bsha)
    req = build_request(f, bsha, [proposal("a", "print('a')\n", bsha), proposal("b", "print('b')\n", bsha)], [p])
    out = f.build_validate(req)
    assert out["valid_candidate_ids"] == ["a", "b"]
    assert Path(f.candidates["a"]["workspace"]).read_text if False else True
    assert Path(f.candidates["a"]["workspace"] + "/agent.py").read_text() == "print('a')\n"
    assert Path(f.candidates["b"]["workspace"] + "/agent.py").read_text() == "print('b')\n"
    assert (base / "agent.py").read_text() == "print('base')\n"


def test_different_bases_crossover_root_and_unused_parent(tmp_path):
    f, base, bsha = make_facade(tmp_path)
    other = harness(tmp_path / "other", "print('other')\n")
    osha = tree_sha(other)
    f.parent_snapshots[osha] = other
    p1, p2 = parent(bsha, "p1"), parent(osha, "p2", "donor")
    cross = proposal("cross", "print('cross')\n", bsha, [p1, p2])
    req = build_request(f, bsha, [cross], [p1, p2])
    assert f.build_validate(req)["valid_candidate_ids"] == ["cross"]
    root_ref = {"role": "root", "root": True, "snapshot_sha": bsha}
    root_prop = proposal("root", "print('root')\n", bsha, [root_ref], root=True)
    root_prop.pop("base_snapshot_sha")
    req2 = build_request(f, bsha, [root_prop], [root_ref])
    req2["idempotency_key"] = "root-1"
    assert f.build_validate(req2)["valid_candidate_ids"] == ["root"]
    with pytest.raises(TrustedRunnerError, match="declared_parent_unused"):
        bad = build_request(f, bsha, [proposal("unused", "print('x')\n", bsha, [p1])], [p1, p2])
        bad["idempotency_key"] = "unused-1"
        f.build_validate(bad)


def test_invalid_base_and_idempotency(tmp_path):
    f, _, bsha = make_facade(tmp_path)
    p = parent(bsha)
    bad = proposal("bad", "print('x')\n", "sha256:" + "0" * 64)
    req = build_request(f, bsha, [bad], [p])
    with pytest.raises(TrustedRunnerError, match="base_snapshot_not_in_set"):
        f.build_validate(req)
    good = build_request(f, bsha, [proposal("ok", "print('x')\n", bsha)], [p])
    first = f.build_validate(good)
    assert f.build_validate(good) == first
    good["proposals"][0]["edits"][0]["content"] = "print('changed')\n"
    with pytest.raises(IdempotencyConflict):
        f.build_validate(good)


def test_capability_rejection_raw_scores_and_final_taint(tmp_path):
    f, _, bsha = make_facade(tmp_path, runner=FakeRunner())
    p = parent(bsha)
    f.build_validate(build_request(f, bsha, [proposal("c", "print('c')\n", bsha)], [p]))
    cap = study_cap(f)
    with pytest.raises(TrustedRunnerError, match="unknown_task_capability"):
        f.evaluate_candidates({"candidate_ids": ["c"], "task_set": {**cap, "capability_id": "made-up"},
                               "k": 1, "completeness": "full", "evaluation_scope": "study", "idempotency_key": "e-bad"})
    result = f.evaluate_candidates({"candidate_ids": ["c"], "task_set": cap, "k": 2,
                                    "completeness": "full", "evaluation_scope": "study", "idempotency_key": "e-1"})
    assert result["results"][0]["aggregate_score"] == 0.5
    exam = {"task_set_id": "exam", "task_set_sha": sha({"tasks": ["x"]}), "visibility": "final_exam",
            "adaptation_visible": False, "capability_id": "cap-exam", "scorer_revision": "exam-v1", "task_ids": ["x"]}
    f.issue_capability("cap-exam", exam)
    with pytest.raises(TrustedRunnerError, match="finalization_seal_required"):
        f.evaluate_candidates({"candidate_ids": ["c"], "evaluation_scope": "final_report_only", "final_report_capability_id": "cap-exam", "selection_id": "missing", "finalization_seal": "sha256:" + "0" * 64, "k": 1, "completeness": "full", "idempotency_key": "exam-1"})


def test_final_report_is_private_and_cannot_be_selection_basis(tmp_path):
    f, _, bsha = make_facade(tmp_path, runner=FakeRunner())
    p = parent(bsha)
    f.build_validate(build_request(f, bsha, [proposal("c", "print('c')\n", bsha)], [p]))
    cap = study_cap(f)
    study = f.evaluate_candidates({"candidate_ids": ["c"], "task_set": cap, "k": 1,
                                   "completeness": "full", "evaluation_scope": "study", "idempotency_key": "study"})
    sel = f.select_deploy({"action": "select", "incumbent_id": "missing", "candidate_ids": ["c"],
                           "evaluation_ids": [study["results"][0]["evaluation_id"]], "idempotency_key": "select"})
    f.issue_capability("cap-exam", {"task_set_id": "exam", "task_set_sha": sha({"tasks": ["x"]}),
                                   "visibility": "final_exam", "adaptation_visible": False,
                                   "capability_id": "cap-exam", "scorer_revision": "exam-v1", "task_ids": ["x"]})
    with pytest.raises(TrustedRunnerError, match="finalization_seal_required"):
        f.evaluate_candidates({"candidate_ids": ["c"], "evaluation_scope": "final_report_only",
                               "final_report_capability_id": "cap-exam", "selection_id": sel["selection"]["selection_id"],
                               "k": 1, "completeness": "full", "idempotency_key": "exam-before-deploy"})
    # The seal follows a deploy/none fact; selection itself cannot open the exam.
    f.select_deploy({"action": "deploy", "selection_id": sel["selection"]["selection_id"],
                     "expected_from_snapshot_sha": bsha, "idempotency_key": "deploy"})
    seal = f.finalize(sel["selection"]["selection_id"])
    exam = {"task_set_id": "exam", "task_set_sha": sha({"tasks": ["x"]}), "visibility": "final_exam",
            "adaptation_visible": False, "capability_id": "cap-exam", "scorer_revision": "exam-v1", "task_ids": ["x"]}
    f.issue_capability("cap-exam", exam)
    report = f.evaluate_candidates({"candidate_ids": ["c"], "evaluation_scope": "final_report_only",
                                    "final_report_capability_id": "cap-exam", "selection_id": sel["selection"]["selection_id"],
                                    "finalization_seal": seal, "k": 1, "completeness": "full", "idempotency_key": "exam"})
    assert report["final_report_private"] is True
    assert report["final_report_results"][0]["report_artifact"]["visibility"] == "final_report_private"


@pytest.mark.parametrize("status", ["none", "failed"])
def test_selected_but_not_deployed_preserves_restart_state(tmp_path, status):
    runner = FakeRunner({("inc", "t1", 0): 0.1, ("inc", "t2", 0): 0.1,
                         ("challenger", "t1", 0): 0.9, ("challenger", "t2", 0): 0.9})
    f, base, bsha = make_facade(tmp_path, runner=runner)
    p = parent(bsha)
    f.build_validate(build_request(f, bsha, [proposal("inc", "print('inc')\n", bsha),
                                            proposal("challenger", "print('better')\n", bsha)], [p]))
    cap = study_cap(f)
    evaluations = f.evaluate_candidates({"candidate_ids": ["inc", "challenger"], "task_set": cap,
                                         "k": 1, "completeness": "full", "evaluation_scope": "study",
                                         "idempotency_key": "both"})
    selected = f.select_deploy({"action": "select", "incumbent_id": "inc",
                                "candidate_ids": ["challenger"],
                                "evaluation_ids": [e["evaluation_id"] for e in evaluations["results"]],
                                "idempotency_key": "select-better"})["selection"]
    assert selected["selected_candidate_ids"] == ["challenger"]
    request = {"action": "deploy", "selection_id": selected["selection_id"],
               "deployment_status": status, "expected_from_snapshot_sha": bsha,
               "idempotency_key": "inactive"}
    result = f.select_deploy(request)
    assert result["deployment"]["deployed_candidate_ids"] == []
    assert result["deployment"]["to_snapshot_sha"] == bsha
    assert f.current_snapshot_sha == bsha
    reloaded = TrustedRunnerFacade(tmp_path / "run", parent_snapshots={bsha: base}, runner=runner)
    assert reloaded.current_snapshot_sha == bsha
    assert reloaded.select_deploy(request) == result


def test_selection_deployment_stale_cas_and_rollback(tmp_path):
    f, _, bsha = make_facade(tmp_path, runner=FakeRunner())
    p = parent(bsha)
    f.build_validate(build_request(f, bsha, [proposal("c", "print('c')\n", bsha)], [p]))
    cap = study_cap(f)
    inc = {"candidate_ids": ["c"], "task_set": cap, "k": 1, "completeness": "full", "evaluation_scope": "study", "idempotency_key": "inc"}
    # No scoreful incumbent: selection retains it and does not manufacture a deployment.
    evaluation = f.evaluate_candidates({**inc, "idempotency_key": "cand"})
    selection = f.select_deploy({"action": "select", "incumbent_id": "inc", "candidate_ids": ["c"], "evaluation_ids": [evaluation["results"][0]["evaluation_id"]], "idempotency_key": "sel"})
    sel_id = selection["selection"]["selection_id"]
    deployed = f.select_deploy({"action": "deploy", "selection_id": sel_id, "expected_from_snapshot_sha": bsha, "idempotency_key": "dep"})
    assert deployed["deployment"]["status"] == "none"
    assert deployed["deployment"]["deployed_candidate_ids"] == []
    assert deployed["deployment"]["to_snapshot_sha"] == bsha
    with pytest.raises(TrustedRunnerError, match="stale_expected_snapshot"):
        f.select_deploy({"action": "deploy", "selection_id": sel_id, "expected_from_snapshot_sha": "sha256:" + "f" * 64, "idempotency_key": "dep-stale"})
    with pytest.raises(TrustedRunnerError, match="rollback_requires_validation"):
        f.select_deploy({"action": "rollback", "selection_id": sel_id, "deployment_id": deployed["deployment"]["deployment_id"], "rollback_of": deployed["deployment"]["deployment_id"], "evidence": {"kind": "final_report", "visibility": "final_report_private"}, "idempotency_key": "rb-bad"})
