"""Every credit names a real artefact, and the drawing is the record's own render.

Two claims are guarded, because the README makes both.

**The record.** `registry/leaderboards.json` credits one person with one artefact; the graph,
the details page and the README block are all derived from it. A credit for a handle that is not
in `people`, an artefact path that does not exist, a declared adapter with nobody credited, or a
month that is not a month is refused here rather than drawn. The benchmark check is the strict
one: `data/datasets/*.py` is the catalogue, so every declared adapter must be credited --
a catalogue of 59 that credits 6 is a claim nobody can act on.

**The drawings.** The SVG, `docs/contributors.md` and the README block are compared with a fresh
render, so a record edited without re-rendering fails the build. This is the same discipline as
`tools/datasets_doc.py --check`: a page derived from data is never typed beside it.

The axis-table guard is separate: the README's benchmarks row lists every declared adapter by
name, and it is hand-written prose around one generated row of names, so a test reads it back.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import contributors  # noqa: E402

README = ROOT / "README.md"
RECORD = ROOT / "registry" / "leaderboards.json"


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_the_record_is_valid() -> None:
    found = contributors.problems(_record())
    assert not found, "\n".join(found)


def test_every_declared_adapter_is_credited() -> None:
    """The catalogue is 59; the credits must be 59, not the six that were convenient."""
    record = _record()
    credited = {entry["name"] for entry in record["boards"]["benchmark"]}
    assert credited == set(contributors.declared_benchmarks()), (
        sorted(set(contributors.declared_benchmarks()) ^ credited))


def test_three_people_and_every_one_has_a_credit() -> None:
    people = {person["handle"] for person in _record()["people"]}
    assert people == {"Xubqpanda", "crepuscular-drizzle", "trancefornow"}, people
    credited = {entry["contributor"] for entry in contributors.entries(_record())}
    assert credited == people, "a person is listed with no credit or a credit has no person"


def test_the_readme_block_is_the_record_rendered() -> None:
    text = README.read_text(encoding="utf-8")
    block = contributors._block(text, contributors.BEGIN, contributors.END)
    assert block is not None, "the README has no contributor block"
    assert block == contributors.render_block(_record()), (
        "stale: run python3 tools/contributors.py --write")


def test_the_svg_is_the_record_rendered_and_has_a_tooltip_per_cell() -> None:
    svg = contributors.FIGURE.read_text(encoding="utf-8")
    assert svg == contributors.render_svg(_record()), (
        "the graph is stale; run python3 tools/contributors.py --write")
    tooltips = re.findall(r"<title>([^<]+)</title>", svg)
    assert tooltips, "the graph has no tooltips, so a square says nothing"
    for tooltip in tooltips:
        assert "· @", tooltip
    # One tooltip per non-empty (person, month) cell.
    expected = {(entry["contributor"], entry["since"][:7])
                for entry in contributors.entries(_record())}
    assert len(tooltips) == len(expected), (len(tooltips), len(expected))


def test_the_right_column_reports_total_and_rank() -> None:
    svg = contributors.render_svg(_record())
    order = contributors.totals(contributors.entries(_record()))
    for rank, (handle, count) in enumerate(order, start=1):
        assert f'>{count}</text>' in svg, f"{handle}: total is not drawn"
        assert f"#{rank} of {len(order)}" in svg, f"{handle}: rank is not drawn"


def test_the_details_page_lists_every_entry_per_person() -> None:
    text = contributors.DETAILS.read_text(encoding="utf-8")
    assert text == contributors.render_details(_record()), (
        "docs/contributors.md is stale; run python3 tools/contributors.py --write")
    for entry in contributors.entries(_record()):
        assert f"`{entry['name']}`" in text, f"{entry['kind']}/{entry['name']} is not listed"
    for handle in {person["handle"] for person in _record()["people"]}:
        assert f'id="{handle.lower()}"' in text, f"no anchor for @{handle}"


def test_the_benchmarks_row_lists_every_declared_adapter() -> None:
    """The axis table's benchmarks cell is the catalogue a reader can count."""
    text = README.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| **benchmarks** |")]
    assert len(rows) == 1, "the README has no single benchmarks row"
    row = rows[0]
    names = contributors.declared_benchmarks()
    for name in names:
        assert f"`{name}`" in row, f"{name} is declared but missing from the row"
    assert f"{len(names)} declared" in row


def test_an_unknown_contributor_is_refused() -> None:
    record = _record()
    broken = json.loads(json.dumps(record))
    broken["boards"]["harness"][0]["contributor"] = "somebody-new"
    found = contributors.problems(broken)
    assert any("somebody-new" in line for line in found), found


def test_a_credit_without_an_adapter_is_refused() -> None:
    record = _record()
    broken = json.loads(json.dumps(record))
    broken["boards"]["benchmark"] = broken["boards"]["benchmark"][:-1]
    found = contributors.problems(broken)
    assert any("declared but not credited" in line for line in found), found
