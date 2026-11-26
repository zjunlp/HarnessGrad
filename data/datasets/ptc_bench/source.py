"""Pinned source and case validation for the trusted PTC-Bench runtime."""
import hashlib
import json
from pathlib import Path
import sys

REVISION = "fa4462e5ad1ea3cde2011aaf6396de5d76da4489"
SOURCE_SHA256 = "6121aabf40f21c544d4cc3fce98c62afe0c2f3a38ef62ed7909441535f63e9ba"
BUNDLE_SHA256 = "c2572354f62205690d5ff6fc089d1d3de77fa37fe90607ad50ef133db8205bbb"
COUNTS = {"main": 100, "error_recovery": 70, "multi_turn": 70}
METRIC = "ptc_bench.official_task_pass"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verified_case(path, expected):
    raw = Path(path).read_bytes()
    if digest(raw) != expected:
        raise ValueError("PTC-Bench case digest mismatch")
    config = json.loads(raw)
    task = config["task"]
    task_id = f"ptc_bench/{config['split']}/{task['id']}"
    if config["revision"] != REVISION or config["task_id"] != task_id:
        raise ValueError("PTC-Bench case identity mismatch")
    return config


def bootstrap(bundle):
    source = Path(bundle).resolve(strict=True)
    raw = (source / "manifest.json").read_bytes()
    if digest(raw) != BUNDLE_SHA256:
        raise ValueError("PTC-Bench upstream manifest digest mismatch")
    declared = json.loads(raw)
    actual = set()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("PTC-Bench bundle symlinks are forbidden")
        if path.is_dir():
            continue
        name = path.relative_to(source).as_posix()
        if name == "manifest.json":
            continue
        if name not in declared or not path.is_file() or digest(path.read_bytes()) != declared[name]:
            raise ValueError(f"PTC-Bench bundle file mismatch: {name}")
        actual.add(name)
    if actual != declared.keys():
        raise ValueError("PTC-Bench bundle inventory mismatch")
    if "ptc_bench" in sys.modules:
        raise ValueError("PTC-Bench source must be selected before importing upstream")
    sys.path.insert(0, str(source))
