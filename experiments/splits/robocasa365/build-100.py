"""Freeze the RoboCasa365 100/100/100 experiment from released task IDs."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = Path(__file__).resolve().parent
ALGORITHM = "robocasa365-catalog-100-v1"
SEED = 0
QUOTAS = {"atomic": 15, "composite_upstream": 70, "composite_estimated": 15}
TEST_INDICES = ("00", "01")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    os.environ["HG_ROBOCASA365_POOL"] = "all"
    from data.datasets import robocasa365 as dataset
    from data.scaffolds import custom_split

    strata = {stratum: [] for stratum in QUOTAS}
    for case in dataset.CASES.values():
        if case["pool"] != "catalog":
            continue
        if case["group"] == "atomic":
            stratum = "atomic"
        elif case["task"] in dataset.PROTOCOL.FALLBACK_MEDIAN_SECONDS:
            stratum = "composite_estimated"
        else:
            stratum = "composite_upstream"
        strata[stratum].append(case["task_id"])

    def rank(task_id):
        return sha256(f"{ALGORITHM}:{SEED}:{task_id}".encode()).digest(), task_id

    train, validation = [], []
    for stratum, quota in QUOTAS.items():
        ordered = sorted(strata[stratum], key=rank)
        if len(ordered) < 2 * quota:
            raise ValueError(f"RoboCasa365 stratum is too small: {stratum}")
        train.extend(ordered[:quota])
        validation.extend(ordered[quota:2 * quota])
    test = sorted(task for task in dataset.OFFICIAL_EVAL if task.rsplit("/", 1)[1] in TEST_INDICES)
    if len(train) != 100 or len(validation) != 100 or len(test) != 100:
        raise ValueError("expected 100 train, validation and test episodes")

    tasks, _ = dataset.load()
    manifest = custom_split.create("robocasa365", tasks, train_count=100, source="non-official-eval")
    manifest.update(source=custom_split.NON_OFFICIAL_SUBSET,
                    recipe={"algorithm": ALGORITHM, "seed": SEED, "per_side_by_stratum": QUOTAS},
                    split={"train": sorted(train), "eval": sorted(validation)},
                    excluded=sorted(set(dataset.CASES) - set(train) - set(validation)))
    custom_split.validate("robocasa365", tasks, manifest)

    files = {"study-100.json": json.dumps(manifest, indent=2) + "\n",
             "test-100.txt": "\n".join(test) + "\n"}
    for name, text in files.items():
        path = DIRECTORY / name
        if args.check:
            if path.read_text(encoding="utf-8") != text:
                raise ValueError(f"RoboCasa365 split differs from the frozen recipe: {name}")
        else:
            path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
