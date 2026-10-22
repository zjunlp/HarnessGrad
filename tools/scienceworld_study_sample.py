#!/usr/bin/env python3
"""Build the 100-per-fold study setting for ScienceWorld from the benchmark's own folds.

    python3 tools/scienceworld_study_sample.py --archive /tmp/goldpaths-all.zip --write
    python3 tools/scienceworld_study_sample.py --archive ... --check

**Why this file exists.** `data/datasets/scienceworld.py` loads the benchmark's `test` fold
and splits it by task-id hash, because only the test fold's identity was transcribed into
git. Running a study on that split would train on test-fold material and then score the rest
of the same fold — the adapter says so in its own docstring. The benchmark has three folds
(`train` 3,442 / `dev` 1,721 / `test` 1,744 gold paths) inside
`goldpaths/goldpaths-all.zip`, and a study needs all three with the same shape as AppWorld:
train on `train`, score every checkpoint on `dev`, and keep `test` for the exam.

**The setting this writes.** 100 tasks per fold, stratified by task type (29 types have gold
paths) so every fold covers every type, deterministic from a fixed seed, and committed as
`data/datasets/scienceworld/study_300.json`. It carries the archive sha256 and the fold
counts, so a machine that has the archive can rebuild and compare; the sampled tasks are
identified by `(task_name, variation)`, which is all the environment needs to regenerate a
world.

**Why stratified rather than uniform.** ScienceWorld's variations are wildly uneven —
`inclined-plane-friction-named-surfaces` has 1,386 of the 6,907 paths while
`identify-life-stages-2` has 10. A uniform 100 would be ~20% one task type and would miss
several types entirely; the study would then be measuring two different benchmark mixes and
call them the same setting.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import zipfile
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "data" / "datasets" / "scienceworld"
OUT = ASSETS / "study_300.json"
TEST_FOLD = ASSETS / "test_fold.json"

#: Fixed so that the sample is a property of this file rather than of the machine that ran it.
SEED = 20261021
PER_FOLD = 100
FOLDS = ("train", "dev", "test")

#: The step budget the benchmark's own experiments use; a longer gold path is still a task,
#: it just cannot be finished with the reference length. Counted, not filtered (see
#: `data/datasets/scienceworld.py` and its 299 over-budget test-fold paths).
STEP_BUDGET = 100

ARCHIVE_CANDIDATES = (
    pathlib.Path("/tmp/goldpaths-all.zip"),
    pathlib.Path("/tmp/scw/ScienceWorld-main/goldpaths/goldpaths-all.zip"),
)


def archive_bytes(path: pathlib.Path | None) -> tuple[bytes, str]:
    """The zip payload and the member that holds the gold table."""
    candidates = [path] if path else list(ARCHIVE_CANDIDATES)
    chosen = next((p for p in candidates if p and p.is_file()), None)
    if chosen is None:
        raise SystemExit("no goldpaths archive found; pass --archive (see the module docstring)")
    raw = chosen.read_bytes()
    # The upstream release may ship the table directly or inside a one-member outer zip.
    if zipfile.is_zipfile(io.BytesIO(raw)):
        outer = zipfile.ZipFile(io.BytesIO(raw))
        inner = [n for n in outer.namelist() if n.endswith("goldpaths-all.zip")]
        if inner:
            raw = outer.read(inner[0])
    member = zipfile.ZipFile(io.BytesIO(raw)).namelist()[0]
    return raw, member


def fold_table(raw: bytes, member: str) -> dict[str, list[dict]]:
    """`fold -> [{'task_name', 'variation', 'gold_moves'}]`, in archive order."""
    table = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read(member))
    out: dict[str, list[dict]] = {fold: [] for fold in FOLDS}
    for entry in table.values():
        name = str(entry["taskName"])
        for gold in entry["goldActionSequences"]:
            out[gold["fold"]].append({"task_name": name,
                                      "variation": int(gold["variationIdx"]),
                                      "gold_moves": len(gold["path"])})
    for fold in FOLDS:
        out[fold].sort(key=lambda r: (r["task_name"], r["variation"]))
    return out


def per_type(k: int, counts: dict[str, int]) -> dict[str, int]:
    """Split `k` over task types under each type's own capacity.

    The floor makes coverage a property rather than a hope: with 29 types and `k = 100` every
    type gets three before any type gets four. The floor is then capped by what the fold
    actually holds, and any shortfall goes to types that still have spare rows, by largest
    remainder. Measured: without the cap, the `dev` fold samples 99, because
    `identify-life-stages-2` has fewer than three variations there.
    """
    names = sorted(counts)
    total = sum(counts.values()) or 1
    base = k // len(names)
    quota = {name: min(base, counts[name]) for name in names}
    rest = k - sum(quota.values())
    guard = 0
    while rest > 0 and guard < len(names) * 4:
        guard += 1
        spare = [n for n in names if quota[n] < counts[n]]
        if not spare:
            break
        spare.sort(key=lambda n: (-(counts[n] / total - quota[n] / k), n))
        for name in spare[:rest]:
            quota[name] += 1
        rest = k - sum(quota.values())
    return quota


def sample_fold(rows: list[dict], k: int, fold: str, seed: int) -> list[dict]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["task_name"]].append(row)
    quota = per_type(k, {name: len(items) for name, items in by_type.items()})
    picked: list[dict] = []
    for name in sorted(by_type):
        want = min(quota[name], len(by_type[name]))
        # A stable per-type stream: str seeds are hashed by sha512, but naming the digest
        # here keeps the rule readable and independent of CPython's Random internals.
        stream = int.from_bytes(hashlib.sha256(f"{seed}|{fold}|{name}".encode()).digest()[:8], "big")
        import random
        rng = random.Random(stream)
        picked.extend(rng.sample(by_type[name], want))
    if len(picked) > k:                       # only if quotas were capped, not expected
        import random
        random.Random(seed).shuffle(picked)
        picked = picked[:k]
    picked.sort(key=lambda r: (r["task_name"], r["variation"]))
    return picked


def build(archive: pathlib.Path | None) -> dict:
    raw, member = archive_bytes(archive)
    digest = hashlib.sha256(raw).hexdigest()
    recorded = json.loads(TEST_FOLD.read_text())["archive"]
    if digest != recorded["sha256"]:
        raise SystemExit(f"archive sha256 {digest} is not the transcription's {recorded['sha256']}")
    table = fold_table(raw, member)
    measured = {fold: len(table[fold]) for fold in FOLDS}
    if measured != recorded["folds"]:
        raise SystemExit(f"fold counts {measured} != recorded {recorded['folds']}")

    pools = {fold: sample_fold(table[fold], PER_FOLD, fold, SEED) for fold in FOLDS}
    for fold in FOLDS:
        if len(pools[fold]) != PER_FOLD:
            raise SystemExit(f"{fold}: sampled {len(pools[fold])} of {PER_FOLD}")

    ids = {fold: {f"{r['task_name']}/{r['variation']}" for r in pools[fold]} for fold in FOLDS}
    for a, b in (("train", "dev"), ("train", "test"), ("dev", "test")):
        if ids[a] & ids[b]:
            raise SystemExit(f"{a} and {b} share {sorted(ids[a] & ids[b])[:3]}")

    coverage = {}
    for fold in FOLDS:
        moves = sorted(r["gold_moves"] for r in pools[fold])
        coverage[fold] = {
            "types": len({r["task_name"] for r in pools[fold]}),
            "over_budget": sum(1 for m in moves if m > STEP_BUDGET),
            "median_gold_moves": moves[len(moves) // 2],
            "max_gold_moves": moves[-1],
        }
    return {
        "about": [
            "100 tasks per official ScienceWorld fold, stratified over the 29 task types that "
            "have gold paths and sampled deterministically from a fixed seed.",
            "Built by tools/scienceworld_study_sample.py from goldpaths/goldpaths-all.zip; the "
            "archive sha256 and fold counts below are what a rebuild is checked against.",
            "A task is identified by (task_name, variation); the environment regenerates its "
            "world and gold path, so no answer or rubric is stored here.",
        ],
        "setting": {"seed": SEED, "per_fold": PER_FOLD, "step_budget": STEP_BUDGET,
                    "folds": list(FOLDS)},
        "archive": {"sha256": digest, "member": member, "folds": measured,
                    "package_version": "1.2.3"},
        "coverage": coverage,
        "pools": pools,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", type=pathlib.Path, default=None)
    ap.add_argument("--write", action="store_true", help="write the file (default: check)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    payload = build(args.archive)
    text = json.dumps(payload, indent=1, sort_keys=False) + "\n"
    existing = OUT.read_text() if OUT.is_file() else None
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)} ({len(text)} bytes)")
    elif existing != text:
        print(f"{OUT.relative_to(ROOT)} would change")
    else:
        print(f"{OUT.relative_to(ROOT)} is unchanged")
    for fold in FOLDS:
        cov = payload["coverage"][fold]
        print(f"  {fold:5s} n={PER_FOLD} types={cov['types']:2d} "
              f"median_moves={cov['median_gold_moves']:3d} max={cov['max_gold_moves']:3d} "
              f"over_budget={cov['over_budget']:2d}")
    if not args.write and existing != text:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
