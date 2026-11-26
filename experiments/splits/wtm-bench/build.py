"""Build the curated or mixed-level WTM-Bench research split."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets import wtm_bench as adapter

PARTITIONS = ("train", "dev", "test")
SEED = "HarnessGrad-WTM-Bench-v1"
TARGET = 50
SHEET_PART = re.compile(r"xl/(?:worksheets/sheet\d+\.xml|sharedStrings\.xml)")


def workbook_keys(path, sha):
    yield "bytes", sha
    with ZipFile(path) as workbook:
        sheets = sorted(name for name in workbook.namelist() if SHEET_PART.fullmatch(name))
        if sheets:
            yield "sheets", tuple((name, hashlib.sha256(workbook.read(name)).hexdigest())
                                    for name in sheets)
        if "docProps/core.xml" in workbook.namelist():
            meta = {element.tag.rsplit("}", 1)[-1]: element.text
                    for element in ET.fromstring(workbook.read("docProps/core.xml"))}
            if meta.get("creator") and meta.get("created"):
                yield "origin", (meta["creator"], meta["created"])


def families(rows, *, prepared_file=adapter._prepared_file):
    parents = {row["task_id"]: row["task_id"] for row in rows}
    owners = {}

    def root(task_id):
        while parents[task_id] != task_id:
            task_id = parents[task_id]
        return task_id

    for row in rows:
        task_id = row["task_id"]
        for side in ("initial", "expected"):
            sha = row[f"{side}_sha256"]
            for key in workbook_keys(prepared_file(row[side], sha), sha):
                owner = owners.setdefault(key, task_id)
                parents[root(task_id)] = root(owner)
    groups = defaultdict(list)
    for task_id in parents:
        groups[root(task_id)].append(task_id)
    return [sorted(ids) for ids in groups.values()]


def partition(groups):
    def order(ids):
        return -len(ids), hashlib.sha256(f"{SEED}\0{ids[0]}".encode()).digest()

    result = {side: [] for side in PARTITIONS}
    for ids in sorted(groups, key=order):
        side = min(PARTITIONS, key=lambda name: len(result[name]))
        result[side].extend(ids)
    if any(len(ids) != TARGET for ids in result.values()):
        raise ValueError("the pinned workbook groups did not produce three 50-task sets")
    return {side: sorted(ids) for side, ids in result.items()}


def study_sample(source):
    from pyarrow.parquet import read_table

    parquet = source / adapter.STUDY_PARQUET
    if hashlib.sha256(parquet.read_bytes()).hexdigest() != adapter.STUDY_PARQUET_SHA256:
        raise ValueError("WTM-Bench full parquet digest mismatch")
    curated = json.loads((Path(__file__).parent / "study.json").read_text(encoding="utf-8"))
    excluded = set(curated["train"] + curated["eval"])
    names = {row["task"] for row in read_table(parquet, columns=["extra_info"])[0].to_pylist()}

    def rank(name):
        return hashlib.sha256(f"HarnessGrad-WTM-Bench-v2\0{name}".encode()).digest()

    return sorted((name for name in names if f"wtm_bench/{name}" not in excluded), key=rank)[:360]


def build_study(source):
    names = study_sample(source)
    rows = []
    for name in names:
        row = {"task_id": f"wtm_bench/{name}"}
        for side, path in adapter.study_workbook_paths(name).items():
            row[side] = path
            row[f"{side}_sha256"] = hashlib.sha256((source / path).read_bytes()).hexdigest()
        rows.append(row)

    def source_file(path, _sha):
        return source / path

    groups = families(rows, prepared_file=source_file)
    ranks = {row["task_id"]: rank for rank, row in enumerate(rows)}
    group_by_task = {task_id: ids[0] for ids in groups for task_id in ids}
    result = {side: [] for side in PARTITIONS}
    for ids in sorted(groups, key=lambda group: (-len(group), min(ranks[task] for task in group))):
        side = min(PARTITIONS, key=lambda name: len(result[name]))
        result[side].extend(ids)

    by_id = {row["task_id"]: row for row in rows}
    membership = []
    count = adapter.STUDY_COUNT // len(PARTITIONS)
    for offset, (side, ids) in enumerate(result.items()):
        if len(ids) < count:
            raise ValueError("WTM-Bench workbook groups left a partition too small")
        levels = adapter.STUDY_LEVELS
        selected = sorted(ids, key=ranks.__getitem__)[:count]
        for i, task_id in enumerate(selected):
            row = by_id[task_id]
            membership.append({"task_id": task_id, "split": side, "level": levels[(i + offset) % len(levels)],
                               "workbook_group": group_by_task[task_id],
                               **{field: row[field] for field in ("initial_sha256", "expected_sha256")}})
        result[side] = selected
    return {side: sorted(ids) for side, ids in result.items()}, membership


def write_or_check(path, content, check):
    if check:
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"split file differs from the frozen recipe: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--study-source", type=Path, help="Local full parquet and 360 candidate workbook pairs")
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    output = options.output_dir or Path(__file__).resolve().parent
    if options.study_source:
        splits, membership = build_study(options.study_source)
        manifest = "[\n" + ",\n".join("  " + json.dumps(row) for row in membership) + "\n]\n"
        manifest_dir = options.output_dir or adapter.ASSETS
        write_or_check(manifest_dir / "study-300.json", manifest, options.check)
        if not options.output_dir:
            output /= "mixed"
    else:
        groups = families(adapter.records())
        splits = partition(groups)
        print(f"{len(groups)} workbook groups, largest {max(map(len, groups))}")
    plan = {"train": splits["train"], "eval": sorted(splits["dev"] + splits["test"])}
    files = {"study.json": json.dumps(plan, indent=2) + "\n",
             "dev.txt": "\n".join(splits["dev"]) + "\n",
             "test.txt": "\n".join(splits["test"]) + "\n"}
    for name, content in files.items():
        write_or_check(output / name, content, options.check)
    print(", ".join(f"{side}={len(ids)}" for side, ids in splits.items()))


if __name__ == "__main__":
    main()
