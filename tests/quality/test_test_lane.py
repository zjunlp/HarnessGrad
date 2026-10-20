"""The fast lane must select what a change can reach, and say what it cannot.

`tools/test_lane.py` exists because the full suite costs more than the loop it gates. A
selection tool that quietly under-selects is worse than no tool, so these tests pin the two
directions: a changed module does select the tests that mention it, and a changed module
that nothing mentions is reported instead of dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.test_lane import module_tokens, plan  # noqa: E402


def _test(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_a_module_turns_into_the_names_a_test_would_mention():
    tokens = module_tokens("data/datasets/gsm8k.py")
    assert "data.datasets.gsm8k" in tokens        # how an import reads
    assert "datasets.gsm8k" in tokens
    assert "gsm8k" in tokens                      # how a test usually writes it


def test_generic_names_are_not_tokens():
    """"run" would select half the suite; it is not a name, it is a word."""
    for path in ("eval/run/runner.py", "tools/run.py", "data/core.py"):
        tokens = module_tokens(path)
        assert "run" not in tokens and "core" not in tokens and "runner" not in tokens, tokens


def test_a_changed_module_selects_the_tests_that_mention_it(tmp_path):
    gsm = _test(tmp_path, "tests/quality/test_gsm8k_dataset.py", "from data.datasets.gsm8k import x\n")
    other = _test(tmp_path, "tests/quality/test_bfcl.py", "nothing relevant\n")
    selected, unmatched = plan(["data/datasets/gsm8k.py"], [gsm, other])
    assert set(selected) == {gsm}
    assert selected[gsm] == ["data.datasets.gsm8k"]
    assert unmatched == []


def test_a_change_nothing_mentions_is_reported_not_dropped(tmp_path):
    other = _test(tmp_path, "tests/quality/test_bfcl.py", "nothing relevant\n")
    selected, unmatched = plan(["eval/verification/native_runtime.py"], [other])
    assert selected == {}
    assert unmatched == ["eval/verification/native_runtime.py"]


def test_output_directories_never_select_anything(tmp_path):
    """Results, docs and the site are output; a test that mentions "docs" is not about them."""
    doc = _test(tmp_path, "tests/quality/test_docs_style.py", "docs\nwebsite\nruns\n")
    selected, unmatched = plan(["docs/dataset-onboarding.md", "website/src/data/x.json",
                                "runs/rsi10-x/curve.jsonl"], [doc])
    assert selected == {}
    assert unmatched == []            # skipped by design, not "unmatched"


def test_a_rename_selects_both_sides(tmp_path):
    old = _test(tmp_path, "tests/quality/test_gsm8k_dataset.py", "data.datasets.gsm8k\n")
    new = _test(tmp_path, "tests/quality/test_bfcl.py", "data.datasets.bfcl_v3\n")
    selected, _ = plan(["data/datasets/gsm8k.py", "data/datasets/bfcl_v3.py"], [old, new])
    assert set(selected) == {old, new}


def test_one_reason_per_test_is_enough(tmp_path):
    test = _test(tmp_path, "tests/quality/test_gsm8k_dataset.py", "data.datasets.gsm8k and gsm8k\n")
    selected, _ = plan(["data/datasets/gsm8k.py"], [test])
    assert selected[test] == ["data.datasets.gsm8k"]
