"""The proposal applier must refuse anything that would silently drop an obligation.

`tools/apply_blocker_proposal.py` is the parent-side integration step for the
`registry/proposals/*.json` fragments that workers emit. Its whole value is the refusal path:
twice during this phase a hand-applied proposal dropped obligations that the blocker's own
`next_action` had carried, and a third time a proposal was applied while the focused tests were
failing. These tests exercise the refusals against a copy of the live registry, so they cannot
damage it.
"""
import copy
import json
from pathlib import Path

import pytest

import tools.apply_blocker_proposal as applier

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "registry" / "dataset-blockers.json"


@pytest.fixture
def registry(tmp_path, attach_synthetic_blocker):
    """A private copy of the live registry plus a writer for proposal documents."""
    document = json.loads(LIVE.read_text(encoding="utf-8"))
    # The pair these tests act on is a **synthetic** entry attached to a published dataset. It used
    # to be read from the live registry, and that went stale four times on 2026-10-15 as blockers
    # closed under reading (A) - hotpotqa, strategyqa and fever among them - and again on
    # 2026-10-16, when the operator withdrew `bfcl_v3/simple-ast-contract` and
    # `triviaqa/hub-export-scorer-linkage` and left `appworld` as the only one. The assertions are
    # unchanged; only the source of the blocker is. See `SYNTHETIC_BLOCKER` in `conftest.py`.
    pair = attach_synthetic_blocker(document)
    target = tmp_path / "registry.json"
    target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def write(name, payload):
        path = tmp_path / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    return target, write, pair


def a_blocker(document, dataset=None, blocker_id=None):
    """One open blocker. Called with no names it returns the live pair's blocker.

    Pinning a default here (`fever/dataset-journal-gap`) went stale the moment that blocker closed
    under reading (A), so the default is now whatever `an_open_blocker` selects - the same function
    the `registry` fixture uses, so the returned blocker and the CLI arguments always agree.
    """
    if dataset is None or blocker_id is None:
        return an_open_blocker(document)[2]
    for row in document["datasets"]:
        if row["dataset"] == dataset:
            for item in row["blockers"]:
                if item["id"] == blocker_id:
                    return item
    raise AssertionError(f"{dataset}/{blocker_id} is not an open blocker")


def an_open_blocker(document):
    """Any open, non-refusal blocker, chosen from the live registry.

    The list-shape tests need *an* open blocker, not a particular one. Pinning a dataset here broke
    twice on 2026-10-15 - `hotpotqa/historical-comparison-receipts` and then
    `strategyqa/dataset-journal-gap` closed under reading (A) - so the fixture now reads whichever
    non-refusal blocker the registry actually has, and fails only if there are none at all.
    """
    for row in sorted(document["datasets"], key=lambda r: r["dataset"]):
        for item in row["blockers"]:
            if item["id"] != "retain-contract-refusal":
                return row["dataset"], item["id"], item
    raise AssertionError("no open non-refusal blocker to exercise the applier with")


def test_valid_single_record_proposal_is_applied(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    old = a_blocker(document)["next_action"]
    proposal = write("ok.json", {
        "dataset": DS, "blocker": BL,
        "observed_append": "TEST-APPEND-MARKER.",
        "next_action_replacement": old + " TEST-REPLACEMENT-MARKER.",
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target),
                         "--apply"]) == 0
    updated = a_blocker(json.loads(target.read_text(encoding="utf-8")))
    assert "TEST-REPLACEMENT-MARKER." in updated["next_action"]
    assert "TEST-APPEND-MARKER." in updated["observed"]
    assert '"valid": true' in capsys.readouterr().out


