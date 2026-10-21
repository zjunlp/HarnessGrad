"""Source-only index regressions: no adapters, task pools or external receipts."""
from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_dataset_blockers as checker  # noqa: E402


@pytest.fixture(autouse=True)
def source_only(monkeypatch, tmp_path):
    original_import = builtins.__import__
    original_open = io.open
    allowed_tmp = tmp_path.resolve()

    def guarded_import(name, *args, **kwargs):
        assert name != "data" and not name.startswith("data."), "adapter imports forbidden"
        return original_import(name, *args, **kwargs)

    def guarded_open(path, *args, **kwargs):
        if isinstance(path, (str, bytes, Path)):
            resolved = Path(path).resolve()
            assert (resolved.is_relative_to(ROOT) or resolved.is_relative_to(allowed_tmp)), \
                "external file reads forbidden"
        return original_open(path, *args, **kwargs)

    def denied(*args, **kwargs):
        pytest.fail("network/runtime/subprocess execution forbidden")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)


@pytest.fixture
def document():
    return checker.read_json(checker.REGISTRY)


def row(document, name):
    return next(item for item in document["datasets"] if item["dataset"] == name)


def test_source_inventory_and_taxonomy_cover_every_publishable_dataset(document, blocker_repository_evidence):
    counts = checker.validate(document)
    taxonomy = checker.literal_declarations(ROOT / "tools/dataset_classes.py", {"CAPABILITY"})
    assert {entry["dataset"] for entry in document["datasets"]} == set(taxonomy["CAPABILITY"])
    assert counts["datasets"] == len(checker.published_declarations())
    assert counts["historical_records"] == len(checker.read_json(checker.HISTORY)["records"])
    # Refusal and absent provisioning must never make a dataset disappear.
    assert {"api_bank", "nestful", "sotuqa", "tau2_bench"} <= set(checker.published_declarations())


