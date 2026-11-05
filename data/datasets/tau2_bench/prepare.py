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
    <runtime>/bin/python data/datasets/tau2_bench/prepare.py --source <extracted v0.1.0 tree> \
        --target <fresh dataset root>/tau2-bench
    python3 data/datasets/tau2_bench/prepare.py --check-only --target <prepared root> \
        --runtime <Python 3.13 runtime directory>

Generation requires Python 3.13 and publishes a complete fresh directory by atomic rename.
--reuse-bundle regenerates into temporary storage and proves reproducibility, without
replacing existing assets. Check-only can run on Python 3.12 and never imports tau2; its
runtime probe checks module discovery and installed metadata, not model/provider health.
"""
import sys

# Set this before any non-builtin imports, including when invoked without python -B.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from data.datasets import tau2_bench as dataset  # noqa: E402

ASSETS = pathlib.Path(__file__).resolve().parent
MAX_STEPS = 200
MAX_ERRORS = 10


def _bootstrap_module():
    # No sys.path mutation or importing the official package during read-only inspection.
    import importlib.util
    spec = importlib.util.spec_from_file_location("tau2_prepare_bootstrap", ASSETS / "lib/bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_file(root: pathlib.Path, relative: str) -> pathlib.Path:
    part = pathlib.Path(relative)
    if part.is_absolute() or ".." in part.parts or str(part) != relative:
        raise ValueError(f"unsafe prepared asset path: {relative}")
    path = root / part
    if any((root / pathlib.Path(*part.parts[:i])).is_symlink()
           for i in range(len(part.parts) + 1)):
        raise ValueError(f"symlink in prepared asset: {relative}")
    if not path.is_file():
        raise ValueError(f"missing prepared asset: {relative}")
    return path


def validate_cases(target: pathlib.Path) -> dict:
    """Validate the pinned inventory and *both* public/private halves, without runtime imports."""
    path = _safe_file(target, "manifest.json")
    if digest(path) != dataset.MANIFEST_SHA256:
        raise ValueError("tau2 case manifest digest mismatch (expected " + dataset.MANIFEST_SHA256 + ")")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "harnessgrad.tau2_cases.v1"
            or manifest.get("release") != dataset.REPO_TAG
            or manifest.get("archive_sha256") != dataset.TARBALL_SHA256):
        raise ValueError("tau2 case manifest identity does not match the pin")
    rows = manifest.get("tasks", [])
    if (manifest.get("counts") != dataset.N_TASKS or len(rows) != sum(dataset.N_TASKS.values())
            or Counter(row["domain"] for row in rows) != dataset.N_TASKS):
        raise ValueError("tau2 case manifest domain/task counts mismatch")
    if manifest.get("max_steps") != MAX_STEPS or manifest.get("max_errors") != MAX_ERRORS:
        raise ValueError("tau2 case manifest limits mismatch")
    seen = set()
    user_counts = Counter({domain: 0 for domain in dataset.DOMAINS})
    for row in rows:
        tid, domain = row["task_id"], row["domain"]
        prefix = f"tau2_bench/{domain}/"
        if not tid.startswith(prefix) or tid in seen or row["path"] != "cases/" + tid[len("tau2_bench/"):]:
            raise ValueError(f"tau2 case identity/duplicate mismatch: {tid}")
        seen.add(tid)
        halves = {}
        for half in ("public", "private"):
            relative = row["path"] + f"/{half}.json"
            asset = _safe_file(target, relative)
            if digest(asset) != row[half + "_sha256"]:
                raise ValueError(f"tau2 {half} task digest mismatch: {tid}")
            value = json.loads(asset.read_text(encoding="utf-8"))
            if value.get("task_id") != tid or value.get("domain") != domain:
                raise ValueError(f"tau2 {half} task identity mismatch: {tid}")
            halves[half] = value
        private = halves["private"]
        if (private.get("release") != dataset.REPO_TAG
                or str(private["task"]["id"]) != tid[len(prefix):]
                or private.get("max_steps") != MAX_STEPS or private.get("max_errors") != MAX_ERRORS):
            raise ValueError(f"tau2 private task pin/limits mismatch: {tid}")
        criteria = private["task"]["evaluation_criteria"]
        if (row["needs_judge"] is not _needs_judge(criteria)
                or row["user_actions"] is not _user_actions(criteria)):
            raise ValueError(f"tau2 task dependency flags mismatch: {tid}")
        user_counts[domain] += row["user_actions"]
    if dict(user_counts) != manifest.get("user_action_tasks"):
        raise ValueError("tau2 user action counts mismatch")
    return manifest


# Probe discovery/installed metadata only: never import litellm/tau2 or initialize providers.
_RUNTIME_PROBE = '''import importlib.util, json, sys
from importlib.metadata import version, PackageNotFoundError
modules = ("pydantic", "loguru", "docstring_parser", "litellm", "tenacity", "deepdiff", "addict", "yaml", "toml", "pandas", "numpy", "dotenv")
missing = [name for name in modules if importlib.util.find_spec(name) is None]
minimums = {"pydantic": (2,), "litellm": (1, 65, 0), "loguru": (0, 7, 3),
            "docstring-parser": (0, 16), "tenacity": (9, 0, 0), "deepdiff": (8, 4, 2),
            "addict": (2, 4, 0), "PyYAML": (6, 0, 2), "toml": (0, 10, 2), "pandas": (2, 2, 3)}
outdated = []
versions = {}
for name, minimum in minimums.items():
    try:
        installed = version(name)
        versions[name] = installed
        numeric = tuple(int(part) for part in installed.split(".")[:len(minimum)])
        if numeric < minimum:
            outdated.append(name + "==" + installed)
    except (PackageNotFoundError, ValueError):
        outdated.append(name + " (missing or unrecognized version)")
print(json.dumps({"version": list(sys.version_info[:2]), "prefix": sys.prefix, "missing": missing,
                  "outdated": outdated, "dependencies": versions}))
'''


def check_runtime(runtime: pathlib.Path) -> dict:
    python = runtime / "bin/python"
    try:
        proc = subprocess.run([str(python), "-I", "-B", "-c", _RUNTIME_PROBE],
                              capture_output=True, text=True, check=True, timeout=20)
        info = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError(f"cannot inspect tau2 runtime {python}: {exc}") from exc
    if info.get("version") != [3, 13]:
        raise ValueError(f"tau2 runtime requires Python 3.13: {python}")
    if pathlib.Path(info["prefix"]).resolve() != runtime.resolve():
        raise ValueError(f"tau2 runtime prefix mismatch: {python}")
    if info["missing"]:
        raise ValueError("tau2 runtime missing dependencies: " + ", ".join(info["missing"]))
    if info.get("outdated"):
        raise ValueError("tau2 runtime dependency versions not ready: " + ", ".join(info["outdated"]))
    return info


def check_only(target: pathlib.Path, runtime: pathlib.Path | None = None) -> dict:
    _bootstrap_module().validate_bundle(target / "bundle")
    manifest = validate_cases(target)
    info = check_runtime(runtime if runtime is not None else target / "runtime")
    return {"tasks": len(manifest["tasks"]), "manifest_sha256": digest(target / "manifest.json"),
            "runtime": info, "check_only": True}


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: pathlib.Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def bundle(source: pathlib.Path, target: pathlib.Path, reuse: bool = False) -> pathlib.Path:
    """Copy the runtime source and domain data, never the task sets or trajectories."""
    total = target / "bundle"
    if total.exists():
        if not reuse:
            raise ValueError("use a fresh preparation root, or --reuse-bundle to keep it")
        _bootstrap_module().validate_bundle(total)
        return total
    shutil.copytree(source / "src", total / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(source / "LICENSE", total / "LICENSE")
    if (source / "data/tau2/user_simulator").is_dir():
        shutil.copytree(source / "data/tau2/user_simulator", total / "data/tau2/user_simulator",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for domain in dataset.DOMAINS:
        src = source / "data/tau2/domains" / domain
        dst = total / "data/tau2/domains" / domain
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "*tasks*", "*trajectory*", "*trajectories*", "__pycache__"))
    files = {str(p.relative_to(total)): digest(p) for p in sorted(total.rglob("*")) if p.is_file()}
    write(total / "manifest.json", {"release": dataset.REPO_TAG, "files": files})
    _bootstrap_module().validate_bundle(total)
    return total


def prepare(source: pathlib.Path, target: pathlib.Path, reuse: bool = False) -> dict:
    """Stage a full new payload, or prove an existing payload regenerates identically.

    Never replace an existing prepared tree: it may contain a live runtime. Reuse reads its
    pinned bundle in place (no 92 MB copy), generates only cases in a sibling staging directory,
    and compares every generated digest to the pinned inventory before returning success.
    """
    source, target = source.resolve(), target.absolute()
    if sys.version_info[:2] != (3, 13):
        raise ValueError("tau2 preparation requires Python 3.13")
    existing = target.exists()
    if existing:
        if not reuse:
            raise ValueError("target already exists; use a fresh target or --reuse-bundle to check reproducibility")
        _bootstrap_module().validate_bundle(target / "bundle")
        validate_cases(target)  # Never repair/overwrite a partial or corrupt live payload.
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="." + target.name + "-prepare-", dir=target.parent) as tmp:
        staged = pathlib.Path(tmp) / "payload"
        staged.mkdir()
        total = target / "bundle" if existing else bundle(source, staged)
        manifest = _generate(source, staged, total)
        validate_cases(staged)  # Includes the expected manifest digest, not a self-authored receipt.
        if not existing:
            if target.exists():
                raise ValueError("target appeared during preparation; refusing to overwrite it")
            staged.rename(target)  # Same filesystem: the complete payload becomes visible at once.
        return manifest


def _generate(source: pathlib.Path, target: pathlib.Path, total: pathlib.Path) -> dict:
    if any(name == "tau2" or name.startswith("tau2.") for name in sys.modules):
        raise ValueError("tau2 is already imported; prepare in a fresh Python 3.13 process")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TAU2_DATA_DIR"] = str(total / "data")
    sys.pycache_prefix = str(total / ".bytecode-disabled")
    sys.path.insert(0, str(total / "src"))
    sys.path.insert(0, str(ASSETS / "lib"))
    from tau2.data_model.tasks import Task  # noqa: E402  (after the bundle is on sys.path)

    rows = []
    seen = set()
    for domain in dataset.DOMAINS:
        path = source / "data/tau2/domains" / domain / "tasks.json"
        tasks = json.loads(path.read_text(encoding="utf-8"))
        if len(tasks) != dataset.N_TASKS[domain]:
            raise SystemExit(f"{domain}: {len(tasks)} tasks, expected {dataset.N_TASKS[domain]}")
        template = None
        for raw in tasks:
            task = Task.model_validate(raw)
            task_name = str(task.id)
            if not task_name or task_name in (".", "..") or "/" in task_name or "\\" in task_name:
                raise ValueError(f"unsafe task id: {task_name}")
            tid = f"tau2_bench/{domain}/{task_name}"
            if tid in seen:
                raise ValueError(f"duplicate task id: {tid}")
            seen.add(tid)
            config = {"release": dataset.REPO_TAG, "task_id": tid, "domain": domain,
                      "task": raw, "max_steps": MAX_STEPS, "max_errors": MAX_ERRORS}
            if template is None:
                constructor = _constructor(domain, total, config, task)
                env = constructor(solo_mode=False)
                template = {"policy": env.get_policy(),
                            "tools": [tool.openai_schema for tool in env.get_tools()],
                            "user_tools": [tool.openai_schema for tool in env.get_user_tools()]
                            if env.user_tools is not None else []}
            public = {"task_id": tid, "domain": domain, **template}
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
    parser.add_argument("--source", help="extracted v0.1.0 tree (preparation only)")
    parser.add_argument("--target", required=True, help="prepared dataset root")
    parser.add_argument("--runtime", help="Python 3.13 runtime directory (default: <target>/runtime)")
    parser.add_argument("--check-only", action="store_true",
                        help="validate pins, both case halves and runtime readiness; no writes or model calls")
    parser.add_argument("--reuse-bundle", action="store_true",
                        help="read an existing payload's bundle and prove identical regeneration; never overwrite")
    args = parser.parse_args()
    if not args.check_only and not args.source:
        parser.error("--source is required for preparation (not for --check-only)")
    try:
        target = pathlib.Path(args.target)
        if args.check_only:
            result = check_only(target, pathlib.Path(args.runtime) if args.runtime else None)
        else:
            manifest = prepare(pathlib.Path(args.source), target, args.reuse_bundle)
            result = {"tasks": len(manifest["tasks"]), "manifest_sha256": digest(target / "manifest.json")}
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"tau2 preparation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
