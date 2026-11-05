"""`DATASETS.md` is generated, checked in, and must not drift from the adapters.

The document exists so that a reader deciding whether a dataset is usable does not have to
read four files per dataset: the adapter for the metric, `downloads()` and `ORACLE_SOURCE`,
the control table in `tests/quality/test_dataset_controls.py` and `DOWNLOADS.md` for the
pin. It is also a table of *claims*, and a stale row is a reader being told the wrong thing
-- the same failure the document exists to prevent. So both halves are tested:

* a fresh render is self-consistent: `--write` then `--check` passes, and a single changed
  byte fails -- written into `tmp_path`, so a run never mutates the checked-in file;
* the checked-in copy still matches the adapters and carries **no host-local fact**. The
  document is committed and read on other machines, so "are the bytes here?" is not a
  cell: `--check` must pass with `provision_status()` monkeypatched to the opposite answer,
  and with it raising, which is what a machine with no dataset bytes at all looks like.

The unimportable-adapter and provision-status cases monkeypatch the tool's module seams
rather than adding a broken file to `data/datasets/` or touching the data root: the
property under test is the renderer's, and an adapter that misbehaves is exactly where a
table is most tempting to let crash -- or to start describing one host.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry  # noqa: E402
import tools.datasets_doc as doc  # noqa: E402

DOC_PATH = ROOT / "DATASETS.md"

#: A table row starts with the dataset name in a code span; notes and bullets do not.
ROW = re.compile(r"^\| `([a-z0-9_]+)` \|")

#: Cell positions, 0-based, in the order `render()` writes the row. Named rather than
#: counted at each use so that a reordered column breaks one place, not six assertions.
KIND, METRIC, DEVIATION, PROVISION, REFUSAL = 1, 2, 3, 5, 8

def _declared_refusals() -> tuple[str, ...]:
    """The adapters that declare `REFUSED`, read as a literal rather than imported.

    Derived rather than listed: the set became empty on 2026-11-04, when `tau2_bench` -- the
    last refusal -- became an ordinary dataset, and a hand-written tuple would have kept
    asserting a row that no longer says that. Read with `ast` because importing every adapter
    here would make this module need the dataset storage to be configured, which is the one
    thing `docs/DATASETS.md` is written not to depend on.
    """
    found = []
    for path in sorted((ROOT / "data" / "datasets").glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = node.targets if isinstance(node, ast.Assign) else []
            if not any(isinstance(t, ast.Name) and t.id == "REFUSED" for t in targets):
                continue
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                continue
            if isinstance(value, str) and value.strip():
                found.append(path.stem)
    return tuple(found)
REFUSED = _declared_refusals()

#: The three spellings a host answer takes. The header may explain them in prose; a
#: *readiness cell* may not be one of them, because it would describe the machine that ran
#: the command rather than the dataset.
HOST_ANSWERS = frozenset({"yes", "no", "unknown"})

#: The command the `provision` cell carries, and the command the header documents for
#: verification. Kept as constants so the assertion and the prose cannot drift apart.
FETCH = "python3 tools/datasets.py --fetch --dataset "
CONTROL = "python3 data/scaffolds/qa/control.py --dataset <name> --oracle"


def _cells(line: str) -> list[str]:
    """The cells of one markdown row. `\\|` is an escaped pipe, not a separator."""
    body = line.strip()
    assert body.startswith("|") and body.endswith("|"), line
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body[1:-1])]


def _readiness_lines(text: str) -> list[str]:
    """The lines of the `## Readiness` section.

    Scoped, not whole-file: the header explains the columns in a table of its own, whose
    first cells (`dataset`, `kind`, ...) are column names rather than datasets.
    """
    _head, separator, rest = text.partition("## Readiness")
    assert separator, "the document has no `## Readiness` section"
    return rest.partition("## Notes")[0].splitlines()


def _row_names(text: str) -> list[str]:
    """Every dataset row name in document order, duplicates included."""
    return [match.group(1) for line in _readiness_lines(text)
            if (match := ROW.match(line))]


def _table(text: str) -> dict[str, list[str]]:
    """Dataset -> cells, for every readiness row in `text`."""
    out: dict[str, list[str]] = {}
    for line in _readiness_lines(text):
        match = ROW.match(line)
        if match:
            out[match.group(1)] = _cells(line)
    return out


def _row(text: str, name: str) -> list[str]:
    rows = _table(text)
    assert name in rows, f"no readiness row for {name}"
    return rows[name]


def _host_table(text: str) -> dict[str, str]:
    """Dataset -> `provisioned here`, for the `--host` section only."""
    _head, separator, rest = text.partition("## Provisioned on this host")
    assert separator, "the rendered text has no `--host` section"
    out: dict[str, str] = {}
    for line in rest.splitlines():
        match = ROW.match(line)
        if match:
            out[match.group(1)] = _cells(line)[1]
    return out


def _review_queue(text: str) -> set[str]:
    """The dataset names under `### Deviations to review`; empty when the queue is empty."""
    _head, separator, rest = text.partition("### Deviations to review")
    if not separator:
        return set()
    rest = rest.partition("\n### ")[0]
    return {line.split("`")[1] for line in rest.splitlines() if line.startswith("* `")}


def test_write_then_check_passes_and_one_changed_byte_fails(tmp_path):
    """The generated document is self-consistent, deterministic, and `--check` notices.

    The copy lives in `tmp_path`: a suite that rewrote the checked-in file would make
    every run a commit-worthy diff, and the point of this test is the generator, not the
    artifact.
    """
    target = tmp_path / "DATASETS.md"
    assert doc.main(["--write", "--path", str(target)]) == 0
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    # No timestamps, no ordering by dict insertion: two renders are the same bytes.
    assert text == doc.render_document()
    # `--write` never carries the `--host` section, or the committed file would describe
    # the machine that regenerated it.
    assert "## Provisioned on this host" not in text
    assert doc.main(["--check", "--path", str(target)]) == 0

    # One byte, in a line the document claims is generated.
    changed = text.replace("## Readiness", "## Readines", 1)
    assert len(changed) == len(text) - 1, "the test changed more than a byte"
    target.write_text(changed, encoding="utf-8")
    assert doc.main(["--check", "--path", str(target)]) == 1


def test_every_published_dataset_appears_exactly_once():
    """Published, once each -- as many rows as `registry.published()` names.

    The count is derived, not pinned: this assertion was `== 39` and broke the moment a
    delivery added twenty adapters, which is a test about the registry's size rather than
    about the table. What matters is the set equality below; the derived count only makes
    a missing row's message legible.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    names = _row_names(text)
    expected = len(registry.published())
    assert len(names) == expected, f"the table has {len(names)} rows, not {expected}"
    assert len(names) == len(set(names)), f"duplicate rows: {sorted(names)}"
    assert set(names) == set(doc._published()), (
        "DATASETS.md and the published source declarations disagree; run "
        "python3 tools/datasets_doc.py --write")
    # Sorted by dataset, so a reader can find a name without scanning.
    assert names == sorted(names)


