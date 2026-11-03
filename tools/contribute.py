#!/usr/bin/env python3
"""Put yourself on a board with one command, and let the command do the checking.

Why a tool instead of a form in a document
------------------------------------------
The boards are a record (`registry/leaderboards.json`) rendered into the README, so a
contribution is always three edits: the row, the render, the pull request. That is exactly the
kind of small sequence a person gets wrong at 2am -- a typo in the path, a name that does not
match the directory, a row added in the wrong board -- and each mistake costs a review round
trip. This tool performs the sequence, and refuses the row *before* it exists rather than after
a reviewer reads it.

The bar is deliberately low
---------------------------
A contribution is admitted because it runs, not because it scores. So the check here is the one
that can be automated and that a reviewer would otherwise have to trust: the thing named
actually exists and declares itself consistently (`harness.json` parses and names an
entrypoint; a method preset points at a readable file; a benchmark adapter declares a `KIND`).
There is no target score, no minimum number of tasks and no comparison with another entry.
What a human still reviews is the sentence in the pull request that says how it was run.

`--contact` is optional and never rendered into the README. It is stored in the record so the
website's board can show a way to reach a contributor; the table on GitHub shows the handle,
because a public repository should not be the place an email address is harvested from.

Usage
-----
    python3 tools/contribute.py harness --name my-harness --path base_harness/my-harness \
        --contributor someone --contact someone@example.com --note "what it is"
    python3 tools/contribute.py method --name my-method --preset experiments/method-my.json ...
    python3 tools/contribute.py benchmark --name my-bench --adapter data/datasets/my_bench.py ...
    python3 tools/contribute.py result --benchmark gsm8k --metric exact_match --score 0.6 \
        --method dgm --harness loop --harness-sha <40 hex> --receipt runs/<id>/end-summary.json ...

`--dry-run` prints the row and touches nothing. Nothing is committed: the tool prints the two
paths to stage, because a contribution is a pull request, not a push.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "registry" / "leaderboards.json"
HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SHA = re.compile(r"^[0-9a-f]{40}$")

#: board -> the field that names its artefact, and a sentence for the error a person sees.
BOARDS = {
    "harness": ("path", "the harness directory (it must contain harness.json)"),
    "method": ("preset", "the experiment preset that starts your method"),
    "benchmark": ("adapter", "the dataset adapter file"),
}


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def _write(record: dict) -> None:
    RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _harness_problems(path: Path) -> list[str]:
    manifest = path / "harness.json"
    if not manifest.is_file():
        return [f"{manifest.relative_to(ROOT)} does not exist"]
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"harness.json is not valid JSON: {exc}"]
    problems = []
    entrypoint = data.get("entrypoint") or data.get("command")
    if not entrypoint:
        problems.append("harness.json names no entrypoint")
    elif isinstance(entrypoint, str) and not (path / entrypoint.split()[0]).exists():
        problems.append(f"harness.json entrypoint does not exist in the directory: {entrypoint}")
    if not data.get("env_kinds"):
        problems.append("harness.json does not declare env_kinds")
    return problems


def _method_problems(preset: Path) -> list[str]:
    if not preset.is_file():
        return [f"{preset.relative_to(ROOT)} does not exist"]
    try:
        data = json.loads(preset.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{preset.name} is not valid JSON: {exc}"]
    problems = []
    if not data.get("method"):
        problems.append(f"{preset.name} declares no method")
    entrypoint = data.get("method_entrypoint")
    if entrypoint and isinstance(entrypoint, str):
        head = entrypoint.split()[0]
        if head.endswith(".py") and not (ROOT / head).is_file():
            problems.append(f"method_entrypoint does not exist: {head}")
    return problems


def _benchmark_problems(adapter: Path) -> list[str]:
    if not adapter.is_file():
        return [f"{adapter.relative_to(ROOT)} does not exist"]
    text = adapter.read_text(encoding="utf-8")
    problems = []
    if not re.search(r"^KIND\s*=\s*[\"']", text, re.MULTILINE):
        problems.append(f"{adapter.name} declares no KIND, so the registry cannot classify it")
    if "def load" not in text:
        problems.append(f"{adapter.name} has no load(), so nothing can materialise a task")
    return problems


def _add(args) -> int:
    record = _record()
    panel, (field, what) = args.board, BOARDS[args.board]
    if not HANDLE.match(args.contributor):
        print(f"error: --contributor must be a GitHub handle, got {args.contributor!r}",
              file=sys.stderr)
        return 2
    if args.board == "harness":
        target = Path(args.path).resolve()
        problems = _harness_problems(target)
    elif args.board == "method":
        target = Path(args.preset).resolve()
        problems = _method_problems(target)
    else:
        target = Path(args.adapter).resolve()
        problems = _benchmark_problems(target)
    if problems:
        print(f"error: {what}", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 2

    entries = record["boards"][panel]
    if any(entry["name"] == args.name for entry in entries):
        print(f"error: {panel}/{args.name} is already on the board; edit that row instead",
              file=sys.stderr)
        return 2

    row = {"name": args.name, field: str(target.relative_to(ROOT)),
           "contributor": args.contributor, "since": args.since,
           "adoption": int(args.adoption), "note": args.note}
    if args.contact:
        row["contact"] = args.contact
    entries.append(row)
    entries.sort(key=lambda entry: entry["name"])
    print(json.dumps(row, indent=2))
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    _write(record)
    subprocess.run([sys.executable, str(ROOT / "tools" / "leaderboards.py"), "--write"],
                   check=True, cwd=ROOT)
    print("\nnext:\n"
          "  git add registry/leaderboards.json README.md\n"
          "  git commit -m \"board: add <name>\"\n"
          "  open a pull request, and in its description say in one sentence how you ran it\n"
          "  (`--check-only` output pasted is enough; a score is not required)")
    return 0


def _result(args) -> int:
    record = _record()
    receipt = Path(args.receipt).resolve()
    problems = []
    if not receipt.is_file():
        problems.append(f"receipt does not exist in this repository: {args.receipt}")
    else:
        try:
            receipt.relative_to(ROOT)
        except ValueError:
            problems.append("the receipt must be committed in this repository, not a path outside it")
    if not SHA.match(args.harness_sha):
        problems.append("--harness-sha must be the 40-hex tree or commit id the score was measured on")
    if not HANDLE.match(args.contributor):
        problems.append(f"--contributor must be a GitHub handle, got {args.contributor!r}")
    if problems:
        for line in problems:
            print(f"error: {line}", file=sys.stderr)
        return 2
    entry = {"benchmark": args.benchmark, "metric": args.metric, "score": float(args.score),
             "method": args.method, "harness": args.harness, "harness_sha": args.harness_sha,
             "receipt": str(receipt.relative_to(ROOT)), "contributor": args.contributor,
             "since": args.since}
    if args.contact:
        entry["contact"] = args.contact
    print(json.dumps(entry, indent=2))
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    record.setdefault("results", {"entries": []}).setdefault("entries", []).append(entry)
    _write(record)
    subprocess.run([sys.executable, str(ROOT / "tools" / "leaderboards.py"), "--write"],
                   check=True, cwd=ROOT)
    print("\nnext: git add registry/leaderboards.json README.md && git commit && open a pull request")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("board", choices=("harness", "method", "benchmark", "result"))
    parser.add_argument("--name", default="", help="the entry's name (must match its artefact)")
    parser.add_argument("--path", default="", help="harness directory, for the harness board")
    parser.add_argument("--preset", default="", help="experiment preset, for the method board")
    parser.add_argument("--adapter", default="", help="adapter file, for the benchmark board")
    parser.add_argument("--contributor", required=True, help="your GitHub handle")
    parser.add_argument("--contact", default="",
                        help="optional; stored in the record for the website board, never "
                             "rendered into the README")
    parser.add_argument("--note", default="", help="one line: what the thing is")
    parser.add_argument("--adoption", default="1", help="independent reproductions you know of")
    parser.add_argument("--since", default="", help="date, default today")
    parser.add_argument("--benchmark", default="")
    parser.add_argument("--metric", default="")
    parser.add_argument("--score", default="0")
    parser.add_argument("--method", default="")
    parser.add_argument("--harness", default="")
    parser.add_argument("--harness-sha", default="")
    parser.add_argument("--receipt", default="")
    parser.add_argument("--dry-run", action="store_true", help="print the row, write nothing")
    args = parser.parse_args()

    if not args.since:
        import datetime
        args.since = datetime.date.today().isoformat()
    if args.board == "result":
        return _result(args)
    if not args.name:
        print("error: --name is required for a support-board entry", file=sys.stderr)
        return 2
    return _add(args)


if __name__ == "__main__":
    sys.exit(main())
