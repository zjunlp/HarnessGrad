"""Offline provenance gate and local synthetic regressions, with no copied reference.

The mandatory upstream gate fails, never skips, when acquired source or pins are absent.
See tests/fixtures/triviaqa_upstream/README.md for the acquisition contract and limits.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import socket
import string
import sys
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests/fixtures/triviaqa_upstream"
REPOSITORY = "https://github.com/mandarjoshi90/triviaqa"
SOURCE_PATH = "evaluation/triviaqa_evaluation.py"
DATASET_REVISION = "0f7faf33a3908546c6fd5b73a660e0f8ff173c2f"
EM_FUNCTIONS = ("normalize_answer", "get_ground_truths", "exact_match_score",
                "metric_max_over_ground_truths")
NORMALIZE_CASES = ("Fictional Azure City", "the Fictional Azure City!",
                   "  spaced   out  ", "A-Team", "The_A-Team", "an Apple",
                   "YES", "", "The The", "MÜNCHEN", "don't", "red–blue")
ALIAS_CASE = {"NormalizedAliases": ["fictional azure city", "invented blue town"],
              "HumanAnswers": ["The Invented Blue Town!"]}


class FixtureContractError(AssertionError):
    """A blocked provenance gate, not a measured zero."""


def require(condition, message):
    if not condition:
        raise FixtureContractError(
            f"TriviaQA upstream provenance blocked: {message}. "
            "See tests/fixtures/triviaqa_upstream/README.md; no network fallback.")


def read_upstream_fixture(directory=FIXTURE):
    """Validate all receipt pins before returning source; commit linkage needs review."""
    receipt_path = directory / "provenance.json"
    require(receipt_path.is_file(), "missing provenance.json and acquired fixture")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise FixtureContractError("invalid TriviaQA provenance JSON") from exc
    require(isinstance(receipt, dict), "receipt must be an object")
    require(receipt.get("schema_version") == 1, "unsupported receipt schema")
    require(receipt.get("repository") == REPOSITORY, "wrong scorer repository")
    commit = receipt.get("commit")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
            and commit != "0" * 40, "missing immutable scorer commit")
    require(commit != DATASET_REVISION, "HuggingFace dataset pin is not a scorer pin")
    require(receipt.get("source_path") == SOURCE_PATH, "wrong source path")
    acquisition = receipt.get("acquisition")
    require(isinstance(acquisition, dict), "missing acquisition attestation")
    require(acquisition.get("method") in (
        "local_git_checkout", "upstream_archive", "github_git_objects"),
            "unsupported acquisition method")
    for field in ("origin", "acquired_at_utc", "reviewed_by", "commit_link_evidence"):
        require(isinstance(acquisition.get(field), str) and acquisition[field].strip(),
                f"missing acquisition {field}")
    files = receipt.get("files")
    require(isinstance(files, dict), "missing file pins")
    license_path = receipt.get("license_path")
    require(isinstance(license_path, str) and license_path != SOURCE_PATH
            and license_path in files, "missing separately acquired license pin")
    require(SOURCE_PATH in files, "missing scorer file pin")
    verified = {}
    for name, pins in files.items():
        path = PurePosixPath(name)
        require(name and not path.is_absolute() and ".." not in path.parts
                and str(path) == name, "file paths must be canonical and relative")
        candidate = directory / name
        require(candidate.resolve().is_relative_to(directory.resolve()),
                "fixture path escapes its directory")
        require(candidate.is_file(), f"missing acquired file {name}")
        require(isinstance(pins, dict), f"missing pins for {name}")
        for key, length in (("sha256", 64), ("git_blob_sha1", 40)):
            require(isinstance(pins.get(key), str)
                    and re.fullmatch(rf"[0-9a-f]{{{length}}}", pins[key]),
                    f"missing {key} for {name}")
        content = candidate.read_bytes()
        require(hashlib.sha256(content).hexdigest() == pins["sha256"],
                f"SHA-256 mismatch for {name}")
        git_blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
        require(hashlib.sha1(git_blob).hexdigest() == pins["git_blob_sha1"],
                f"Git blob mismatch for {name}")
        verified[name] = content
    return verified[SOURCE_PATH]


def upstream_em_namespace(content):
    """Compile four unchanged EM definitions; this does not run the full evaluator.

    Only re/string are supplied. Extra dependencies or incompatible syntax fail;
    no stubs, downloads, source rewriting, or local implementation substitution.
    """
    tree = ast.parse(content, filename=SOURCE_PATH)
    definitions = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name in EM_FUNCTIONS]
    require(sorted(node.name for node in definitions) == sorted(EM_FUNCTIONS),
            "source must define each EM function exactly once")
    future_imports = [node for node in tree.body
                      if isinstance(node, ast.ImportFrom) and node.module == "__future__"]
    selected = ast.Module(body=[*future_imports, *definitions], type_ignores=[])
    namespace = {"re": re, "string": string}
    exec(compile(selected, SOURCE_PATH, "exec"), namespace)
    return namespace


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network access is forbidden in TriviaQA provenance tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def triviaqa(monkeypatch):
    # Suppress import-time pool construction BEFORE loading a private module instance.
    # Neither provisioned payloads nor globally cached data.datasets.triviaqa state enter tests.
    from data.scaffolds.qa import base

    def forbidden(*args, **kwargs):
        pytest.fail("released TriviaQA payload reads are forbidden in synthetic tests")

    monkeypatch.setattr(base, "parquet_rows", forbidden)
    monkeypatch.setattr(base.QASpec, "records", lambda self: [])
    spec = importlib.util.spec_from_file_location(
        "data._triviaqa_provenance_test", ROOT / "data/datasets/triviaqa.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_rows", forbidden)
    monkeypatch.setattr(module._SPEC, "_rows", forbidden)
    return module


def test_em_components_match_pinned_upstream_fixture(triviaqa):
    """Compare only when an acquired fixture exists; the separate gate remains explicit."""
    source = read_upstream_fixture()
    upstream = upstream_em_namespace(source)
    for text in NORMALIZE_CASES:
        assert triviaqa.normalize_answer(text) == upstream["normalize_answer"](text), text
    official_gold = upstream["get_ground_truths"](ALIAS_CASE)
    assert triviaqa.get_ground_truths(ALIAS_CASE) == official_gold
    gold = triviaqa._gold({"answer": {
        "normalized_aliases": ALIAS_CASE["NormalizedAliases"],
        "human_answers": ALIAS_CASE["HumanAnswers"], "value": "Fictional Azure City"}})
    assert gold["accepted"] == official_gold
    for prediction in ("Fictional Azure City", "Invented Blue Town!", "wrong", ""):
        for truth in official_gold:
            assert triviaqa.exact_match_score(prediction, truth) == upstream[
                "exact_match_score"](prediction, truth)
        expected = upstream["metric_max_over_ground_truths"](
            upstream["exact_match_score"], prediction, official_gold)
        assert triviaqa.metric_max_over_ground_truths(
            triviaqa.exact_match_score, prediction, gold["accepted"]) == expected
        assert triviaqa.score(prediction, gold)["reward"] == float(expected)


@pytest.mark.parametrize("text,expected", [
    ("the Fictional Azure City!", "fictional azure city"),
    ("  spaced   out  ", "spaced out"), ("A-Team", "team"),
    ("The_A-Team", "team"), ("an Apple", "apple"), ("", ""),
])
def test_local_normalization_regression(triviaqa, text, expected):
    """These values describe the current adapter, not unacquired upstream code."""
    assert triviaqa.normalize_answer(text) == expected


def test_local_any_alias_differs_from_canonical_value_only(triviaqa):
    gold = {"value": "Fictional Azure City",
            "accepted": triviaqa.get_ground_truths(ALIAS_CASE)}
    result = triviaqa.score("Invented Blue Town", gold)
    assert result["reward"] == 1.0
    assert triviaqa.exact_match_score("Invented Blue Town", gold["value"]) == 0.0
    assert result["official"]["canonical_value"] == gold["value"]
    assert "f1" not in result["official"]
    assert not hasattr(triviaqa, "f1_score")


def test_local_missing_aliases_are_rejected_without_reading_test_split(triviaqa):
    # Fabricated blinded shape only; no withheld/test payload is inspected.
    with pytest.raises(ValueError, match="no answer aliases"):
        triviaqa._gold({"answer": {"value": "<unk>", "normalized_aliases": []}})


def test_local_empty_reduction_is_an_adapter_extension(triviaqa):
    assert triviaqa.metric_max_over_ground_truths(triviaqa.exact_match_score, "", []) == 0.0


def test_local_gold_discards_question_and_document_grouping(triviaqa):
    row = {"question_id": "fabricated-q1", "document_id": "fabricated-d1",
           "answer": {"value": "Fictional Azure City",
                      "normalized_aliases": ["fictional azure city"]}}
    gold = triviaqa._gold(row)
    assert "question_id" not in gold and "document_id" not in gold
    # Illustrative policies only, not a reconstruction of upstream aggregation:
    # duplicate q1 gets two hits, q2 one miss; rows and unique questions differ.
    row_rewards = [triviaqa.score(p, gold)["reward"]
                   for p in ("Fictional Azure City", "Fictional Azure City", "wrong")]
    assert sum(row_rewards) / len(row_rewards) == pytest.approx(2 / 3)
    assert sum([row_rewards[0], row_rewards[2]]) / 2 == 0.5


@pytest.fixture
def integrity_fixture(tmp_path):
    """Synthetic inert bytes validate the receipt; never an upstream scorer."""
    files = {SOURCE_PATH: b"# SYNTHETIC integrity-test bytes, not evaluator code.\n",
             "LICENSE": b"SYNTHETIC integrity-test text, not an upstream license.\n"}
    receipt = {
        "schema_version": 1, "repository": REPOSITORY,
        "commit": hashlib.sha1(b"SYNTHETIC test commit identity").hexdigest(),
        "source_path": SOURCE_PATH, "license_path": "LICENSE",
        "acquisition": {"method": "local_git_checkout", "origin": "SYNTHETIC test",
                        "acquired_at_utc": "2026-10-14T00:00:00Z",
                        "reviewed_by": "SYNTHETIC test",
                        "commit_link_evidence": "SYNTHETIC test; no upstream identity"},
        "files": {},
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
        receipt["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(),
                                 "git_blob_sha1": hashlib.sha1(blob).hexdigest()}
    (tmp_path / "provenance.json").write_text(json.dumps(receipt), encoding="utf-8")
    return tmp_path, receipt, files


def test_contract_checks_all_recorded_bytes(integrity_fixture):
    directory, _, files = integrity_fixture
    assert read_upstream_fixture(directory) == files[SOURCE_PATH]
    with pytest.raises(FixtureContractError, match="each EM function"):
        upstream_em_namespace(files[SOURCE_PATH])


def test_contract_absent_fixture_fails_instead_of_skipping(tmp_path):
    with pytest.raises(FixtureContractError, match="missing provenance.json"):
        read_upstream_fixture(tmp_path)


@pytest.mark.parametrize("name", [SOURCE_PATH, "LICENSE"])
def test_contract_missing_file_fails(integrity_fixture, name):
    directory, _, _ = integrity_fixture
    (directory / name).unlink()
    with pytest.raises(FixtureContractError, match="missing acquired file"):
        read_upstream_fixture(directory)


@pytest.mark.parametrize("name", [SOURCE_PATH, "LICENSE"])
def test_contract_changed_bytes_fail(integrity_fixture, name):
    directory, _, _ = integrity_fixture
    with (directory / name).open("ab") as changed:
        changed.write(b"SYNTHETIC drift\n")
    with pytest.raises(FixtureContractError, match="SHA-256 mismatch"):
        read_upstream_fixture(directory)


@pytest.mark.parametrize("field,value,message", [
    ("commit", None, "immutable scorer commit"),
    ("commit", "main", "immutable scorer commit"),
    ("commit", "0" * 40, "immutable scorer commit"),
    ("commit", DATASET_REVISION, "dataset pin is not a scorer pin"),
    ("repository", "https://huggingface.co/datasets/mandarjoshi/trivia_qa", "repository"),
    ("source_path", "data/datasets/triviaqa.py", "source path"),
    ("license_path", SOURCE_PATH, "license pin"),
    ("acquisition", None, "acquisition attestation"),
])
def test_contract_incomplete_or_wrong_receipt_fails(integrity_fixture, field, value, message):
    directory, receipt, _ = integrity_fixture
    receipt[field] = value
    (directory / "provenance.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(FixtureContractError, match=message):
        read_upstream_fixture(directory)


@pytest.mark.parametrize("key,value,message", [
    ("sha256", None, "missing sha256"),
    ("git_blob_sha1", None, "missing git_blob_sha1"),
    ("git_blob_sha1", "0" * 40, "Git blob mismatch"),
])
def test_contract_missing_or_wrong_hash_fails(integrity_fixture, key, value, message):
    directory, receipt, _ = integrity_fixture
    receipt["files"][SOURCE_PATH][key] = value
    (directory / "provenance.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(FixtureContractError, match=message):
        read_upstream_fixture(directory)


def test_contract_path_escape_fails(integrity_fixture):
    directory, receipt, _ = integrity_fixture
    receipt["files"]["../untrusted.py"] = receipt["files"][SOURCE_PATH]
    (directory / "provenance.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(FixtureContractError, match="canonical and relative"):
        read_upstream_fixture(directory)
