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


def test_every_listed_contributor_has_a_credit() -> None:
    people = {person["handle"] for person in _record()["people"]}
    assert {"Xubqpanda", "crepuscular-drizzle", "trancefornow", "harbor-framework"} <= people
    credited = {entry["contributor"] for entry in contributors.entries(_record())}
    assert credited == people, "a person is listed with no credit or a credit has no person"


def test_the_readme_block_is_the_record_rendered() -> None:
    text = README.read_text(encoding="utf-8")
    block = contributors._block(text, contributors.BEGIN, contributors.END)
    assert block is not None, "the README has no contributor block"
    assert block == contributors.render_block(_record()), (
        "stale: run python3 tools/contributors.py --write")


def test_each_board_has_its_own_growth_curve() -> None:
    """Three boards, three curves; a board nobody has contributed to has no line."""
    record = _record()
    titles = {"harness": "Harnesses", "method": "RSI methods", "benchmark": "Benchmarks"}
    assert set(contributors.FIGURES) == set(titles)
    for board, path in contributors.FIGURES.items():
        svg = path.read_text(encoding="utf-8")
        assert contributors.snapshot_date(path) is not None
        assert svg == contributors.render_svg(record, board, contributors.snapshot_date(path)), (
            f"{path.name} is stale; run python3 tools/contributors.py --write")
        assert titles[board] in svg, f"{path.name} has no title for its board"
        entries = [entry for entry in contributors.entries(record) if entry["kind"] == board
                   and entry['since'] <= contributors.snapshot_date(path)]
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
        items = [entry for entry in contributors.entries(record) if entry["kind"] == board]
        days = contributors._window(record)
        for handle, values in contributors.cumulative_series(items, days).items():
            assert values == sorted(values)
            assert values[-1] == sum(e['contributor'] == handle and e['since'] <= days[-1] for e in items)


def test_window_and_cumulative_edges() -> None:
    days = contributors._window({'updated': '2024-03-10'})
    assert len(days) == 30 and days[0] == '2024-02-10' and days[-1] == '2024-03-10'
    assert '2024-02-29' in days
    def credit(date):
        return {'contributor': 'alice', 'since': date, 'name': 'x', 'kind': 'harness'}
    items = [credit('2024-02-09') for _ in range(10)] + [credit('2024-02-10') for _ in range(2)] + [credit('2024-02-11'), credit('2024-03-10'), credit('2024-03-11')]
    points = contributors.cumulative_series(items, days)['alice']
    assert points[0] == 12 and points[1] == 13 and points[-1] == 14
    assert contributors.cumulative_series([credit('2024-02-09')] * 10, days)['alice'] == [10] * 30
    assert contributors.cumulative_series([], days) == {}
    assert contributors.cumulative_series([credit('2024-03-11')], days) == {}
    record = {'updated': days[-1], 'boards': {'harness': items, 'method': [], 'benchmark': []}}
    svg = contributors.render_svg(record, 'harness')
    assert '2024-02-10 · @alice · cumulative 12 of 14' in svg
    assert '2024-03-10 · @alice · cumulative 14 of 14' in svg
    assert '14 entries' in svg and '15 entries' not in svg
    empty = contributors.render_svg(record, 'method', '2024-03-10')
    assert 'data-as-of="2024-03-10"' in empty and 'No entries as of 2024-03-10' in empty


def test_interactive_cumulative_window_executes_in_node() -> None:
    import shutil
    import subprocess
    import pytest

    if not shutil.which('node'):
        pytest.skip('node not installed')
    days = contributors._window({'updated': '2024-03-10'})
    items = [{'name': str(i), 'contributor': 'alice', 'since': date}
             for i, date in enumerate(['2024-02-09'] * 10 + ['2024-02-10'] * 2 +
                                      ['2024-02-11', '2024-03-10', '2024-03-11'])]
    record = {'updated': days[-1], 'people': [{'handle': 'alice'}],
              'boards': {'harness': items, 'method': [], 'benchmark': []}}
    page = contributors.render_html(record)
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    script = page.split('<script>')[-1].split('</script>')[0]
    harness = r'''
const RealDate = Date;
global.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : ['2024-03-10T12:00:00Z'])); } };
const made = [];
function element(tag) { return {tag, children: [], style: {}, value: '', textContent: '',
  setAttribute(k,v) { this[k]=v; }, appendChild(x) { this.children.push(x); },
  addEventListener(k,f) { this[k]=f; }}; }
const nodes = Object.fromEntries(['data','sub','tip','win','end','board','panels'].map(x => [x,element(x)]));
nodes.data.textContent = DATA_JSON; nodes.win.value = '30'; nodes.board.value = 'harness';
global.document = {getElementById: x => nodes[x], createElement: element,
  createElementNS: (_, tag) => { const n = element(tag); made.push(n); return n; }};
'''.replace('DATA_JSON', json.dumps(data))
    checks = r'''
const labels = made.filter(n => n.tag === 'text').map(n => n.textContent);
if (!labels.includes('14 entries') || labels.includes('15 entries')) throw Error(JSON.stringify(labels));
const circles = made.filter(n => n.tag === 'circle' && n.mousemove);
if (circles.length !== 3) throw Error('wrong point count ' + circles.length);
circles[0].mousemove({clientX:0,clientY:0});
if (!nodes.tip.innerHTML.includes('cumulative 12')) throw Error(nodes.tip.innerHTML);
'''
    subprocess.run(['node', '-e', harness + script + checks], check=True, capture_output=True, text=True)


