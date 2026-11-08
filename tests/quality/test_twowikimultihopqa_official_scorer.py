"""Offline authored loader/metric controls and explicit 2Wiki release integrations.

HG_TEST_TWOWIKI_DATA_ROOT opts into existing release/source/witness artifacts.
Historical full-pool counts and measured aggregates remain integration assertions;
default tests never infer that an authored fixture proves release provenance.
"""
from __future__ import annotations

import hashlib
import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from stdlib_authored_fixture_helpers import adapter_at

# A private module instance cannot inherit another test's cached pool or ambient storage.
dataset = adapter_at('twowikimultihopqa', Path('/__hg_authored_unprepared_twowiki__'))
ADAPTER = ROOT / 'data/datasets/twowikimultihopqa.py'


def require_files(root, *relative):
    missing = [str(root / item) for item in relative if not (root / item).is_file()]
    if missing:
        pytest.skip('2Wiki explicit integration root missing files: ' + ', '.join(missing))


@pytest.fixture
def release_root(monkeypatch):
    selected = os.environ.get('HG_TEST_TWOWIKI_DATA_ROOT')
    if not selected:
        pytest.skip('released 2Wiki opt-in: set HG_TEST_TWOWIKI_DATA_ROOT to an existing pinned data/source root')
    root = Path(selected).expanduser()
    if not root.is_dir():
        pytest.skip('2Wiki explicit integration root missing directory: ' + str(root))
    monkeypatch.setattr(dataset, 'DATASET_ROOT', root)
    monkeypatch.setattr(dataset, '_ALIASES', None)
    return root


@pytest.fixture
def source(release_root):
    relative = '_src/2wikimultihop-' + dataset.OFFICIAL_COMMIT + '/2wikimultihop_evaluate_v1.1.py'
    require_files(release_root, relative)
    path = release_root / relative
    assert hashlib.sha256(path.read_bytes()).hexdigest() == dataset.EVALUATOR_SHA256
    return path


@pytest.fixture
def synthetic_rows(tmp_path, monkeypatch):
    """Authored dev+alias bytes run through the unchanged JSON/JSONL loader."""
    rows = [{'_id': 'authored-' + str(i), 'question':'Authored question.',
             'answer':'The Crimson City', 'answer_id':'Q_answer', 'type':'comparison',
             'context':[['Public Page ' + str(n), ['Public sentence.']] for n in range(10)],
             'supporting_facts':[['Public Page 0',0]],
             'evidences':[['Subject','relation','Object']], 'evidences_id':[]}
            for i in range(2)]
    (tmp_path / dataset.DATA_FILE).write_text(json.dumps(rows))
    (tmp_path / dataset.ALIAS_FILE).write_text(json.dumps({
        'Q_id':'Q_answer','aliases':['PRIVATE_ALIAS'], 'demonyms':['Crimson Citizens']}) + '\n')
    monkeypatch.setattr(dataset,'DATASET_ROOT',tmp_path)
    monkeypatch.setattr(dataset,'EXPECTED_ROWS',2)
    monkeypatch.setattr(dataset,'_ALIASES',None)
    monkeypatch.setattr(dataset._SPEC,'root',tmp_path)
    monkeypatch.setattr(dataset._SPEC,'_records',[])
    monkeypatch.setattr(dataset._SPEC,'load_error',None)
    monkeypatch.setattr(dataset._SPEC,'_load_cause',None)
    loaded = dataset._rows()['dev']
    assert loaded == rows
    tasks, _ = dataset.load()
    assert len(tasks) == len(rows)
    assert 'PRIVATE_ALIAS' not in json.dumps(tasks)
    return loaded


#: The deterministic prediction rule the end-to-end comparison uses. Fixed here so that the
#: two aggregates below are a property of the pinned data, not of an agent.
SLICE = 500


@pytest.fixture
def rows(release_root) -> list[dict]:
    require_files(release_root, dataset.DATA_FILE, dataset.ALIAS_FILE)
    for relative, expected in ((dataset.DATA_FILE,dataset.DATA_SHA256),(dataset.ALIAS_FILE,dataset.ALIAS_SHA256)):
        assert hashlib.sha256((release_root / relative).read_bytes()).hexdigest() == expected
    return dataset._rows()["dev"]


@pytest.fixture
def predictions(rows) -> dict[str, str]:
    """Every fifth answer empty, even rows the gold verbatim, odd rows its first token."""
    return {row["_id"]: ("" if i % 5 == 0 else
                         row["answer"] if i % 2 == 0 else row["answer"].split()[0])
            for i, row in enumerate(rows[:SLICE])}


