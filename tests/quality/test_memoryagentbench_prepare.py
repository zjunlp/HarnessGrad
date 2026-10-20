"""Offline input boundary and selected-artifact validation, without a model."""
import json
from pathlib import Path

import pytest

from data.datasets.memoryagentbench.lib import inputs, storage
from data.datasets.memoryagentbench.prepare import build_artifacts, prepare, validate_raw


class Sentences:
    def tokenize(self, text):
        return text.split("|") if text else []


class Encoding:
    def encode(self, text, **kwargs):
        return text


def row(source="ruler_qa1_197K"):
    return {"context": "Visible material|Second sentence", "questions": ["Where is {gold_secret}?", "Who?"],
            "answers": [["private-answer-a"], ["private-answer-b"]],
            "metadata": {"source": source, "qa_pair_ids": ["repeat", "repeat"],
                         "question_types": ["single-session-user", "multi-session"],
                         "question_ids": ["official-1", "official-2"],
                         "keypoints": ["secret-keypoint"], "haystack_sessions": [["private-history"]]}}


def artifacts():
    return build_artifacts("Accurate_Retrieval", 0, row(), Sentences(), Encoding())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prepared(tmp_path):
    public, service, gold = artifacts()
    identifier = public["task_id"]
    write_json(tmp_path / "pool.json", {"schema": "hg-mab-pool-v1", "dataset_revision": storage.DATASET_REVISION, "tasks": [public]})
    write_json(tmp_path / f"service/{identifier}.json", service)
    write_json(tmp_path / f"gold/{identifier}.json", gold)
    write_json(tmp_path / "entity2id.json", {"Example": 1})
    manifest = {"schema": "hg-mab-manifest-v1", "dataset_revision": storage.DATASET_REVISION,
                "artifacts": {str(p.relative_to(tmp_path)): storage.file_sha256(p) for p in tmp_path.rglob("*.json")}}
    write_json(tmp_path / "manifest.json", manifest)
    return identifier


def update_artifact(root, relative, payload):
    write_json(root / relative, payload)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["artifacts"][relative] = storage.file_sha256(root / relative)
    write_json(root / "manifest.json", manifest)


def test_original_ids_stable_and_independent_of_duplicate_qa_ids():
    public, service, gold = artifacts()
    assert len({q["id"] for q in service["questions"]}) == 2
    assert public["task_id"].startswith("mab-" + storage.DATASET_REVISION)
    assert public["task_id"] != storage.task_id("Accurate_Retrieval", 1)
    assert public["task_id"] != storage.task_id("Conflict_Resolution", 0)
    assert list(gold["gold_by_id"]) == [q["id"] for q in service["questions"]]


def test_public_and_service_have_no_gold_or_judge_metadata():
    public, service, gold = artifacts()
    public_json = json.dumps(public)
    service_json = json.dumps(service)
    assert "Visible material" not in public_json
    assert "Where is" not in public_json
    for secret in ("private-answer", "secret-keypoint", "private-history", "official-1", "single-session-user"):
        assert secret not in public_json and secret not in service_json
    assert gold["judge_metadata_by_id"][service["questions"][0]["id"]]["question"] == row()["questions"][0]


def test_summary_preserves_entire_keypoint_list_not_question_aligned():
    sample = row("infbench_sum_eng_shots2")
    sample["metadata"]["keypoints"] = ["first", "second", "third"]
    _, service, gold = build_artifacts("Long_Range_Understanding", 0, sample, Sentences(), Encoding())
    for q in service["questions"]:
        assert gold["judge_metadata_by_id"][q["id"]]["keypoints"] == ["first", "second", "third"]


@pytest.mark.parametrize("field", ["questions", "answers"])
def test_unaligned_questions_rejected(field):
    sample = row(); sample[field] = []
    with pytest.raises(ValueError, match="unaligned"):
        build_artifacts("Accurate_Retrieval", 0, sample, Sentences(), Encoding())


def test_misaligned_private_metadata_rejected():
    sample = row(); sample["metadata"]["question_types"] = ["only-one"]
    with pytest.raises(ValueError, match="not aligned"):
        build_artifacts("Accurate_Retrieval", 0, sample, Sentences(), Encoding())


@pytest.mark.parametrize("context,expected", [("", []), ("a" * 4096, ["a" * 4096]),
    ("a" * 4097, ["", "a" * 4097]), ("a" * 4096 + "|b", ["a" * 4096, "b"]),
    ("a|b", ["a b"])])
def test_frozen_chunk_boundary_semantics(context, expected):
    assert inputs.split_context(context, Sentences(), Encoding()) == expected


def test_templates_insert_question_once_and_ignore_no_metadata():
    templates = inputs.read_spec("templates.json")
    for source in templates["source_to_family"]:
        assert "literal {answer} {question}" in inputs.render_question(source, "literal {answer} {question}")
    assert "label:" not in inputs.render_question("icl_banking77_5900shot_balance", "example")
    with pytest.raises(ValueError):
        inputs.render_question("unknown", "example")


