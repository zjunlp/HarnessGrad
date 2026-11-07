#!/usr/bin/env python3
"""Materialize the Reasoning Gym pool once, so no run pays for generating it.

**Why this file exists.** Reasoning Gym generates its tasks procedurally, so there are no
published data files to download. The historical recipe pins a package version and a seed,
while the frozen digest pins the exact task bytes. It is **expensive to render on every call**:
measured, rendering all 89 usable families at 20
items each (1,780 tasks) costs **15.6 s**, and at 5 items each (445 tasks) still **5.1 s**.
A dataset whose cost is per task hurts the control loop, the lint and every importing
subprocess, never the run itself -- the lesson already paid for twice (skill doc §15.9 and
§21). So the pool is materialized here, once, into `DATASET_ROOT/pool.jsonl`, and the adapter
reads that file the way every other QA dataset reads its table.

**The family list is measured, not chosen.** A family is usable here when, in the pinned
version, all three hold for the generated entry: the reference answer scores 1.0, the empty
answer scores 0.0, and the entry carries a non-empty answer. Of 106 registered families that
left **89** (probe of 2026-10-08; the 16 excluded scored their own reference answer below 1.0
or accepted an empty answer, and `composite` cannot be built without sub-datasets). The rule
matters because a family that accepts an empty answer would give the do-nothing control
reward 1 -- gate 2 would be red for a reason that is the dataset's, not the harness's.

    python data/datasets/reasoning_gym/prepare.py --import-pool /path/to/frozen/pool.jsonl
    python data/datasets/reasoning_gym/prepare.py --check

Without an option, regenerate with the prepared execution interpreter. Publish
only if the result matches the historical frozen pool's digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.datasets import reasoning_gym as adapter  # noqa: E402

PACKAGE, VERSION = adapter.PACKAGE, adapter.VERSION
SEED, PER_FAMILY = adapter.SEED, adapter.PER_FAMILY

#: The 89 families whose scorer is deterministic and offline in `VERSION` (see the docstring
#: for the rule and the measurement). Sorted, so a regeneration diff is readable.
FAMILIES = (
    "ab", "acre", "aiw", "arc_1d", "base_conversion", "basic_arithmetic", "bf",
    "binary_alternation", "binary_matrix", "bitwise_arithmetic", "caesar_cipher",
    "calendar_arithmetic", "chain_sum", "circuit_logic", "codeio", "coin_flip",
    "complex_arithmetic", "count_bits", "count_primes", "course_schedule", "cryptarithm",
    "decimal_arithmetic", "decimal_chain_sum", "dice", "family_relationships",
    "figlet_font", "futoshiki", "game_of_life", "game_of_life_halting", "gcd",
    "group_anagrams", "gsm_symbolic", "intermediate_integration", "isomorphic_strings",
    "jugs", "kakurasu", "knight_swap", "knights_knaves", "largest_island", "lcm",
    "leg_counting", "letter_counting", "letter_jumble", "list_functions",
    "mahjong_puzzle", "manipulate_matrix", "maze", "mini_sudoku", "modulo_grid",
    "n_queens", "needle_haystack", "number_filtering", "number_format",
    "number_sequence", "number_sorting", "palindrome_generation",
    "palindrome_partitioning", "path_star", "polynomial_equations",
    "polynomial_multiplication", "pool_matrix", "products", "quantum_lock",
    "ransom_note", "rectangle_count", "rotate_matrix", "rotten_oranges",
    "self_reference", "shortest_path", "simple_equations", "simple_geometry",
    "simple_integration", "sokoban", "spell_backward", "spiral_matrix",
    "string_insertion", "string_manipulation", "string_splitting", "string_synthesis",
    "sudoku", "survo", "syllogism", "time_intervals", "tower_of_hanoi", "tsumego",
    "word_ladder", "word_sequence_reversal", "word_sorting", "zebra_puzzles",
)

DATASET_ROOT = adapter.DATASET_ROOT
POOL = DATASET_ROOT / adapter.POOL


def _entry(name: str, index: int) -> dict:
    """One task, and the entry object the official scorer needs.

    The **entry** travels with the task on purpose: `Dataset.score_answer(answer, entry)`
    reads `entry["answer"]` and, for some families, `entry["metadata"]`, so a check that had
    only the answer string could not call the official scorer at all.
    """
    from reasoning_gym import create_dataset

    dataset = create_dataset(name, size=PER_FAMILY, seed=SEED)
    item = dataset[index]
    # No `task_id` here on purpose: the platform names the tasks (`reasoning_gym/eval/<n>`,
    # `QASpec.records()`), and a second id in the row would disagree with it -- the first
    # version carried `reasoning_gym/<family>/<index>` and a test duly looked for an id that
    # does not exist. The row's coordinates are `family` and `index`.
    return {
        "family": name,
        "index": index,
        "question": str(item["question"]).strip(),
        "entry": json.loads(json.dumps(item, default=str)),
    }


def _render_pool() -> str:
    """Render in the managed interpreter without writing the dataset directory."""
    rows = [_entry(name, i) for name in FAMILIES for i in range(PER_FAMILY)]
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _publish(raw: bytes) -> tuple[str, int]:
    sha = hashlib.sha256(raw).hexdigest()
    if sha != adapter.POOL_SHA256:
        raise ValueError(f"Reasoning Gym pool SHA-256 mismatch: got {sha}, expected {adapter.POOL_SHA256}. "
                         "Use --import-pool with a verified copy of the frozen pool.")
    target = Path(POOL)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".pool-", delete=False) as stream:
            staging = Path(stream.name)
            stream.write(raw)
        os.replace(staging, target)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return sha, len(raw.splitlines())


def build() -> tuple[str, int]:
    """Generate in the bound environment and publish only the pinned bytes."""
    return _publish(adapter._run_worker("pool").encode("utf-8"))


def digest() -> str:
    return hashlib.sha256(POOL.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify the frozen pool digest without writing")
    mode.add_argument("--import-pool", type=Path, help="import a local copy of the frozen pool by SHA-256")
    args = parser.parse_args(argv)

    try:
        if args.check:
            sha = digest()
            print(f"{POOL.name}: {POOL.stat().st_size} bytes sha256={sha}")
            return 0 if sha == adapter.POOL_SHA256 else 1
        sha, count = _publish(args.import_pool.read_bytes()) if args.import_pool else build()
        print(f"wrote {POOL} ({count} tasks), sha256={sha}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
