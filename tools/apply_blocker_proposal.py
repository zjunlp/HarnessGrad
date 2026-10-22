#!/usr/bin/env python3
"""Apply one reviewed blocker-proposal fragment to the registry, with obligation-loss checks.

Worker proposals arrive as `registry/proposals/<worker>.json`. This tool is the parent-side
integration step: it verifies the proposal against the *current* registry, refuses when the
proposal would silently drop an obligation the old `next_action` carried, and only then writes.
It never touches status, family, evidence, history or `shared_blockers` - only the two prose
fields a proposal is allowed to change.

Shapes accepted for the proposal document:

* a single record: ``{"dataset": ..., "blocker": ..., "observed_append": ...,
  "next_action_replacement": ...}`` (``next_action`` is accepted as an alias), or
* a clause-level list: ``{"changes": [{"dataset":..., "blocker":..., "class":...,
  "quoted_clause":..., "replacement":...}, ...]}`` as produced by a staleness audit. Entries for
  the requested dataset/blocker are collapsed, and every entry must carry the *same*
  replacement - otherwise the tool refuses rather than letting one entry overwrite another.

Usage::

    python3 tools/apply_blocker_proposal.py registry/proposals/w-x.json \
        --dataset fever --blocker dataset-journal-gap            # dry run, reports only
    python3 tools/apply_blocker_proposal.py registry/proposals/w-x.json \
        --dataset fever --blocker dataset-journal-gap --apply    # writes, then re-checks

Exit codes: 0 applied or clean dry run, 1 refusal or validation failure, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "registry" / "dataset-blockers.json"
CHECKER = ROOT / "tools" / "check_dataset_blockers.py"

# Sentence boundaries used by the registry's prose. Abbreviations that must not split.
_ABBREVIATIONS = ("e.g.", "i.e.", "cf.", "vs.", "etc.", "no.", "fig.", "al.")
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+")


class Refusal(ValueError):
    """A proposal that must not be applied without an explicit override."""


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_blocker(document: dict, dataset: str, blocker_id: str) -> dict:
    for row in document["datasets"]:
        if row["dataset"] != dataset:
            continue
        for blocker in row["blockers"]:
            if blocker["id"] == blocker_id:
                return blocker
    raise Refusal(f"no open blocker {dataset}/{blocker_id} in the registry")


def sentences(text: str) -> list[str]:
    protected = text
    for index, abbreviation in enumerate(_ABBREVIATIONS):
        protected = protected.replace(abbreviation, f"\x00{index}\x00")
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(protected) if part.strip()]
    restored = []
    for part in parts:
        for index, abbreviation in enumerate(_ABBREVIATIONS):
            part = part.replace(f"\x00{index}\x00", abbreviation)
        restored.append(part)
    return restored


def tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^0-9a-z]+", text.lower()) if token}


def coverage(old_sentence: str, new_sentences: list[str]) -> float:
    """Best token-containment of one old sentence in any single new sentence."""
    old = tokens(old_sentence)
    if not old:
        return 1.0
    best = 0.0
    for new in new_sentences:
        hit = len(old & tokens(new)) / len(old)
        best = max(best, hit)
    return best


def collapse(proposal: dict, dataset: str, blocker: str) -> dict:
    """Reduce either proposal shape to {observed_append, next_action, quoted_clauses}."""
    if isinstance(proposal, list):
        # A bare list of per-dataset records, as w-metriccmp emits: normalise it to the
        # `changes` shape, accepting either `replacement` or `next_action_replacement`.
        proposal = {"changes": [
            {"dataset": item.get("dataset"), "blocker": item.get("blocker"),
             "quoted_clause": item.get("quoted_clause"),
             "replacement": item.get("replacement") or item.get("next_action_replacement"),
             "observed_append": item.get("observed_append")}
            for item in proposal]}
    if "changes" in proposal:
        entries = [item for item in proposal["changes"]
                   if item.get("dataset") == dataset and item.get("blocker") == blocker]
        if not entries:
            raise Refusal(f"proposal has no change entry for {dataset}/{blocker}")
        replacements = {item["replacement"] for item in entries}
        if len(replacements) != 1:
            raise Refusal(
                f"{len(replacements)} distinct replacement strings for {dataset}/{blocker}: "
                "refusing so one entry cannot overwrite another")
        observed = {item.get("observed_append") or "" for item in entries}
        if len(observed) != 1:
            raise Refusal(
                f"{len(observed)} distinct observed_append strings for {dataset}/{blocker}")
        return {
            "observed_append": observed.pop(), "next_action": replacements.pop(),
            "quoted_clauses": [item["quoted_clause"] for item in entries if item.get("quoted_clause")],
        }
    next_action = proposal.get("next_action_replacement") or proposal.get("next_action")
    if not next_action:
        raise Refusal("proposal carries no next_action_replacement")
    quoted = proposal.get("quoted_clause") or []
    if isinstance(quoted, str):
        quoted = [quoted]
    return {"observed_append": proposal.get("observed_append") or "",
            "next_action": next_action, "quoted_clauses": quoted}


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def acknowledged(sentence: str, acknowledgements: list[str]) -> bool:
    """An uncovered sentence is acknowledged when a supplied phrase matches it either way."""
    target = normalise(sentence)
    for phrase in acknowledgements:
        if not phrase.strip():
            continue
        needle = normalise(phrase)
        if needle in target or target in needle:
            return True
    return False


def validate(blocker: dict, payload: dict, min_coverage: float, force: bool,
             acknowledgements: list[str]) -> list[str]:
    """Return the refusal list; empty means the proposal may be applied."""
    problems: list[str] = []
    old_action = blocker["next_action"]
    new_action = payload["next_action"]
    for clause in payload["quoted_clauses"]:
        if clause not in old_action:
            problems.append(f"quoted_clause not present verbatim in next_action: {clause[:90]!r}")
    if new_action.strip() == old_action.strip():
        problems.append("next_action_replacement is identical to the current value")
    unacknowledged = [(ratio, text) for ratio, text in _uncovered(old_action, new_action, min_coverage)
                      if not acknowledged(text, acknowledgements)]
    if unacknowledged and not force:
        problems.append(
            f"{len(unacknowledged)} sentence(s) of the current next_action are not carried by the "
            "replacement and were not acknowledged; pass --acknowledge for each one you intend to "
            "drop (or --force after reading them):")
        problems.extend(f"    coverage {ratio:.2f}: {text}" for ratio, text in unacknowledged)
    return problems


def _uncovered(old_action: str, new_action: str, min_coverage: float) -> list[tuple[float, str]]:
    new_sentences = sentences(new_action)
    uncovered = []
    for old_sentence in sentences(old_action):
        ratio = coverage(old_sentence, new_sentences)
        if ratio < min_coverage:
            uncovered.append((ratio, old_sentence))
    return uncovered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("proposal", type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--blocker", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--min-coverage", type=float, default=0.6)
    parser.add_argument("--acknowledge", action="append", default=[], metavar="TEXT",
                        help="a phrase naming a clause you intend to drop; repeatable")
    parser.add_argument("--force", action="store_true",
                        help="apply despite unacknowledged dropped sentences")
    parser.add_argument("--apply", action="store_true",
                        help="write the registry; without it this is a dry run")
    args = parser.parse_args(argv)

    document = load(args.registry)
    try:
        blocker = find_blocker(document, args.dataset, args.blocker)
        payload = collapse(load(args.proposal), args.dataset, args.blocker)
        problems = validate(blocker, payload, args.min_coverage, args.force, args.acknowledge)
    except Refusal as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1
    if problems:
        print("REFUSED:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    old_action, old_observed = blocker["next_action"], blocker["observed"]
    forced = [(r, t) for r, t in _uncovered(old_action, payload["next_action"], args.min_coverage)
              if not acknowledged(t, args.acknowledge)]
    if forced:
        print(f"WARNING: {len(forced)} uncovered sentence(s) applied without acknowledgement:",
              file=sys.stderr)
        for ratio, text in forced:
            print(f"    coverage {ratio:.2f}: {text}", file=sys.stderr)
    already = bool(payload["observed_append"]) and payload["observed_append"].strip() in old_observed
    if payload["observed_append"] and not already:
        blocker["observed"] = old_observed.rstrip() + " " + payload["observed_append"].strip()
    blocker["next_action"] = payload["next_action"].strip()

    print(f"{args.dataset}/{args.blocker}")
    print(f"  next_action  {len(old_action)} -> {len(blocker['next_action'])} chars")
    print(f"  observed     {len(old_observed)} -> {len(blocker['observed'])} chars"
          + ("  (append already present: no-op)" if already else ""))
    if not args.apply:
        print("  dry run: nothing written (pass --apply)")
        return 0

    original = args.registry.read_text(encoding="utf-8")
    args.registry.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    checked = subprocess.run([sys.executable, str(CHECKER), "--registry", str(args.registry)],
                             capture_output=True, text=True, errors="replace", cwd=ROOT)
    output = (checked.stdout or checked.stderr).strip()
    print(f"  checker: {output}")
    if checked.returncode != 0 or '"valid": true' not in output:
        args.registry.write_text(original, encoding="utf-8")
        print("  checker did not validate: registry restored", file=sys.stderr)
        return 1
    print("  applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
