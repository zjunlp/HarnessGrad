"""Opt-in synthetic GSM8K fixture; never opens a provisioned dataset."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


#: A well-formed blocker that is **not** a live registry entry. It exists so the blocker-tooling
#: tests can exercise a real blocker without depending on how many the live registry happens to
#: carry. That dependency kept breaking: the mutation tests were pinned to `jericho`, then to
#: `bfcl_v3`, and the list-record control needed two open non-refusal blockers at once. On
#: 2026-10-16 the operator withdrew `bfcl_v3/simple-ast-contract` and
#: `triviaqa/hub-export-scorer-linkage`, leaving `appworld` as the only one; closing that too
#: would have left none and the controls would have stopped testing anything.
#:
#: It must satisfy `tools/check_dataset_blockers.py::_blocker` exactly - the applier re-validates
#: the whole document after writing - so the evidence entry points at a tracked file and its
#: locator appears in that file verbatim.
SYNTHETIC_BLOCKER = {
    "id": "synthetic-fixture-blocker",
    "family": "measurement-coverage",
    "status": "measurement",
    "observed": "Synthetic fixture entry. It is not a live requirement and is not in the real "
                "registry; it is attached to a published dataset only inside a private copy.",
    "evidence": [{"kind": "repository", "path": "AGENTS.md",
                  "locator": "## Dataset taxonomy"}],
    "next_action": "Synthetic fixture obligation, used only to exercise the blocker tooling. "
                   "It is not a live requirement and it authorizes nothing.",
}


@pytest.fixture
def synthetic_blocker():
    """A copy of `SYNTHETIC_BLOCKER`, so a test that mutates it cannot leak into another."""
    import copy

    return copy.deepcopy(SYNTHETIC_BLOCKER)


@pytest.fixture
def attach_synthetic_blocker():
    """Attach `SYNTHETIC_BLOCKER` to a **published** dataset inside a private document copy.

    It cannot live on a synthetic dataset: `checker.validate` requires every registry dataset to
    be a published declaration whose `demand`, `surface` and `exercises` match
    `tools/dataset_classes.py`, so an invented name is refused before the blocker is ever read.
    `agentdojo` sorts first among the published names, which keeps the choice deterministic.
    """
    import copy

    def attach(document, dataset="agentdojo"):
        for row in document["datasets"]:
            if row["dataset"] == dataset:
                row["blockers"].append(copy.deepcopy(SYNTHETIC_BLOCKER))
                return (dataset, SYNTHETIC_BLOCKER["id"])
        raise AssertionError(f"{dataset} is not a published dataset in this registry")

    return attach


@pytest.fixture
def site_archive():
    """Optional local copy of exported site sources; never fetch external references.

    `/site` citations are placeholders, not verified on-disk repository evidence.
    Keep source-content regressions runnable with an explicitly provisioned,
    ignored packet without making a fresh clone depend on those private records.
    """
    def source(reference):
        relative = Path(reference).relative_to("/site")
        assert ".." not in relative.parts
        path = ROOT / "tests/fixtures/hg_migration_site" / relative
        if not path.is_file():
            pytest.skip(f"site source packet absent from this clone: {reference}")
        return path
    return source


@pytest.fixture
def blocker_repository_evidence():
    """Only whole-registry checks need the ignored hg_* observation packets."""
    import json

    registry = json.loads((ROOT / "registry/dataset-blockers.json").read_text())
    owners = registry["shared_blockers"] + [
        blocker for row in registry["datasets"] for blocker in row["blockers"]]
    missing = sorted({item["path"] for owner in owners for item in owner["evidence"]
                      if item["kind"] == "repository"
                      and item["path"].startswith("tests/fixtures/hg_")
                      and not (ROOT / item["path"]).is_file()})
    if missing:
        pytest.skip("ignored blocker observation packet absent from this clone: "
                    + ", ".join(missing))


@pytest.fixture(scope="module")
def synthetic_gsm8k():
    """Build an isolated adapter using three synthetic test and five train rows.

    Split names exercise the adapter contract; these are authored unit-test data,
    not released questions or benchmark measurements.
    """
    sys.path.insert(0, str(ROOT))
    from data.scaffolds.qa import base

    answers = {
        "test": ("18", "3.5", "-3.25"),
        "train": ("42", "1250", "-3", "7000", "18.0"),
    }
    tables = {
        split: [
            {"question": f"Synthetic {split} question {i}: compute a value.",
             "answer": f"Synthetic solution for {split} row {i}.\n#### {answer}"}
            for i, answer in enumerate(values)
        ]
        for split, values in answers.items()
    }
    reads = []

    def synthetic_rows(path):
        split = Path(path).name.split("-", 1)[0]
        assert split in tables, path
        reads.append(split)
        return tables[split]

    spec = importlib.util.spec_from_file_location(
        "gsm8k_synthetic_contract", ROOT / "data" / "datasets" / "gsm8k.py")
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "parquet_rows", synthetic_rows)
        spec.loader.exec_module(module)
    assert reads == ["test", "train"]
    assert module._LOAD_ERROR is None
    # Keep any later table rebuild confined to the same synthetic rows.
    module._SPEC._rows = lambda: tables
    return module
