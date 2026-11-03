"""The README's boards are the record, rendered -- and its counts are the registry's.

Two different claims are guarded here, because the README makes both.

**The boards.** `registry/leaderboards.json` is the record; `tools/leaderboards.py` is the
only writer of the table in the README. The test regenerates and compares, so a pull request
that adds a row without re-rendering fails, and a hand-edited table is caught the moment it
disagrees with the record. This is the same discipline as `tools/datasets_doc.py --check`:
the page is derived from the data, never typed beside it.

**The counts.** The README says "59 declared, 55 runnable, 4 refused". Those numbers are read
from the tree -- the adapter files, and the contract refusals recorded in
`registry/dataset-blockers.json` -- rather than trusted, because a dataset that changes state
without the README changing is exactly the drift a reader would not notice. They are counted
from declarations and not from imports, so the guard means the same thing on a bare machine.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import leaderboards  # noqa: E402

README = ROOT / "README.md"
RECORD = ROOT / "registry" / "leaderboards.json"
BLOCKERS = ROOT / "registry" / "dataset-blockers.json"


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def _refused() -> list[str]:
    """Datasets whose declared state is a contract refusal, from the blocker record."""
    blockers = json.loads(BLOCKERS.read_text(encoding="utf-8"))
    refused = []
    for entry in blockers["datasets"]:
        families = {item.get("family") for item in entry.get("blockers") or []}
        if "contract-refusal" in families:
            refused.append(entry["dataset"])
    return sorted(refused)


def test_the_record_is_valid() -> None:
    found = leaderboards.problems(_record())
    assert not found, "\n".join(found)


def test_the_readme_boards_are_the_record_rendered() -> None:
    text = README.read_text(encoding="utf-8")
    block = leaderboards.current_block(text)
    assert block is not None, "README has no generated board block"
    assert block == leaderboards.render(_record()), (
        "the README boards are stale; run python3 tools/leaderboards.py --write")


def test_the_board_block_is_replaced_not_duplicated() -> None:
    text = README.read_text(encoding="utf-8")
    assert text.count(leaderboards.BEGIN) == 1, "the board block appears twice"
    assert text.count(leaderboards.END) == 1


def test_every_board_row_names_something_in_the_tree() -> None:
    """A row that names nothing runnable is a name, not a contribution."""
    record = _record()
    for board, (field, exists) in leaderboards.REQUIRED.items():
        for entry in record["boards"][board]:
            path = entry[field]
            assert exists(path), f"{board}/{entry['name']}: {path} does not exist"


def test_the_block_is_capped_and_points_at_the_full_board() -> None:
    rendered = leaderboards.render(_record())
    assert leaderboards.SITE in rendered, "the block must point at the full board"
    for heading in ("### Harnesses", "### RSI methods", "### Benchmarks",
                    "### Measured results"):
        assert heading in rendered, f"the block has no {heading!r} section"
    rows = [line for line in rendered.splitlines()
            if line.startswith("| ") and line.count("|") >= 6]
    # Three support boards, each capped at TOP rows plus its header and separator.
    assert len(rows) <= 3 * (leaderboards.TOP + 2), len(rows)


def test_the_readme_counts_match_the_registry() -> None:
    # Declared, not published: `published()` is a fact about what this interpreter can
    # import, and the README is a claim about the repository. Counting imports here would
    # make the guard fail on a clone without the optional extras -- the same defect it is
    # supposed to catch elsewhere.
    declared = sorted(path.stem for path in (ROOT / "data/datasets").glob("*.py")
                      if path.stem != "__init__")
    refused = _refused()
    text = README.read_text(encoding="utf-8")
    assert len(refused) == 4, f"the refused set changed: {refused}"
    assert f"{len(declared)} declared" in text, f"README does not say {len(declared)} declared"
    assert f"{len(declared) - len(refused)} runnable" in text, (
        f"README does not say {len(declared) - len(refused)} runnable")
    assert f"{len(refused)} refused" in text, f"README does not say {len(refused)} refused"
    for name in refused:
        assert f"`{name}`" in text, f"the refused dataset {name} is not named in the README"


def test_the_count_python_floor_is_the_project_floor() -> None:
    """The README's badge and prose agree with `pyproject.toml` and the readiness check."""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    floor = project["requires-python"].lstrip(">=")
    assert floor == "3.12", project["requires-python"]
    text = README.read_text(encoding="utf-8")
    assert f"python-{floor}" in text, "the README badge still advertises another floor"
    assert f"Python **{floor}+**" in text, "the README prose still advertises another floor"


def test_a_row_without_a_receipt_cannot_be_a_result() -> None:
    """The admission rule, exercised: a score without a receipt is refused."""
    record = _record()
    broken = {
        "schema": record["schema"],
        "boards": record["boards"],
        "results": {"entries": [{
            "benchmark": "gsm8k", "metric": "exact_match", "score": 0.6,
            "method": "dgm", "harness": "loop", "contributor": "someone",
            "harness_sha": "0" * 40, "receipt": "runs/not-committed/end-summary.json",
        }]},
    }
    found = leaderboards.problems(broken)
    assert any("receipt does not exist" in line for line in found), found


def test_a_result_without_a_group_or_a_hash_is_refused() -> None:
    record = _record()
    for missing in ("benchmark", "metric", "harness_sha", "contributor"):
        entry = {"benchmark": "gsm8k", "metric": "exact_match", "score": 0.6,
                 "method": "dgm", "harness": "loop", "contributor": "someone",
                 "harness_sha": "0" * 40, "receipt": "README.md"}
        entry.pop(missing)
        broken = {"schema": record["schema"], "boards": record["boards"],
                  "results": {"entries": [entry]}}
        found = leaderboards.problems(broken)
        assert any(f"no {missing}" in line for line in found), (missing, found)