def test_catalog_discovery_does_not_depend_on_runtime_imports(monkeypatch):
    expected = doc._published()
    monkeypatch.setattr(registry, "published", lambda: ())
    assert len(expected) == 56
    assert doc._published() == expected


def test_readable_source_survives_missing_runtime_without_loading_tasks(tmp_path, monkeypatch):
    adapters = tmp_path / "data/datasets"
    adapters.mkdir(parents=True)
    source = adapters / "metadata_probe.py"
    source.write_text('''\
KIND = "dataset"
ORACLE_SOURCE = "authored"
METRIC_NAME = "authored metadata probe"
def downloads():
    return [{"kind": "http", "url": "https://example.invalid/source.zip",
             "name": "authored source", "version": "v1", "size": "1 KB", "dst": "source.zip"}]
raise RuntimeError("eager task pool must not execute")
''')
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    original = doc.importlib.import_module

    def unavailable(name, *args, **kwargs):
        if name == "data.datasets.metadata_probe":
            raise ModuleNotFoundError("synthetic missing optional runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(doc.importlib, "import_module", unavailable)
    row = doc.row_for("metadata_probe", {})
    assert row.kind == "dataset" and row.oracle == "authored"
    assert row.metric == "authored metadata probe" and not row.import_error
    assert "--fetch --dataset metadata_probe" in row.provision
    assert doc._provisioned(doc._import_adapter("metadata_probe")) == "unknown"


def test_catalog_is_independent_of_server_storage_configuration(tmp_path, monkeypatch):
    before = doc.render_document()
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "another-server-data"))
    monkeypatch.setenv("HG_DATA_KUMO", str(tmp_path / "separate-kumo"))
    assert doc.render_document() == before


