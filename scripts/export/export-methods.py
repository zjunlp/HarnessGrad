#!/usr/bin/env python3
"""Generate the site's Methods pages from the method directories and the published results.

Why generated: a method page states (a) the rule, (b) every deliberate deviation from the paper
it ports, and (c) the benchmark rows it appears in. Two of those three already exist in the
repository -- `methods/<name>/method.json` carries the deviations, and the results snapshot
carries the rows -- so a hand-written page would drift from both the first time either changed.

The rule paragraph comes from the module docstring: it is the only place the rule is written
down next to the code that implements it.

Writes (relative to the website branch checkout root):
    src/content/docs/methods/<slug>.mdx
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import sys

D = pathlib.Path(__file__).resolve().parent
#: Methods pages are read from the site's own data (`src/data/results/*.json`), so this script
#: needs no run receipts -- but each page quotes the method's own source, which lives in the
#: **platform** repository. `HG_PLATFORM` is that checkout; there is no sensible default
#: because guessing one is how a page ends up describing a method version that was never run.
SITE = pathlib.Path(os.environ.get("HG_SITE") or D.parents[1])
ROOT = pathlib.Path(os.environ.get("HG_PLATFORM") or "")
if not (ROOT / "methods").is_dir():
    raise SystemExit(
        f"export-methods: no platform checkout at {ROOT or '(unset)'}. Set HG_PLATFORM to the "
        f"HarnessGrad checkout that holds methods/<name>/run.py -- see scripts/export/README.md.")
RESULTS = SITE / "src" / "data" / "results" / "alfworld.json"
#: The Hub layout lives in exactly one file; this page reads it rather than repeating the
#: directory names, so a method page cannot point at a different improver's directory than the
#: benchmark page links for the same method.
HUB = SITE / "src" / "data" / "hub.json"
#: The six settled methods plus the two the A line re-ran. A method that appears in the
#: benchmark table needs a page of its own -- the table links to it, and a link with no page
#: fails `check-links` (which is how this list and `export-site.METHODS` were found to have
#: drifted apart).
METHODS = ("sica_ci", "ahe", "dgm", "harnessx", "hyperagents", "tthe",
           "dream_rsi", "meta_harness")
SPLITS = ("valid_unseen", "valid_seen", "valid_train")


def docstring(method: str) -> str:
    source = ROOT / "methods" / method / "run.py"
    text = ast.get_docstring(ast.parse(source.read_text(encoding="utf-8"))) or ""
    return text


def rule_paragraph(method: str) -> str:
    """The first paragraph of the docstring, which states what the method decides."""
    text = docstring(method)
    paragraph = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    joined = " ".join(paragraph)
    # Drop the scaffolding sentence the ports share; it explains the file, not the rule.
    joined = re.sub(r"\s*Why this file exists.*$", "", joined)
    return joined.strip()


def deviations(method: str) -> list[dict]:
    path = ROOT / "methods" / method / "method.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("deviations") or []


def cost(method: str) -> tuple[float | None, float | None]:
    """Agent and improver output tokens over the rounds that were measured, in millions.

    The agent side is what the harness spent acting; the improver side is what the method spent
    editing. They are different quantities and a reader comparing methods wants both: the cheapest
    harness to run and the cheapest method to apply are not the same ranking.
    """
    # **Read from the snapshot, not from a path with a stamp in it.** This used to hard-code
    # `rsi10-20261017T123952Z-<method>-train`, so the two A-line methods -- trained under a
    # later stamp -- silently returned `None` and the index crashed formatting it. The snapshot
    # already knows which run each method belongs to (`export-site.run_stamp`), so there is
    # exactly one place that decision is made.
    for entry in results()["methods"]:
        if entry["name"] != method:
            continue
        rounds = entry.get("cost") or {}
        if not rounds:
            return None, None
        task = sum(((r.get("task") or {}).get("tokens") or 0) for r in rounds.values())
        improver = sum(((r.get("improver") or {}).get("output") or 0)
                       for r in rounds.values())
        return task / 1e6, improver / 1e6
    return None, None


def results() -> dict:
    """The snapshot, with every stack's methods flattened to one list.

    The snapshot groups methods under their setup (`stacks[].methods`), because a result is only
    meaningful with the stack that produced it. A method page needs the rows, not the grouping,
    so this flattens while keeping the rest of the document (reference scores, coverage) intact.
    """
    if not RESULTS.is_file():
        raise SystemExit(f"no results snapshot at {RESULTS}; run export-site.py first")
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    methods = []
    for stack in data.get("stacks") or []:
        methods.extend(stack.get("methods") or [])
    if not methods:
        methods = data.get("methods") or []
    data["methods"] = methods
    return data


def best_row(data: dict, method: str, split: str) -> tuple[int, float] | None:
    """The best measured round for one method on one split, with its round number."""
    for entry in data["methods"]:
        if entry["name"] != method:
            continue
        scored = [(cell["round"], cell["eval"][split]) for cell in entry["rounds"]
                  if split in (cell.get("eval") or {})]
        if not scored:
            return None
        return max(scored, key=lambda pair: (pair[1], pair[0]))
    return None


def record_link(method: str, data: dict) -> str | None:
    """This method's directory on the Hub, or nothing if it is not published."""
    if not HUB.is_file():
        return None
    entry = (json.loads(HUB.read_text(encoding="utf-8")) or {}).get("alfworld") or {}
    if entry.get("status") != "published" or not entry.get("dataset"):
        return None
    stacks = data.get("stacks") or [{}]
    improver = stacks[0].get("improver") or {}
    family = (entry.get("families") or {}).get(improver.get("improver_model"))
    if not family:
        return None
    path = "/".join(p for p in (entry.get("root"), entry.get("agent"), family, method) if p)
    return f"https://huggingface.co/datasets/{entry['dataset']}/tree/main/{path}/"


