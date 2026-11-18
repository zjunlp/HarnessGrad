"""Task-bound native scorer inputs, under trusted spec/check custody.

The digest names a canonical path -> byte-digest inventory. Native consumers get
private copies hashed while copying, never a reopened actor path. Runtime/code
custody and upstream provenance are separate obligations.

The byte-only inventory cannot authenticate SQLite WAL dependencies. Bound
consumers refuse WAL-format main files, including already copied or checkpointed
ones: their headers cannot prove that an omitted WAL was empty. Preparing a
single-file database is a separate trusted operation, never an implicit conversion.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


def inventory_digest(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=128)
def _file_digest(path: str, stat_identity: tuple) -> str:
    # Reuse source identities while building many specs for the same static DB/JAR.
    # The consuming check always hashes its own private copy without this cache.
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bind(spec: dict, task_id: str, paths: list[str]) -> dict:
    entries = {e["path"]: e for e in spec["inputs"]}
    files = {}
    for path in paths:
        entry = entries[path]
        if "content" in entry:
            files[path] = hashlib.sha256(entry["content"].encode("utf-8")).hexdigest()
        else:
            source = Path(entry["from"])
            stat = source.stat()
            files[path] = _file_digest(str(source), (stat.st_dev, stat.st_ino, stat.st_size,
                                                     stat.st_mtime_ns, stat.st_ctime_ns))
    digest = inventory_digest(files)
    spec["scorer_input"] = {"task_id": task_id, "path": "native-inputs",
                            "sha256": digest, "files": files}
    spec["argv"] += ["--task-id", task_id, "--scorer-input-sha256", digest,
                     "--scorer-input-files", json.dumps(files, sort_keys=True)]
    helper = Path(__file__).resolve()
    spec["inputs"] += [{"path": "data/__init__.py", "content": ""},
                       {"path": "data/scaffolds/__init__.py", "content": ""},
                       {"path": "data/scaffolds/scorer_input.py", "from": str(helper)}]
    return spec


def add_arguments(parser) -> None:
    parser.add_argument("--task-id")
    parser.add_argument("--scorer-input-sha256")
    parser.add_argument("--scorer-input-files")


def run_bound(args, paths: dict[str, Path], score) -> int:
    """Validate exact consumed inventory before reaching any scoring boundary.

    Legacy manual calls remain explicitly unbound, as in the QA check; a selected
    bound spec cannot accept their receipt through native dispatch.
    """
    receipt = {"task_id": args.task_id, "path": "native-inputs", "sha256": None,
               "expected_sha256": args.scorer_input_sha256, "verified": False, "files": {}}
    if all(v is None for v in (args.task_id, args.scorer_input_sha256, args.scorer_input_files)):
        return score(paths, receipt)
    with tempfile.TemporaryDirectory(prefix="hg-scorer-input-") as scratch:
        try:
            copies = {}
            for name, path in paths.items():
                # Preserve relative names for file-format dispatch and CSV dataset lookup.
                rel = Path(name)
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("input inventory path must stay relative")
                target = Path(scratch) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                header = b""
                with Path(path).open("rb") as src, target.open("wb") as dst:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        if not header:
                            header = block[:20]
                        digest.update(block)
                        dst.write(block)
                # Inspect the same bytes that were copied and hashed, not a second
                # open or neighboring actor paths. SQLite header bytes 18/19 are
                # the write/read format versions (2 denotes WAL). A missing WAL
                # is indistinguishable here from a cleanly checkpointed database;
                # matching main-file digests cannot establish logical equivalence.
                if (header[:16] == b"SQLite format 3\x00"
                        and 2 in header[18:20]):
                    raise ValueError(
                        f"SQLite WAL input {name!r} requires a database-aware snapshot; "
                        "main-file-only binding is unsupported (including checkpointed WAL mode)")
                copies[name] = target
                receipt["files"][name] = digest.hexdigest()
            receipt["sha256"] = inventory_digest(receipt["files"])
            expected = json.loads(args.scorer_input_files or "null")
            receipt["verified"] = bool(args.task_id and isinstance(expected, dict)
                                        and expected == receipt["files"]
                                        and args.scorer_input_sha256 == receipt["sha256"])
            if not receipt["verified"]:
                raise ValueError("task/digest inventory mismatch")
        except (OSError, ValueError) as exc:
            evidence = {"measured": False, "error": f"scorer input binding refused: {exc}",
                        "scorer_input": receipt}
            output = Path(args.evidence_file)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
            print(evidence["error"], file=sys.stderr)
            return 3
        # Keep the authenticated copies alive, preserving route-specific exceptions.
        return score(copies, receipt)