def test_dry_run_writes_nothing(registry):
    target, write, (DS, BL) = registry
    before = target.read_text(encoding="utf-8")
    document = json.loads(before)
    proposal = write("dry.json", {
        "dataset": DS, "blocker": BL,
        "next_action_replacement": a_blocker(document)["next_action"] + " EXTRA.",
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == before


def test_refuses_a_quoted_clause_that_is_not_present(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    old = a_blocker(document)["next_action"]
    proposal = write("badquote.json", {
        "changes": [{"dataset": DS, "blocker": BL,
                     "quoted_clause": "this sentence is not in the registry at all",
                     "replacement": old + " X."}],
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_refuses_a_replacement_that_drops_an_obligation(registry, capsys):
    target, write, (DS, BL) = registry
    proposal = write("short.json", {
        "dataset": DS, "blocker": BL,
        "next_action_replacement": f"Keep working on {DS}.",
    })
    captured = capsys.readouterr()
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 1
    err = capsys.readouterr().err
    assert "not carried by the replacement" in err
    assert "next_action" in err
    assert captured.out == ""
    # The override exists, but only after the dropped sentences have been printed for review.
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target), "--force"]) == 0


def test_refuses_divergent_replacements_in_a_changes_list(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    old = a_blocker(document)["next_action"]
    proposal = write("divergent.json", {
        "changes": [
            {"dataset": DS, "blocker": BL,
             "quoted_clause": "Retain", "replacement": old + " A."},
            {"dataset": DS, "blocker": BL,
             "quoted_clause": "Retain", "replacement": old + " B."},
        ],
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 1
    assert "distinct replacement strings" in capsys.readouterr().err


def test_refuses_an_unknown_blocker(registry, capsys):
    target, write, (DS, BL) = registry
    proposal = write("unknown.json", {
        "dataset": DS, "blocker": "no-such-blocker",
        "next_action_replacement": "x",
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", "no-such-blocker", "--registry", str(target)]) == 1
    assert "no open blocker" in capsys.readouterr().err


def test_identical_replacement_is_refused(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    proposal = write("same.json", {
        "dataset": DS, "blocker": BL,
        "next_action_replacement": a_blocker(document)["next_action"],
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 1
    assert "identical to the current value" in capsys.readouterr().err


def test_observed_append_is_idempotent(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    old_action = a_blocker(document)["next_action"]
    marker = "IDEMPOTENT-MARKER."
    first = write("first.json", {"dataset": DS, "blocker": BL,
                                 "observed_append": marker,
                                 "next_action_replacement": old_action + " A."})
    assert applier.main([str(first), "--dataset", DS, "--blocker", BL, "--registry", str(target), "--apply"]) == 0
    after_first = a_blocker(json.loads(target.read_text(encoding="utf-8")))
    assert after_first["observed"].count(marker) == 1
    second = write("second.json", {"dataset": DS, "blocker": BL,
                                   "observed_append": marker,
                                   "next_action_replacement": after_first["next_action"] + " B."})
    assert applier.main([str(second), "--dataset", DS, "--blocker", BL, "--registry", str(target), "--apply"]) == 0
    assert "no-op" in capsys.readouterr().out
    assert a_blocker(json.loads(target.read_text(encoding="utf-8")))["observed"].count(marker) == 1


def test_a_broken_registry_is_restored(registry, capsys, tmp_path):
    """If the write would leave the registry invalid, the original bytes come back."""
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    before = target.read_text(encoding="utf-8")
    # A proposal that is clean by the applier's own checks but corrupts the registry on write is
    # simulated by pointing the checker at a registry whose statuses the checker rejects.
    broken = tmp_path / "broken.json"
    mutated = copy.deepcopy(document)
    a_blocker(mutated)["status"] = "not-a-real-status"
    broken.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
    original = broken.read_text(encoding="utf-8")
    proposal = write("ok2.json", {"dataset": DS, "blocker": BL,
                                  "next_action_replacement": a_blocker(mutated)["next_action"] + " A."})
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(broken), "--force", "--apply"]) == 1
    assert broken.read_text(encoding="utf-8") == original
    assert "restored" in capsys.readouterr().err


def test_acknowledge_phrases_allow_named_drops_but_only_named_ones(registry, capsys):
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    old = a_blocker(document)["next_action"]
    # Drop exactly one sentence, and name it.
    first = applier.sentences(old)[0]
    proposal = write("named.json", {
        "dataset": DS, "blocker": BL,
        "next_action_replacement": old.replace(first + " ", ""),
    })
    base = [str(proposal), "--dataset", DS, "--blocker", BL,
            "--registry", str(target), "--min-coverage", "0.99"]
    # A phrase that does not name the dropped sentence must not unlock it.
    assert applier.main(base + ["--acknowledge", "something else entirely"]) == 1
    assert "not carried by the replacement" in capsys.readouterr().err
    # Naming it does.
    assert applier.main(base + ["--acknowledge", first[:60]]) == 0
    assert "dry run" in capsys.readouterr().out


def test_forced_apply_warns_about_unacknowledged_drops(registry, capsys):
    target, write, (DS, BL) = registry
    proposal = write("forced.json", {
        "dataset": DS, "blocker": BL,
        "next_action_replacement": f"Keep working on {DS}.",
    })
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target), "--force"]) == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "uncovered sentence(s) applied without acknowledgement" in captured.err


def test_accepts_a_bare_list_of_per_dataset_records(registry, capsys):
    """w-metriccmp emits a bare JSON list with next_action_replacement per dataset."""
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    dataset, blocker_id, blocker = an_open_blocker(document)
    old = blocker["next_action"]
    proposal = write("list.json", [
        {"dataset": dataset, "blocker": blocker_id,
         "determination": "different",
         "observed_append": "LIST-SHAPE-MARKER.",
         "next_action_replacement": old + " LIST-SHAPE."},
    ])
    assert applier.main([str(proposal), "--dataset", dataset,
                         "--blocker", blocker_id, "--registry", str(target),
                         "--apply"]) == 0
    updated = a_blocker(json.loads(target.read_text(encoding="utf-8")),
                        dataset=dataset, blocker_id=blocker_id)
    assert updated["next_action"].endswith("LIST-SHAPE.")
    assert "LIST-SHAPE-MARKER." in updated["observed"]


def test_refuses_a_list_record_with_no_matching_dataset(registry, capsys):
    """A list record for a *different* blocker, applied with `--dataset/--blocker` naming this
    fixture's one, must be refused for having no change entry - not for naming an unknown
    blocker."""
    target, write, (DS, BL) = registry
    document = json.loads(target.read_text(encoding="utf-8"))
    other = [(row["dataset"], item["id"], item) for row in sorted(
        document["datasets"], key=lambda r: r["dataset"])
        for item in row["blockers"]
        if item["id"] != "retain-contract-refusal" and (row["dataset"], item["id"]) != (DS, BL)]
    if not other:
        # Only one non-refusal blocker is left in the live registry (`appworld`), so there is no
        # second live pair to take. This control does not actually need one: the applier must
        # refuse on "no change entry" *before* it ever resolves the name, which is the whole
        # point of the assertion below. So the distinct pair is synthesised rather than the
        # control being dropped or skipped - pinning it to a live second blocker is what broke
        # this test when `bfcl_v3/simple-ast-contract` was withdrawn on 2026-10-16.
        other = [("__control__", "not-a-real-blocker", {"next_action": "Synthetic control text."})]
    dataset, blocker_id, blocker = other[0]
    proposal = write("list2.json", [
        {"dataset": dataset, "blocker": blocker_id,
         "next_action_replacement": blocker["next_action"] + " X."},
    ])
    assert applier.main([str(proposal), "--dataset", DS, "--blocker", BL, "--registry", str(target)]) == 1
    assert "no change entry" in capsys.readouterr().err
