"""The AppWorld adapter's two split knobs: a study file, and which pool is the eval side.

Why this test exists: the adapter used to fix both sides at import -- study on train+dev,
score on both test files -- so a run could not express the protocol the platform measures
every dataset on: study on the official train split, score each checkpoint on the official
dev split, and hold the test splits back for one final exam.  `HG_APPWORLD_STUDY_IDS` and
`HG_APPWORLD_POOL` separate those three stages, and this file pins what each one does,
including the two refusals: an unknown study id and a `train` pool.

The expected sets come from the release's own split files (`split_ids()`), not from the
adapter's pool, because the claim being tested is that the knobs select official tasks
rather than tasks this adapter invented.

**Why reload and not a subprocess.**  Both knobs are read when the module is imported, so
each case sets its environment with `monkeypatch` and re-imports.  That mutates a module
other test files also import, so an autouse fixture puts it back on its defaults after
every case; leaving it on `pool=dev` would silently change what the official-scorer test
file measures.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.datasets.appworld as appworld  # noqa: E402

STUDY_KNOB = "HG_APPWORLD_STUDY_IDS"
POOL_KNOB = "HG_APPWORLD_POOL"


@pytest.fixture(scope="module")
def official() -> dict[str, list[str]]:
    """The release's four declared task lists, decrypted from the official bundle."""
    return appworld.split_ids()


@pytest.fixture(autouse=True)
def _back_to_defaults(monkeypatch):
    """Re-import the adapter under a clean environment once the case is over."""
    yield
    for name in (STUDY_KNOB, POOL_KNOB):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(appworld)


def _reload(monkeypatch, **env):
    """Import the adapter with exactly `env` set, so one case cannot leak into the next."""
    for name in (STUDY_KNOB, POOL_KNOB):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    importlib.reload(appworld)
    return appworld


def _ids_by_split(module) -> dict[str, list[str]]:
    """The release ids per official split, in the order this adapter stores them."""
    ids: dict[str, list[str]] = {}
    for task_id, row in module._ROWS.items():
        ids.setdefault(row["split"], []).append(task_id)
    return ids


def test_without_the_knobs_the_two_way_split_is_unchanged(monkeypatch, official):
    """The default must stay the sides this adapter has always reported.

    Byte-for-byte, not just in sizes: a run recorded before the knobs existed carries a
    `split_sha`, and moving one id between sides would make its scores incomparable with
    every later default run.
    """
    module = _reload(monkeypatch)
    assert module._LOAD_ERROR is None
    rows = list(module._ROWS.values())
    assert module.SPLIT == {
        "train": [row["task_id"] for row in rows if row["split"] in {"train", "dev"}],
        "eval": [row["task_id"] for row in rows if row["split"] in {"test_normal",
                                                                   "test_challenge"}],
    }
    assert (len(module.SPLIT["train"]), len(module.SPLIT["eval"])) == (147, 585)
    assert set(module.SPLIT["train"]) == set(official["train"] + official["dev"])
    assert set(module.SPLIT["eval"]) == set(official["test_normal"]
                                            + official["test_challenge"])
    assert module.OFFICIAL_EVAL == module.SPLIT["eval"]
    # The run sees every released task, because the two sides cover the whole release.
    tasks, _ = module.load()
    assert [task["task_id"] for task in tasks] == list(module._ROWS)


def test_a_study_file_is_the_study_side_in_file_order(monkeypatch, official, tmp_path):
    """The file is the study side: no sampling, no reordering, no extra ids.

    Order is a property a reader can diff against the file, which is why it is asserted
    against the file's own order rather than against the release's.
    """
    study = tmp_path / "study.txt"
    named = [official["train"][4], official["train"][1], official["train"][7]]
    study.write_text("\n".join(named) + "\n", encoding="utf-8")

    module = _reload(monkeypatch, **{STUDY_KNOB: study})
    assert module._LOAD_ERROR is None
    assert module.SPLIT["train"] == named
    assert set(module.SPLIT["eval"]) == set(official["test_normal"]
                                            + official["test_challenge"])
    tasks, _ = module.load()
    loaded = {task["task_id"] for task in tasks}
    assert loaded == set(named) | set(module.SPLIT["eval"])
    # A train task the file did not name is on neither side, so it is not in this run.
    unnamed = set(official["train"]) - set(named)
    assert not (unnamed & loaded)


@pytest.mark.parametrize("content", ["", "\n", " , \n"])
def test_a_study_file_that_names_no_task_is_refused(monkeypatch, tmp_path, content):
    """An empty file is a failed job, not a study set of zero tasks."""
    study = tmp_path / "study.txt"
    study.write_text(content, encoding="utf-8")
    module = _reload(monkeypatch, **{STUDY_KNOB: study})
    with pytest.raises(ValueError, match="names no task ids"):
        module.load()


