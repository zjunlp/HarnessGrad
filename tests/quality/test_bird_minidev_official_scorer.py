"""BIRD Mini-Dev: the official EX transcription, the prompt, and the two released golds.

The first version of this file asserted that the *test* could compare two lists and that
`732 == 732`; it never touched the adapter's own comparison, never read the prompt, and read
an absolute path with no skip. What the gates need here is narrower and sharper:

* **gate 3** -- the check's `calculate_ex` is the *bytes* of
  `bird-bench/mini_dev@abd11b6 evaluation/evaluation_ex.py`, compared against the pinned
  archive, and upstream's own function loaded from that archive agrees with the check on a
  grid of result sets;
* **the prompt** -- every task ships the schema of *its own* database, rendered from the
  release's `dev_tables.json`, and ships neither the gold query nor the database;
* **the released golds** -- the question JSON's `SQL` and the `.sql` file agree for all 500
  rows, because the adapter reads one and the official evaluator reads the other;
* **"cannot measure" is not "wrong"** -- the check exits 3 with no reward file when the
  *environment* is broken and 0 with a reward file when the *agent* is. Both paths are
  exercised here on a synthetic database, so the distinction is tested, not asserted;
* **an unprovisioned machine** -- importing the adapter without its data leaves `_LOAD_ERROR`
  set instead of raising, which is the regression the first version introduced (the dataset
  silently disappeared from `registry.published()`).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import data.datasets.bird_minidev as dataset  # noqa: E402

ADAPTER = ROOT / "data" / "datasets" / "bird_minidev.py"
CHECK = ROOT / "data" / "datasets" / "bird_minidev" / "verify" / "check.py"
CLI = ROOT / "data" / "datasets" / "bird_minidev" / "cli" / "bird"
ARCHIVE = dataset.DATASET_ROOT / "_src" / "evaluation.tar.gz"
MEMBER = f"mini_dev-{dataset.EVALUATION_COMMIT}/evaluation/evaluation_ex.py"


# ------------------------------------------------------------------ the pinned release ---


def test_the_zip_is_the_declared_release():
    """Both digests in one pass: sha256 is the pin and md5 is the object's ETag, which is what
    the download URL itself can be checked against (`Last-Modified` supplies the date half of
    `RELEASE`)."""
    archive = dataset.DATASET_ROOT / "_src" / "minidev.zip"
    if not archive.is_file():
        pytest.skip("bird_minidev: fetch the declared release")
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with archive.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            sha.update(block)
            md5.update(block)
    assert sha.hexdigest() == dataset.DATA_SHA256
    assert md5.hexdigest() == dataset.DATA_MD5
    assert dataset.RELEASE.endswith(dataset.DATA_MD5.upper())


def test_the_two_released_gold_channels_agree():
    """The adapter reads `mini_dev_sqlite_gold.sql`; the question JSON also carries `SQL`, and
    the official evaluator splits the `.sql` lines on a tab. All three must agree, or the gold
    the platform scores against is not the gold upstream scores against."""
    questions = json.loads(dataset.QUESTIONS.read_text(encoding="utf-8"))
    lines = dataset.GOLD.read_text(encoding="utf-8").splitlines()
    assert len(questions) == len(lines) == dataset.EXPECTED_ROWS == 500
    for question, line in zip(questions, lines):
        assert line.count("\t") == 1, line
        sql, db_id = line.split("\t")
        assert db_id == question["db_id"]
        assert " ".join(sql.split()) == " ".join(question["SQL"].split())
    assert [row["gold_sql"] for row in dataset._rows()] == [line.split("\t")[0] for line in lines]


def test_the_schema_is_the_release_schema_for_every_database():
    """The defect this rewrite exists for: the prompt used to ship `{"db_id", "evidence"}`
    under the name `schema.json` -- no tables, no columns -- and no database either, so no
    harness could answer. The official schema is in the release (`dev_tables.json`), and it is
    what the agent gets now."""
    tables = json.loads(dataset.TABLES.read_text(encoding="utf-8"))
    assert len(tables) == 11
    for entry in tables:
        text = dataset.schema_text(entry["db_id"])
        assert text.count("CREATE TABLE") == len(entry["table_names_original"])
        for table in entry["table_names_original"]:
            assert f"CREATE TABLE {table} (" in text
        for table_index, name in entry["column_names_original"]:
            if table_index >= 0:
                assert re.search(rf"^\s+{re.escape(name)}\s", text, re.M), (entry["db_id"], name)
        assert entry["db_id"] in text.splitlines()[0]


def test_every_task_ships_its_own_schema_and_the_cli():
    dbs = sorted({record["db_id"] for record in dataset._RECORDS})
    assert len(dbs) == 11
    for db_id in dbs:
        task_id = next(r["task_id"] for r in dataset._RECORDS if r["db_id"] == db_id)
        files = dataset.SETUP[task_id]["files"]
        assert [entry["path"] for entry in files] == ["TASK.md", "schema.sql", "bird/bin/bird"]
        assert files[1]["content"] == dataset.schema_text(db_id)
        assert Path(files[2]["from"]) == CLI and CLI.is_file()


def test_the_prompt_carries_no_grade_and_no_database():
    """`difficulty` is the benchmark's own annotation and travels in the gold; the database
    holds the answer rows and travels only in `VERIFY.inputs`."""
    record = dataset._RECORDS[0]
    setup = json.dumps(dataset.SETUP[record["task_id"]])
    assert record["gold_sql"] not in setup
    assert "difficulty" not in setup and record["difficulty"] not in setup
    assert record["question"] in setup
    paths = [entry["path"] for entry in dataset.SETUP[record["task_id"]]["files"]]
    assert not any("sqlite" in path for path in paths)
    sources = [entry.get("from", "") for entry in dataset.VERIFY[record["task_id"]]["inputs"]]
    assert any(source.endswith(f"{record['db_id']}.sqlite") for source in sources)


# -------------------------------------------------------------- gate 3: the scorer ---


def _upstream_source() -> str:
    if not ARCHIVE.is_file():
        pytest.skip("bird_minidev: fetch the declared evaluation archive")
    with tarfile.open(ARCHIVE) as tar:
        return tar.extractfile(MEMBER).read().decode()


def _upstream_calculate_ex_source() -> str:
    """Just the function, from upstream's bytes.

    Not the whole file: `evaluation_ex.py` imports `evaluation_utils` and `func_timeout` at
    module level, and neither is needed to run a four-line comparison. Slicing the source is
    what lets the differential call *upstream's own code* rather than a paraphrase of it.
    """
    source = _upstream_source()
    return source[source.index("def calculate_ex("):source.index("def execute_model(")].rstrip()


def test_the_transcription_is_upstreams_bytes():
    assert CHECK.read_text(encoding="utf-8").count(_upstream_calculate_ex_source()) == 1


def _calculate_ex_from(source: str, name: str):
    namespace: dict = {"__file__": name}
    exec(compile(source, name, "exec"), namespace)
    return namespace["calculate_ex"]


@pytest.mark.parametrize("predicted,gold,expected", [
    ([(1,), (2,)], [(2,), (1,)], 1),          # order is irrelevant: set equality
    ([(1,), (1,)], [(1,)], 1),                # duplicates collapse, as upstream's `set` does
    ([(1,)], [(1,), (2,)], 0),
    ([], [], 1),
    ([(1,)], [], 0),
    ([("a", None)], [("a", None)], 1),
])
def test_ex_differential_against_the_pinned_source(predicted, gold, expected):
    """Upstream's own function, executed from the archive, on the same inputs."""
    upstream = _calculate_ex_from(_upstream_calculate_ex_source(), MEMBER)
    ours = _calculate_ex_from(CHECK.read_text(encoding="utf-8"), str(CHECK))
    assert upstream(predicted, gold) == expected
    assert ours(predicted, gold) == expected