def _aggregate(rows, predictions, *, use_aliases: bool = True) -> tuple[float, float]:
    """The official aggregate: per-question max, summed, over N, times 100, rounded to 2.

    The accumulation is a plain `+=` in **gold-file order**, because that is upstream's
    (`metrics['f1'] += max_f1`, `round(metrics[k] / N * 100, 2)`) and a published
    2-decimal metric is sensitive to the order and to the rounding point. `score()`
    reproduces it because it calls the same functions; this function exists to be an
    independent second path to the same number.
    """
    em_total = f1_total = 0.0
    for row in rows:
        gold = dataset._gold(row)
        if not use_aliases:
            gold = {**gold, "aliases": []}
        verdict = dataset.score(predictions[row["_id"]], gold)
        em_total += float(verdict["correct"])
        f1_total += verdict["reward"]
    n = len(rows)
    return round(em_total / n * 100, 2), round(f1_total / n * 100, 2)


# ------------------------------------------------------------- the transcription ---


def test_transcribed_scorer_region_has_its_reviewed_digest():
    source_text = ADAPTER.read_text()
    region = source_text[source_text.index('def normalize_answer(s):'):
                         source_text.index('# ------------------------------------------------------------------- the platform')]
    assert hashlib.sha256(region.encode()).hexdigest() == 'e18ccdf4b87d16c30e03a62b1dc9d0906c0b0130ae5eace17885d2a9ea1b13c9'


def test_the_source_file_is_the_pinned_one(source, release_root):
    require_files(release_root, "_src/2wikimultihop.tar.gz")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == dataset.EVALUATOR_SHA256
    archive = dataset.DATASET_ROOT / "_src" / "2wikimultihop.tar.gz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == dataset.SOURCE_SHA256


def test_the_transcription_is_byte_identical_to_upstream(source):
    """The whole region, as a substring of the adapter -- so an edit cannot hide.

    Sliced from `def normalize_answer` to the `__main__` block, which is the part of
    upstream that is the metric. The adapter's own module header is *above* the region and
    is where the single deliberate difference lives (`import json` for `import ujson as
    json`), which is why the region itself needs no substitution and can be compared
    exactly: a test that compares "after the edits I remember making" is a test that
    forgives the edit nobody remembers.
    """
    upstream = source.read_text()
    region = upstream[upstream.index("def normalize_answer(s):"):
                      upstream.index("if __name__ == '__main__':")]
    assert region.rstrip().endswith("print(json.dumps(metrics, indent=4))")
    assert ADAPTER.read_text().count(region) == 1


def test_legacy_answer_only_is_labeled_and_does_not_claim_joint():
    """Plain text retains Ans F1; full structured coverage is tested on synthetic rows."""
    for name in ("update_sp", "update_evi", "normalize_evi", "eval"):
        assert callable(getattr(dataset, name)), name
    verdict = dataset.score("anything", {"answer": "fabricated answer", "aliases": []})
    assert verdict["official"]["mode"] == "legacy_answer_only"
    assert "sp_f1" not in verdict["official"] and "joint_f1" not in verdict["official"]
    assert "Sup/Evi/Joint" in verdict["why"]


# ------------------------------------------------------- the upstream program runs ---


