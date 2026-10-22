#!/usr/bin/env python3
"""Every dataset's division, as the adapter declares it -- one table, regenerated.

    python3 tools/split_inventory.py            # print the table
    python3 tools/split_inventory.py --check     # compare with docs/dataset-splits.md
    python3 tools/split_inventory.py --write     # rewrite the table in that document

The platform's split rule changed on 2026-10-24: an adapter declares the **benchmark's**
division where the benchmark states one, and declares **nothing** where it does not. An
adapter may no longer cut its own pool with a hash third, which every one of these used to
do. This tool exists so the state of all 40 datasets is one readable table instead of 40
docstrings, and so the table cannot drift from the adapters: `--check` fails if it does,
and `tests/quality/test_split_inventory.py` runs it.

Three states, and the table names which one a dataset is in:

* `declared` -- the benchmark publishes both sides and the adapter says which is which;
  `SPLIT` is a dict and both sides are non-empty.
* `none` -- the adapter declares no division; `SPLIT` is `None` and a run covers every
  task. `OFFICIAL_EVAL` then says what the pool is: `None` (the pool *is* the benchmark's
  scored split), `[]` (the pool is not part of its evaluation set at all), or the ids.
* `not provisioned` -- the adapter needs a payload that is not on this host, so it cannot
  answer without it. The row carries the root from the load error.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOC = ROOT / "docs" / "dataset-splits.md"
BEGIN = "<!-- split-inventory:begin -->"
END = "<!-- split-inventory:end -->"


def _row(name: str) -> dict:
    module = importlib.import_module(f"data.datasets.{name}")
    row = {"dataset": name}
    if getattr(module, "REFUSED", None):
        row.update(state="refused", source="-", official="-", tasks="-", train="-", eval="-")
        return row
    split = getattr(module, "SPLIT", None)
    try:
        tasks, _ = module.load()
        row["tasks"] = len(tasks)
    except Exception as exc:  # noqa: BLE001 - the absence is the answer here
        row.update(state="not provisioned", source="-", official="-", tasks="-",
                   train="-", eval="-", detail=f"{type(exc).__name__}: {exc}")
        return row
    official = getattr(module, "OFFICIAL_EVAL", "absent")
    row["tasks"] = len(tasks)
    row["source"] = getattr(module, "SPLIT_SOURCE", "absent")
    if split is None:
        row["state"] = "none"
        row["train"] = "-"
        row["eval"] = "-"
        row["official"] = ("absent" if official == "absent"
                           else "None (the pool is the scored split)" if official is None
                           else "[] (not an evaluation set)" if not official
                           else str(len(official)))
    else:
        row["state"] = "declared"
        row["train"] = str(len(split["train"]))
        row["eval"] = str(len(split["eval"]))
        row["official"] = ("absent" if official == "absent"
                           else "None" if official is None else str(len(official)))
    return row


def table(names: list[str] | None = None) -> str:
    from data.scaffolds import registry
    names = names or list(registry.published())
    rows = [_row(name) for name in names]
    lines = [
        "| dataset | state | split_source | tasks | train | eval | OFFICIAL_EVAL |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(f"| `{row['dataset']}` | {row['state']} | {row['source']} | "
                     f"{row['tasks']} | {row['train']} | {row['eval']} | {row['official']} |")
    unprovisioned = [row for row in rows if row["state"] == "not provisioned"]
    if unprovisioned:
        lines += ["", "Not provisioned on this host, with the root each one named:", ""]
        for row in unprovisioned:
            lines.append(f"* `{row['dataset']}` -- {row['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="rewrite the table in the document")
    group.add_argument("--check", action="store_true", help="fail if the document is stale")
    parser.add_argument("--json", action="store_true", help="machine-readable rows")
    args = parser.parse_args()

    if args.json:
        from data.scaffolds import registry
        print(json.dumps([_row(name) for name in registry.published()], indent=1))
        return 0

    rendered = table()
    if args.write:
        text = DOC.read_text()
        start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
        DOC.write_text(text[:start] + "\n\n" + rendered + "\n" + text[end:])
        print(f"wrote {DOC.relative_to(ROOT)}")
        return 0
    if args.check:
        text = DOC.read_text()
        current = text[text.index(BEGIN) + len(BEGIN):text.index(END)].strip() + "\n"
        if current != rendered:
            print("docs/dataset-splits.md is stale; run tools/split_inventory.py --write",
                  file=sys.stderr)
            return 1
        print("docs/dataset-splits.md matches the adapters")
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
