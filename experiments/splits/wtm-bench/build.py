"""Build a workbook-grouped 50/50/50 split of the pinned WTM-Bench Level 5 set."""
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


def workbook_keys(row, side):
    sha = row[f"{side}_sha256"]
    path = adapter._prepared_file(row[side], sha)
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


def families(rows):
    parents = {row["task_id"]: row["task_id"] for row in rows}
    owners = {}

    def root(task_id):
        while parents[task_id] != task_id:
            task_id = parents[task_id]
        return task_id

    for row in rows:
        task_id = row["task_id"]
        for side in ("initial", "expected"):
            for key in workbook_keys(row, side):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    groups = families(adapter.records())
    splits = partition(groups)
    plan = {"train": splits["train"], "eval": sorted(splits["dev"] + splits["test"])}
    files = {"study.json": json.dumps(plan, indent=2) + "\n",
             "dev.txt": "\n".join(splits["dev"]) + "\n",
             "test.txt": "\n".join(splits["test"]) + "\n"}
    for name, content in files.items():
        path = options.output_dir / name
        if options.check:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(f"split file differs from the frozen recipe: {path}")
        else:
            options.output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    print(f"{len(groups)} workbook groups, largest {max(map(len, groups))}; "
          + ", ".join(f"{side}={len(ids)}" for side, ids in splits.items()))


if __name__ == "__main__":
    main()
