"""Offline semantic checks for the DGM and HyperAgents fidelity components."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dgm = load(ROOT / "methods/dgm/archive.py", "dgm_archive_alignment")
dgm_stage = load(ROOT / "methods/dgm/staged_eval.py", "dgm_stage_alignment")
hyper = load(ROOT / "methods/hyperagents/archive.py", "hyper_archive_alignment")
hyper_stage = load(ROOT / "methods/hyperagents/staged_eval.py", "hyper_stage_alignment")


def test_dgm_keep_all_retains_rejected_and_non_best_nodes_but_unusable_nodes_are_not_parents():
    archive = dgm.DGMArchive()
    archive = archive.add(dgm.ArchiveNode("root", 0.8))
    archive = archive.add(dgm.ArchiveNode("low", 0.1, ("root",), rejected=True))
    archive = archive.add(dgm.ArchiveNode("best", 0.9, ("root",)))
    assert [n.node_id for n in archive.retained()] == ["root", "low", "best"]
    assert [n.node_id for n in archive.parent_candidates()] == ["root", "best"]
    assert archive.weights()["root"] < archive.weights()["best"]


def test_parent_refs_preserve_multi_parent_lineage_without_chain_conversion():
    artifact = dgm.ArtifactState.from_files(
        {"agent.py": "v"}, parent_refs=("left", "right"), patch_kind="crossover")
    node = dgm.ArchiveNode("merge", 0.7, ("left", "right"), artifact)
    counts = dgm.child_counts((node,))
    assert node.parent_refs == ("left", "right")
    assert counts == {"left": 1, "right": 1}
    assert artifact.as_dict()["patch_kind"] == "crossover"


def test_dgm_keep_better_is_against_initial_and_seed_is_reproducible():
    archive = dgm.DGMArchive(retention="keep_better", noise_leeway=0.1)
    archive = archive.add(dgm.ArchiveNode("root", 0.5))
    archive = archive.add(dgm.ArchiveNode("high", 0.95, ("root",)))
    archive = archive.add(dgm.ArchiveNode("low", 0.3, ("root",)))
    assert [n.node_id for n in archive.nodes] == ["root", "high"]
    a, ra = archive.sample_parents(5, 77)
    b, rb = archive.sample_parents(5, 77)
    assert [n.node_id for n in a] == [n.node_id for n in b]
    assert ra == rb and ra["with_replacement"]


def test_dgm_archive_rejects_duplicate_unknown_lineage_and_nonfinite_scores():
    import math
    archive = dgm.DGMArchive().add(dgm.ArchiveNode("root", 0.5))
    for node in (dgm.ArchiveNode("root", 0.5),
                 dgm.ArchiveNode("orphan", 0.5, ("missing",)),
                 dgm.ArchiveNode("nan", math.nan)):
        try:
            archive.add(node)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid archive node was accepted")


def test_dgm_staged_schedule_escalates_and_consumes_budget_once():
    scheduler = dgm_stage.DGMStagedScheduler((dgm_stage.Stage("small", 2),
                                               dgm_stage.Stage("medium", 3),
                                               dgm_stage.Stage("full", 5, True)),
                                              escalation_threshold=0.5)
    assert scheduler.next_stage([], "n").name == "small"
    prior = [dgm_stage.Evaluation("n", "small", 2, 2, 1.0)]
    stage, consumed, record = scheduler.consume(prior, "n", 3)
    assert stage.name == "medium" and consumed == 3
    assert record["budget_after"] == 0


def test_dgm_full_threshold_ignores_partial_high_score():
    scheduler = dgm_stage.DGMStagedScheduler(
        (dgm_stage.Stage("small", 1), dgm_stage.Stage("medium", 1),
         dgm_stage.Stage("full", 1, True)), baseline_score=0.5,
        archive_full_results=(dgm_stage.ArchiveFullResult("partial", 0.95, 50, 100),))
    partial = dgm_stage.Evaluation("n", "medium", 5, 5, 0.95,
                                   required_full_population=100, full_population=50)
    # Candidate coverage is not the historical archive eligibility gate.
    assert scheduler.next_stage([partial], "n").name == "full"
    complete_low = dgm_stage.Evaluation("n2", "medium", 100, 100, 0.5,
                                        required_full_population=100, full_population=100)
    strict = dgm_stage.DGMStagedScheduler(
        (dgm_stage.Stage("small", 1), dgm_stage.Stage("medium", 1),
         dgm_stage.Stage("full", 1, True)), baseline_score=0.8,
        archive_full_results=(dgm_stage.ArchiveFullResult("prior", 0.8, 100, 100),))
    assert strict.next_stage([complete_low], "n2") is None


def test_dgm_medium_candidate_uses_only_full_enough_history_and_inclusive_boundary():
    stages = (dgm_stage.Stage("small", 1), dgm_stage.Stage("medium", 1),
              dgm_stage.Stage("full", 1, True))
    history = (
        dgm_stage.ArchiveFullResult("eligible-1", 0.8, 90, 100),
        dgm_stage.ArchiveFullResult("eligible-2", 0.7, 100, 100),
        dgm_stage.ArchiveFullResult("partial-high", 1.0, 89, 100),
    )
    scheduler = dgm_stage.DGMStagedScheduler(
        stages, baseline_score=0.2, archive_full_results=history)
    # The candidate has only 50/100 submissions, but the historical threshold is 0.7.
    candidate = dgm_stage.Evaluation("child", "medium", 50, 30, 0.7,
                                     required_full_population=100, full_population=50)
    below = dgm_stage.Evaluation("child-low", "medium", 50, 30, 0.699)
    assert scheduler.full_eval_threshold(baseline=0.2, archive_results=history) == 0.7
    assert scheduler.next_stage([candidate], "child").name == "full"
    assert scheduler.next_stage([below], "child-low") is None


def test_dgm_stage_inputs_reject_invalid_score_and_cost_domains():
    import math
    for constructor in (
            lambda: dgm_stage.Stage("bad", -1),
            lambda: dgm_stage.MediumCandidate("n", 1, 1, math.nan),
            lambda: dgm_stage.MediumCandidate("n", 1, 1, 1.1),
            lambda: dgm_stage.ArchiveFullResult("n", 0.5, 101, 100),
            lambda: dgm_stage.ArchiveFullResult("n", -0.1, 100, 100),
            lambda: dgm_stage.DGMStagedScheduler(baseline_score=math.inf),
            lambda: dgm_stage.DGMStagedScheduler(full_coverage_fraction=0.0)):
        try:
            constructor()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid staged-evaluation input was accepted")


def test_hyperagents_selector_variants_and_parallel_seed():
    nodes = (hyper.ArchiveNode("a", 0.2), hyper.ArchiveNode("b", 0.9),
             hyper.ArchiveNode("c", 0.5, ("b",) * 30))
    random_parents, random_report = hyper.sample_parents(nodes, 4, kind="random", seed=11)
    again, again_report = hyper.sample_parents(nodes, 4, kind="random", seed=11)
    assert [n.node_id for n in random_parents] == [n.node_id for n in again]
    assert random_report == again_report and random_report["with_replacement"]
    assert hyper.weights_for(nodes, "best") == {"a": 0.0, "b": 1.0, "c": 0.0}
    assert hyper.weights_for(nodes, "uniform") == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_hyperagents_patch_roles_and_rollbacks_are_distinct():
    ids = hyper.patch_identities(["task_agent.py"], patch_id="p1")
    assert ids["task"] is not None and ids["meta"] is None
    rollback = hyper.rollback_identity(patch_id="r1", role="meta_rollback",
                                       target="meta_agent.py", parent_patch_id="p0")
    assert rollback.role == "meta_rollback" and not rollback.applied
    state = hyper.ArtifactState("a1", ("root",), task_patch=ids["task"],
                                meta_rollback=rollback)
    assert state.as_dict()["task_patch"]["role"] == "task_agent"
    assert state.as_dict()["meta_rollback"]["role"] == "meta_rollback"


def test_hyperagents_staged_fraction_is_recorded_without_score_discount():
    scheduler = hyper_stage.HyperStagedScheduler()
    record = scheduler.record(hyper_stage.HyperEvaluation("n", 0.25, False, 0.8))
    assert record["stage"] == "full"
    assert record["staged_fraction"] == 0.25
    assert record["score_rescaling"] == "none"
    assert scheduler.next_stage(hyper_stage.HyperEvaluation("n", 0.25, False, 0.3)) == "staged_complete"
    assert scheduler.next_stage(hyper_stage.HyperEvaluation("n", 0.25, False, 0.4)) == "full"