def test_spec_tampering_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(inputs, "SPECS", tmp_path)
    (tmp_path / "templates.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        inputs.read_spec("templates.json")


def test_missing_resource_is_reported_before_initializing_tokenizers(tmp_path, monkeypatch):
    versions = inputs.read_spec("chunk-policy.json")["dependencies"]
    monkeypatch.setattr(inputs.importlib.metadata, "version", versions.__getitem__)
    with pytest.raises(ValueError, match="resource_missing"):
        inputs.initialize(tmp_path)


def test_corrupt_resource_is_rejected(tmp_path, monkeypatch):
    versions = inputs.read_spec("chunk-policy.json")["dependencies"]
    monkeypatch.setattr(inputs.importlib.metadata, "version", versions.__getitem__)
    path = tmp_path / "nltk/tokenizers/punkt_tab/english/abbrev_types.txt"
    path.parent.mkdir(parents=True); path.write_bytes(b"x" * 619)
    with pytest.raises(ValueError, match="resource_hash_mismatch"):
        inputs.initialize(tmp_path)


def test_dependency_version_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(inputs.importlib.metadata, "version", lambda package: "unexpected")
    with pytest.raises(ValueError, match="dependency_version_mismatch"):
        inputs.initialize(tmp_path)


def test_raw_pin_and_missing_file_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="raw_missing"):
        validate_raw(tmp_path)
    first = next(iter(inputs.read_spec("source.json")["files"]))
    path = tmp_path / first; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw_hash_mismatch"):
        validate_raw(tmp_path)


def test_prepared_round_trip(tmp_path):
    identifier = prepared(tmp_path)
    assert len(storage.load_pool(tmp_path)["tasks"]) == 1
    assert len(storage.load_service(tmp_path, identifier)["questions"]) == 2
    assert len(storage.load_gold(tmp_path, identifier)["gold_by_id"]) == 2
    assert storage.load_entity2id(tmp_path) == {"Example": 1}


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "mab-123", "x/y", "", None])
def test_path_traversal_and_invalid_ids_rejected(tmp_path, identifier):
    with pytest.raises(ValueError, match="task ID"):
        storage.load_service(tmp_path, identifier)


def test_corrupt_artifact_and_manifest_revision_rejected(tmp_path):
    identifier = prepared(tmp_path)
    (tmp_path / f"service/{identifier}.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        storage.load_service(tmp_path, identifier)
    manifest = json.loads((tmp_path / "manifest.json").read_text()); manifest["dataset_revision"] = "changed"
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="revision"):
        storage.load_manifest(tmp_path)


def test_service_future_metadata_injection_rejected_even_with_new_digests(tmp_path):
    identifier = prepared(tmp_path)
    payload = storage.load_service(tmp_path, identifier); payload["gold_by_id"] = {"secret": "answer"}
    payload["input_sha256"] = storage.canonical_sha256({k: v for k, v in payload.items() if k != "input_sha256"})
    update_artifact(tmp_path, f"service/{identifier}.json", payload)
    with pytest.raises(ValueError, match="fields"):
        storage.load_service(tmp_path, identifier)


def test_missing_gold_question_cannot_be_silently_dropped(tmp_path):
    identifier = prepared(tmp_path)
    gold = storage.load_gold(tmp_path, identifier); gold["gold_by_id"].pop(next(iter(gold["gold_by_id"])))
    update_artifact(tmp_path, f"gold/{identifier}.json", gold)
    with pytest.raises(ValueError, match="question IDs"):
        storage.load_gold(tmp_path, identifier)


def test_unselected_id_cannot_load(tmp_path):
    prepared(tmp_path)
    with pytest.raises(ValueError, match="not selected"):
        storage.load_gold(tmp_path, storage.task_id("Accurate_Retrieval", 1))


def test_manifest_symlink_escape_is_rejected(tmp_path):
    identifier = prepared(tmp_path)
    path = tmp_path / f"service/{identifier}.json"; path.unlink(); path.symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="escapes"):
        storage.load_service(tmp_path, identifier)


def test_output_and_selection_validation_precedes_expensive_preparation(tmp_path):
    existing = tmp_path / "exists"; existing.mkdir()
    with pytest.raises(FileExistsError):
        prepare(tmp_path, tmp_path, existing)
    with pytest.raises(ValueError, match="outside"):
        prepare(tmp_path, tmp_path, Path(__file__).resolve().parents[2] / "forbidden-prepared")
    for selected in ([], [storage.task_id("Accurate_Retrieval", 0)] * 2, ["../invalid"]):
        with pytest.raises(ValueError):
            prepare(tmp_path, tmp_path, tmp_path / "fresh", selected)


def test_duplicate_json_keys_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text('{"schema":"hg-mab-manifest-v1","schema":"duplicate"}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        storage.load_manifest(tmp_path)
