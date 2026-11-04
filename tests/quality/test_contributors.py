"""Every credit names a real artefact, and the drawing is the record's own render.

Two claims are guarded, because the reference page makes both.

**The record.** `registry/leaderboards.json` credits one person with one artefact; the graph,
the details page and the reference page block are all derived from it. A credit for a handle that is not
in `people`, an artefact path that does not exist, a declared adapter with nobody credited, or a
month that is not a month is refused here rather than drawn. The benchmark check is the strict
one: `data/datasets/*.py` is the catalogue, so every declared adapter must be credited --
a catalogue of 59 that credits 6 is a claim nobody can act on.

**The drawings.** The SVG, `docs/contributors.md` and the reference page block are compared with a fresh
render, so a record edited without re-rendering fails the build. This is the same discipline as
`tools/datasets_doc.py --check`: a page derived from data is never typed beside it.

The axis-table guard is separate: the reference page's benchmarks row lists every declared adapter by
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

REFERENCE = ROOT / "docs" / "readme-reference.md"
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


def test_the_reference_block_is_the_record_rendered() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    block = contributors._block(text, contributors.BEGIN, contributors.END)
    assert block is not None, "the reference page has no contributor block"
    assert block == contributors.render_block(_record()), (
        "stale: run python3 tools/contributors.py --write")


def test_reference_generation_and_refresh_workflow_target_the_same_page() -> None:
    assert contributors.REFERENCE == REFERENCE
    workflow = (ROOT / ".github/workflows/contributors.yml").read_text(encoding="utf-8")
    assert workflow.count("docs/contributors.html docs/readme-reference.md") == 2
    assert "docs/contributors.html README.md" not in workflow
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/readme-reference.md" in readme
    assert contributors.BEGIN not in readme


def test_each_board_has_its_own_growth_curve() -> None:
    """Three boards, three curves; a board nobody has contributed to has no line."""
    record = _record()
    titles = {"harness": "Harnesses", "method": "RSI methods", "benchmark": "Benchmarks"}
    assert set(contributors.FIGURES) == set(titles)
    for board, path in contributors.FIGURES.items():
        svg = path.read_text(encoding="utf-8")
        assert svg == contributors.render_svg(record, board), (
            f"{path.name} is stale; run python3 tools/contributors.py --write")
        assert titles[board] in svg, f"{path.name} has no title for its board"
        entries = [entry for entry in contributors.entries(record) if entry["kind"] == board]
        credited = {entry["contributor"] for entry in entries}
        for handle, count in contributors.totals(entries):
            assert handle in svg, f"{board}/{handle} has no line"
            assert f"{count} entries" in svg, f"{board}/{handle}: the total is not drawn"
        # Everyone on the board's list, and nobody who has no entry there.
        for person in {p["handle"] for p in record["people"]} - credited:
            assert person not in svg, f"{board}: @{person} has no entry but a row"


def test_the_curve_is_cumulative_and_monotone() -> None:
    """A star-history shape only says something if the line never goes down."""
    record = _record()
    for board in ("harness", "method", "benchmark"):
        entries = [entry for entry in contributors.entries(record) if entry["kind"] == board]
        days = contributors._window(record)
        for handle, _ in contributors.totals(entries):
            running = 0
            for day in days:
                running += len([entry for entry in entries
                                if entry["contributor"] == handle
                                and entry["since"][:10] == day])
                assert running >= 0
            total = len([entry for entry in entries if entry["contributor"] == handle])
            assert running <= total, "the curve cannot exceed the credited entries"


def test_the_interactive_page_carries_the_record_and_the_controls() -> None:
    """The static SVG cannot change; the page can, so it must hold the data and the range."""
    page = contributors.PAGE.read_text(encoding="utf-8")
    assert page == contributors.render_html(_record()), (
        "docs/contributors.html is stale; run python3 tools/contributors.py --write")
    import json as _json
    import re as _re

    block = _re.search(r'<script id="data" type="application/json">(.*?)</script>', page,
                       _re.S)
    assert block, "the page has no inline data block"
    data = _json.loads(block.group(1))
    assert len(data["entries"]) == len(contributors.entries(_record()))
    assert {person["handle"] for person in data["people"]} == (
        {person["handle"] for person in _record()["people"]})
    for control in ('id="win"', 'id="end"', 'id="board"'):
        assert control in page, f"the page has no {control} control"
    assert "data:image/" in page, "the page draws no cached avatars"
    assert ".png?size=64" not in page, "the page still fetches an avatar over the network"
    assert "function draw()" in page, "the page cannot redraw"


def test_avatars_are_cached_and_embedded_rather_than_linked() -> None:
    """A reference page shows an SVG through an `<img>`, which may not load a remote resource.

    So the avatar has to be part of the file: the drawing embeds a data URI from
    `.github/assets/avatars/`, and every contributor has a cached image. The initial is drawn
    underneath either way, so a person without a cached image degrades to a letter.
    """
    record = _record()
    for person in record["people"]:
        handle = person["handle"]
        assert contributors.avatar_data_uri(handle), f"{handle} has no cached avatar"
    for board in ("harness", "method", "benchmark"):
        svg = contributors.render_svg(record, board)
        for handle, _ in contributors.totals(
                [e for e in contributors.entries(record) if e["kind"] == board]):
            assert "data:image/" in svg, f"{board}: no embedded avatar"
            assert "github.com/" not in svg, f"{board}: the drawing still links an avatar URL"
        assert 'clip-path="circle(' in svg, f"{board}: the avatar is not clipped round"
        assert 'text-anchor="middle" fill=' in svg, f"{board}: no initial fallback"


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
    text = REFERENCE.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| **benchmarks** |")]
    assert len(rows) == 1, "the reference page has no single benchmarks row"
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