# --------------------------------------------------- the check, on a synthetic database ---


@pytest.fixture
def world(tmp_path) -> Path:
    """A two-row database, so the check's four outcomes are cheap to exercise."""
    database = tmp_path / "database.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE numbers (value INTEGER)")
    connection.executemany("INSERT INTO numbers VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    (tmp_path / "gold.sql").write_text("SELECT value FROM numbers\n")
    (tmp_path / "check.py").write_text(CHECK.read_text(encoding="utf-8"))
    # Mirror VERIFY.inputs: a copied scorer cannot import the platform's helper
    # unless the fixture stages it, just as the adapter's bound spec does.
    helper = tmp_path / "data/scaffolds"
    helper.mkdir(parents=True)
    (helper.parent / "__init__.py").write_text("")
    (helper / "__init__.py").write_text("")
    (helper / "scorer_input.py").write_bytes((ROOT / "data/scaffolds/scorer_input.py").read_bytes())
    return tmp_path


def _run_check(work: Path) -> tuple[int, str | None, dict]:
    proc = subprocess.run([sys.executable, "check.py", "--db", "database.sqlite",
                           "--gold", "gold.sql", "--pred", "answer.sql",
                           "--reward-file", "verifier/reward.txt",
                           "--evidence-file", "verifier/evidence.json"],
                          cwd=work, capture_output=True, text=True, timeout=120)
    reward_file = work / "verifier" / "reward.txt"
    reward = reward_file.read_text().strip() if reward_file.is_file() else None
    evidence = json.loads((work / "verifier" / "evidence.json").read_text())
    return proc.returncode, reward, evidence


def test_the_check_scores_the_gold_and_rejects_a_wrong_query(world):
    (world / "answer.sql").write_text("SELECT value FROM numbers\n")
    code, reward, evidence = _run_check(world)
    assert (code, reward, evidence["measured"], evidence["reward"]) == (0, "1", True, 1.0)
    (world / "answer.sql").write_text("SELECT value FROM numbers WHERE value = 1\n")
    code, reward, evidence = _run_check(world)
    assert (code, reward, evidence["measured"], evidence["reward"]) == (0, "0", True, 0.0)
    # A query that does not run is a wrong answer, not an unmeasurable one (upstream: res = 0).
    (world / "answer.sql").write_text("SELECT nope FROM numbers\n")
    code, reward, evidence = _run_check(world)
    assert (code, reward, evidence["measured"]) == (0, "0", True)
    assert "did not run" in evidence["why"]


def test_a_missing_answer_is_a_measured_zero_not_an_invalid_task(world):
    """The platform's rule: the task ran and the agent did not answer."""
    code, reward, evidence = _run_check(world)
    assert (code, reward, evidence["measured"], evidence["reward"]) == (0, "0", True, 0.0)
    assert "no answer" in evidence["why"]


def test_a_broken_environment_writes_no_reward_file(world):
    """Upstream catches this in the same `try` as the prediction and scores 0. Here it is
    `measured: false` and exit 3, because a dataset defect and a wrong answer must not look
    alike in a curve -- the distinction `docs/dataset-onboarding-skill.md` §5 is about."""
    (world / "gold.sql").write_text("SELECT missing_column FROM numbers\n")
    (world / "answer.sql").write_text("SELECT value FROM numbers\n")
    code, reward, evidence = _run_check(world)
    assert code == 3 and reward is None
    assert evidence["measured"] is False and "cannot measure" in evidence["error"]


def test_the_module_imports_without_its_data(tmp_path):
    """The regression this rewrite fixes: the table was read unguarded at import, so an
    unprovisioned machine lost the dataset from `registry.published()` (27 -> 26) instead of
    reporting it. The module body must survive a missing root and carry the reason into
    `load()`."""
    source = ADAPTER.read_text(encoding="utf-8")
    patched = re.sub(r'DATASET_ROOT = Path\("[^"]*"\)',
                     f'DATASET_ROOT = Path({str(tmp_path)!r})', source, count=1)
    assert patched != source, "the DATASET_ROOT line moved; fix this test rather than the regex"
    namespace = {"__name__": "data.bird_minidev_unprovisioned", "__file__": str(ADAPTER)}
    exec(compile(patched, str(ADAPTER), "exec"), namespace)   # must not raise
    assert namespace["_RECORDS"] == []
    assert "FileNotFoundError" in namespace["_LOAD_ERROR"]
    with pytest.raises(ValueError) as excinfo:
        namespace["load"]()
    assert "--fetch --dataset bird_minidev" in str(excinfo.value)
    # No division at all -- not an empty pair of sides: the benchmark states none.
    assert namespace["SPLIT"] is None
    assert namespace["SPLIT_SOURCE"] == "none" and namespace["OFFICIAL_EVAL"] is None


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_platform_control(arm, expected):
    """Gates 1 and 2 through `eval.run.runner.run_one` with the sandbox on, which is where a
    `VERIFY.inputs` path error would show up."""
    proc = subprocess.run([sys.executable, "data/datasets/bird_minidev/control.py", arm, "--platform"],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"score    {expected}" in proc.stdout
    assert "sandbox=True" in proc.stdout
