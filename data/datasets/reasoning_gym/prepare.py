#!/usr/bin/env python3
"""Materialize the Reasoning Gym pool once, so no run pays for generating it.

**Why this file exists.** Reasoning Gym generates its tasks procedurally, so there are no
bytes to download -- only a pinned pip version and a seed. That makes the pool reproducible
but **expensive to render on every call**: measured, rendering all 89 usable families at 20
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

    python3 data/datasets/reasoning_gym/prepare.py            # write the pool, print its digest
    python3 data/datasets/reasoning_gym/prepare.py --check    # is the pool on disk the pinned one?
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

PACKAGE = "reasoning-gym"

#: The pinned version. A version, not `main`: the family list above was measured against
#: this one, and a scorer change in a later release is a different dataset.
VERSION = "0.1.25"

#: One seed for the whole pool and one item count per family. Both are part of the pin:
#: `create_dataset(name, size, seed)` is deterministic, so these three numbers reproduce
#: every task and every reference answer byte-for-byte.
SEED = 20261008
PER_FAMILY = 20

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

DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/reasoning_gym")
POOL = DATASET_ROOT / "pool.jsonl"


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


def build() -> tuple[str, int]:
    """Write the pool and return `(sha256, task count)`."""
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    rows = [_entry(name, i) for name in FAMILIES for i in range(PER_FAMILY)]
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    POOL.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode()).hexdigest(), len(rows)


def digest() -> str:
    return hashlib.sha256(POOL.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="only report whether the pool on disk is the pinned one")
    args = parser.parse_args(argv)

    if args.check:
        if not POOL.is_file():
            print(f"missing: {POOL}")
            return 1
        print(f"{POOL.name}: {POOL.stat().st_size} bytes sha256={digest()}")
        return 0

    sha, count = build()
    print(f"wrote {POOL} ({count} tasks over {len(FAMILIES)} families)")
    print(f"sha256={sha}")
    print("record that digest in data/datasets/reasoning_gym.py; the test asserts it matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
