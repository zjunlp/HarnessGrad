"""Validated, portable prepared artifacts. Hashes detect corruption, not authorship.

Only the trusted host reads gold and manifests. Never mount a prepared root into
an actor container: service files contain future material and gold is private.
"""
import hashlib
import json
from pathlib import Path
import re

DATASET_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
SPLITS = ("Accurate_Retrieval", "Conflict_Resolution", "Long_Range_Understanding", "Test_Time_Learning")
_ID = re.compile(r"mab-" + DATASET_REVISION + r"-(?:" + "|".join(s.lower() for s in SPLITS) + r")-r[0-9]{4}")
_HEX = re.compile(r"[0-9a-f]{64}")


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_task_id(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("invalid MemoryAgentBench task ID")
    return value


def task_id(split, row_index):
    if split not in SPLITS or type(row_index) is not int or not 0 <= row_index <= 9999:
        raise ValueError("invalid original row identity")
    return f"mab-{DATASET_REVISION}-{split.lower()}-r{row_index:04d}"


def question_id(identifier, index):
    validate_task_id(identifier)
    if type(index) is not int or not 0 <= index <= 9999:
        raise ValueError("invalid original question index")
    return f"{identifier}-q{index:04d}"


def _path(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("unsafe artifact path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("artifact path escapes prepared root")
    return path


def _json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)


def load_manifest(root):
    manifest = _json(_path(root, "manifest.json"))
    if (not isinstance(manifest, dict) or manifest.get("schema") != "hg-mab-manifest-v1"
            or manifest.get("dataset_revision") != DATASET_REVISION
            or not isinstance(manifest.get("artifacts"), dict)
            or "pool.json" not in manifest["artifacts"]
            or "entity2id.json" not in manifest["artifacts"]):
        raise ValueError("invalid prepared manifest or dataset revision")
    for name, digest in manifest["artifacts"].items():
        _path(root, name)
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError("invalid artifact digest")
        if name not in ("pool.json", "entity2id.json"):
            parts = name.split("/")
            if len(parts) != 2 or parts[0] not in ("service", "gold") or not parts[1].endswith(".json"):
                raise ValueError("unexpected prepared artifact")
            validate_task_id(parts[1][:-5])
    return manifest


def _artifact(root, name):
    manifest = load_manifest(root)
    path = _path(root, name)
    if name not in manifest["artifacts"] or file_sha256(path) != manifest["artifacts"][name]:
        raise ValueError("prepared artifact checksum mismatch: " + name)
    return _json(path)


def load_pool(root):
    pool = _artifact(root, "pool.json")
    if (not isinstance(pool, dict) or set(pool) != {"schema", "dataset_revision", "tasks"}
            or pool["schema"] != "hg-mab-pool-v1" or pool["dataset_revision"] != DATASET_REVISION
            or not isinstance(pool["tasks"], list) or not pool["tasks"]):
        raise ValueError("invalid prepared pool")
    fields = {"task_id", "episode_id", "source", "split", "row_index", "chunk_count", "question_count", "input_sha256"}
    seen = set()
    from .scoring import PRIMARY_METRICS
    for item in pool["tasks"]:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("invalid pool task fields")
        identifier = validate_task_id(item["task_id"])
        if (identifier in seen or identifier != task_id(item["split"], item["row_index"])
                or item["episode_id"] != identifier or item["source"] not in PRIMARY_METRICS
                or type(item["chunk_count"]) is not int or item["chunk_count"] < 0
                or type(item["question_count"]) is not int or item["question_count"] <= 0
                or not isinstance(item["input_sha256"], str) or not _HEX.fullmatch(item["input_sha256"])):
            raise ValueError("invalid pool task metadata")
        seen.add(identifier)
    expected = {"pool.json", "entity2id.json"} | {f"{kind}/{identifier}.json" for kind in ("service", "gold") for identifier in seen}
    if set(load_manifest(root)["artifacts"]) != expected:
        raise ValueError("manifest artifacts disagree with selected pool")
    return pool


def validate_service(payload):
    fields = {"schema", "task_id", "episode_id", "source", "chunks", "questions", "input_sha256"}
    if not isinstance(payload, dict) or set(payload) != fields or payload["schema"] != "hg-mab-service-input-v1":
        raise ValueError("invalid service input fields")
    identifier = validate_task_id(payload["task_id"])
    from .scoring import PRIMARY_METRICS
    if (payload["episode_id"] != identifier or payload["source"] not in PRIMARY_METRICS
            or not isinstance(payload["chunks"], list) or not all(isinstance(c, str) for c in payload["chunks"])
            or not isinstance(payload["questions"], list) or not payload["questions"]):
        raise ValueError("invalid service episode")
    for index, question in enumerate(payload["questions"]):
        if (not isinstance(question, dict) or set(question) != {"id", "text"}
                or question["id"] != question_id(identifier, index)
                or not isinstance(question["text"], str) or not question["text"].strip()):
            raise ValueError("invalid service question")
    if payload["input_sha256"] != canonical_sha256({k: v for k, v in payload.items() if k != "input_sha256"}):
        raise ValueError("service input checksum mismatch")
    return payload


def _pool_item(root, identifier):
    validate_task_id(identifier)
    matches = [item for item in load_pool(root)["tasks"] if item["task_id"] == identifier]
    if not matches:
        raise ValueError("task not selected in prepared pool")
    return matches[0]


def load_service(root, identifier):
    item = _pool_item(root, identifier)
    payload = validate_service(_artifact(root, f"service/{identifier}.json"))
    if (any(payload[k] != item[k] for k in ("task_id", "episode_id", "source", "input_sha256"))
            or len(payload["chunks"]) != item["chunk_count"] or len(payload["questions"]) != item["question_count"]):
        raise ValueError("service input disagrees with pool")
    return payload


def load_gold(root, identifier):
    item = _pool_item(root, identifier)
    payload = _artifact(root, f"gold/{identifier}.json")
    fields = {"schema", "task_id", "episode_id", "source", "input_sha256", "gold_by_id", "judge_metadata_by_id"}
    if (not isinstance(payload, dict) or set(payload) != fields or payload["schema"] != "hg-mab-gold-v1"
            or any(payload[k] != item[k] for k in ("task_id", "episode_id", "source", "input_sha256"))):
        raise ValueError("gold artifact disagrees with pool")
    expected = {question_id(identifier, i) for i in range(item["question_count"])}
    if any(not isinstance(payload[key], dict) or set(payload[key]) != expected for key in ("gold_by_id", "judge_metadata_by_id")):
        raise ValueError("gold question IDs disagree with pool")
    from .scoring import score_question
    metadata_fields = {"question", "question_type", "question_id", "qa_pair_id", "keypoints"}
    for qid in expected:
        score_question(item["source"], None, payload["gold_by_id"][qid])
        meta = payload["judge_metadata_by_id"][qid]
        if not isinstance(meta, dict) or set(meta) != metadata_fields or not isinstance(meta["question"], str):
            raise ValueError("invalid judge metadata")
    return payload


def load_entity2id(root):
    mapping = _artifact(root, "entity2id.json")
    if not isinstance(mapping, dict) or not mapping or any(not isinstance(k, str) or type(v) is not int for k, v in mapping.items()):
        raise ValueError("invalid recommendation entity mapping")
    return mapping