def test_pristine_scope_inventory_requires_review_on_any_dataset_opt_in():
    """w-scope is an empty-instance finding, not a permanent pristine exemption.

    Inspect source without importing adapters or loading task pools. A changed
    declaration must invalidate the dated finding and trigger a scope review.
    """
    declarations = []
    for path in sorted((ROOT / "data/datasets").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.Constant) and node.value == "pristine-offline"
                       for node in ast.walk(tree)), "new pristine opt-in: re-review w-scope"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "isolation_class":
                    declarations.append((path.stem, ast.literal_eval(value)))
    assert sorted(declarations) == [
        ("appworld", "snapshot-service"), ("terminal_bench", "snapshot-service"),
    ], "changed dataset isolation declarations: re-review w-scope"
    scaffold = ast.parse((ROOT / "data/scaffolds/qa/base.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Constant) and node.value == "pristine-offline"
                   for node in ast.walk(scaffold)), "new QA opt-in: re-review w-scope"


def test_pristine_scope_finding_preserves_residual_requirements_and_open_blockers(document):
    """Withdrawn shared requirements retain w-scope boundaries and search evidence.

    E4/E5/E6 are standing limits after the operator's withdrawal, not solved gates.
    No real-dataset pristine validation follows.
    """
    shared = {item["id"]: item for item in document["shared_blockers"]}
    assert set(shared) == set()
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    observed = withdrawal.split(
        "## Shared requirement withdrawn: `artifact-only-state-export`", 1)[1].split(
        "## Dataset requirements withdrawn", 1)[0]
    assert "**Searched:** `w-scope` reviewed all 28 declared datasets" in observed
    former_action = " ".join(line.removeprefix("> ") for line in observed.splitlines()
                             if line.startswith("> "))
    assert former_action == (
        "Provision authoritative per-task export and private-input contracts for pristine scorers; "
        "validate ownership, quiescence/trial binding, scoring equivalence and live lifecycle/cleanup. "
        "Review snapshot/service state and recording surfaces under their declared class without "
        "forcing an inferred artifact allowlist.")
    for boundary in (
        "zero pristine-offline declarations or selections",
        "SATISFIED FOR THE CURRENT DATASET SET (zero applicable pristine datasets)",
        "no real-dataset pristine export, private-input safety or backend ownership validation is established",
        "Every future pristine opt-in must provision authoritative per-task export",
        "validate actual backend/source/projection ownership",
        "E4/E5/E6 remain PARTIAL",
        "E4/E5/E6 remain PARTIAL as standing limits",
    ):
        assert boundary in observed
    assert "`/site/w-scope-pristine-applicability.md`,\nlocator `## Exact registry wording`" in observed
    scorer = withdrawal.split("## Shared requirement withdrawn: `scorer-offline-closure`", 1)[1].split(
        "## Shared requirement withdrawn: `artifact-only-state-export`", 1)[0]
    assert "`w-s6` traced the path" in scorer
    assert "flag only flips after the admission the clause itself\nhelps withhold" in scorer
    assert "directory-substitution/restore counterexample evades those probes" in observed
    assert document["review"]["production_authorized"] is False


# Exact migrated review identities, including the two original filing conventions.
_JOURNAL_REVIEWS = {
    "fever": ("/site/dataset-reviews/fever.md", "# FEVER source-only journal review"),
    "physicsquestions": ("/site/dataset-reviews/physicsquestions.md", "# Physics Questions source-only journal review"),
    "reasoning_gym": ("/site/dataset-reviews/reasoning_gym.md", "# Reasoning Gym source-only journal review"),
    # The first *new* journal-coverage blocker since the 2026-10-15 closures, and a new filing
    # rather than a migration: `aime` is the platform's first ported Harbor adapter, and its
    # review carries the provenance and the instruction-fidelity decision that route needs.
    "aime": ("/site/dataset-reviews/aime.md", "# AIME source-only journal review"),
    **{name: ("/site/dataset-source-reviews-b2.md", f"## {name} source-only review")
       for name in ("sportsunderstanding", "spreadsheetbench", "strategyqa")},
}


def test_every_journal_coverage_dataset_points_to_its_migrated_review(document):
    """Every *open* journal-coverage blocker must point at its migrated review.

    All six reviewed datasets closed their journal-coverage blocker on 2026-10-15, and `aime`
    opened the next one when it was registered as the platform's first ported Harbor adapter, so
    the loop below finally has a live set to guard rather than only a hypothetical next entry. It
    refuses a blocker whose review identity was never mapped instead of raising `KeyError`. The
    non-vacuity check rides on the mapping itself - every identity must name a published dataset,
    and the count is pinned so the parametrized content check below cannot shrink silently. The
    pin is a floor on care, not a ceiling: registering a dataset raises it deliberately.
    """
    published = {e["dataset"] for e in document["datasets"]}
    assert set(_JOURNAL_REVIEWS) <= published, sorted(set(_JOURNAL_REVIEWS) - published)
    assert len(_JOURNAL_REVIEWS) == 7, sorted(_JOURNAL_REVIEWS)
    covered = set()
    for entry in document["datasets"]:
        for blocker in entry["blockers"]:
            if blocker["family"] != "journal-coverage":
                continue
            name = entry["dataset"]
            assert name in _JOURNAL_REVIEWS, (
                f"{name} carries an open journal-coverage blocker with no migrated review; add its "
                "identity to _JOURNAL_REVIEWS")
            path, locator = _JOURNAL_REVIEWS[name]
            assert {"kind": "external-reference", "path": path,
                    "locator": locator} in blocker["evidence"], name
            covered.add(name)
    expected = {e["dataset"] for e in document["datasets"]
                for b in e["blockers"] if b["family"] == "journal-coverage"}
    assert covered == expected, \
        f"guard did not reach every journal-coverage dataset: {sorted(expected - covered)}"


@pytest.mark.parametrize("name", sorted(_JOURNAL_REVIEWS))
def test_archived_journal_review_still_states_closed_and_open_work(name, site_archive):
    """Retain content checks when optional exported source records are provisioned."""
    reference, _ = _JOURNAL_REVIEWS[name]
    path = site_archive(reference)
    relative = path.relative_to(ROOT)
    assert not relative.is_absolute() and ".." not in relative.parts
    assert path.resolve().is_relative_to(ROOT.resolve())
    assert path.is_file(), f"{name}: missing review {relative}"
    source = path.read_text(encoding="utf-8")
    headings = [line for line in source.splitlines() if line.lstrip().startswith("#")]

    def _norm(text):
        return "".join(ch for ch in text.lower() if ch.isalnum())

    assert any(_norm(name) in _norm(h) for h in headings), \
        f"{name}: {relative} has no heading naming this dataset"
    assert any(m in source for m in ("CLOSED:", "Closed:", "Closed at source-review level:")), \
        f"{name}: {relative} does not say what it closed"
    assert any(m in source for m in ("OPEN:", "Next:", "Still open")), \
        f"{name}: {relative} does not say what it left open"


def test_new_declared_dataset_fails_coverage_even_when_its_import_would_fail(document, tmp_path):
    (tmp_path / "data" / "datasets").mkdir(parents=True)
    for name in checker.published_declarations():
        (tmp_path / "data" / "datasets" / f"{name}.py").write_text('KIND = "dataset"\n')
    (tmp_path / "data" / "datasets" / "new_dataset.py").write_text(
        'KIND = "dataset"\nraise RuntimeError("cannot import")\n')
    (tmp_path / "tools").mkdir()
    original = (ROOT / "tools/dataset_classes.py").read_text()
    (tmp_path / "tools/dataset_classes.py").write_text(original)
    with pytest.raises(checker.RegistryError, match="taxonomy/published declarations differ"):
        checker.validate(document, root=tmp_path)


def test_fixtures_are_not_published_and_metadata_is_never_executed(tmp_path):
    (tmp_path / "data" / "datasets").mkdir(parents=True)
    (tmp_path / "data" / "datasets" / "fixture.py").write_text('KIND = "fixture"\nraise AssertionError\n')
    (tmp_path / "data" / "datasets" / "default.py").write_text('raise AssertionError\n')
    (tmp_path / "data" / "datasets" / "published.py").write_text('KIND: str = "dataset"\nraise AssertionError\n')
    assert checker.published_declarations(tmp_path) == ("published",)


@pytest.mark.parametrize("mutation, message", [
    ("missing", "missing published datasets"),
    ("duplicate", "duplicate dataset"),
    ("fixture", "unpublished dataset"),
    ("demand", "demand x surface drift"),
    ("surface", "demand x surface drift"),
    ("exercises", "exercise drift"),
    ("flat-category", "fields must be"),
    ("unknown-status", "unknown status"),
    ("unknown-family", "unknown blocker family"),
    ("unknown-shared", "unknown shared blocker"),
    ("duplicate-blocker", "duplicate blocker"),
    ("missing-action", "empty text"),
    ("missing-evidence", "evidence required"),
    ("stale-anchor", "stale evidence locator"),
    ("missing-path", "missing evidence"),
    ("escape-path", "must stay inside root"),
    ("authorize", "cannot authorize production"),
])
def test_registry_refuses_incomplete_or_misleading_entries(document, mutation, message,
                                                          blocker_repository_evidence,
                                                          attach_synthetic_blocker):
    # This has to mutate something, and an empty `blockers` list is a supported state since
    # `docs/enforcement-proof-decision.md`, so it corrupts a **synthetic** entry attached to a
    # published dataset inside a private copy. Pinning a real dataset here went stale twice
    # (`jericho`, then `bfcl_v3`, withdrawn by the operator on 2026-10-16) and reading the
    # registry dynamically stopped working once `appworld` was the only non-refusal blocker left.
    # The synthetic entry removes the dependency on the live population entirely.
    document = copy.deepcopy(document)
    dataset, blocker_id = attach_synthetic_blocker(document)
    target = row(document, dataset)
    blocker = next(item for item in target["blockers"] if item["id"] == blocker_id)
    repository_evidence = next(item for item in blocker["evidence"]
                               if item["kind"] == "repository")
    if mutation == "missing":
        document["datasets"].remove(target)
    elif mutation == "duplicate":
        document["datasets"].append(copy.deepcopy(target))
    elif mutation == "fixture":
        target["dataset"] = "demo"
    elif mutation in {"demand", "surface"}:
        target[mutation] = "memory"
    elif mutation == "exercises":
        target["exercises"] = [target["surface"]]
    elif mutation == "flat-category":
        target["category"] = "interactive_fiction"
    elif mutation == "unknown-status":
        blocker["status"] = "ready"
    elif mutation == "unknown-family":
        blocker["family"] = "all-clear"
    elif mutation == "unknown-shared":
        target["shared_blockers"].append("imaginary")
    elif mutation == "duplicate-blocker":
        target["blockers"].append(copy.deepcopy(blocker))
    elif mutation == "missing-action":
        blocker["next_action"] = ""
    elif mutation == "missing-evidence":
        blocker["evidence"] = []
    elif mutation == "stale-anchor":
        repository_evidence["locator"] = "fabricated absent journal entry"
    elif mutation == "missing-path":
        repository_evidence["path"] = "docs/does-not-exist.txt"
    elif mutation == "escape-path":
        repository_evidence["path"] = "../external-receipt.json"
    elif mutation == "authorize":
        document["review"]["production_authorized"] = True
    with pytest.raises(checker.RegistryError, match=message):
        checker.validate(document)


def test_within_task_memory_exercise_does_not_create_a_memory_demand(document):
    target = row(document, "jericho")
    assert target["demand"] == "environment"
    assert target["surface"] == "interactive_fiction"
    assert "memory" in target["exercises"]
    memory_datasets = {entry["dataset"] for entry in document["datasets"]
                       if entry["demand"] == "memory"}
    assert "jericho" not in memory_datasets
    assert "personamem_v2" in memory_datasets


def test_agentdojo_production_gate_is_revoked_and_the_privacy_closure_stands(document):
    """The self-imposed production gate is gone; the privacy facts it sat beside are not.

    `production-route-admission` was revoked by operator decision 2026-10-15. It was never a
    statement that AgentDojo cannot run -- the adapter is an ordinary dataset with a released
    oracle and a live code path. What it recorded was that the *custody* of the external
    prepared runtime at `/mnt/20t/xubuqiang/datasets/agentdojo` rested on an inherited
    metadata inventory rather than an independently authenticated manifest, and the platform
    had responded to that by refusing its own production route.

    Revoking the gate does not revoke the privacy closure it was recorded next to, and this
    test keeps pinning that: the metadata review is still closed, and the registry still
    cannot authorize production. A dataset becoming runnable must not become a dataset
    becoming authorized.
    """
    target = row(document, "agentdojo")
    assert [item["id"] for item in target["blockers"]] == [], (
        "the production gate was revoked; if a blocker has returned, name it here and say why")
    assert document["review"]["production_authorized"] is False
    journal = (ROOT / "docs/adapter-integration-journal.md").read_text()
    assert "../reports/agentdojo-metadata-privacy-codex180-report.md" in journal


def test_archived_agentdojo_privacy_closure(site_archive):
    closure = site_archive("/site/agentdojo-metadata-privacy-codex180-report.md")
    assert closure.is_file(), "the metadata privacy closure record is gone"
    assert "Metadata review closure and production boundary" in closure.read_text(
        encoding="utf-8")


def test_scienceworld_code_review_keeps_runtime_and_v4_history_blocked(document):
    """The `prevent-platform-writes` requirement was withdrawn; the history was not.

    Operator decision 2026-10-15 (`docs/enforcement-proof-decision.md`): a requirement whose
    only content is "prove the enforcement mechanism cannot fail" is out of scope. The
    platform still sandboxes and still detects platform writes -- what was withdrawn is the
    demand for proof that it cannot fail.

    Two things this test now pins, and both matter more than the blocker it lost: the
    historical v4 record is still `incomplete` with a null effect, and the registry still
    cannot authorize production. Removing a requirement must not quietly re-read an
    unfinished run as finished.
    """
    target = row(document, "scienceworld")
    assert [item["id"] for item in target["blockers"]] == [], (
        "prevent-platform-writes was withdrawn; if a blocker has returned, this test should "
        "name it and say why it is back")
    # The shared requirements are withdrawn with limits; incomplete history stands.
    assert set(target["shared_blockers"]) == set()
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    for blocker_id in ("scorer-offline-closure", "artifact-only-state-export"):
        assert blocker_id not in {item["id"] for item in document["shared_blockers"]}
        assert f"## Shared requirement withdrawn: `{blocker_id}`" in withdrawal
    assert "flag only flips after the admission the clause itself\nhelps withhold" in withdrawal
    assert "directory-substitution/restore counterexample evades those probes" in withdrawal
    assert document["review"]["production_authorized"] is False
    baseline = checker.read_json(checker.HISTORY)
    assert target["historical_runs"] == [
        item["record"] for item in baseline["records"] if item["dataset"] == "scienceworld"
    ]
    assert target["historical_runs"][0]["status"] == "incomplete"
    assert target["historical_runs"][0]["effect"] is None


def test_b7_local_runtime_closures_retain_all_shared_gates(document):
    """Two dataset-local runtime gates closed with measured evidence; the shared ones stand.

    `jericho`'s `independent-runtime-proof` and `scienceworld`'s `prevent-platform-writes` are
    both gone. The first closed because `b7` measured it -- fresh external-root noop/oracle
    controls at 0/30 and 30/30, with a parent collector reading the final kernel mounts and
    source identities before the subject ran, so the observation does not depend on the
    child's own verdict. The second was withdrawn by operator decision
    (`docs/enforcement-proof-decision.md`).

    `b7` also proposed replacing both with a per-dataset `shared-runtime-admission` blocker.
    That was **not** merged: its content is the shared gate the entry already lists under
    `shared_blockers`, and duplicating a shared requirement per dataset is how a registry
    grows two sources of truth for one fact. The evidence is not lost with it -- the reports
    retain journal citations below, because measured evidence outlives the gate it was
    gathered for. Exported source contents are checked separately when provisioned.
    """
    shared = [item["id"] for item in document["shared_blockers"]]
    for name in ("jericho", "scienceworld"):
        target = row(document, name)
        assert target["shared_blockers"] == shared, f"{name}: shared gates drifted"
        assert target["blockers"] == [], (
            f"{name}: a dataset-local blocker has returned; if that is deliberate, name it "
            "here and say why, rather than re-adding the shared duplicate")
    assert (ROOT / "tests/quality/test_runtime_blocker_evidence_b7.py").is_file()
    journal = (ROOT / "docs/adapter-integration-journal.md").read_text()
    for name in ("runtime-blockers-b7.md", "jericho-runtime-b7.json", "scienceworld-runtime-b7.json"):
        assert f"../reports/{name}" in journal
    assert document["review"]["production_authorized"] is False


@pytest.mark.parametrize("report", ["/site/runtime-blockers-b7.md", "/site/jericho-runtime-b7.json",
                                  "/site/scienceworld-runtime-b7.json"])
def test_archived_b7_closure_evidence(report, site_archive):
    assert site_archive(report).is_file(), f"{report}: the evidence behind the closure is gone"


def test_bfcl_ast_contract_withdrawal_is_recorded_and_the_containment_boundary_survives(document):
    """Two BFCL gates are gone, and they went for different reasons; the decoder boundary is not.

    `release-index-custody` required *human custodian approval* of historical tag/archive
    bindings. Operator decision 2026-10-15: requirements whose content is "someone
    independent must sign this" are revoked, and the operator will review them personally
    once the rest is wired up (`docs/independent-review-deferred.md`). The requirement is
    deferred, not judged unnecessary, which is why the deferral is recorded rather than the
    entry simply vanishing.

    `simple-ast-contract` was then **withdrawn by operator scope decision on 2026-10-16** -
    "relax these two, there is no need for evidence this rigorous" - because the missing facts
    are producer-held build-time facts that a rebuild cannot supply. That is a **scope
    withdrawal, not a finding that the evidence is unobtainable**, and it supersedes the
    earlier reading that this gate stays because it asks for work rather than a signature.
    The withdrawal and its named limits are recorded in `registry/withdrawn-requirements.md`.

    What must NOT be read as withdrawn: the retained decoder containment boundary revives with
    any future official-scorer work, and production stays unauthorized.
    """
    blockers = {item["id"]: item for item in row(document, "bfcl_v3")["blockers"]}
    assert "release-index-custody" not in blockers, (
        "the approval gate was revoked and deferred to operator review")
    assert "simple-ast-contract" not in blockers, (
        "the operator withdrew this gate on 2026-10-16; it must not reappear without a new "
        "operator decision")

    # Whitespace-normalised: the record is prose and wraps, so a raw `in` would fail on a line
    # break rather than on a missing sentence.
    withdrawn = " ".join(
        (ROOT / "registry" / "withdrawn-requirements.md").read_text(encoding="utf-8").split())
    assert "`bfcl_v3 / simple-ast-contract`" in withdrawn, "the withdrawal record is gone"
    assert "`triviaqa / hub-export-scorer-linkage`" in withdrawn, "the withdrawal record is gone"
    assert "Scope note for these two entries" in withdrawn, (
        "the scope note is what separates this withdrawal from a proven-unobtainable finding")
    assert "Withdrawn by operator scope decision on 2026-10-16" in withdrawn
    # The limits a reader must carry forward, and the condition that revives the boundary.
    for carried in ("87 exact compiled-build entries remain UNKNOWN",
                    "18669", "17944",
                    "local alias-max normalized row-EM",
                    "no invented 725-member explanation",
                    "function-name diagnostic",
                    "guarded_decode",
                    "reopens the original technical gate"):
        assert carried in withdrawn, f"withdrawal record dropped: {carried!r}"
    assert document["review"]["production_authorized"] is False


def test_archived_bfcl_review_deferral(site_archive):
    deferral = site_archive("/site/independent-review-deferred.md")
    assert deferral.is_file(), "the deferral record is gone; the review would be lost"
    assert "release-index-custody" in deferral.read_text(encoding="utf-8")


def test_twowiki_source_closure_keeps_dependency_and_data_gates_external(document):
    target = row(document, "twowikimultihopqa")
    blockers = {item["id"]: item for item in target["blockers"]}
    assert "independent-evaluator-source" not in blockers
    assert "independent-evaluator-source" not in target["shared_blockers"]
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    for blocker_id, family in (
        ("ujson-native-offline-closure", "scorer-dependency-network"),
        ("hub-data-provenance", "source-provenance"),
    ):
        assert blocker_id not in blockers
        section = withdrawal.split(f"### `twowikimultihopqa / {blocker_id}`", 1)[1].split(
            "\n### ", 1)[0]
        assert f"**Former family:** `{family}`" in section
        if blocker_id == "ujson-native-offline-closure":
            assert '**Searched:** PyPI build attestation (**404, "No provenance available"**)' in section
            assert "**reproducible rebuild** from the pinned commit `59875e08`" in section
            assert "**Result:** the rebuild **did not reproduce**" in section
        else:
            assert "**Searched:** exact-revision upload history, release/tag inventories" in section
            assert "**opaque ETag and no content digest**" in section


def test_twowiki_source_closure_preserves_shared_gates_and_mixed_metric_history(document):
    target = row(document, "twowikimultihopqa")
    # Four earlier shared blockers left this set deliberately and visibly, without
    # loosening the assertion:
    #   `paired-filesystem-quotas` was *withdrawn* -- enforcement still does not exist, the
    #     operator decided the platform does not require it
    #     (`docs/filesystem-quota-decision.md`);
    #   `pilot-guidance-conflict` was *completed* -- the planning documents were aligned with
    #     `AGENTS.md`, so the requirement no longer exists and the entry is gone rather than
    #     parked in the registry under a status the schema does not have;
    #   `official-scorer-identity` and `snapshot-owned-cleanup` were *withdrawn* with it --
    #     both demanded proof that a mechanism cannot fail
    #     (`docs/enforcement-proof-decision.md`).
    # The `pristine-verifier` source-review action is now completed by the opt-in
    # pinned offline runtime/inspection path (`docs/w-shared-verifier-review.md`).
    # w-obs also completes `runtime-observations`: ChessBench records its real
    # controller class; w-fix preserves unknown evidence as noncomparable and
    # refuses comparison (docs/w-fix-unknown-isolation-records.md). Remove exactly these two
    # completed review gates. The operator subsequently withdrew the dependency/export
    # requirements with search evidence; E4/E5/E6 remain partial as standing limits.
    assert set(target["shared_blockers"]) == set()
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    for blocker_id in ("scorer-offline-closure", "artifact-only-state-export"):
        assert blocker_id not in {b["id"] for b in document["shared_blockers"]}
        assert f"## Shared requirement withdrawn: `{blocker_id}`" in withdrawal
    assert "`w-s6` traced the path" in withdrawal
    assert "flag only flips after the admission the clause itself\nhelps withhold" in withdrawal
    assert "directory-substitution/restore counterexample evades those probes" in withdrawal
    assert "pristine-verifier" not in {b["id"] for b in document["shared_blockers"]}
    assert "runtime-observations" not in {b["id"] for b in document["shared_blockers"]}
    notes = "\n".join(document["review"]["notes"])
    assert "pristine-verifier review action closed" in notes
    assert "runtime-observations source-review action closed" in notes
    for gone in ("paired-filesystem-quotas", "pilot-guidance-conflict",
                 "official-scorer-identity", "snapshot-owned-cleanup"):
        assert gone not in target["shared_blockers"]
    # B6 completed the separate historical metadata review and rejected combining the
    # generations. A closed review is removed from this active-only registry; the frozen
    # original history must still remain partial with no effect. So the blocker is gone and
    # the evidence for closing it is pinned by name rather than left as a claim.
    assert "recovery-comparability" not in {item["id"] for item in target["blockers"]}
    journal = (ROOT / "docs/adapter-integration-journal.md").read_text()
    assert "close recovery-comparability and smoke-scorer-comparability as completed historical reviews" in journal
    baseline = checker.read_json(checker.HISTORY)
    assert target["historical_runs"] == [
        item["record"] for item in baseline["records"]
        if item["dataset"] == "twowikimultihopqa"
    ]
    original, = target["historical_runs"]
    assert original["record_id"] == "twowiki-original-invalid-pilot"
    assert original["run_id"] is None
    assert original["status"] == "partial-measurement"
    assert original["effect"] is None
    for fact in ("48 scored tasks", "two malformed-output invalids",
                 "mixed legacy Answer F1/structured Joint F1"):
        assert fact in original["observation"]
    assert document["review"]["production_authorized"] is False


@pytest.mark.parametrize("reference, heading", [
    ("/site/w-shared-verifier-review.md", "## CLOSED: pristine-verifier - opt-in runtime identity review completed"),
    ("/site/w-obs-runtime-review.md", "## CLOSED: runtime-observations - observed topology required before aggregation"),
    ("/site/b6-dataset-blocker-review.md", "recovery-comparability — review closed; combined comparison rejected"),
])
def test_archived_shared_and_comparability_closure_headings(reference, heading, site_archive):
    assert heading in site_archive(reference).read_text(encoding="utf-8")


def test_twowiki_task_capability_is_separate_from_harness_exercises(document):
    target = row(document, "twowikimultihopqa")
    assert (target["demand"], target["surface"]) == ("evidence", "information_seeking")
    assert target["exercises"] == ["context"]
    assert "category" not in target



@pytest.mark.parametrize("mutation", ["remove", "complete", "zero", "coverage", "path", "id", "move"])
def test_historical_incompletes_cannot_be_removed_reclassified_or_rescored(document, mutation, blocker_repository_evidence):
    target = row(document, "chessbench_puzzles")
    history = target["historical_runs"][-1]
    if mutation == "remove":
        target["historical_runs"].remove(history)
    elif mutation == "complete":
        history["status"] = "complete"
    elif mutation == "zero":
        history["effect"] = 0.0
    elif mutation == "coverage":
        history["observation"] = "150/150 scored"
    elif mutation == "path":
        history["evidence"][-1]["path"] += ".rewritten"
    elif mutation == "id":
        history["run_id"] = "reused-id"
    elif mutation == "move":
        target["historical_runs"].remove(history)
        row(document, "gsm8k")["historical_runs"].append(history)
    with pytest.raises(checker.RegistryError, match="historical"):
        checker.validate(document)


def test_history_baseline_pin_is_independent_and_checked(document, monkeypatch, blocker_repository_evidence):
    assert hashlib.sha256(checker.HISTORY.read_bytes()).hexdigest() == checker.HISTORY_SHA256
    monkeypatch.setattr(checker, "HISTORY_SHA256", "0" * 64)
    with pytest.raises(checker.RegistryError, match="baseline pin changed"):
        checker.validate(document)


@pytest.mark.parametrize("record_id", [
    "appworld-initial-infrastructure-smoke", "bird-v4-incomplete", "bird-v5-partial-coverage",
    "chess-original-incomplete", "chess-recovery-incomplete", "ds1000-v4-pending-review",
    "gaia-v2-incomplete", "gaia-v3-incomplete", "hotpot-v4-incomplete",
    "scienceworld-v4-incomplete", "twowiki-original-invalid-pilot",
])
def test_every_reviewed_historical_record_is_required(document, record_id, blocker_repository_evidence):
    for entry in document["datasets"]:
        entry["historical_runs"] = [record for record in entry["historical_runs"]
                                    if record["record_id"] != record_id]
    with pytest.raises(checker.RegistryError, match=f"removed or changed: {record_id}"):
        checker.validate(document)


def test_history_can_append_without_overwriting_old_records(document, blocker_repository_evidence):
    target = row(document, "chessbench_puzzles")
    new = copy.deepcopy(target["historical_runs"][-1])
    new["record_id"] = "new-independent-interruption"
    new["run_id"] = "new-run-id"
    target["historical_runs"].append(new)
    assert checker.validate(document)["historical_records"] == 12


def test_new_historical_record_cannot_reuse_an_existing_run_id(document, blocker_repository_evidence):
    target = row(document, "chessbench_puzzles")
    new = copy.deepcopy(target["historical_runs"][-1])
    new["record_id"] = "different-record-same-run"
    target["historical_runs"].append(new)
    with pytest.raises(checker.RegistryError, match="duplicate historical run ID"):
        checker.validate(document)


def test_chess_future_launch_guidance_requires_new_ids_and_preserves_history(document):
    target = row(document, "chessbench_puzzles")
    assert "recovery-id-conflict" not in {blocker["id"] for blocker in target["blockers"]}
    assert "interruption-closure" not in {blocker["id"] for blocker in target["blockers"]}
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    assert "### `chessbench_puzzles / interruption-closure`" in withdrawal
    baseline = checker.read_json(checker.HISTORY)
    assert target["historical_runs"] == [
        item["record"] for item in baseline["records"]
        if item["dataset"] == "chessbench_puzzles"
    ]
    journal = (ROOT / "docs/adapter-integration-journal.md").read_text(encoding="utf-8")
    audit = next(line for line in journal.splitlines()
                 if line.startswith("- Interruption audit: termination authority is undetermined"))
    assert "Every future attempt must use a genuinely new unique run ID" in audit
    assert "never resume or overwrite any used run ID" in audit
    for history in target["historical_runs"]:
        assert history["status"] == "incomplete" and history["effect"] is None
        assert f'`{history["run_id"]}`' in audit
        assert f'launch a fresh `{history["run_id"]}`' not in journal


@pytest.mark.parametrize("name, blocker_id, family, evidence, retained", [
    ("gaia", "frozen-lifecycle-receipt", "snapshot-cleanup",
     ("Retain policy refusal: do not provision or run GAIA.",
      '`"receipt_validation": "not-performed"`',
      "No permitted live run\ncan supply that evidence here"),
     ("gaia-validation-pilot-h0-20261117-v2", "gaia-validation-pilot-h0-20261117-v3",
      "9 persisted trials", "10 done events", "snapshot exit 137", "`effect=null`")),
    ("tau_bench", "local-state-lifecycle", "snapshot-cleanup",
     ("Keep the runtime gate open.", "selected-only custody plus independent",
      "run/trial/manifest-bound clean-state restoration", "both clean databases\nare absent",
      "no independent selected runtime/lifecycle receipt"),
     ("`historical_runs` stays empty", "no frozen tau-bench run or independent receipt is invented",
      "without a runtime pass, readiness claim or production\nauthorization")),
    ("chessbench_puzzles", "interruption-closure", "termination-audit",
     ("independent audit/receipt authentication", "termination/quiescence review",
      "`docs/independent-review-deferred.md`", "operator withdrew the independent-approval gates",
      "That standalone exported source is not available"),
     ("chess-pilot-h0-20261013", "chess-pilot-h0-20261013-recovery-01", "19 and 149/150",
      "Never resume, rerun or overwrite either ID", "`effect=null`", "not measured zeros")),
])
def test_operator_withdrawn_runtime_requirements_retain_evidence_and_limits(
        document, name, blocker_id, family, evidence, retained):
    assert blocker_id not in {blocker["id"] for blocker in row(document, name)["blockers"]}
    withdrawal = (ROOT / "registry/withdrawn-requirements.md").read_text(encoding="utf-8")
    heading = f"### `{name} / {blocker_id}`"
    assert heading in withdrawal
    section = withdrawal.split(heading, 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
    assert f"**Former family:** `{family}`. **Former status:** `runtime`." in section
    for label in ("**Asked for:**", "**Evidence reviewed:**",
                  "**Why it cannot be advanced here:**", "**Retained:**"):
        assert label in section
    for fact in evidence + retained:
        assert fact in section
    assert document["review"]["production_authorized"] is False


def test_archived_chess_launch_guidance(document, site_archive):
    target = row(document, "chessbench_puzzles")
    journal = (ROOT / "docs/adapter-integration-journal.md").read_text(encoding="utf-8")
    text = " ".join(site_archive("/site/dataset-blockers.md").read_text(encoding="utf-8").split())
    assert "Every future attempt must use a genuinely new unique run ID" in text
    assert "never resume or overwrite any used run ID" in text
    for history in target["historical_runs"]:
        assert history["status"] == "incomplete" and history["effect"] is None
        assert f'`{history["run_id"]}`' in text
        assert f'launch a fresh `{history["run_id"]}`' not in journal


@pytest.mark.parametrize("raw", ['{"datasets": [], "datasets": []}', '{"score": NaN}'])
def test_noncanonical_json_is_rejected(tmp_path, raw):
    path = tmp_path / "registry.json"
    path.write_text(raw)
    with pytest.raises(checker.RegistryError):
        checker.read_json(path)


def test_cli_produces_counts_and_nonzero_invalid_result(document, tmp_path, capsys, blocker_repository_evidence):
    assert checker.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True and output["production_authorized"] is False
    document["datasets"].pop()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document))
    assert checker.main(["--registry", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["valid"] is False