def test_the_refused_datasets_carry_their_reason():
    """A refusal is a result, so the row says why rather than leaving a blank cell.

    Cross-checked in both directions: a refusal the adapters declare must be documented, and a
    row that carries a refusal cell must be one an adapter actually refuses. The second half is
    what catches a lifted refusal whose prose outlives it.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    documented = {name for name in doc._published()
                  if _row(text, name)[REFUSAL] not in ("", "—", "-")}
    assert documented == set(REFUSED), (
        f"documented refusals {sorted(documented)} vs adapter refusals {sorted(REFUSED)}")
    for name in REFUSED:
        refused = _row(text, name)[REFUSAL]
        assert refused not in ("", "—", "-"), f"{name} has no refusal reason"
        assert refused.startswith(f"{name}:"), (
            f"{name}'s refusal cell does not open with its own name: {refused[:80]!r}")
        assert len(refused) > 40, f"{name}'s refusal cell is not an explanation"


def test_the_recorded_deviations_reach_their_rows():
    """`bfcl_v3` and `hotpotqa` report a metric that is not the benchmark's, and the row
    has to say so -- it is the one fact a reader cannot infer from the metric name."""
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in ("bfcl_v3", "hotpotqa"):
        cell = _row(text, name)[DEVIATION]
        assert cell.startswith("yes:"), f"{name}'s row lost its deviation: {cell!r}"
        assert doc.DEVIATIONS[name] in cell, (
            f"{name}'s row is not the recorded deviation from `DEVIATIONS`")
    assert "function-name set equality" in _row(text, "bfcl_v3")[DEVIATION]
    assert "joint F1" in _row(text, "hotpotqa")[DEVIATION]
    # The deviation is not the metric: both are on the row, and they are different claims.
    assert "function-name set equality" in _row(text, "bfcl_v3")[METRIC]
    assert "answer F1" in _row(text, "hotpotqa")[METRIC]


def test_the_settled_deviations_carry_their_numbers():
    """`triviaqa` and `tau_bench` were reviewed flags; the readings found a real deviation
    of the "right metric, wrong rows" kind, and a deviation a reader cannot size is a
    deviation they cannot use: the cell names how many rows of how many."""
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in ("triviaqa", "tau_bench"):
        cell = _row(text, name)[DEVIATION]
        assert cell.startswith("yes:"), f"{name}'s row lost its deviation: {cell!r}"
        assert doc.DEVIATIONS[name] in cell, (
            f"{name}'s row is not the recorded deviation from `DEVIATIONS`")
    # TriviaQA: rows scored against the two domain units its own evaluator divides by.
    assert "17,944" in _row(text, "triviaqa")[DEVIATION]
    assert "7,993" in _row(text, "triviaqa")[DEVIATION]
    assert "68,621" in _row(text, "triviaqa")[DEVIATION]
    # tau-bench: the official test files against what `load()` returns.
    assert "139 of the 165" in _row(text, "tau_bench")[DEVIATION]


def test_a_cleared_review_flag_says_no_and_keeps_its_reading():
    """`appworld`'s prose warns about a pool, not a metric; a person read the adapter and
    found the reward *is* the benchmark's own TGC unit on every official test task.

    So the flag leaves the queue -- and the reading that cleared it stays in the document,
    because a `no` nobody can check is exactly what the queue exists to prevent.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert _row(text, "appworld")[DEVIATION] == "no"
    assert "appworld" not in _review_queue(text), (
        "appworld is cleared, so it is no longer a row a person is asked to look at")
    _head, separator, cleared_section = text.partition("### Review flags a reading cleared")
    assert separator, "the cleared reading is not in the document"
    assert doc.CLEARED["appworld"] in cleared_section
    assert "appworld" in cleared_section
    # Every cleared entry is a live reading, not stale paperwork: the adapter still warns,
    # and the entry is the reading and not an empty `no`.
    for name, reading in doc.CLEARED.items():
        assert name in doc._published(), f"{name} is not a published dataset"
        module = doc._import_adapter(name)
        assert doc._deviation_hint(module) is not None, (
            f"{name} no longer warns, so its `CLEARED` entry records nothing")
        assert reading, f"{name} is cleared with no reading"


