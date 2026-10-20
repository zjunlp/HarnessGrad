"""Private prepared artifacts and frozen source identities."""
import hashlib
import json
from pathlib import Path
import re

from .protocol import REVISION, CODE_REVISION, METRIC, SPLITS

_TASK = re.compile(r"personamem-v2-(train|val|benchmark)-p([0-9]{4})-text32k")


def task_id(split, persona):
    if split not in SPLITS or type(persona) is not int or not 0 <= persona < 1000:
        raise ValueError("invalid native split/persona")
    return f"personamem-v2-{split}-p{persona:04d}-text32k"


def validate_id(value):
    if not isinstance(value, str) or not _TASK.fullmatch(value):
        raise ValueError("invalid task identity")
    return value


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root):
    value = json.loads((Path(root) / "manifest.json").read_text())
    if (value.get("schema") != "personamem-v2-manifest-v1"
            or value.get("dataset_revision") != REVISION or value.get("code_revision") != CODE_REVISION
            or value.get("metric") != METRIC or not isinstance(value.get("artifacts"), dict)):
        raise ValueError("invalid prepared manifest")
    for name, sha in value["artifacts"].items():
        if name != "pool.json":
            parts = name.split("/")
            if len(parts) != 2 or parts[0] not in ("service", "gold") or not parts[1].endswith(".json"):
                raise ValueError("unexpected prepared artifact")
            validate_id(parts[1][:-5])
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha):
            raise ValueError("invalid artifact digest")
    return value


def artifact(root, name):
    metadata = manifest(root)
    if name not in metadata["artifacts"]:
        raise ValueError("artifact not in prepared manifest")
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or file_sha(path) != metadata["artifacts"][name]:
        raise ValueError("prepared artifact digest or path mismatch")
    return json.loads(path.read_text())


def pool(root):
    value = artifact(root, "pool.json")
    if not isinstance(value, list) or not value:
        raise ValueError("empty prepared pool")
    seen = set()
    for row in value:
        tid = validate_id(row["task_id"])
        if (tid in seen or tid != task_id(row["split"], row["persona_id"])
                or type(row["question_count"]) is not int or row["question_count"] <= 0
                or type(row["chunk_count"]) is not int or row["chunk_count"] <= 0):
            raise ValueError("invalid pool task")
        seen.add(tid)
    expected = {"pool.json"} | {f"{kind}/{tid}.json" for kind in ("gold", "service") for tid in seen}
    if set(manifest(root)["artifacts"]) != expected:
        raise ValueError("pool and manifest disagree")
    return {row["task_id"]: row for row in value}
