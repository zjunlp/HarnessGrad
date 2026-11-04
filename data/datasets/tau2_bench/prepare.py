#!/usr/bin/env python3
"""Prepare the pinned τ² v0.1.0 case assets: public view, private runtime config, manifest.

What is prepared and why it is split
------------------------------------
The harness may see the policy and the *tool schemas*; it must not see the task's user
scenario, the reference actions or the evaluation criteria. So each task becomes two files:
`public.json` (domain, policy text, assistant tool schemas, user tool schemas without their
implementation) and `private.json` (the full task plus the runtime's limits), which only the
trusted runtime and verifier read. The manifest records the digests so a later load can prove
both halves are the ones that were prepared.

The pin is v0.1.0, and the reason is written down in the adapter: only there is `tasks.json`
the base split, and upstream says results below 1.0.1 are not comparable with results at or
above it. This script never calls a model; it constructs each domain environment to read the
policy and the tool schemas.

Usage
-----
    python3 data/datasets/tau2_bench/prepare.py --source <extracted v0.1.0 tree> \
        --target <dataset root>/tau2-bench
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from data.datasets import tau2_bench as dataset  # noqa: E402

ASSETS = pathlib.Path(__file__).resolve().parent
MAX_STEPS = 200
MAX_ERRORS = 10


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def bundle(source: pathlib.Path, target: pathlib.Path, reuse: bool = False) -> pathlib.Path:
    """Copy the runtime source and domain data, never the task sets or trajectories."""
    total = target / "bundle"
    if total.exists():
        # Re-preparing the case files against an unchanged bundle is the common case (the
        # bundle is 92 MB of source and domain data). `--reuse-bundle` says the operator has
        # checked that the copy matches the pin; the files manifest is verified below.
        if not reuse:
            raise SystemExit("use a fresh preparation root, or --reuse-bundle to keep it")
        manifest = json.loads((total / "manifest.json").read_text(encoding="utf-8"))
        listed = {str(p.relative_to(total)): digest(p)
                  for p in sorted(total.rglob("*")) if p.is_file() and p.name != "manifest.json"}
        missing = sorted(set(listed) - set(manifest["files"]))
        changed = sorted(name for name, sha in listed.items() if manifest["files"].get(name) != sha)
        if missing or changed:
            raise SystemExit(f"bundle does not match its manifest: missing {missing[:3]}, "
                             f"changed {changed[:3]}")
        return total
    shutil.copytree(source / "src", total / "src")
    shutil.copy2(source / "LICENSE", total / "LICENSE")
    if (source / "data/tau2/user_simulator").is_dir():
        shutil.copytree(source / "data/tau2/user_simulator", total / "data/tau2/user_simulator")
    for domain in dataset.DOMAINS:
        src = source / "data/tau2/domains" / domain
        dst = total / "data/tau2/domains" / domain
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "*tasks*", "*trajectory*", "*trajectories*", "__pycache__"))
    files = {str(p.relative_to(total)): digest(p) for p in sorted(total.rglob("*")) if p.is_file()}
    write(total / "manifest.json", {"release": dataset.REPO_TAG, "files": files})
    return total


def prepare(source: pathlib.Path, target: pathlib.Path, reuse: bool = False) -> dict:
    total = bundle(source, target, reuse)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["TAU2_DATA_DIR"] = str(total / "data")
    sys.path.insert(0, str(total / "src"))
    sys.path.insert(0, str(ASSETS / "lib"))
    from tau2.data_model.tasks import Task  # noqa: E402  (after the bundle is on sys.path)

    rows = []
    for domain in dataset.DOMAINS:
        path = source / "data/tau2/domains" / domain / "tasks.json"
        tasks = json.loads(path.read_text(encoding="utf-8"))
        if len(tasks) != dataset.N_TASKS[domain]:
            raise SystemExit(f"{domain}: {len(tasks)} tasks, expected {dataset.N_TASKS[domain]}")
        for raw in tasks:
            task = Task.model_validate(raw)
            tid = f"tau2_bench/{domain}/{task.id}"
            config = {"release": dataset.REPO_TAG, "task_id": tid, "domain": domain,
                      "task": raw, "max_steps": MAX_STEPS, "max_errors": MAX_ERRORS}
            constructor = _constructor(domain, total, config, task)
            env = constructor(solo_mode=False)
            public = {"task_id": tid, "domain": domain, "policy": env.get_policy(),
                      "tools": [tool.openai_schema for tool in env.get_tools()],
                      "user_tools": [tool.openai_schema for tool in env.get_user_tools()]
                      if env.user_tools is not None else []}
            relative = f"cases/{domain}/{task.id}"
            write(target / relative / "private.json", config)
            write(target / relative / "public.json", public)
            rows.append({"task_id": tid, "domain": domain, "path": relative,
                         "private_sha256": digest(target / relative / "private.json"),
                         "public_sha256": digest(target / relative / "public.json"),
                         "needs_judge": _needs_judge(raw["evaluation_criteria"]),
                         "user_actions": _user_actions(raw["evaluation_criteria"])})
    manifest = {"schema": "harnessgrad.tau2_cases.v1",
                "user_action_tasks": {domain: sum(1 for row in rows
                                                  if row["domain"] == domain and row["user_actions"])
                                      for domain in dataset.DOMAINS},
                "release": dataset.REPO_TAG,
                "archive_sha256": dataset.TARBALL_SHA256, "counts": dict(dataset.N_TASKS),
                "max_steps": MAX_STEPS, "max_errors": MAX_ERRORS, "tasks": rows}
    write(target / "manifest.json", manifest)
    return manifest


def _user_actions(criteria: dict) -> bool:
    """Whether any gold action is performed by the *user* side.

    Those are the tasks an agent cannot complete alone: the gold state needs actions only the
    simulated customer can take. The adapter used to remember a narrower count; the manifest
    measures it per task instead.
    """
    return any(str(step.get("requestor") or "").lower() == "user"
               for step in criteria.get("actions") or [])


def _needs_judge(criteria: dict) -> bool:
    """Whether the release's scorer makes a model call on this task.

    `EvaluationType.ALL` runs **every** evaluator, `NLAssertionsEvaluator` included, before
    the reward basis multiplies the parts together -- so a task with `nl_assertions` makes a
    judge call even when its basis is the default `[DB, COMMUNICATE]` and the verdict is
    multiplied out. Measured, not inferred: scoring an empty episode of `airline/8` raises
    from `evaluator_nl_assertions.evaluate_nl_assertions` on an unconfigured endpoint.
    (An earlier version of this file asked whether `NL_ASSERTION` was *in the basis*, which
    answered 0; that misread which evaluator runs, not just where.)
    """
    return bool(criteria.get("nl_assertions"))


def _constructor(domain: str, total: pathlib.Path, config: dict, task):
    """The domain's official `get_environment`, with the bundle's data directory bound."""
    module = __import__(f"tau2.domains.{domain}.environment", fromlist=["get_environment"])
    constructor = getattr(module, "get_environment")
    # v0.1.0's constructors take (db=None, solo_mode=False); the bundle's loader finds the
    # data files through TAU2_DATA_DIR, which is set above.
    return lambda **kwargs: constructor(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="extracted v0.1.0 tree")
    parser.add_argument("--target", required=True, help="dataset root to write into")
    parser.add_argument("--reuse-bundle", action="store_true",
                        help="keep an existing bundle whose manifest still matches")
    args = parser.parse_args()
    manifest = prepare(pathlib.Path(args.source), pathlib.Path(args.target), args.reuse_bundle)
    print(json.dumps({"tasks": len(manifest["tasks"]),
                      "manifest_sha256": digest(pathlib.Path(args.target) / "manifest.json")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
