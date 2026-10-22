#!/usr/bin/env python3
"""Validate the source-only blocker index; never import adapters or open receipts."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry/dataset-blockers.json"
HISTORY = ROOT / "tests/fixtures/dataset_blockers/history-codex178.json"
# Pin the review baseline independently of the editable registry.
HISTORY_SHA256 = "4c778d435f4147eb04dbcee4e37a2bac3f0ac5a55584e3adf252ab12972d1300"
STATUSES = {"code-solvable", "external", "runtime", "measurement"}
FAMILIES = {
    "selected-only-privacy", "source-provenance", "scorer-dependency-network",
    "trusted-verifier-runtime", "artifact-only-export", "filesystem-quota",
    "official-scorer-execution", "snapshot-cleanup", "measurement-coverage",
    "metric-comparability", "platform-integrity", "actor-protocol",
    "termination-audit", "contract-refusal", "journal-coverage", "documentation-conflict",
}


class RegistryError(ValueError):
    pass


def _require(condition, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        _require(key not in obj, f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _reject_constant(value):
    raise RegistryError(f"nonfinite JSON constant: {value}")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def literal_declarations(path: Path, names: set[str]) -> dict:
    """Read only module-level literals, without running even taxonomy imports."""
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                _require(target.id not in found, f"{path.name}: repeated {target.id}")
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError) as exc:
                    raise RegistryError(f"{path.name}: {target.id} must be a literal") from exc
    return found


def published_declarations(root: Path = ROOT) -> tuple[str, ...]:
    """Conservative chooser coverage, including unprovisioned/refused datasets.

    `data.scaffolds.registry.published()` imports adapters and may omit an import failure.
    Reading KIND declarations covers every publishable source without loading a
    mixed task pool. A missing literal KIND retains the registry's fixture default.
    """
    return tuple(path.stem for path in sorted((root / "data" / "datasets").glob("*.py"))
                 if literal_declarations(path, {"KIND"}).get("KIND") == "dataset")


def _fields(obj, expected: set[str], label: str) -> None:
    _require(isinstance(obj, dict), f"{label}: expected object")
    _require(set(obj) == expected, f"{label}: fields must be {sorted(expected)}")


def _text(value, label: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{label}: empty text")


def _unique_strings(values, label: str) -> None:
    _require(isinstance(values, list), f"{label}: expected list")
    for value in values:
        _text(value, label)
    _require(len(set(values)) == len(values), f"{label}: duplicates")


def _evidence(items, root: Path, label: str) -> None:
    _require(isinstance(items, list) and bool(items), f"{label}: evidence required")
    kinds = []
    for item in items:
        _fields(item, {"kind", "path", "locator"}, label)
        kind, path = item["kind"], item["path"]
        _text(path, label)
        _text(item["locator"], label)
        _require(kind in {"repository", "external-reference"}, f"{label}: evidence kind")
        kinds.append(kind)
        if kind == "external-reference":
            # This is a historical citation, not a file we have verified or may open.
            _require(Path(path).is_absolute(), f"{label}: external reference must be absolute")
            continue
        rel = Path(path)
        _require(not rel.is_absolute() and ".." not in rel.parts,
                 f"{label}: repository evidence must stay inside root")
        source = root / rel
        _require(source.resolve().is_relative_to(root.resolve()), f"{label}: evidence escapes root")
        _require(source.is_file(), f"{label}: missing evidence {path}")
        _require(item["locator"] in source.read_text(encoding="utf-8"),
                 f"{label}: stale evidence locator in {path}")
    _require("repository" in kinds, f"{label}: repository observation required")


def _blocker(blocker, root: Path, label: str) -> None:
    _fields(blocker, {"id", "family", "status", "observed", "evidence", "next_action"}, label)
    for field in ("id", "observed", "next_action"):
        _text(blocker[field], f"{label}.{field}")
    _require(blocker["family"] in FAMILIES, f"{label}: unknown blocker family")
    _require(blocker["status"] in STATUSES, f"{label}: unknown status")
    _evidence(blocker["evidence"], root, label)


def validate(document: dict, *, root: Path = ROOT) -> dict:
    _fields(document, {"schema_version", "review", "shared_blockers", "datasets"}, "registry")
    _require(type(document["schema_version"]) is int and document["schema_version"] == 1,
             "unsupported schema version")
    _fields(document["review"], {"date", "scope", "production_authorized", "notes"}, "review")
    _text(document["review"]["date"], "review.date")
    _require(document["review"]["scope"] == "repository-source-and-journal-only", "review scope")
    _require(document["review"]["production_authorized"] is False, "registry cannot authorize production")
    _unique_strings(document["review"]["notes"], "review.notes")

    taxonomy = literal_declarations(root / "tools/dataset_classes.py", {"CAPABILITY", "EXERCISES"})
    published = set(published_declarations(root))
    _require(set(taxonomy["CAPABILITY"]) == published, "taxonomy/published declarations differ")
    _require(set(taxonomy["EXERCISES"]) == published, "exercise/published declarations differ")
    _require(isinstance(document["shared_blockers"], list), "shared_blockers: expected list")
    shared = set()
    for blocker in document["shared_blockers"]:
        _blocker(blocker, root, "shared blocker")
        _require(blocker["id"] not in shared, "duplicate shared blocker")
        shared.add(blocker["id"])

    _require(isinstance(document["datasets"], list), "datasets: expected list")
    seen, history, run_ids = set(), {}, set()
    for row in document["datasets"]:
        _fields(row, {"dataset", "demand", "surface", "exercises", "shared_blockers",
                      "blockers", "historical_runs"}, "dataset")
        name = row["dataset"]
        _text(name, "dataset name")
        _require(name not in seen, f"duplicate dataset: {name}")
        _require(name in published, f"unpublished dataset: {name}")
        seen.add(name)
        _require((row["demand"], row["surface"]) == taxonomy["CAPABILITY"][name],
                 f"{name}: demand x surface drift")
        _unique_strings(row["exercises"], f"{name}.exercises")
        _require(row["exercises"] == list(taxonomy["EXERCISES"][name]), f"{name}: exercise drift")
        _unique_strings(row["shared_blockers"], f"{name}.shared_blockers")
        _require(set(row["shared_blockers"]) <= shared, f"{name}: unknown shared blocker")
        # An empty `blockers` list is allowed, and is a statement rather than a blank: it
        # says every requirement this dataset carried was reviewed and either met or
        # withdrawn by decision. `_fields` above already requires the key to be present, so
        # an accidentally-omitted list is still caught; what the old `bool(...)` added was
        # only the inability to express "cleared".
        #
        # This is deliberately NOT the same move as adding a value to `STATUSES` so a
        # finished blocker can stay parked in the registry with a status meaning "done".
        # That keeps a closed requirement counted as live and widens a closed vocabulary;
        # this removes a non-emptiness check so closure has somewhere to land. A dataset
        # cleared this way must have its decision recorded in a checked-in document, and
        # `scienceworld` is the case: see `docs/enforcement-proof-decision.md`.
        _require(isinstance(row["blockers"], list),
                 f"{name}: blockers must be a list")
        ids = set()
        for blocker in row["blockers"]:
            _blocker(blocker, root, name)
            _require(blocker["id"] not in ids, f"{name}: duplicate blocker")
            ids.add(blocker["id"])
        _require(isinstance(row["historical_runs"], list), f"{name}: history must be a list")
        for run in row["historical_runs"]:
            _fields(run, {"record_id", "run_id", "status", "effect", "observation", "evidence"}, name)
            _text(run["record_id"], f"{name}: historical record ID")
            if run["run_id"] is not None:
                _text(run["run_id"], f"{name}: run ID")
                _require(run["run_id"] not in run_ids, f"{name}: duplicate historical run ID")
                run_ids.add(run["run_id"])
            _require(run["status"] in {"incomplete", "partial-measurement", "pending-review"},
                     f"{name}: historical incomplete cannot become complete")
            _require(run["effect"] is None, f"{name}: historical incomplete cannot acquire an effect")
            _text(run["observation"], f"{name}: historical observation")
            _evidence(run["evidence"], root, f"{name}: historical evidence")
            key = run["record_id"]
            _require(key not in history, f"duplicate historical record: {key}")
            history[key] = {"dataset": name, "record": run}
    _require(seen == published, f"missing published datasets: {sorted(published - seen)}")

    raw = HISTORY.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == HISTORY_SHA256, "historical baseline pin changed")
    baseline = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    for old in baseline["records"]:
        key = old["record"]["record_id"]
        _require(history.get(key) == old, f"historical record removed or changed: {key}")
    return {"datasets": len(seen), "shared_blockers": len(shared), "historical_records": len(history)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    args = parser.parse_args(argv)
    try:
        counts = validate(read_json(args.registry))
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 2
    print(json.dumps({"valid": True, **counts, "production_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