def test_the_document_carries_no_host_local_answer():
    """Every cell describes the repository, not the machine that rendered the file.

    The old table had a `provisioned here` column read from `provision_status()`, so a
    public reader could not tell a claim about the benchmark from a claim about the
    author's disk. A `yes` / `no` / `unknown` cell is that bug, so it may not appear at
    all -- not in the row, and not in the file.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "provisioned here" not in text, (
        "the committed document still mentions the host-local column")
    for name, cells in _table(text).items():
        cell = cells[PROVISION]
        assert cell not in HOST_ANSWERS, f"{name}'s provision cell is a host answer"
        assert not cell.startswith("unknown ("), (
            f"{name}'s provision cell is a host answer: {cell!r}")
    # The replacement is guidance a reader can run, not a status.
    assert FETCH in text
    assert CONTROL in text
    # No path from the machine that generated the file either: the data root is resolved
    # per host by `harnessgrad.paths`, so the document names the resolver, not a
    # disk that only exists on one deployment.
    assert "/mnt/" not in text
    assert "harnessgrad.paths.dataset_root()" in text
    assert "<HG_DATA_ROOT>/<dataset-directory>" in text
    assert "no implicit storage directory" in text
    assert ".local/share/harnessgrad" not in text


def test_check_is_invariant_to_provision_status(monkeypatch):
    """`--check` compares declarations, not this machine's disk.

    Every adapter's `provision_status()` is monkeypatched to return the **opposite**
    answer; the committed file must still pass, because no cell reads it. The flip is
    real, not a no-op: `--host` -- the one consumer -- reports the other answer.
    """
    # Supply one explicit host-state control so the flip remains observable even
    # when every real external dataset is unconfigured on a fresh machine.
    monkeypatch.setattr(doc._import_adapter("kumo"), "provision_status", lambda: {"present": False})
    rows = doc.build_rows()
    before = doc.host_status(rows)
    real_import = doc._import_adapter
    wrapped: set[int] = set()

    def opposite_import(name):
        module = real_import(name)
        status = getattr(module, "provision_status", None)
        # Import is cached and both `build_rows()` and `host_status()` call this seam, so
        # wrap each module once: a second wrap would flip the flip and prove nothing.
        if callable(status) and id(module) not in wrapped:
            wrapped.add(id(module))

            def flipped(status=status):
                answer = dict(status() or {})
                answer["present"] = not bool(answer.get("present"))
                return answer

            monkeypatch.setattr(module, "provision_status", flipped, raising=False)
        return module

    monkeypatch.setattr(doc, "_import_adapter", opposite_import)
    flipped = doc.host_status(doc.build_rows())

    assert flipped != before, "the monkeypatch did not reach the host answer"
    for name, answer in before.items():
        if answer == "yes":
            assert flipped[name] == "no", name
        elif answer == "no":
            assert flipped[name] == "yes", name
        else:
            # No hook: nobody asked, so the flip cannot happen and `unknown` stands.
            assert flipped[name] == answer, name

    # The opposite disk state cannot make the committed document stale.
    assert doc.main(["--check", "--path", str(DOC_PATH)]) == 0
    # And the flipped answer is only ever printed by `--host`.
    assert "## Provisioned on this host" not in doc.render(doc.build_rows())


def test_check_passes_on_a_machine_with_no_dataset_bytes(monkeypatch, tmp_path):
    """A host with no bytes at all is a host where every `provision_status()` raises.

    `--check` must still pass: that is the whole point of moving the answer behind
    `--host`. The local section reports the honest `unknown (<Exception>)` -- never a
    fabricated `no`, which would claim the bytes are absent rather than unreadable. And
    real drift is still caught: the guarantee is "fails only on drift", not "never fails".
    """
    real_import = doc._import_adapter

    def absent_import(name):
        module = real_import(name)
        if callable(getattr(module, "provision_status", None)):
            def no_bytes():
                raise FileNotFoundError("no dataset bytes on this machine")
            monkeypatch.setattr(module, "provision_status", no_bytes, raising=False)
        return module

    monkeypatch.setattr(doc, "_import_adapter", absent_import)
    assert doc.main(["--check", "--path", str(DOC_PATH)]) == 0
    host = _host_table(doc.render(doc.build_rows(), doc.host_status(doc.build_rows())))
    assert set(host.values()) <= {"unknown", "unknown (FileNotFoundError)"}
    assert "unknown (FileNotFoundError)" in host.values()

    # The other half of the same guarantee: drift is still reported on such a machine.
    drifted = tmp_path / "DATASETS.md"
    drifted.write_text(DOC_PATH.read_text(encoding="utf-8").replace(
        "## Readiness", "## Readines", 1), encoding="utf-8")
    assert doc.main(["--check", "--path", str(drifted)]) == 1


def test_the_provision_column_says_how_to_get_the_bytes():
    """The `provision` cell is actionable on a fresh checkout: the fetch command for a
    dataset, and the control command (documented in the header) for verification.

    The three datasets with nothing to fetch say so instead of printing a command that
    cannot work: committed bytes, a `pip`-only dependency, or a refusal no download can
    lift.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in ("aime", "hotpotqa", "gsm8k", "agentdojo"):
        cell = _row(text, name)[PROVISION]
        assert f"{FETCH}{name}" in cell, f"{name}'s provision cell is not runnable: {cell!r}"
    # The verification half is in the header, next to the fetch command.
    assert f"{FETCH}<name>" in text
    assert CONTROL in text
    assert "tests/quality/test_dataset_controls.py" in text

    for name in REFUSED:
        assert "no fetch lifts this refusal" in _row(text, name)[PROVISION], name
    assert "ship with the repository" in _row(text, "authored_example_fix_bug")[PROVISION]
    assert "pip install" in _row(text, "reasoning_gym")[PROVISION]