def test_an_unknown_study_id_is_refused(monkeypatch, official, tmp_path):
    """Loudly, because a study set that silently dropped an id changed what was studied.

    The refusal is raised by `load()` rather than at import: `published()` hides a module
    that will not import, and a typo in an environment variable must name itself instead
    of deleting the dataset from the chooser.
    """
    study = tmp_path / "study.txt"
    study.write_text(official["train"][0] + "\nno-such-task_1\n", encoding="utf-8")
    module = _reload(monkeypatch, **{STUDY_KNOB: study})
    with pytest.raises(ValueError, match="no-such-task_1"):
        module.load()


def test_pool_dev_makes_the_official_dev_split_the_eval_side(monkeypatch, official):
    """This is the checkpoint protocol: dev is scored, and therefore not studied."""
    module = _reload(monkeypatch, **{POOL_KNOB: "dev"})
    assert module._LOAD_ERROR is None
    ids = _ids_by_split(module)
    assert module.SPLIT["eval"] == ids["dev"]
    assert len(module.SPLIT["eval"]) == 57
    assert set(module.SPLIT["eval"]) == set(official["dev"])
    # dev cannot be both; the default study side narrows to the official train split.
    assert module.SPLIT["train"] == ids["train"]
    assert set(module.SPLIT["train"]) == set(official["train"])
    assert len(module.load()[0]) == 90 + 57
    # A dev pool holds none of the set the published numbers are computed on, so
    # `--split official` has an empty eval side and is refused rather than invented.
    assert module.OFFICIAL_EVAL == []


def test_a_study_file_and_the_dev_pool_are_the_checkpoint_protocol(
        monkeypatch, official, tmp_path):
    """The two knobs compose: a named study set, dev as the scored split, exam untouched.

    This is the run a checkpoint measurement is: the study file is the method's material,
    dev is what every stored harness state is scored on, and no test task is loaded.
    """
    study = tmp_path / "study.txt"
    named = official["train"][:5]
    study.write_text(",".join(named) + "\n", encoding="utf-8")

    module = _reload(monkeypatch, **{STUDY_KNOB: study, POOL_KNOB: "dev"})
    assert module._LOAD_ERROR is None
    assert module.SPLIT["train"] == named
    assert set(module.SPLIT["eval"]) == set(official["dev"])
    loaded = {task["task_id"] for task in module.load()[0]}
    assert loaded == set(named) | set(official["dev"])
    assert not (loaded & set(official["test_normal"] + official["test_challenge"]))


def test_pool_test_is_the_union_of_the_two_test_files(monkeypatch, official):
    """`test` is the default and the final exam; naming it must select the same tasks."""
    module = _reload(monkeypatch, **{POOL_KNOB: "test"})
    assert set(module.SPLIT["eval"]) == set(official["test_normal"]
                                            + official["test_challenge"])
    assert len(module.SPLIT["eval"]) == 585
    assert module.SPLIT["eval"] == module.OFFICIAL_EVAL


@pytest.mark.parametrize("pool,size", [("test_normal", 168), ("test_challenge", 417)])
def test_one_test_file_can_be_the_pool_alone(monkeypatch, official, pool, size):
    module = _reload(monkeypatch, **{POOL_KNOB: pool})
    assert module.SPLIT["eval"] == _ids_by_split(module)[pool]
    assert set(module.SPLIT["eval"]) == set(official[pool])
    assert len(module.SPLIT["eval"]) == size


def test_a_repeated_study_id_is_refused(tmp_path, monkeypatch):
    """The study score is a mean over tasks; a duplicate would weight one task twice."""
    study = tmp_path / "study.txt"
    study.write_text("07b42fd_1\n07b42fd_1\n", encoding="utf-8")
    module = _reload(monkeypatch, HG_APPWORLD_STUDY_IDS=str(study), HG_APPWORLD_POOL="dev")
    with pytest.raises(ValueError, match="more than once"):
        module.load()


def test_pool_train_is_refused(monkeypatch):
    """A score on the training split measures the material the method studied."""
    module = _reload(monkeypatch, **{POOL_KNOB: "train"})
    with pytest.raises(ValueError, match="HG_APPWORLD_POOL=train") as caught:
        module.load()
    assert "not an evaluation" in str(caught.value)


def test_a_misspelled_pool_names_the_pools_that_exist(monkeypatch):
    """A typo must not fall back to the default pool: that would be silent."""
    module = _reload(monkeypatch, **{POOL_KNOB: "tests"})
    with pytest.raises(ValueError, match="not an official split") as caught:
        module.load()
    for pool in module.EVAL_POOLS:
        assert pool in str(caught.value)
