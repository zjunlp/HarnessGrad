"""Materialize LiveCodeBench release_v6 into one pool file.

    python3 data/datasets/livecodebench/prepare.py            # writes the pool
    python3 data/datasets/livecodebench/prepare.py --check    # re-digests what is there

Why a pool file exists at all
-----------------------------
The six released shards are **4,485,994,821 bytes** and reading plus decoding them takes
**70 seconds** (measured, one pass, this machine): `private_test_cases` is
`pickle.loads(zlib.decompress(base64.b64decode(...)))`, so every row is decompressed to be
read. An adapter that did that at import would make `data/scaffolds/registry.py::published()` -- which
imports every module in `data/` to decide what the Launch form offers -- unusable, and this
repository has already paid for that lesson once (Reasoning Gym: 15.6 s to render its pool).
So the pool is materialized here, once, and the adapter reads a JSONL.

The six shards are **disjoint**, not cumulative
-----------------------------------------------
Measured: 400 + 111 + 101 + 101 + 167 + 175 = 1,055 rows, and the union of their
`question_id`s is also 1,055 -- each problem appears in exactly one shard. (An earlier note in
this repository called them "cumulative", which would have made the pool a subset of the
largest shard.) The per-shard counts are written into the pool's header so the next reader does
not have to re-derive them.

Two files, not one: the tests do not fit in a pool
--------------------------------------------------
Decoded, the 1,055 problems' tests are **9.4 GB** (the first version of this script wrote them
into a single JSONL and produced exactly that). Two consequences, both structural: an adapter
cannot `json.loads` its own table, and the platform materializes `VERIFY.inputs` **per task**,
so the tests have to be addressable one problem at a time.

So `pool.jsonl` holds the metadata only -- `question_id`, `question_title`,
`question_content`, `platform`, `contest_id`, `contest_date`, `difficulty`, `starter_code`,
the public/private case counts, and the problem's test file's `sha256` and size -- and each
problem's tests live in `tests/<question_id>.json` as
`{"inputs": [...], "outputs": [...], "fn_name": ...}`, which is exactly the payload upstream's
`CodeGenerationProblem.get_evaluation_sample()` builds for `testing_util.run_test`, public and
private cases concatenated in that order. The per-problem digest rides in the pool row, so a
test can check a sample of the tests without re-hashing 9.4 GB.

The tests are **not** excluded for size: the whole released pool is the pool, and an oversized
case is upstream's own data rather than a defect on this host. The largest single problem is
recorded in the header so the next reader knows what a run's materialization costs.

Unpickling is a trust decision, and it is pinned
------------------------------------------------
`private_test_cases` is unpickled because that is the released encoding, and it is safe here
for one reason only: the shards are pinned by sha256 in `downloads()` and verified before this
script runs. Nothing unpickled here comes from an agent, a network response at run time, or a
file the platform did not digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import data.datasets.livecodebench as adapter  # noqa: E402

POOL = "pool.jsonl"
TESTS = "tests"
SHARDS = ("test.jsonl", "test2.jsonl", "test3.jsonl", "test4.jsonl",
          "test5.jsonl", "test6.jsonl")


#: The released decode step lives in the adapter, so the pool and the module that reads it
#: cannot drift apart; this is the one place it is written down.
decode_cases = adapter.decode_private_test_cases


def _row(raw: dict, tests_path: pathlib.Path) -> dict:
    """One pool row, and the problem's tests written beside the pool."""
    public = decode_cases(raw["public_test_cases"])
    private = decode_cases(raw["private_test_cases"]) if raw["private_test_cases"] else []
    cases = public + private
    metadata = json.loads(raw["metadata"]) if raw["metadata"] else {}
    payload = json.dumps({"inputs": [case["input"] for case in cases],
                          "outputs": [case["output"] for case in cases],
                          "fn_name": metadata.get("func_name")}, sort_keys=True)
    tests_path.write_text(payload + "\n", encoding="utf-8")
    return {
        "question_id": raw["question_id"],
        "question_title": raw["question_title"],
        "question_content": raw["question_content"],
        "platform": raw["platform"],
        "contest_id": raw["contest_id"],
        "contest_date": raw["contest_date"],
        "difficulty": raw["difficulty"],
        "starter_code": raw["starter_code"] or "",
        "n_public": len(public),
        "n_private": len(private),
        "tests_sha256": hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest(),
        "tests_bytes": len(payload) + 1,
    }


def build() -> tuple[list[dict], dict[str, int]]:
    """`(rows, per-shard row counts)`, streamed -- 4.5 GB never fits in memory at once."""
    directory = adapter.DATASET_ROOT / TESTS
    directory.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for shard in SHARDS:
        path = adapter.DATASET_ROOT / "_src" / shard
        with path.open(encoding="utf-8") as handle:
            count = 0
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                count += 1
                if raw["question_id"] in rows:
                    raise SystemExit(f"{shard}: {raw['question_id']} appears in two shards")
                rows[raw["question_id"]] = _row(
                    raw, directory / f"{raw['question_id']}.json")
        counts[shard] = count
    return [rows[qid] for qid in sorted(rows)], counts


def digest() -> str:
    body = (adapter.DATASET_ROOT / POOL).read_bytes()
    return hashlib.sha256(body).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report the digest of the pool on disk and exit")
    args = parser.parse_args(argv)
    path = adapter.DATASET_ROOT / POOL
    if args.check:
        if not path.is_file():
            print(f"no pool at {path}", file=sys.stderr)
            return 1
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1
        print(f"{path}\n  problems {rows}\n  sha256 {digest()}")
        return 0

    rows, counts = build()
    if len(rows) != adapter.PROBLEMS:
        raise SystemExit(f"expected {adapter.PROBLEMS} problems, built {len(rows)}")
    header = {"kind": "livecodebench-pool", "release": adapter.REVISION,
              "problems": len(rows), "shards": counts,
              "tests": {"public": sum(r["n_public"] for r in rows),
                        "private": sum(r["n_private"] for r in rows),
                        "bytes_total": sum(r["tests_bytes"] for r in rows),
                        "bytes_max": max(r["tests_bytes"] for r in rows),
                        "problem_max_bytes": max(rows, key=lambda r: r["tests_bytes"])["question_id"]}}
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"wrote {path} ({len(rows)} problems, {path.stat().st_size / 1e6:.1f} MB) and "
          f"{adapter.DATASET_ROOT / TESTS} "
          f"({header['tests']['bytes_total'] / 1e9:.2f} GB, largest "
          f"{header['tests']['bytes_max'] / 1e6:.0f} MB on {header['tests']['problem_max_bytes']})")
    print(f"  shards {counts}")
    print(f"  sha256 {digest()}")
    print("record that digest in POOL_SHA256 (data/datasets/livecodebench.py); the test asserts it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
