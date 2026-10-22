#!/usr/bin/env python3
"""Explicit TriviaQA scorer provenance gate; exit nonzero until source is acquired."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.quality.test_triviaqa_official_scorer import read_upstream_fixture, FixtureContractError

try:
    content = read_upstream_fixture()
except FixtureContractError as exc:
    print(f"BLOCKED: {exc}", file=sys.stderr)
    raise SystemExit(2)
print(f"Fixture integrity pins verified ({len(content)} scorer-source bytes).")
print("Origin/commit linkage still requires independent human review; this is not evaluator equivalence.")