def test_an_adapter_that_raises_on_import_is_a_row_not_a_crash(monkeypatch, tmp_path):
    """One broken adapter must not take the other 38 rows down with it."""
    published = doc._published()
    broken = "synthetic_will_not_import"
    assert broken not in published
    real_import = doc._import_adapter

    def fake_published():
        return tuple(sorted({*published, broken}))

    def fake_import(name):
        if name == broken:
            raise ImportError("synthetic adapter boom")
        return real_import(name)

    monkeypatch.setattr(doc, "_published", fake_published)
    monkeypatch.setattr(doc, "_import_adapter", fake_import)

    target = tmp_path / "DATASETS.md"
    assert doc.main(["--write", "--path", str(target)]) == 0
    text = target.read_text(encoding="utf-8")

    row = _row(text, broken)
    assert "ImportError" in row[KIND], (
        f"the exception type is not in the row: {row[KIND]!r}")
    assert row[PROVISION] == "unknown", (
        "a dataset that could not be imported was told how to fetch itself")
    # The other rows still rendered, and the failure is explained under the table.
    assert len(_table(text)) == len(published) + 1
    assert "ImportError: synthetic adapter boom" in text
    assert _row(text, "aime")[METRIC] == "exact string equality after .strip()"
    # The host answer for an unimportable adapter is `unknown`, not `no`.
    assert _host_table(doc.render(doc.build_rows(), doc.host_status(doc.build_rows())))[
        broken] == "unknown"


