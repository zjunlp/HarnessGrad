"""`docs/dataset-splits.md` is generated from the adapters, so it cannot drift silently.

Why a test and not a comment: the document is a table of 39 rows, each of which is a claim
about what a dataset's division is. A row that stops matching its adapter is a reader being
told the wrong division -- the same failure the document exists to prevent -- and nothing
else in the suite would notice. `tools/split_inventory.py --check` renders every row from
the modules and compares, so the fix is always `--write`, never a hand edit.

Two halves are tested, the same way `tests/quality/test_datasets_doc.py` tests
`DATASETS.md`:

* a fresh render is self-consistent: `--write` then `--check` passes, and a single changed
  byte fails -- written into `tmp_path`, so a run never mutates the checked-in file;
* the checked-in copy carries **no host-local fact**. The old table called
  `module.load()` for every dataset, so it recorded "not provisioned" (and a
  `FileNotFoundError` path from the machine that rendered it) for whichever payloads that
  machine did not have -- measured 2026-11-02, it still named `cooperbench`, `costbench`,
  `kumo` and `personamem_v2` after all four had been provisioned, and a fresh clone failed
  `--check` for every dataset. `--check` must therefore pass with every adapter's `load()`
  raising `FileNotFoundError`, which is what a machine with no dataset bytes at all looks
  like, while `--host` -- the one consumer of `load()` -- shows the flipped answers.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402
import tools.split_inventory as inventory  # noqa: E402

DOC_PATH = ROOT / "docs" / "dataset-splits.md"

#: A table row starts with the dataset name in a code span; prose and bullets do not.
ROW = re.compile(r"^\| `([a-z0-9_]+)` \|")

#: Cell positions, 0-based, in the order `table()` writes the row. Named rather than
#: counted at each use so that a reordered column breaks one place, not six assertions.
STATE, SOURCE, TRAIN, EVAL, OFFICIAL = 1, 2, 3, 4, 5

#: The datasets whose adapter refuses the benchmark by design (`REFUSED`).
REFUSED = ("api_bank", "nestful", "sotuqa", "tau2_bench")

def _published_in(state: str) -> tuple[str, ...]:
    """Every published dataset the tool classifies as `state`, derived -- never listed.

    These used to be three hand-written tuples (`DECLARED`, `UNDECLARED`, `REFUSED`) and
    they rotted exactly as fast as the registry grows: the upstream delivery added three
    declared datasets and PR 31 moved `strongreject` from `undeclared` to `none`, and each
    time this module failed for a reason that had nothing to do with the property it
    tests. `row_for` reads declarations only (no payload), so deriving the groups costs a
    dataset import and is the same answer the tool prints.
    """
    return tuple(name for name in registry.published()
                 if inventory.row_for(name).state == state)


DECLARED = _published_in("declared")
UNDECLARED = _published_in("undeclared")
REFUSED = _published_in("refused")

#: Every spelling the generated table may use for "no size is stated here". A digit would
#: be a fact about a disk, so it may not appear in a `train`/`eval`/`OFFICIAL_EVAL` cell.
NO_NUMBER = frozenset({inventory.DASH, inventory.COMPUTED, inventory.ABSENT,
                       "None", "None (the pool is the scored split)",
                       "[] (not an evaluation set)"})


def _generated(text: str) -> list[str]:
    """The lines between the two `split-inventory` markers -- what `--write` owns."""
    assert inventory.BEGIN in text and inventory.END in text, "the document lost its markers"
    body = text.split(inventory.BEGIN, 1)[1].split(inventory.END, 1)[0]
    return body.splitlines()


def _cells(line: str) -> list[str]:
    """The cells of one markdown row. `\\|` is an escaped pipe, not a separator."""
    body = line.strip()
    assert body.startswith("|") and body.endswith("|"), line
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body[1:-1])]


def _table(text: str) -> dict[str, list[str]]:
    """Dataset -> cells, for every generated row."""
    out: dict[str, list[str]] = {}
    for line in _generated(text):
        match = ROW.match(line)
        if match:
            out[match.group(1)] = _cells(line)
    return out


def _host_table(text: str) -> dict[str, str]:
    """Dataset -> state, for the `--host` view only."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            out[match.group(1)] = _cells(line)[STATE]
    return out


def _row(text: str, name: str) -> list[str]:
    rows = _table(text)
    assert name in rows, f"no row for {name}"
    return rows[name]


def _no_bytes(monkeypatch) -> list[str]:
    """Make every adapter's `load()` raise, as on a machine with no dataset at all.

    Returns the names whose `load()` was patched. The patch is applied to the real adapter
    modules, so it reaches `--host` through the tool's `_load()` seam exactly as a missing
    payload would -- and the message is unique to the patch, which is what proves it fired.
    """
    patched: list[str] = []
    for name in registry.published():
        module = importlib.import_module(registry.module_name(name))
        if not callable(getattr(module, "load", None)):
            continue

        def absent(*_args, **_kwargs):
            raise FileNotFoundError("no dataset bytes on this machine")

        monkeypatch.setattr(module, "load", absent)
        patched.append(name)
    assert patched, "no adapter exposed a load() to patch"
    return patched


