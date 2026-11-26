#!/usr/bin/env python3
"""Prepare the pinned PTC-Bench source, separating private cases from reusable code."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harnessgrad.paths import dataset_root
from source import BUNDLE_SHA256, COUNTS, REVISION, SOURCE_SHA256, canonical, digest


def _selected_source(name):
    return (
        name in {"LICENSE", "ptc_bench/__init__.py", "ptc_bench/utils.py"}
        or name.startswith(("ptc_bench/constants/", "ptc_bench/eval_checker/"))
        or name.startswith("ptc_bench/data/multi_turn_func_doc/")
    )


def prepare(archive_path, output):
    with archive_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("PTC-Bench source archive digest mismatch")
        stream.seek(0)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".prepare-ptc-", dir=output.parent) as temp:
            stage = Path(temp) / "prepared"
            bundle = stage / "bundle"
            bundle.mkdir(parents=True)
            inventory = {}
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                prefix = f"I-PTC-{REVISION}/"
                for member in archive:
                    if not member.name.startswith(prefix):
                        continue
                    name = member.name.removeprefix(prefix)
                    if not _selected_source(name) or member.isdir():
                        continue
                    path = Path(name)
                    if not member.isfile() or path.is_absolute() or ".." in path.parts or name in inventory:
                        raise ValueError("invalid pinned PTC-Bench bundle member")
                    raw = archive.extractfile(member).read()
                    target = bundle / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    inventory[name] = digest(raw)
                manifest = canonical(inventory)
                if digest(manifest) != BUNDLE_SHA256:
                    raise ValueError("PTC-Bench upstream bundle identity mismatch")
                (bundle / "manifest.json").write_bytes(manifest)
                rows = []
                seen = set()
                for split, count in COUNTS.items():
                    tasks = archive.extractfile(prefix + f"ptc_bench/data/{split}.jsonl")
                    if split == "multi_turn":
                        answers = {}
                    else:
                        gold = archive.extractfile(prefix + f"ptc_bench/data/possible_answer/{split}.jsonl")
                        answers = {row["id"]: row["ground_truth"] for row in map(json.loads, gold)}
                    split_rows = []
                    for task in map(json.loads, tasks):
                        original_id = task["id"]
                        if not re.fullmatch(r"[A-Za-z0-9_-]+", original_id) or original_id in seen:
                            raise ValueError("invalid or duplicate PTC-Bench task ID")
                        seen.add(original_id)
                        expected_turns = 2 if split == "multi_turn" else 1
                        if len(task["question"]) != expected_turns or any(
                            len(turn) != 1 or turn[0]["role"] != "user"
                            or not isinstance(turn[0]["content"], str)
                            for turn in task["question"]
                        ):
                            raise ValueError("unsupported PTC-Bench question structure")
                        task_id = f"ptc_bench/{split}/{original_id}"
                        relative = f"cases/{split}/{original_id}.json"
                        # Tool methods can use insertion order when ranking ties.
                        # The provenance index is canonical, but simulator state follows
                        # the object's original order in the released JSONL.
                        config = {"revision": REVISION, "split": split, "task_id": task_id,
                                  "task": task, "ground_truth": answers.get(original_id, {})}
                        raw = json.dumps(config, separators=(",", ":")).encode("utf-8")
                        case = stage / relative
                        case.parent.mkdir(parents=True, exist_ok=True)
                        case.write_bytes(raw)
                        split_rows.append({"task_id": task_id, "split": split, "case": relative,
                                           "sha256": digest(raw)})
                    if len(split_rows) != count:
                        raise ValueError(f"unexpected PTC-Bench task count for {split}")
                    rows.extend(split_rows)
            catalog = canonical({"revision": REVISION, "source_sha256": SOURCE_SHA256, "rows": rows})
            (stage / "catalog.json").write_bytes(catalog)
            if output.exists():
                raise FileExistsError(f"prepared directory already exists: {output}")
            stage.rename(output)
    return digest(catalog)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    root = dataset_root("ptc_bench").resolve()
    print(prepare(args.archive or root / "source.tar.gz", root / "prepared"))