def test_unknown_is_rendered_differently_from_no():
    """`unknown` (no `provision_status` hook) is not `no` (the hook said absent): reading
    them as the same thing would turn "nobody asked" into "the bytes are missing".

    The distinction lives in the `--host` section now; the cells are synthetic so the
    assertion does not depend on which bytes this machine happens to have.
    """
    class Hookless:
        pass

    class Present:
        @staticmethod
        def provision_status():
            return {"present": True}

    class Absent:
        @staticmethod
        def provision_status():
            return {"present": False}

    class Broken:
        @staticmethod
        def provision_status():
            raise RuntimeError("boom")

    assert doc._provisioned(Hookless()) == "unknown"
    assert doc._provisioned(Present()) == "yes"
    assert doc._provisioned(Absent()) == "no"
    assert doc._provisioned(Broken()) == "unknown (RuntimeError)"

    # The real hook-less adapters say `unknown` in the local section, and the committed
    # document says none of the three anywhere -- including for these rows.
    host = _host_table(doc.render(doc.build_rows(), doc.host_status(doc.build_rows())))
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in ("authored_example_fix_bug", "tau_bench", "terminal_bench"):
        assert host[name] == "unknown", name
        assert _row(text, name)[PROVISION] not in HOST_ANSWERS, name


def test_host_is_print_only(tmp_path):
    """`--host` is a question about this machine, so it may never reach a mode that
    writes or trusts the committed file; the parser refuses the combination."""
    target = tmp_path / "DATASETS.md"
    for argv in (["--write", "--path", str(target), "--host"],
                 ["--check", "--path", str(target), "--host"]):
        with pytest.raises(SystemExit) as caught:
            doc.main(argv)
        assert caught.value.code == 2, argv
    assert not target.exists(), "--write ran even though --host was rejected"


def test_host_prints_the_local_section_and_write_never_does(tmp_path, capsys):
    """The machine-local answer still exists, as its own opt-in section."""
    assert doc.main(["--host"]) == 0
    printed = capsys.readouterr().out
    assert "## Provisioned on this host" in printed
    assert "| dataset | provisioned here |" in printed
    assert all(value in HOST_ANSWERS or re.fullmatch(r"unknown \([A-Za-z_][A-Za-z0-9_]*\)", value)
               for value in _host_table(printed).values())

    target = tmp_path / "DATASETS.md"
    assert doc.main(["--write", "--path", str(target)]) == 0
    assert "## Provisioned on this host" not in target.read_text(encoding="utf-8")


def test_the_checked_in_document_matches_the_adapters():
    """The checked-in file is the artifact a reader opens, so it is compared with a fresh
    render cell for cell -- there is no host-local column left to exclude. Rows whose
    adapter does not import here are skipped: their row *is* host-dependent, and the
    `## Notes` section reports them anyway."""
    fresh_rows = doc.build_rows()
    fresh = doc.render(fresh_rows)
    checked = DOC_PATH.read_text(encoding="utf-8")
    # The prose header is host-independent: it must match byte for byte.
    assert checked.split("**At a glance.**")[0] == fresh.split("**At a glance.**")[0]
    fresh_table, checked_table = _table(fresh), _table(checked)
    for row in fresh_rows:
        if row.import_error:
            continue
        assert row.dataset in checked_table, (
            f"{row.dataset} is missing from the checked-in DATASETS.md")
        assert checked_table[row.dataset] == fresh_table[row.dataset], (
            f"{row.dataset}'s checked-in row is stale; run "
            f"python3 tools/datasets_doc.py --write")
    # The strongest form of the same claim: `--check` passes on the checked-in file.
    assert doc.main(["--check", "--path", str(DOC_PATH)]) == 0
