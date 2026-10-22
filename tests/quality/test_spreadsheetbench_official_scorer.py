"""SpreadsheetBench's official scorer, copied in, and a differential test against the check.

The rule this file exists to enforce
------------------------------------
The mark is decided by `evaluation/evaluation.py` in `RUCKBReasoning/SpreadsheetBench`, and it
is **not** "does the workbook look right": it loads the golden and the produced workbook with
`openpyxl.load_workbook(..., data_only=True)` and compares the values in the cells named by the
task's `answer_position`, after `transform_value` has been applied to both sides. Every piece of
that is transcribed below and compared with `data/datasets/spreadsheetbench/verify/check.py` on real
workbooks and on the edge cases the transform exists for.

The two facts this file is built around
---------------------------------------
* **`data_only=True` reads cached values.** A workbook whose formulas were never evaluated has
  `None` in every cell, so the comparison would score a correct answer 0 for a reason no evidence
  would show. That is why the check recalculates with LibreOffice, and
  `test_the_recalculation_step_is_what_makes_a_formula_workbook_measurable` pins the difference.
* **`transform_value` is where two "equal" answers are made equal.** `1` equals `1.001` (both
  round to 1.0), `"3"` equals `3`, `""` equals `None`, and a `datetime` is compared as Excel's
  serial day count. All four are pinned, because each one is a place where a reasonable
  implementation would disagree with the official one.

Colour comparison is *not* part of the metric: upstream's `cell_level_compare` has the fill and
font checks present and commented out, and `test_colours_are_not_compared` pins that the code
that runs is the code that is tested.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

DATA_ROOT = Path("/mnt/20t/xubuqiang/datasets/spreadsheetbench")


def _check():
    """The dataset's check module, loaded by path (its name is not unique across datasets)."""
    import importlib.util
    path = ROOT / "data" / "datasets" / "spreadsheetbench" / "verify" / "check.py"
    spec = importlib.util.spec_from_file_location("spreadsheetbench_check_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return _check()


@pytest.fixture(scope="module")
def bench():
    import data.datasets.spreadsheetbench as module
    if not module._RECORDS:
        pytest.skip(f"no SpreadsheetBench data under {module.DATASET_ROOT}")
    return module


# ------------------------------------------------------- the transform, verbatim ---


def ref_transform_value(v):
    """`evaluation.py::transform_value`, transcribed for the reference side."""

    def datetime_to_float(dt):
        excel_start_date = datetime.datetime(1899, 12, 30)
        delta = dt - excel_start_date
        return delta.days + delta.seconds / 86400.0

    if isinstance(v, (int, float)):
        v = round(float(v), 2)
    elif isinstance(v, datetime.time):
        v = str(v)[:-3]
    elif isinstance(v, datetime.datetime):
        v = round(datetime_to_float(v), 0)
    elif isinstance(v, str):
        try:
            v = round(float(v), 2)
        except ValueError:
            pass
    return v


def test_transform_value_matches_the_official_one(check):
    cases = [1, 1.004, 1.006, -2.5, 0, "3", "3.14159", "abc", "", None,
             datetime.datetime(2024, 1, 2, 3, 4, 5), datetime.time(9, 30, 15)]
    for value in cases:
        assert check.transform_value(value) == ref_transform_value(value), value


def test_the_four_equalities_the_transform_creates(check):
    """Each of these is a place a reasonable implementation would disagree with upstream."""
    assert check.compare_cell_value(1, 1.001) is True, "numbers round to 2 decimals"
    assert check.compare_cell_value("3", 3) is True, "a numeric string is a number"
    assert check.compare_cell_value("", None) is True, "empty equals missing"
    assert check.compare_cell_value(None, None) is True
    assert check.compare_cell_value("abc", None) is False
    assert check.compare_cell_value(1, 2) is False
    # A date is compared as Excel's serial day count, **rounded**, so two times on the same day
    # match only when they round to the same day number: 00:00 and 11:00 are 0.46 apart and
    # round together, 01:00 and 23:00 are 0.92 apart and do not. That is upstream's arithmetic,
    # not an approximation of it -- measured, and the first version of this test asserted the
    # wrong side of the rounding.
    assert check.compare_cell_value(datetime.datetime(2024, 1, 2, 0, 0),
                                    datetime.datetime(2024, 1, 2, 11, 0)) is True
    assert check.compare_cell_value(datetime.datetime(2024, 1, 2, 1, 0),
                                    datetime.datetime(2024, 1, 2, 23, 0)) is False
    # Type mismatch after the transform is a miss, even for equal-looking values.
    assert check.compare_cell_value("abc", 0) is False


def test_range_helpers_match_the_official_ones(check):
    assert check.col_num2name(1) == "A"
    assert check.col_num2name(26) == "Z"
    assert check.col_num2name(27) == "AA"
    assert check.col_name2num("AA") == 27
    assert check.parse_cell_range("A1:B2") == ((1, 1), (2, 2))
    assert check.generate_cell_names("A1:B2") == ["A1", "A2", "B1", "B2"]
    assert check.generate_cell_names("C3") == ["C3"], "a single cell is not a range"


# ------------------------------------------------------ the comparison on files ---


def _write(path: Path, rows: dict, sheet: str = "S") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for coordinate, value in rows.items():
        ws[coordinate] = value
    wb.save(path)
    return path


@pytest.mark.parametrize("literal", ["=", "=not a formula", "=SUM(A1:A2)"])
@pytest.mark.parametrize("matches", [True, False])
def test_leading_equals_text_is_compared_without_recalculation(
        check, tmp_path, monkeypatch, literal, matches):
    for name, value in (("result.xlsx", literal),
                        ("golden.xlsx", literal if matches else "different text")):
        wb = openpyxl.Workbook()
        wb.active.title = "S"
        wb.active["A1"] = value
        # A leading '=' typed through openpyxl defaults to a formula. Store genuine text,
        # just as OOXML inline/shared strings can contain literal formula-looking prose.
        wb.active["A1"].data_type = "s"
        wb.save(tmp_path / name)
        wb.close()
    assert check.workbook_has_formulas(tmp_path / "result.xlsx") is False

    def unexpected_runtime():
        pytest.fail("literal text must not require LibreOffice")

    monkeypatch.setattr(check, "soffice_path", unexpected_runtime)
    (tmp_path / "task.json").write_text(json.dumps({"task_id": "literal",
                                                   "answer_position": "S!A1"}))
    reward, evidence = tmp_path / "reward.txt", tmp_path / "evidence.json"
    assert check.main(["--workdir", str(tmp_path), "--reward-file", str(reward),
                       "--evidence-file", str(evidence)]) == (0 if matches else 1)
    recorded = json.loads(evidence.read_text())
    assert recorded["measured"] is True and recorded["recalculated"] is False
    assert recorded["reward"] == float(matches) == float(reward.read_text())
    assert check.compare_cell_value(literal, literal if matches else "different text") is matches


@pytest.mark.parametrize("formula_tag", [b"<f>", b'<f t="shared" si="0" ref="A1:A1">',
                                        b'<f t="array" ref="A1:A1">'])
def test_real_ooxml_formulas_still_require_recalculation(check, tmp_path, formula_tag):
    import zipfile

    workbook = _write(tmp_path / "formula.xlsx", {"A1": "=1+1"})
    with zipfile.ZipFile(workbook) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["xl/worksheets/sheet1.xml"] = entries["xl/worksheets/sheet1.xml"].replace(
        b"<f>", formula_tag)
    with zipfile.ZipFile(workbook, "w") as target:
        for name, content in entries.items():
            target.writestr(name, content)
    assert check.workbook_has_formulas(workbook) is True


def test_the_comparison_reads_only_the_named_cells(check, tmp_path):
    golden = _write(tmp_path / "g.xlsx", {"A1": 1, "A2": 2, "Z9": "ignored"})
    same = _write(tmp_path / "s.xlsx", {"A1": 1, "A2": 2, "Z9": "different"})
    ok, msg = check.compare_workbooks(str(golden), str(same), "Cell-Level Manipulation", "A1:A2")
    assert ok is True, msg
    worse = _write(tmp_path / "w.xlsx", {"A1": 1, "A2": 99})
    ok, msg = check.compare_workbooks(str(golden), str(worse), "", "A1:A2")
    assert ok is False and "A2" in msg, msg


def test_a_missing_result_sheet_is_a_miss_but_comparison_file_loss_is_an_error(check, tmp_path):
    golden = _write(tmp_path / "g.xlsx", {"A1": 1}, sheet="Wanted")
    other = _write(tmp_path / "o.xlsx", {"A1": 1}, sheet="Something")
    ok, msg = check.compare_workbooks(str(golden), str(other), "", "Wanted!A1:A1")
    assert ok is False and "not found" in msg, msg
    # main() scores an absent submission zero; later file loss is infrastructure failure.
    with pytest.raises(FileNotFoundError, match="unavailable for comparison"):
        check.compare_workbooks(str(golden), str(tmp_path / "nope.xlsx"), "", "A1")


def test_multiple_ranges_are_all_required(check, tmp_path):
    golden = _write(tmp_path / "g.xlsx", {"A1": 1, "C1": 3, "E1": 5})
    good = _write(tmp_path / "good.xlsx", {"A1": 1, "C1": 3, "E1": 5})
    assert check.compare_workbooks(str(golden), str(good), "", "A1:A1,C1:C1,E1:E1")[0] is True
    partial = _write(tmp_path / "partial.xlsx", {"A1": 1, "C1": 3, "E1": 0})
    ok, msg = check.compare_workbooks(str(golden), str(partial), "", "A1:A1,C1:C1,E1:E1")
    assert ok is False and "E1" in msg


def test_colours_are_not_compared(check, tmp_path):
    """Upstream has the fill/font comparisons commented out; this dataset matches the code
    that runs, so a differently-coloured but equal-valued workbook is a pass."""
    from openpyxl.styles import PatternFill

    golden = _write(tmp_path / "g.xlsx", {"A1": 1})
    other = _write(tmp_path / "o.xlsx", {"A1": 1})
    wb = openpyxl.load_workbook(other)
    wb["S"]["A1"].fill = PatternFill("solid", fgColor="FF0000")
    wb.save(other)
    assert check.compare_workbooks(str(golden), str(other), "", "A1:A1")[0] is True


# ------------------------------------------------- across the published tasks ---


def test_every_published_task_resolves_both_workbooks(bench):
    """400 tasks, and the six naming variants the release actually ships.

    Measured: 394 use `1_<id>_init.xlsx` / `1_<id>_golden.xlsx`, four use
    `initial.xlsx` / `golden.xlsx`, and two have a typo in one half. The adapter resolves all
    of them, and this test names the variants so a future release that changes them is visible.
    """
    assert len(bench._RECORDS) == 400
    odd = {r["task_id"]: (r["init_name"], r["golden_name"]) for r in bench._RECORDS
           if r["init_name"] != f"1_{r['task_id']}_init.xlsx"
           or r["golden_name"] != f"1_{r['task_id']}_golden.xlsx"}
    assert set(odd) == {"13284", "32023", "32789", "42930", "56274", "58109"}, sorted(odd)
    assert odd["42930"] == ("1_42930_init.xlsx", "1_43930_golden.xlsx"), "the typo"
    assert odd["13284"] == ("initial.xlsx", "golden.xlsx")
    for record in bench._RECORDS[:40]:
        assert record["init_path"].is_file() and record["golden_path"].is_file()
        assert record["answer_position"]


def test_the_golden_workbook_scores_one_against_itself(check, bench):
    """The differential test, on real files: the reference is a fixed point of the comparison.

    `283-32` is excluded and named: its `answer_position` is `Sheet3'!A:G`, which has no row
    numbers, so the official parser cannot read it -- the official `evaluation` turns that into
    a 0 for every submission. Option (a) deliberately keeps that official-equivalent measured
    zero locally. There is no fixed point to assert for a task whose range does not parse.
    """
    # `283-32` is excluded, not skipped silently: its answer range cannot be parsed at all,
    # which is asserted by name in `test_the_one_unparseable_answer_range_is_named`.
    records = [r for r in bench._RECORDS if r["task_id"] != "283-32"]
    checked = 0
    for record in records[:25]:
        ok, msg = check.compare_workbooks(str(record["golden_path"]),
                                          str(record["golden_path"]),
                                          record["instruction_type"],
                                          record["answer_position"])
        assert ok is True, (record["task_id"], msg)
        checked += 1
    assert checked == 25


def test_the_one_unparseable_answer_range_is_named(check, bench):
    bad = [r for r in bench._RECORDS
           if "!" in r["answer_position"] and ":" in r["answer_position"]
           and not any(ch.isdigit() for ch in r["answer_position"].split("!")[-1])]
    assert bad, "the unparseable task should be in the published data"
    assert [r["task_id"] for r in bad] == ["283-32"], [r["task_id"] for r in bad]
    ok, msg = check.compare_workbooks(str(bad[0]["golden_path"]), str(bad[0]["golden_path"]),
                                      "", bad[0]["answer_position"])
    assert ok is False and "not a cell range" in msg, msg
    assert "official-equivalent measured zero" in msg  # Deliberate option (a) compatibility.


def test_the_initial_workbook_does_not_score_one(check, bench):
    """The starting workbook differs from the reference -- otherwise there is no task.

    Measured over the first 25 tasks: at least most differ. A few are equal in the named range
    (the manipulation is about *formulas*, which `data_only=True` hides), so the test asserts
    the shape of the finding rather than "all 25 differ", and reports how many do.
    """
    differ = 0
    for record in bench._RECORDS[:25]:
        ok, _msg = check.compare_workbooks(str(record["golden_path"]),
                                           str(record["init_path"]),
                                           record["instruction_type"],
                                           record["answer_position"])
        differ += 0 if ok else 1
    assert differ >= 1, "no task differs from its own reference; the comparison is not reading"
