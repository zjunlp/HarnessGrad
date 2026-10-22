"""The machine-checkable half of the dataset contract, run as a suite.

`docs/dataset-onboarding-skill.md` records the judgement lessons -- the ones that need
a person. This file is the other half: the invariants a program can decide. It runs
`tools/dataset_lint.py` over every published dataset, and over a **deliberately broken
copy** so that the checks are shown to fail rather than assumed to.

Why a broken copy and not only the real datasets
------------------------------------------------
A rule that has never rejected anything is indistinguishable from a rule that cannot.
Measured on this platform already: a check that counted requests was satisfied by the
platform's own health check, so it passed for a harness that never made a call. The
broken copy below is the same idea applied to the lint -- each assertion names the
violation it introduced.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import data.scaffolds.registry as registry  # noqa: E402
import dataset_lint  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return dataset_lint.load_schema()


def test_lint_accepts_every_published_dataset(schema):
    """The datasets the platform offers, linted with the same rules a new one faces."""
    checked, skipped = [], []
    for name in registry.published():
        result = dataset_lint.lint_dataset(name, schema)
        if result.unprovisioned:
            skipped.append(f"{name} ({result.unprovisioned})")
            continue
        assert result.ok, f"{name}: " + "; ".join(result.errors)
        checked.append(name)
    assert checked, f"nothing was linted; skipped: {skipped}"
    assert "alfworld" in checked


#: A module that breaks one rule per line, so a failure names its own cause. It is
#: written into `data/` only for the duration of the test: `lint_dataset` imports
#: `data.datasets.<name>`, and a copy that is not imported as a dataset is not a copy.
BROKEN_SOURCE = '''\
"""Deliberately broken dataset, written by tests/quality/test_dataset_lint.py."""
KIND = "datset"                       # not a known kind

DATASET_ROOT = {platform_root!r}      # data inside the platform tree

def load():
    # `goal` missing, duplicate task id, and a third task with no verify entry at all.
    return ([{{"task_id": "a"}}, {{"task_id": "a"}},
             {{"task_id": "c", "goal": "g"}}], {{"a": ""}})

VERIFY = {{
    "a": {{
        "kind": "command",
        "argv": ["true"],
        # no `reward_file`; nested `dst`; neither `content` nor `from`.
        "inputs": [{{"dst": "/a/b"}}],
    }},
}}

def downloads():
    return [{{
        "kind": "http", "name": "thing", "version": "main", "size": "1 MB",
        "note": "too short",
        # `url` and `dst` missing
    }}]
'''


def test_lint_rejects_a_deliberately_broken_copy(schema, tmp_path):
    """Every rule fires on the copy that breaks it, and the failure says which one.

    The broken copy is imported as `data._lint_broken_copy` without ever being written
    into `data/`: its directory is appended to the package's `__path__` instead, so
    `importlib` resolves the name exactly as it would for a real adapter while nothing
    appears in the checkout. Writing it there and unlinking it again -- which this test
    used to do -- put a file into a tree the platform hashes around every run and every
    concurrent test process walks, so for a moment another file could see a module that
    was about to vanish (`FileNotFoundError`), or the integrity check could report a
    change nobody made.
    """
    name = "_lint_broken_copy"
    path = tmp_path / f"{name}.py"
    path.write_text(BROKEN_SOURCE.format(platform_root=str(ROOT)))
    package = importlib.import_module("data.datasets")
    package.__path__.append(str(tmp_path))
    importlib.invalidate_caches()
    try:
        result = dataset_lint.lint_dataset(name, schema)
    finally:
        package.__path__.remove(str(tmp_path))
        sys.modules.pop(f"data.datasets.{name}", None)
        importlib.invalidate_caches()

    assert not (ROOT / "data" / "datasets" / f"{name}.py").exists(), \
        "the fixture must not touch the checkout"

    assert not result.ok, "the broken copy passed the lint"
    joined = "\n".join(result.errors)
    for fragment in (
        "KIND must be one of",                    # wrong kind
        "inside the platform tree",               # data root in the tree
        "duplicate task_id",                      # duplicate id
        "missing ['goal']",                       # incomplete task
        "have no VERIFY entry",                   # uncovered task
        "without `reward_file`",                  # no reward file
        "is nested",                              # nested dst
        "neither `content` nor `from`",           # unusable input
        "is not pinned",                          # version=main
        "is missing ['url', 'dst']",              # incomplete http entry
        "plain-language note",                    # note too short
        "`ORACLE_SOURCE` must be one of",         # oracle provenance not declared
    ):
        assert fragment in joined, f"lint missed {fragment!r}:\n{joined}"


def test_the_oracle_rule_refuses_silence_and_an_unvalidated_authorship(schema):
    """The rule the authored-reference contract rests on, exercised on the rule itself.

    `test_lint_accepts_every_published_dataset` is the integration half: it forces all 19
    runnable adapters to declare `ORACLE_SOURCE`. This is the unit half, because the cases
    that matter are the ones no shipped dataset has -- an author who wrote a reference by
    hand and said nothing (silence defaults to nothing, deliberately), and one who said
    `authored` without naming what validates it or how much it covers.
    """
    from types import SimpleNamespace

    def lint(**attributes):
        result = dataset_lint.Result("probe")
        dataset_lint.lint_oracle(SimpleNamespace(**attributes), schema, result)
        return result

    silent = lint()
    assert not silent.ok, "silence about the oracle passed the lint"
    assert "ORACLE_SOURCE" in silent.errors[0]
    assert "released" in silent.errors[0] and "authored" in silent.errors[0]

    assert not lint(ORACLE_SOURCE="donated").ok
    assert lint(ORACLE_SOURCE="released").ok

    unvalidated = lint(ORACLE_SOURCE="authored",
                       ORACLE_VALIDATED_BY="the released tests")
    assert not unvalidated.ok and "ORACLE_COVERAGE" in unvalidated.errors[0]
    uncovered = lint(ORACLE_SOURCE="authored", ORACLE_COVERAGE="3 of 1055 tasks")
    assert not uncovered.ok and "ORACLE_VALIDATED_BY" in uncovered.errors[0]
    assert lint(ORACLE_SOURCE="authored", ORACLE_VALIDATED_BY="the released tests",
                ORACLE_COVERAGE="3 of 1055 tasks").ok


def test_the_broken_copy_is_gone_and_the_real_datasets_still_pass(schema):
    """The synthetic copy must not have left a module behind for `published()`.

    Kept as a separate assertion now that the copy never enters the checkout: it pins
    that extending `data.datasets.__path__` for the fixture left no residue in the package, which
    is the way this test could still go wrong.
    """
    assert not (ROOT / "data" / "datasets" / "_lint_broken_copy.py").exists()
    assert "_lint_broken_copy" not in registry.published()
    assert dataset_lint.lint_dataset("alfworld", schema).ok