def test_write_then_check_passes_and_one_changed_byte_fails(tmp_path):
    """The generated table is self-consistent, deterministic, and `--check` notices.

    The copy lives in `tmp_path`: a suite that rewrote the checked-in file would make
    every run a commit-worthy diff, and the point of this test is the generator, not the
    artifact.
    """
    target = tmp_path / "dataset-splits.md"
    target.write_text(DOC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert inventory.main(["--write", "--path", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert text == DOC_PATH.read_text(encoding="utf-8"), (
        "a fresh --write no longer reproduces the committed file")
    assert inventory.main(["--check", "--path", str(target)]) == 0

    # One byte, inside the region the document claims is generated.
    changed = text.replace("| `agentdojo` | none |", "| `agentdojo` | non |", 1)
    assert len(changed) == len(text) - 1, "the test changed more than a byte"
    target.write_text(changed, encoding="utf-8")
    assert inventory.main(["--check", "--path", str(target)]) == 1


def test_every_published_dataset_appears_exactly_once():
    """Published, once each. A missing row is a dataset a reader cannot look up."""
    names = list(_table(DOC_PATH.read_text(encoding="utf-8")))
    assert len(names) == len(set(names)), f"duplicate rows: {sorted(names)}"
    assert set(names) == set(registry.published()), (
        "docs/dataset-splits.md and registry.published() disagree; run "
        "python3 tools/split_inventory.py --write")
    # Sorted by dataset, so a reader can find a name without scanning.
    assert names == sorted(names)


def test_the_committed_document_carries_no_host_fact():
    """Every cell is a fact about the repository, not about the machine that rendered it.

    The old table had a `tasks` column and a `not provisioned` state, both read from
    `load()`, plus a bullet list of the `FileNotFoundError` each absent payload raised. A
    committed file that changes when a dataset is provisioned cannot be checked on another
    machine, so none of those spellings may appear at all -- not in a row, and not in the
    prose that explains the columns.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "not provisioned" not in text, (
        "the committed document still names the host-only state")
    assert "FileNotFoundError" not in text, (
        "the committed document still carries an exception from one machine")
    # No path from the machine that generated the file either: the data root is resolved
    # per host by `data/scaffolds/roots.py`, so the document names no disk at all.
    for prefix in ("/mnt/", "/home/", "~/.local"):
        assert prefix not in text, f"the committed document contains {prefix!r}"
    rows = _table(text)
    for name, cells in rows.items():
        assert cells[STATE] != "not provisioned", name
        assert cells[STATE] in {"declared", "none", "undeclared", "refused"}, (
            f"{name}'s state is not one of the four declared states: {cells[STATE]!r}")


def test_no_committed_cell_prints_a_size():
    """A size answers "how many tasks are on this disk", so no cell may print one.

    `train`/`eval`/`OFFICIAL_EVAL` are declarations, and the only declared answers are the
    absence markers in `NO_NUMBER`. A digit in one of them would be the bug this whole
    split is about -- and it would silently change when a payload arrives.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    rows = _table(text)
    for name, cells in rows.items():
        for index in (TRAIN, EVAL, OFFICIAL):
            cell = cells[index]
            assert cell in NO_NUMBER, (
                f"{name}'s {index} cell states a value that may come from the payload: "
                f"{cell!r}")
            assert not any(character.isdigit() for character in cell), (
                f"{name}'s cell carries a number: {cell!r}")
    for name in DECLARED:
        assert _row(text, name)[STATE] == "declared", name
        assert _row(text, name)[TRAIN] == inventory.COMPUTED, name
        assert _row(text, name)[EVAL] == inventory.COMPUTED, name
    for name in REFUSED:
        assert _row(text, name) == [f"`{name}`", "refused", "-", "-", "-", "-"], name
    for name in UNDECLARED:
        assert _row(text, name)[STATE] == "undeclared", name
        assert _row(text, name)[SOURCE] == inventory.ABSENT, name


def test_the_refused_and_none_rows_keep_their_official_answer():
    """`None` and `[]` are declarations and the rows must keep them.

    They are the two honest one-sided answers -- the pool *is* the scored split, or the
    pool is not an evaluation set -- and losing them to a generic marker would throw away
    the only part of the column that is a repository fact.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert _row(text, "reasoning_gym")[OFFICIAL] == "[] (not an evaluation set)"
    for name in ("aime", "strategyqa", "agentdojo"):
        assert _row(text, name)[OFFICIAL] == "None (the pool is the scored split)", name
    # `tau3_bench` states `None` literally even though its division is declared.
    assert _row(text, "tau3_bench")[OFFICIAL] == "None"
    # A plain `None` means "state not declared", not "no study material".
    assert _row(text, "tau3_bench")[STATE] == "declared"


def test_check_is_invariant_to_a_machine_with_no_dataset_bytes(monkeypatch, tmp_path):
    """`--check` compares declarations, not this machine's disk.

    Every adapter's `load()` raises `FileNotFoundError` -- a host with no dataset bytes at
    all -- and `--check` must still pass, because no committed cell reads a payload. Real
    drift is still caught on such a machine, so the guarantee is "fails only on drift",
    not "never fails".
    """
    patched = _no_bytes(monkeypatch)
    assert set(patched) == set(registry.published()), "not every adapter's load() was patched"

    # The tool's only payload reader is `_load`; prove the committed render cannot reach
    # it, on top of the per-adapter patch above.
    def explode(_module):
        raise AssertionError("the committed render read a dataset payload")

    monkeypatch.setattr(inventory, "_load", explode)
    assert inventory.main(["--check"]) == 0

    drifted = tmp_path / "dataset-splits.md"
    drifted.write_text(DOC_PATH.read_text(encoding="utf-8").replace(
        "| --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --x |", 1), encoding="utf-8")
    assert inventory.main(["--check", "--path", str(drifted)]) == 1


def test_host_shows_the_flipped_answers(monkeypatch, capsys):
    """`--host` is the view that *does* read the payload, and it flips with the patch.

    This is the proof that the patch in the test above is real: the same monkeypatch that
    leaves `--check` untouched turns every runnable dataset into the absent state in the
    host view, with the patched message. Without it, `--host` would have answered the same
    as before -- and the whole point of the split would be untested.
    """
    patched = _no_bytes(monkeypatch)
    assert set(patched) == set(registry.published()), "the patch did not cover every adapter"
    capsys.readouterr()
    assert inventory.main(["--host"]) == 0
    printed = capsys.readouterr().out
    assert "no dataset bytes on this machine" in printed, (
        "the patched load() did not reach --host, so it is not proof of anything")
    host = _host_table(printed)
    runnable = [name for name in registry.published() if name not in REFUSED]
    for name in runnable:
        assert host[name] == "not provisioned", name
    for name in REFUSED:
        assert host[name] == "refused", name
    # Every patched dataset is named in the local list, with the exception text.
    assert printed.count("` -- FileNotFoundError: no dataset bytes on this machine") == len(
        runnable)
    # The same answer, in the machine-readable form: the payload view carries the detail.
    assert inventory.main(["--host", "--json"]) == 0
    host_json = json.loads(capsys.readouterr().out)
    detail = "FileNotFoundError: no dataset bytes on this machine"
    for row in host_json:
        if row["dataset"] in REFUSED:
            assert row["detail"] == "", row["dataset"]
        else:
            assert row["detail"] == detail, row["dataset"]
            assert row["tasks"] == inventory.DASH, row["dataset"]


def test_host_is_print_only(tmp_path):
    """`--host` is a question about this machine, so it may never reach a mode that
    writes or trusts the committed file; the parser refuses the combination."""
    before = DOC_PATH.read_bytes()
    target = tmp_path / "dataset-splits.md"
    target.write_text(DOC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    for argv in (["--write", "--path", str(target), "--host"],
                 ["--check", "--path", str(target), "--host"]):
        with pytest.raises(SystemExit) as caught:
            inventory.main(argv)
        assert caught.value.code == 2, argv
    assert target.read_text(encoding="utf-8") == before.decode("utf-8"), (
        "a mode ran even though --host was rejected")
    assert DOC_PATH.read_bytes() == before


def test_json_keeps_its_shape_and_its_size_out_of_the_committed_view(capsys):
    """`--json` stays machine-readable with the keys callers already had.

    The values of the payload-derived keys (`tasks`, `train`, `eval`, `official`) are now
    the explicit marker unless `--host` asks for this host's numbers, so a caller that wants
    a size has to say which machine it means.
    """
    keys = {"dataset", "state", "source", "tasks", "train", "eval", "official", "detail"}
    capsys.readouterr()
    assert inventory.main(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["dataset"] for row in rows} == set(registry.published())
    for row in rows:
        assert set(row) == keys, row["dataset"]
        assert row["detail"] == "", row["dataset"]
        assert row["tasks"] == inventory.COMPUTED or row["tasks"] == inventory.DASH
        assert "FileNotFoundError" not in json.dumps(row)

    assert inventory.main(["--json", "--host"]) == 0
    host = json.loads(capsys.readouterr().out)
    assert {row["dataset"] for row in host} == set(registry.published())
    for row in host:
        assert set(row) == keys, row["dataset"]


def test_json_never_combines_with_write_or_check(tmp_path):
    """`--json` is a print mode; a `--write --json` that silently wrote would be a trap."""
    target = tmp_path / "dataset-splits.md"
    for argv in (["--json", "--write", "--path", str(target)],
                 ["--json", "--check", "--path", str(target)]):
        with pytest.raises(SystemExit) as caught:
            inventory.main(argv)
        assert caught.value.code == 2, argv
    assert not target.exists()


def test_the_split_inventory_matches_every_adapter():
    """The checked-in file is the artifact a reader opens, so `--check` is run for real."""
    import subprocess

    proc = subprocess.run([sys.executable, "tools/split_inventory.py", "--check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr or proc.stdout