def page(method: str, data: dict, generated: str) -> str:
    reference = data["reference"]
    row = best_row(data, method, "valid_unseen")
    deviation_items = deviations(method)
    rule = rule_paragraph(method)
    lines = [
        "---",
        f"title: {method.replace('_', ' ')}",
        f"description: What {method} decides, and how it differs from the method it ports.",
        "---",
        "",
        "{/* Generated by scripts/export/export-methods.py. Do not edit by hand; last export "
        f"{generated}. " + "*/}",
        "",
        "## Rule",
        "",
        rule or "_The module docstring is empty; the rule is in `run.py`._",
        "",
        "The method returns one edited harness per round. It never sees a score for the harness it "
        "is producing — the platform measures every candidate on the same study set and records "
        "the result in `curve.jsonl`.",
        "",
    ]
    lines += ["## Measured results", "",
              "Scores, rounds and per-split comparisons are on the "
              "[ALFWorld benchmark page](../../benchmarks/alfworld/) — one table for every method, "
              "so the same number cannot say two different things in two places.", ""]
    record = record_link(method, data)
    if record:
        lines += ["## Record", "",
                  "This method's rounds on the Hub — each round's harness, the edit that produced "
                  f"it, and its per-task trajectories: [open the directory]({record}).", ""]
    lines += ["## Deviations from the method it ports", ""]
    if deviation_items:
        lines += [f"{len(deviation_items)} deliberate difference(s), recorded in "
                  f"`methods/{method}/method.json`:", ""]
        for item in deviation_items:
            what = str(item.get("what") or "").strip()
            lines.append(f"- {what}")
        lines.append("")
    else:
        lines += ["_No deviations recorded._", ""]
    lines += [
        "## Code",
        "",
        f"`methods/{method}/run.py` and its rule test in `tests/quality/`. "
        "`python3 tools/method_lint.py` prints one line per method.",
        "",
    ]
    return "\n".join(lines)


def index(data: dict, generated: str) -> str:
    """One row per method: what it is, and where its record is.

    No scores here on purpose. A method's numbers belong to the benchmark that measured them,
    and a second copy on this page is a copy that can disagree with the first one.
    """
    head = ["---", "title: Methods",
            "description: The harness-improvement methods integrated in this platform, the rule "
            "each one applies, and its recorded deviations.", "---", "",
            "{/* Generated by scripts/export/export-methods.py; last export "
            f"{generated}. " + "*/}", "",
            "A method here is a **decision rule**: given the traces of the harness it was given "
            "and the score that harness earned, it produces one edited harness. The platform "
            "measures the result; the method never scores itself.", "",
            "| method | deviations from the ported method | record |",
            "| --- | ---: | ---: |"]
    for method in METHODS:
        link = record_link(method, data)
        cell = f'[↗]({link})' if link else "—"
        head.append(f"| [{method.replace('_', ' ')}]({method}/) | "
                    f"{len(deviations(method))} | {cell} |")
    head += ["",
             "Measured results are not repeated here: each method's scores, rounds and "
             "per-split comparisons are on the [ALFWorld benchmark page]"
             "(../benchmarks/alfworld/), next to every other method measured on the same tasks.",
             ""]
    return "\n".join(head)


def main() -> int:
    data = results()
    folder = SITE / "src" / "content" / "docs" / "methods"
    folder.mkdir(parents=True, exist_ok=True)
    generated = os.environ.get("HG_GENERATED_AT") or data.get("generated_at", "")
    for method in METHODS:
        (folder / f"{method}.mdx").write_text(page(method, data, generated), encoding="utf-8")
    # The methods index was removed on 2026-10-22 with the benchmarks index: the sidebar lists the
    # method pages itself, and an index that repeats them is a second place to keep in sync.
    print(f"wrote {len(METHODS)} method page(s) into {folder.relative_to(SITE)}")
    for method in METHODS:
        row = best_row(data, method, "valid_unseen")
        print(f"  {method:12s} best={('r%d %.3f' % row) if row else '—':>12s} "
              f"deviations={len(deviations(method))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
