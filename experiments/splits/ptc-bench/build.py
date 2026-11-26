"""Freeze the PTC-Bench research partition from the three pinned upstream JSONL files."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re


SEED = 42
SOURCES = (
    ("main", 100, "5390a6bc60249adb71d494b9677b98c81351da8b0b3b103160e16b751ac08562"),
    ("error_recovery", 70, "a9eaea6b7dd9959d4ae8eda706b75aa33ba675522fe202a4b0599a66dc757f33"),
    ("multi_turn", 70, "c3479e8ccf3ac7e15fe83e85613cd6bed55c640e9515272388ab818cb7ae70ff"),
)
PARTITIONS = ("train", "dev", "test")
# Keys describe the number of main, recovery, and multi-turn variants in one
# task family. Values specify how many whole families go to train, dev, test.
FAMILY_COUNTS = {
    (1, 1, 1): (17, 17, 16),
    (1, 0, 0): (17, 16, 17),
    (0, 1, 0): (6, 7, 6),
    (0, 0, 1): (6, 6, 7),
    (0, 1, 1): (0, 0, 1),
}


def partition(data_dir):
    families = defaultdict(list)
    prompts = {}
    roster = set()
    for source, count, sha in SOURCES:
        raw = (data_dir / f"{source}.jsonl").read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError(f"{source} differs from the pinned PTC-Bench release")
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if len(rows) != count:
            raise ValueError(f"unexpected {source} task count")
        for row in rows:
            task_id = f"ptc_bench/{source}/{row['id']}"
            if task_id in roster:
                raise ValueError(f"duplicate task ID: {task_id}")
            roster.add(task_id)
            family = re.sub(r"^ptc_(?:intermediate_|mt_)?", "", row["id"])
            families[family].append((source, task_id))
            for turn in row["question"]:
                for message in turn:
                    if message["role"] == "user":
                        prompt = " ".join(message["content"].casefold().split())
                        if prompts.setdefault(prompt, family) != family:
                            raise ValueError("identical user prompts belong to different task families")

    strata = defaultdict(list)
    for family, members in families.items():
        by_source = Counter(part for part, _ in members)
        strata[tuple(by_source[source] for source, _, _ in SOURCES)].append(family)
    if set(strata) != set(FAMILY_COUNTS):
        raise ValueError("unexpected distribution of task-family variants")

    result = {side: [] for side in PARTITIONS}
    for variant_counts, members in strata.items():
        sizes = FAMILY_COUNTS[variant_counts]
        if len(members) != sum(sizes):
            raise ValueError(f"unexpected number of task families for {variant_counts}")
        names = sorted(members, key=lambda name: (hashlib.sha256(f"{SEED}\0{name}".encode()).digest(), name))
        start = 0
        for side, size in zip(PARTITIONS, sizes, strict=True):
            result[side].extend(task_id for family in names[start:start + size]
                                for _, task_id in families[family])
            start += size
    flat = [task for side in PARTITIONS for task in result[side]]
    if len(flat) != len(roster) or set(flat) != roster or any(len(ids) != 80 for ids in result.values()):
        raise ValueError("partition must cover each task exactly once in three equally sized sets")
    return {side: sorted(ids) for side, ids in result.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="upstream ptc_bench/data directory")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true", help="verify existing artifacts without rewriting")
    args = parser.parse_args()
    splits = partition(args.data_dir)
    files = {
        "study.json": json.dumps({"train": splits["train"], "eval": sorted(splits["dev"] + splits["test"])}, indent=2) + "\n",
        "dev.txt": "\n".join(splits["dev"]) + "\n",
        "test.txt": "\n".join(splits["test"]) + "\n",
    }
    if not args.check:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        path = args.output_dir / name
        if args.check:
            if path.read_text() != contents:
                raise ValueError(f"split file differs from frozen recipe: {path}")
        else:
            path.write_text(contents)
    for side, ids in splits.items():
        counts = Counter(task_id.split("/")[1] for task_id in ids)
        print(side, len(ids), dict(counts))


if __name__ == "__main__":
    main()
