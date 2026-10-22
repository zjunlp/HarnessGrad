#!/usr/bin/env python3
"""Pin, audit and prepare KUMO's first 50 instances per Harbor scenario.

No model calls or third-party dependencies. Run in a fresh process: upstream's
`env` package uses a global registry. Outputs stay outside the platform tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REVISION = "576748764f7d72634654d0784944a485eca9047c"
SCENARIOS = {"easy": "truth_num=4+action_num=6+valid_truth_num=1",
             "hard": "truth_num=12+action_num=16+valid_truth_num=1"}
DEFAULT_ROOT = Path(os.environ.get("HG_KUMO_DATA", Path(__file__).resolve().parents[4] / "datasets/kumo"))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def check_checkout(source):
    if not (source / ".git").exists():
        inventory = json.loads((Path(__file__).parent / "source-files.json").read_text())
        for name, expected in inventory.items():
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"upstream source changed: {name}")
        for path in source.rglob("*.py"):
            name = path.relative_to(source).as_posix()
            if name.startswith(("env/", "common/")) and name not in inventory:
                raise ValueError(f"unexpected upstream module: {name}")
        return {name: digest for name, digest in inventory.items()
                if name.startswith(("env/", "common/")) or name == "LICENSE"}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source).decode("ascii").strip()
    if revision != REVISION:
        raise ValueError("KUMO checkout is not at the pinned revision")
    entries = subprocess.check_output(["git", "ls-tree", "-r", "-z", "HEAD"], cwd=source)
    hashes = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        name = name.decode()
        if not (name.startswith(("env/", "common/")) or name == "LICENSE"):
            continue
        raw = (source / name).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != meta.split()[2].decode():
            raise ValueError(f"upstream source changed: {name}")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    # Untracked Python can change imports despite matching every tracked blob.
    for path in source.rglob("*.py"):
        relative = path.relative_to(source).as_posix()
        if relative.startswith(("env/", "common/")) and relative not in hashes:
            raise ValueError(f"unexpected upstream module: {relative}")
    return hashes


def prepare(root):
    source = root / "upstream"
    source_hashes = check_checkout(source)
    sys.path.insert(0, str(source))
    from env import load_env

    target = root / "prepared-v1"
    if target.exists():
        raise ValueError(f"prepared output already exists; preserve it before rebuilding: {target}")
    audit, tasks, exclusions = [], [], []
    for difficulty, scenario in SCENARIOS.items():
        for path in sorted((source / "env").glob(f"*/{scenario}.jsonl")):
            books = path.parent / "knowledge_book" / scenario
            if not books.is_dir():
                continue  # OOD hard is not a Harbor scenario; lacks books and is truncated.
            with path.open() as stream:
                for index in range(50):
                    row = json.loads(next(stream))
                    if row["seed"] != index:
                        raise ValueError(f"seed/book mismatch: {path}:{index}")
                    domain = path.parent.name
                    tid = f"kumo/{difficulty}/{domain}/{index}"
                    environment = load_env(domain, row)
                    accepted = [t for t in row["truths"] if environment.env.evaluate_answer(t)]
                    repeated = [t for t in row["truths"] if environment.env.evaluate_answer(t)]
                    if repeated != accepted or environment.env.evaluate_answer(""):
                        raise ValueError(f"unstable scorer or nonzero noop: {tid}")
                    audit.append({"task_id": tid, "accepted": accepted, "gold": row["valid_truth"],
                                  "gold_correct": row["valid_truth"] in accepted,
                                  "repeat_matches": True})
                    if not accepted:
                        exclusions.append({"task_id": tid, "reason": "official_scorer_accepts_no_candidate"})
                        continue
                    if row["valid_truth"] not in accepted:
                        raise ValueError(f"released gold fails despite reachable score: {tid}")
                    book = (books / f"seed={index}.txt").read_text()
                    if not book.strip() or set(row["actions"]) != set(row["observations"]):
                        raise ValueError(f"incomplete task: {tid}")
                    public = {"task_id": tid, "domain": domain, "difficulty": difficulty,
                              "truths": row["truths"], "actions": row["actions"],
                              "knowledge_book": book,
                              "max_queries": 50}
                    rules = {action: {"type": environment.env.ta_mapping[action]["type"],
                                      "states": [[state, sorted(ruled_out)] for state, ruled_out
                                                 in environment.env.ta_mapping[action]["states"].items()]}
                             for action in row["observations"]}
                    private = {"schema": "kumo.case.v1", "task_id": tid, "source_revision": REVISION,
                               "truths": row["truths"], "actions": row["actions"], "max_queries": 50,
                               "observations": row["observations"], "valid_truth": row["valid_truth"],
                               "rules": rules, "seed": index}
                    relative = f"{difficulty}/{domain}/{index}"
                    public_sha = write(target / relative / "public.json", public)
                    private_sha = write(target / relative / "private.json", private)
                    tasks.append({"task_id": tid, "path": relative, "difficulty": difficulty,
                                  "public_sha256": public_sha, "private_sha256": private_sha})
    if len(audit) != 5300 or len(exclusions) != 10 or len(tasks) != 5290:
        raise ValueError("pinned source audit count changed")
    manifest = {"schema": "kumo.manifest.v1", "source_revision": REVISION,
                "metric": "kumo.official.consistency_accuracy.v1", "protocol": "kumo.query50.v1",
                "source_tasks": 5300, "tasks": tasks, "exclusions": exclusions}
    digest = write(target / "manifest.json", manifest)
    write(root / "official-audit.json", {"source_revision": REVISION, "rows": audit,
                                         "source_sha256": source_hashes})
    print(json.dumps({"eligible": len(tasks), "excluded": len(exclusions),
                      "manifest_sha256": digest, "manifest": str(target / "manifest.json")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--fetch", action="store_true", help="download the pinned sparse checkout first")
    args = parser.parse_args()
    root = args.root.resolve()
    platform = Path(__file__).resolve().parents[3]
    if root == platform or platform in root.parents:
        parser.error("dataset root must be outside the platform tree")
    if args.fetch and not (root / "upstream").exists():
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth=1", "--filter=blob:none", "--no-checkout",
                        "https://github.com/linhaowei1/kumo.git", str(root / "upstream")], check=True)
        for command in (["sparse-checkout", "init", "--cone"],
                        ["sparse-checkout", "set", "env", "common", "agents", "llm", "examples", "templates"],
                        ["fetch", "--depth=1", "--filter=blob:none", "origin", REVISION],
                        ["checkout", "--detach", "FETCH_HEAD"]):
            subprocess.run(["git", *command], cwd=root / "upstream", check=True)
    prepare(root)


if __name__ == "__main__":
    main()