def test_invalid_dates_are_refused() -> None:
    record = _record()
    record['boards']['harness'][0]['since'] = '2024-02-30'
    record['updated'] = '2024-13-01'
    found = contributors.problems(record)
    assert any('invalid since date' in error for error in found)
    assert 'invalid updated date' in found


def test_generated_block_is_only_figures() -> None:
    block = contributors.render_block(_record())
    assert block.count('![') == 3
    assert 'Measured results' not in block and '| # |' not in block


def test_isolated_write_and_check(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(contributors, 'ROOT', tmp_path)
    monkeypatch.setattr(contributors, 'README', tmp_path / 'README.md')
    monkeypatch.setattr(contributors, 'DETAILS', tmp_path / 'docs/contributors.md')
    monkeypatch.setattr(contributors, 'PAGE', tmp_path / 'docs/contributors.html')
    monkeypatch.setattr(contributors, 'FIGURES', {b: tmp_path / f'.github/assets/contributors-{b}.svg' for b in contributors.BOARDS})
    monkeypatch.setattr(contributors, 'declared_benchmarks', lambda: [e['name'] for e in _record()['boards']['benchmark']])
    monkeypatch.setattr(contributors, 'problems', lambda record: [])
    monkeypatch.setattr(contributors, 'utc_today', lambda: '2026-11-04')
    (tmp_path / 'docs').mkdir()
    contributors.README.write_text('before\n' + contributors.BEGIN + '\nstale\n' + contributors.END + '\nafter\n')
    monkeypatch.setattr(sys, 'argv', ['contributors.py', '--write'])
    assert contributors.main() == 0
    assert contributors.README.read_text().endswith('\nafter\n')
    assert {contributors.snapshot_date(path) for path in contributors.FIGURES.values()} == {'2026-11-04'}
    monkeypatch.setattr(contributors, 'utc_today', lambda: '2026-11-05')
    monkeypatch.setattr(sys, 'argv', ['contributors.py', '--check'])
    assert contributors.main() == 0  # yesterday's snapshot remains reproducible
    monkeypatch.setattr(sys, 'argv', ['contributors.py', '--check', '--as-of', '2026-11-05'])
    assert contributors.main() == 1
    monkeypatch.setattr(sys, 'argv', ['contributors.py', '--write', '--as-of', '2026-11-03'])
    assert contributors.main() == 0
    assert {contributors.snapshot_date(path) for path in contributors.FIGURES.values()} == {'2026-11-03'}
    monkeypatch.setattr(sys, 'argv', ['contributors.py', '--check'])
    assert contributors.main() == 0
    path = contributors.FIGURES['harness']
    original = path.read_text()
    path.write_text(original.replace('data-as-of="2026-11-03"', 'data-as-of="2026-11-04"'))
    assert contributors.main() == 1  # mixed snapshots
    for path in contributors.FIGURES.values():
        path.write_text(path.read_text().replace('data-as-of="2026-11-03"', 'data-as-of="2026-11-06"').replace('data-as-of="2026-11-04"', 'data-as-of="2026-11-06"'))
    assert contributors.main() == 1  # future snapshot
    assert 'snapshot date' in capsys.readouterr().err


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
    """A README shows an SVG through an `<img>`, which may not load a remote resource.

    Cached avatars are embedded, and contributors without a cached image must
    remain visible via their initial; rendering never fetches remote images.
    """
    record = _record()
    harbor_avatar = contributors.avatar_data_uri("harbor-framework")
    assert harbor_avatar and harbor_avatar.startswith("data:image/png;base64,")
    assert harbor_avatar in contributors.render_svg(record, "benchmark")
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
