#!/usr/bin/env python3
"""Every dataset's division, as the adapter declares it -- one table, regenerated.

Usage
-----
    python3 tools/split_inventory.py                 # print the committed table
    python3 tools/split_inventory.py --write         # rewrite it in docs/dataset-splits.md
    python3 tools/split_inventory.py --check         # fail if that document is stale (exit 1)
    python3 tools/split_inventory.py --host          # ... plus what THIS host has on disk
    python3 tools/split_inventory.py --json          # either view, machine-readable
    python3 tools/split_inventory.py --path <file>   # --write/--check another copy

The platform's split rule changed on 2026-10-24: an adapter declares the **benchmark's**
division where the benchmark states one, and declares **nothing** where it does not. An
adapter may no longer cut its own pool with a hash third, which every one of these used to
do. This tool exists so the state of all 39 published datasets is one readable table
instead of 39 docstrings, and so the table cannot drift from the adapters: `--check`
fails if it does, and `tests/quality/test_split_inventory.py` runs it.

No committed cell is a fact about this machine
---------------------------------------------
`docs/dataset-splits.md` is committed and read on machines that are not the one that
rendered it, so a committed cell describes the **repository**: which state a dataset is in,
what `SPLIT_SOURCE` says produced its division, and what its `OFFICIAL_EVAL` means. "How
many task ids are on each side?" is not such a cell. It used to be one, because the old
renderer called `load()` for every dataset and kept the answer -- measured 2026-11-02 on
this checkout, which is why the committed file named `cooperbench`, `costbench`, `kumo`
and `personamem_v2` as "not provisioned" after they had been provisioned, while a fresh
clone failed `--check` for *every* dataset. Any host fact is now behind `--host`, and
`--write`/`--check` refuse to combine with it, exactly as `tools/datasets_doc.py` does.

Sizes are not declarations
--------------------------
Even a *provisioned* host cannot render `len(SPLIT["eval"])` as a repository fact: `SPLIT`
is not a literal in most adapters. `QASpec.build()` derives both sides from the task
records it reads (`gsm8k`, `triviaqa`, ...), `appworld` reads `pool.jsonl`,
`costbench`/`costbench_dynamic`/`personamem_v2` read a prepared manifest, and
`tau3_bench` extends its train side with ids from its roster. So the number answers "how
many tasks are on this disk", which changes when a payload is (un)provisioned or an
`HG_*_POOL`/study-file knob moves -- not "what does the adapter declare". Those cells are
`unknown (computed)`, and `--host` prints the number together with the task count and the
exact exception a dataset without a payload raises. `OFFICIAL_EVAL` is the one place a
declared id list is *not* rendered either: it is computed from the same records.

`tau_bench` is the case this rule cannot tell apart: it computes its sides from a roster
**committed** under `data/datasets/tau_bench/`, so its sizes *are* reproducible from the
repository alone. A computed size may still be a payload fact, and telling the two apart
would need a hand-maintained list of which adapters read the data root -- a list that
itself goes stale. The cell is therefore `unknown (computed)` and `--host` prints the
number, which is the same choice `tools/datasets_doc.py` makes for `provision`: never a
number the file cannot attribute.

Four states, and the table names which one a dataset is in
----------------------------------------------------------
* `declared` -- `SPLIT_SOURCE` is `"declared"`: the benchmark publishes both sides and the
  adapter says which is which. The sides are `unknown (computed)` here (see above);
  `OFFICIAL_EVAL` is the scored side's ids, also computed, except where an adapter
  declares it literally (`tau3_bench`: `None`).
* `none` -- the adapter declares no division: `SPLIT_SOURCE` is `"none"`, or a prose
  source (the authored bundle), or absent with no `SPLIT` at all (`locomo`). A run covers
  every task. `OFFICIAL_EVAL` then says what the pool is: `None` (the pool *is* the
  benchmark's scored split) or `[]` (the pool is not part of its evaluation set).
* `undeclared` -- the adapter answers no `SPLIT_SOURCE` and builds `SPLIT` from its payload
  (`strongreject`: a hash third). Whether it divides its pool is therefore not a fact this
  file can state, and `--host` prints what the payload says.
* `refused` -- `REFUSED` is set: this benchmark cannot be measured, so it has no division
  to report and `load()` raises.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data.scaffolds.registry as registry  # noqa: E402
from harnessgrad.paths import DataRootNotConfigured  # noqa: E402

DOC = ROOT / "docs" / "dataset-splits.md"
BEGIN = "<!-- split-inventory:begin -->"
END = "<!-- split-inventory:end -->"

#: `-` is "this question does not apply" -- a refusal reports no division at all.
DASH = "-"

#: The value a size gets when the adapter computes it from its pool. Not a number, because
#: a number here would be a fact about whichever disk rendered the file. `--host` prints
#: it; nothing else may.
COMPUTED = "unknown (computed)"

#: `SPLIT_SOURCE` is missing entirely (the adapter does not say what produced `SPLIT`).
ABSENT = "absent"

#: A value the adapter does not have: `getattr` raised `AttributeError`, which is how the
#: lazy adapters answer for a payload that is not there.
_MISSING = object()


@dataclass(frozen=True)
class Row:
    """One dataset's line, in one of the two views.

    Every rendered cell is a non-empty string: `DASH`, `COMPUTED` and `ABSENT` are
    spellings, not absences, so a blank cell can never be mistaken for a measured zero.

    `tasks` and `detail` are payload facts; the committed render leaves `tasks` as
    `COMPUTED` (it keeps the key so `--json` keeps its shape) and `detail` empty, and the
    table does not print either column.
    """

    dataset: str
    state: str
    source: str
    tasks: str
    train: str
    eval: str
    official: str
    #: `will not import` / `FileNotFoundError: ...` -- `--host` only.
    detail: str = ""


# --------------------------------------------------------------- the seams the test uses ---
# `_published` and `_import_adapter` are the only ways this module reaches the adapters;
# `_load` is the only place it reads a payload. `build_rows()`/`table()` never call
# `_load`, which is the property the tests pin: a committed render must not be able to
# depend on which bytes this machine has. The test for "a machine with no data at all"
# patches every adapter's `load()` and watches `--check` not care while `--host` flips.


def _published() -> tuple[str, ...]:
    """Every dataset a reader can choose, in name order."""
    return tuple(sorted(registry.published()))


def _import_adapter(name: str):
    """Runtime adapter, or source declarations when external data is unavailable."""
    if registry.provider(name) is not None:
        return _source_adapter(name, provider=True)
    try:
        return importlib.import_module(registry.module_name(name))
    except Exception as exc:
        return _source_adapter(name, error=exc)


def _source_adapter(name: str, *, provider=False, error=None):
    # Some legacy adapters construct task pools at import. A split catalog
    # still reads their declarations without configuring or opening storage.
    path = ROOT / "data/datasets" / f"{name}.py"
    tree = ast.parse(path.read_bytes())

    def unavailable():
        raise error or RuntimeError('static declarations cannot load task payloads')

    values = {"__file__": str(path), "load": unavailable,
              "_lazy_declarations": any(isinstance(node, ast.FunctionDef)
                  and node.name == "__getattr__" for node in tree.body)}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id not in {
                    "SPLIT_SOURCE", "OFFICIAL_EVAL", "REFUSED"}:
                continue
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                if target.id == "OFFICIAL_EVAL":
                    values[target.id] = _MISSING
    if provider:
        # Construct QASpec without calling build or rows: only declared policy.
        from tools.datasets import _source_downloads
        downloads = _source_downloads(path, resolve_root=False)
        spec = downloads.__globals__.get('_SPEC') if downloads else None
        if spec is not None:
            values['SPLIT_SOURCE'] = 'declared' if spec.study else 'none'
            values['OFFICIAL_EVAL'] = (_MISSING if spec.study else
                                      None if spec.pool_is == 'eval' else [])
    return SimpleNamespace(**values)


def _load(module):
    """`(tasks, scorable)` from the adapter -- the only payload read this tool makes."""
    return module.load()


# ------------------------------------------------------------------------ declarations ---


def _attribute(module, name: str):
    """The module's declared attribute, or `_MISSING` when it cannot answer.

    The lazy adapters raise `AttributeError` for a name they build from a payload that is
    not on this host (`costbench`, `personamem_v2`, `strongreject`), which is a missing
    declaration, not a crash -- and `getattr(..., default)` would hide the difference
    between "declared `None`" and "not declared".
    """
    try:
        return getattr(module, name)
    except (AttributeError, DataRootNotConfigured):
        return _MISSING


def _refused(module) -> bool:
    """Does the adapter refuse this benchmark by design? (`REFUSED` is a sentence.)"""
    refused = _attribute(module, "REFUSED")
    return refused is not _MISSING and bool(refused)


def _state(module, source) -> str:
    """The dataset's state, from declarations only.

    `SPLIT_SOURCE` is the declaration: `"declared"` when the benchmark states the
    division, `"none"` when it states none, and a prose source (the authored bundle) for
    the one adapter that describes its own. Two adapters answer no `SPLIT_SOURCE` at all,
    and they are not the same case:

    * `locomo` declares no `SPLIT` either, so it declares no division -- `none`;
    * `strongreject` computes `SPLIT` from its payload and answers attributes on demand,
      so "it declares no division" is not something this file may claim -- `undeclared`.

    Reading `SPLIT` to tell them apart would be exactly the bug this tool exists to
    prevent: for `strongreject` its value depends on whether the CSV is on the disk.
    """
    if source is _MISSING or not isinstance(source, str) or not source.strip():
        lazy = (getattr(module, "_lazy_declarations", False)
                or callable(getattr(module, "__getattr__", None)))
        return "undeclared" if lazy else "none"
    return "declared" if source.strip() == "declared" else "none"


def _official(module, state: str) -> str:
    """The `OFFICIAL_EVAL` cell.

    Two kinds of answer, and the difference is whether it enumerates ids:

    * `None` / `[]` -- a claim about the benchmark ("the pool is the scored split" / "the
      pool is not an evaluation set"). It is the same on every host, so it is a cell.
    * an id list -- a size, and sizes are `COMPUTED` (see the module docstring). Under a
      declared division that includes the empty list a *no-payload* host computes, which
      is why a declared row renders `unknown (computed)` even when this host shows ids:
      the cell must not change shape with the disk. `None` under a declared division is
      the one literal an adapter states (`tau3_bench`), and it is printed.
    """
    value = _attribute(module, "OFFICIAL_EVAL")
    if state == "declared":
        return "None" if value is None else COMPUTED
    if value is _MISSING:
        return ABSENT
    if value is None:
        return "None (the pool is the scored split)"
    if not value:
        return "[] (not an evaluation set)"
    return COMPUTED


def _cell(text: str) -> str:
    """One table cell: no pipes, no line breaks, no trailing space."""
    return " ".join(str(text).replace("|", "\\|").split())


def _source_cell(source) -> str:
    """`split_source` as a cell: the declaration, or `absent` when there is none."""
    if source is _MISSING or not isinstance(source, str) or not source.strip():
        return ABSENT
    return _cell(source)


def row_for(name: str) -> Row:
    """One committed row, from the adapter's declarations. Reads no payload."""
    module = _import_adapter(name)
    if _refused(module):
        return Row(dataset=name, state="refused", source=DASH, tasks=DASH,
                   train=DASH, eval=DASH, official=DASH)
    source = _attribute(module, "SPLIT_SOURCE")
    state = _state(module, source)
    sides = COMPUTED if state == "declared" else DASH
    return Row(dataset=name, state=state, source=_source_cell(source), tasks=COMPUTED,
               train=sides, eval=sides, official=_official(module, state))


def build_rows() -> list[Row]:
    """Every published dataset's committed row, sorted by dataset name."""
    return [row_for(name) for name in _published()]


# ----------------------------------------------------------------------- the committed ---


def table(rows: list[Row]) -> str:
    """The generated part of `docs/dataset-splits.md`: the table and nothing else.

    No timestamp, no ordering by dict insertion: two renders are the same bytes, and no
    cell is read from the data root.
    """
    lines = [
        "| dataset | state | split_source | train | eval | OFFICIAL_EVAL |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| `{row.dataset}` | {row.state} | {row.source} | "
                     f"{row.train} | {row.eval} | {row.official} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the host ---


def _host_row(name: str) -> Row:
    """One row of the payload view: `load()`, then the sizes it produced."""
    module = _import_adapter(name)
    source = _source_cell(_attribute(module, "SPLIT_SOURCE"))
    if _refused(module):
        return Row(dataset=name, state="refused", source=DASH, tasks=DASH,
                   train=DASH, eval=DASH, official=DASH)
    try:
        provider = registry.provider(name)
        if provider is not None:
            snapshot = provider.inventory()
            module = SimpleNamespace(**snapshot['metadata'])
            source = _source_cell(_attribute(module, 'SPLIT_SOURCE'))
            count = snapshot['tasks']
            if _refused(module):
                return Row(dataset=name, state='refused', source=DASH, tasks=DASH,
                           train=DASH, eval=DASH, official=DASH)
        else:
            tasks, _scorable = _load(module)
            count = len(tasks)
    except Exception as exc:  # noqa: BLE001 - the absence is the answer here, not a crash
        return Row(dataset=name, state="not provisioned", source=source, tasks=DASH,
                   train=DASH, eval=DASH, official=DASH,
                   detail=f"{type(exc).__name__}: {exc}")
    split = _attribute(module, "SPLIT")
    official = _attribute(module, "OFFICIAL_EVAL")
    if split is _MISSING or split is None:
        state, train, eval_ = "none", DASH, DASH
    else:
        state, train, eval_ = "declared", str(len(split["train"])), str(len(split["eval"]))
    return Row(dataset=name, state=state, source=source, tasks=str(count),
               train=train, eval=eval_, official=_official_cell(official))


def _official_cell(value) -> str:
    """`OFFICIAL_EVAL` for the host view: the count, or the declaration it spells."""
    if value is _MISSING:
        return ABSENT
    if value is None:
        return "None (the pool is the scored split)"
    if not value:
        return "[] (not an evaluation set)"
    return str(len(value))


def host_rows() -> list[Row]:
    """Every published dataset's payload view, sorted by dataset name. `--host` only."""
    return [_host_row(name) for name in _published()]


def host_section(rows: list[Row]) -> str:
    """The local answer, printed only for `--host`.

    A separate document, not a column, so the committed table cannot carry it: `main()`
    rejects `--host` with `--write`/`--check`, and `table()`/`build_rows()` never read a
    payload. This view *is* expected to go stale -- provisioning a dataset changes it --
    which is exactly why it may not be committed.
    """
    refused = sum(1 for row in rows if row.state == "refused")
    missing = [row for row in rows if row.detail]
    answered = len(rows) - refused - len(missing)
    lines = [
        "# Split inventory -- this host's payloads (local only)",
        "",
        "`python3 tools/split_inventory.py --host` calls every adapter's `load()`, so every "
        "number below is a fact about the machine that ran it, not about the repository: "
        "provisioning or removing a payload changes it. It is printed and never written -- "
        "`--write` and `--check` refuse to combine with `--host`. The committed table is "
        "`python3 tools/split_inventory.py`.",
        "",
        f"**On this host.** {answered} of {len(rows)} published datasets answered `load()`; "
        f"{len(missing)} did not (`not provisioned`); {refused} are refused by design.",
        "",
        "| dataset | state | split_source | tasks | train | eval | OFFICIAL_EVAL |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(f"| `{row.dataset}` | {row.state} | {row.source} | {row.tasks} | "
                     f"{row.train} | {row.eval} | {row.official} |")
    if missing:
        lines += ["", "Not provisioned on this host, with the root each one named:", ""]
        lines += [f"* `{row.dataset}` -- {row.detail}" for row in missing]
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------- the modes ---


def _display(path: Path) -> str:
    """A path as a reader typed it: relative when it is inside the repository."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the table in the document")
    mode.add_argument("--check", action="store_true",
                      help="fail if the document is stale (exit 1)")
    parser.add_argument("--host", action="store_true",
                        help="print the payload-derived view for this host; print mode "
                             "only, because the committed document carries no host fact")
    parser.add_argument("--json", action="store_true", help="machine-readable rows")
    parser.add_argument("--path", default=None,
                        help=f"the document --write/--check operate on (default "
                             f"{DOC.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    if args.host and (args.write or args.check):
        parser.error("--host reads this machine's dataset payloads, so it is printed "
                     "only: neither --write nor --check may see it, or the committed "
                     "document would start describing one host")
    if args.json and (args.write or args.check):
        parser.error("--json is a print mode; it does not combine with --write or --check")
    target = Path(args.path) if args.path else DOC

    if args.host:
        rows = host_rows()
        print(json.dumps([asdict(row) for row in rows], indent=1) if args.json
              else host_section(rows), end="")
        return 0

    rows = build_rows()
    rendered = table(rows)
    if args.json:
        print(json.dumps([asdict(row) for row in rows], indent=1))
        return 0
    if args.write:
        text = target.read_text(encoding="utf-8")
        start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
        target.write_text(text[:start] + "\n\n" + rendered + "\n" + text[end:],
                          encoding="utf-8")
        print(f"wrote {_display(target)} ({len(rows)} dataset(s))")
        return 0
    if args.check:
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"{_display(target)} does not exist; run tools/split_inventory.py --write",
                  file=sys.stderr)
            return 1
        current = text[text.index(BEGIN) + len(BEGIN):text.index(END)].strip() + "\n"
        if current != rendered:
            print(f"{_display(target)} is stale; run tools/split_inventory.py --write",
                  file=sys.stderr)
            return 1
        print(f"{_display(target)} matches the adapters ({len(rows)} dataset(s))")
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