def test_the_upstream_evaluator_agrees_end_to_end(rows, predictions, tmp_path, source):
    """The original program, on this pool, with these predictions, and the same numbers.

    Nothing of this repository is on the path the subprocess sees except the two JSON
    files, so the only way the two can agree is if the adapter's `score()` and upstream's
    `eval()` are the same metric.
    """
    pytest.importorskip("ujson", reason="upstream imports ujson; the adapter does not")
    gold = rows[:SLICE]
    (tmp_path / "gold.json").write_text(json.dumps(gold, ensure_ascii=False))
    (tmp_path / "pred.json").write_text(json.dumps(
        {"answer": predictions, "sp": {}, "evidence": {}}, ensure_ascii=False))
    proc = subprocess.run(
        [sys.executable, str(source), str(tmp_path / "pred.json"),
         str(tmp_path / "gold.json"), str(dataset.DATASET_ROOT / dataset.ALIAS_FILE)],
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    # Every row is missing `sp` and `evidence`, so upstream prints one line per row per
    # task before the metrics; the dict is the first `{` on its own, at the end.
    official = json.loads(proc.stdout[proc.stdout.index("{"):])
    em, f1 = _aggregate(gold, predictions)
    assert (official["em"], official["f1"]) == (em, f1)
    # And the measured values, so that a change in either half is caught even if both
    # halves were changed the same way: 500 rows of the pinned dev pool.
    assert (em, f1) == (55.8, 68.18)


def test_released_normalization_order_measurement(rows):
    """The normalization order is load-bearing, and this is the measurement.

    Upstream lowercases first. Doing it after `re.sub(r"\\b(a|an|the)\\b", ...)` leaves a
    capitalised `The`/`A`/`An` -- and a gold answer is a Wikipedia surface form, so a tenth
    of them start with one.
    """
    assert dataset.normalize_answer("The City") == "city"
    assert dataset.normalize_answer("A Tale") == "tale"

    def wrong_order(s: str) -> str:
        s = __import__("re").sub(r"\b(a|an|the)\b", " ", s)
        s = "".join(ch for ch in s if ch not in set(__import__("string").punctuation))
        return " ".join(s.lower().split())

    changed = sum(1 for row in rows[:2000]
                  if dataset.normalize_answer(row["answer"]) != wrong_order(row["answer"]))
    assert changed == 196, f"{changed} of 2000 gold answers normalize differently"


def test_the_alias_channel_is_part_of_the_gold_and_of_the_metric(rows, predictions):
    """Gold is `{answer} | aliases`, and dropping the aliases moves the published number.

    Three claims, each of which fails on its own: an alias is a scoring answer; every
    `answer_id` in the pool resolves in the official table (a 100% hit rate is evidence
    that the table and the rows are the same release); and the aliases are not decoration
    -- an answer-only reading of this dataset scores 0.64 lower on the same predictions.
    """
    golds = [dataset._gold(row) for row in rows]
    assert len([gold for gold in golds if gold["aliases"]]) == 10603

    # An alias is an answer: full marks through the alias channel, strictly less without
    # it. The row is searched for rather than picked by hand, because "the first alias
    # happens to normalize the same as the span" would make a hand-picked row vacuous.
    for gold in golds:
        for alias in gold["aliases"]:
            if dataset.score(alias, {**gold, "aliases": []})["reward"] < 1.0:
                assert dataset.score(alias, gold)["reward"] == 1.0
                break
        else:
            continue
        break
    else:  # pragma: no cover - 5,869 rows carry such an alias
        pytest.fail("no alias in the pool scores below 1.0 without the alias channel")

    _, with_aliases = _aggregate(rows[:SLICE], predictions)
    _, without_aliases = _aggregate(rows[:SLICE], predictions, use_aliases=False)
    assert (with_aliases, without_aliases) == (68.18, 67.54)


# ------------------------------------------------------------------- the pool ---


def test_every_dev_row_scores_its_gold_one_and_the_empty_answer_zero(rows):
    """Gate 1 and gate 2 for the whole pool, not for a sample: 12,576 rows."""
    assert len(rows) == dataset.EXPECTED_ROWS == 12576
    for row in rows:
        gold = dataset._gold(row)
        assert dataset.score(gold["answer"], gold)["reward"] == 1.0
        assert dataset.score("", gold)["reward"] == 0.0


def test_a_gold_without_an_answer_is_reported_rather_than_scored():
    verdict = dataset.score("anything", {"answer": "", "aliases": []})
    assert verdict["reward"] == 0.0 and not verdict["correct"]
    assert "not a row of this pool" in verdict["why"]


def test_the_question_carries_the_ten_paragraphs_and_no_annotation(synthetic_rows):
    rows = synthetic_rows
    """The authored prompt contains its ten public paragraphs and no gold annotations."""
    row = rows[0]
    question = dataset._question(row)
    assert len(row["context"]) == 10
    for title, sentences in row["context"]:
        assert title in question
        for index, sentence in enumerate(sentences):
            assert f"[{index}] {sentence}" in question
    for annotation in ("supporting_facts", "evidences", "answer_id", "entity_ids", '"type"'):
        assert annotation not in question
    for pair in row["supporting_facts"]:
        assert json.dumps(pair) not in question
    for triple in row["evidences"]:
        assert json.dumps(triple) not in question

    # The type travels in the gold -- the paper publishes per-type numbers, so the platform
    # can too -- and the answer is not in what the agent is *set up* with.
    gold = dataset._gold(row)
    assert gold["type"] == row["type"] and gold["id"] == row["_id"]
    setup = json.dumps(dataset.SETUP[dataset._RECORDS[0]["task_id"]])
    assert row["answer"] not in setup


def test_the_witness_mirror_agrees_with_the_pool_on_every_row(rows):
    """Two independently published mirrors, 12,576 rows, no disagreement.

    The official release is a Dropbox link this machine cannot reach, so this is the whole
    of the provenance argument for `dev.json` being the official dev set: a second mirror,
    published by someone else from a different pipeline, agrees on every question, every
    answer and every supporting-fact pair. The witness has no `answer_id`, which is
    precisely why it cannot be the pool.
    """
    require_files(dataset.DATASET_ROOT, dataset.WITNESS_FILE)
    pq = pytest.importorskip("pyarrow.parquet",
                             reason="the witness is a parquet file")
    witness = pq.read_table(dataset.DATASET_ROOT / dataset.WITNESS_FILE).to_pylist()
    by_id = {row["_id"]: row for row in rows}
    assert len(witness) == len(rows) == dataset.EXPECTED_ROWS
    for other in witness:
        mine = by_id.get(other["id"])
        assert mine is not None, other["id"]
        assert mine["question"] == other["question"]
        assert mine["answer"] == other["answer"]
        assert [list(pair) for pair in mine["supporting_facts"]] == [
            [title, sent_id] for title, sent_id in zip(other["supporting_facts"]["title"],
                                                       other["supporting_facts"]["sent_id"])]


def test_normalization_order_on_authored_strings():
    assert dataset.normalize_answer('The City') == 'city'
    assert dataset.normalize_answer('A Tale') == 'tale'
    assert dataset.normalize_answer('An Example!') == 'example'


def test_authored_loader_refuses_duplicate_native_ids(synthetic_rows):
    duplicate = [synthetic_rows[0], synthetic_rows[0]]
    (dataset.DATASET_ROOT / dataset.DATA_FILE).write_text(json.dumps(duplicate))
    with pytest.raises(ValueError, match='exactly 2 unique'):
        dataset._rows()


def test_aliases_change_the_authored_loader_score(synthetic_rows):
    gold = dataset._gold(synthetic_rows[0])
    assert set(gold['aliases']) == {'PRIVATE_ALIAS','Crimson Citizens'}
    assert dataset.score('PRIVATE_ALIAS', gold)['reward'] == 1.0
    assert dataset.score('PRIVATE_ALIAS', {**gold,'aliases':[]})['reward'] == 0.0
    predictions = {row['_id']:'PRIVATE_ALIAS' for row in synthetic_rows}
    assert _aggregate(synthetic_rows,predictions) == (100.0,100.0)
    assert _aggregate(synthetic_rows,predictions,use_aliases=False) == (0.0,0.0)


@pytest.mark.parametrize('correct', [True,False])
def test_platform_control(correct, synthetic_rows, tmp_path, monkeypatch):
    """Real native sandbox controls on one authored selected task, no released exam."""
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import run_one
    tasks, _ = dataset.load()
    record = dataset._SPEC.ensure()[0]
    setup = dataset._SPEC.setup(record)
    verifier = dataset._SPEC.verifier(record)
    submission = dataset._oracle_submission(record['gold']) if correct else {'schema_version':1,'answer':'WRONG'}
    source_dir = tmp_path / 'actor';source_dir.mkdir()
    (source_dir / 'harness.json').write_text(json.dumps({
        'name':'twowiki-authored-control','version':'1','entrypoint':'agent.py','env_kinds':['files']}))
    (source_dir / 'agent.py').write_text(
        'import argparse,json\nfrom pathlib import Path\n'
        'p=argparse.ArgumentParser();p.add_argument("--task");p.add_argument("--workdir");a=p.parse_args();w=Path(a.workdir)\n'
        'assert not (w/"gold.json").exists()\n'
        'assert "PRIVATE_ALIAS" not in Path(a.task).read_text()\n'
        + '(w/"answer.txt").write_text(' + repr(json.dumps(submission)) + ')\n'
        + '(w/"trace.jsonl").write_text(json.dumps({"usage":{"calls":0,"input_tokens":0,"output_tokens":0}})+"\\n")\n')
    work = tmp_path / 'work'
    repo = work / 'control/workspace';stage_workspace(source_dir,repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    result = run_one(repo,tasks[0],sandbox=True,setup=setup,verifier=verifier,
                     timeout_s=30,verify_timeout_s=30,run_id='authored-twowiki')
    assert not result.get('invalid'),result
    assert result['exit_code'] == 0,result
    assert result['verdict']['score'] == float(correct),result
    assert not result['verdict'].get('unmeasured'),result
    assert result['verdict']['evidence']['measured'] is True
