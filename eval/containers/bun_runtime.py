"""Offline Bun dependency preparation at the trusted platform boundary.

Candidates contain source, never a copied installation. Bun re-links cached
third-party packages and resolves workspace links against this candidate.
Neither lifecycle scripts nor package downloads are permitted here.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from eval.ckpt.source_tree import runtime_spec, BUN_GENERATED
from eval.ckpt.bun_dependencies import (RuntimeUnavailable, source_files, dependency_identity,
                                   _module_dirs, attach_layout)


def _links_owned(source):
    for directory, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in BUN_GENERATED]
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve().is_relative_to(source):
                raise RuntimeUnavailable(f"Bun source link escapes candidate: {path.relative_to(source)}")


def runtime_settings():
    bun = Path(os.environ.get("HG_BUN_BINARY") or os.environ.get("HG_AGENT_OPENCODE_BUN")
               or shutil.which("bun") or "").resolve()
    cache_value = (os.environ.get("HG_BUN_CACHE") or os.environ.get("HG_OPENCODE_DEPENDENCY_CACHE")
                   or os.environ.get("BUN_INSTALL_CACHE_DIR"))
    if not bun.is_file() or not cache_value or not Path(cache_value).is_dir():
        raise RuntimeUnavailable("Bun requires a configured Bun binary and a populated offline dependency cache")
    return bun, Path(cache_value).resolve()


def _declared_roots(repo) -> list[Path]:
    """The `runtime_paths` a harness declares: the trees the platform binds read-only.

    A bun candidate normally carries its own source, and the platform's rule has been that it
    must -- `state_after_round<N>/` is a copy of the tree, and a candidate whose source lives
    somewhere else is not the artifact anyone measured. That rule broke on real harnesses:
    `opencode` needs the opencode monorepo, 224 MB and 4,689 packages, and every round
    would copy it into the run record and re-hash it.

    So a harness may now **declare** its source outside the candidate: the path goes in the
    manifest's `runtime_paths`, which is already the platform's own declaration of "bind this
    for the harness". Nothing is admitted that the manifest did not name, and the platform
    still binds it read-only -- the boundary moves from "must be inside" to "must be declared".
    """
    try:
        manifest = json.loads((Path(repo) / "harness.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(str(entry)).expanduser() for entry in manifest.get("runtime_paths") or []]


def owned_source(repo):
    """The bun runtime spec and the source tree it names, inside the candidate or declared.

    Returns `(spec, source, entry, external)`. `external` is true when the source came from a
    declared `runtime_paths` entry rather than from the candidate itself; preparation and
    validation treat those two differently (an external tree is already installed and must not
    be written to).
    """
    spec = runtime_spec(repo)
    if spec is None:
        return None
    root = Path(repo).resolve()
    candidates = [root] + _declared_roots(root)
    for index, base in enumerate(candidates):
        base = base.resolve()
        # A declared entry may *be* the source tree, or may *contain* a directory named by
        # `runtime.source`. Both are unambiguous once the entrypoint has to exist inside it,
        # and the narrower one is what lets a manifest bind the source without binding its
        # siblings: `.../prior-art/opencode` rather than all of `.../prior-art`.
        source = base if (index and base.name == spec["source"]) else (base / spec["source"]).resolve()
        entry = (source / spec["entrypoint"]).resolve()
        if not source.is_dir() or not entry.is_file():
            continue
        if not entry.is_relative_to(source):
            raise RuntimeUnavailable("Bun entry must belong to its declared source")
        if index and not (source == base or source.is_relative_to(base)):
            raise RuntimeUnavailable(
                f"Bun source escapes its declared runtime path: {source}")
        return spec, source, entry, bool(index)
    raise RuntimeUnavailable(
        "Bun source and entry must belong to this candidate, or be declared in "
        "harness.json's `runtime_paths`")


def admit(repo, env):
    """Formal execution cannot opt into the adapter's external-source diagnostic."""
    try:
        if env.get("HG_AGENT_OPENCODE_EXTERNAL_SOURCE") not in (None, "", "0"):
            raise RuntimeUnavailable("External source diagnostic mode is forbidden in formal execution")
        spec = runtime_spec(repo)
        if spec is None:
            return {}
        _, source, entry, _external = owned_source(repo)
        for name, expected in (("HG_AGENT_OPENCODE_SOURCE", source),
                               ("HG_HARNESS_SOURCE", source), ("HG_HARNESS_ENTRYPOINT", entry)):
            if env.get(name) and Path(env[name]).resolve() != expected:
                raise RuntimeUnavailable("Source override is diagnostic-only; execute the declared candidate")
        return {"HG_HARNESS_SOURCE": str(source), "HG_HARNESS_ENTRYPOINT": str(entry)}
    except (OSError, ValueError) as exc:
        raise RuntimeUnavailable(str(exc)) from exc


def _run(argv, *, cwd, env, timeout=120):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                                text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeUnavailable(f"Bun preparation failed: {type(exc).__name__}") from exc
    if result.returncode:
        # No credentials are passed to preparation; diagnostics are package/parser data.
        raise RuntimeUnavailable(f"Bun preparation exit {result.returncode}: {result.stderr[-1800:]}")
    return result.stdout


def validate_source(repo: Path, *, raise_runtime_errors: bool = False) -> list[str]:
    """Parse source; opt in to distinguishing syntax findings from runtime faults."""
    try:
        owned = owned_source(repo)
        if owned is None:
            return []
        _, source, _, external = owned
        if external:
            # An external tree is prepared by whoever installed it, not by this platform, and
            # re-transpiling 4,689 packages on every round would cost more than the run. Its
            # identity is recorded by the harness, not checked here.
            return []
        bun, _ = runtime_settings()
        _links_owned(source)
        # Parse runtime source without importing/evaluating it. Not a TypeScript typecheck.
        script = """const fs = require('fs'), path = require('path');
const root = process.argv[1];
function walk(dir) { for (const e of fs.readdirSync(dir, {withFileTypes:true})) {
 if (['node_modules','.git'].includes(e.name)) continue;
 const p=path.join(dir,e.name);
 if(e.isDirectory()) walk(p);
 else if (/\\.[cm]?[jt]sx?$/.test(p) && !p.endsWith('.d.ts')) {
  const loader=p.endsWith('tsx')?'tsx':p.endsWith('jsx')?'jsx':p.endsWith('ts')?'ts':'js';
  try { new Bun.Transpiler({loader}).transformSync(fs.readFileSync(p,'utf8')); }
  catch(e) { console.log(JSON.stringify({syntax_error:p+': '+e.message})); process.exit(0); }
 }
}}
walk(root); console.log(JSON.stringify({ok:true}));"""
        with tempfile.TemporaryDirectory(prefix="hg-bun-parser-") as neutral:
            output = _run([str(bun), f"--config={os.devnull}", "--no-env-file", "--no-install",
                  "--no-macros", "-e", script, str(source)],
                 cwd=neutral, env={"PATH": os.environ.get("PATH", ""), "HOME": neutral})
        try:
            result = json.loads(output)
        except ValueError as exc:
            raise RuntimeUnavailable("Bun parser returned an invalid receipt") from exc
        if isinstance(result, dict) and isinstance(result.get("syntax_error"), str):
            return [result["syntax_error"]]
        if result != {"ok": True}:
            raise RuntimeUnavailable("Bun parser returned an invalid receipt")
    except (ValueError, OSError) as exc:
        if raise_runtime_errors:
            raise RuntimeUnavailable(str(exc)) from exc
        return [str(exc)]
    return []


def _dependency_layout(source, bun, cache, key, env, *, package):
    """Publish an atomic, dependency-only store and relocatable workspace layout."""
    import fcntl
    stores = cache.parent / "bun-dependencies"
    stores.mkdir(exist_ok=True)
    store = stores / key
    with (stores / (key + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (store / "layout.json").is_file():
            return store, True
        staging = Path(tempfile.mkdtemp(prefix=key[:12] + "-", dir=stores))
        try:
            build = staging / "build"
            shutil.copytree(source, build, symlinks=True,
                            ignore=shutil.ignore_patterns(*BUN_GENERATED, "_harnessgrad"))
            _run([str(bun), f"--config={os.devnull}", "--no-env-file", "install",
                  "--offline", "--frozen-lockfile", "--ignore-scripts", "--linker", "isolated",
                  "--backend", "hardlink", "--filter", package + "..."],
                 cwd=build, env={**env, "HOME": str(staging)})
            packages = build / "node_modules/.bun"
            if not packages.is_dir():
                raise RuntimeUnavailable("Bun isolated package store was not produced")
            # A shared package must not resolve a workspace from the build template.
            for path in packages.rglob("*"):
                if path.is_symlink() and not path.resolve().is_relative_to(packages):
                    raise RuntimeUnavailable("Third-party dependency store contains a workspace/external link")
            layout = []
            for directory in _module_dirs(build):
                relative = directory.relative_to(build)
                layout.append(relative.as_posix())
                shutil.copytree(directory, staging / "layout" / relative, symlinks=True,
                                ignore=shutil.ignore_patterns(".bun"))
            shutil.move(str(packages), staging / "packages")
            shutil.rmtree(build)
            (staging / "layout.json").write_text(json.dumps(layout))
            (staging / "provenance.json").write_text(json.dumps({
                "dependency_key": key, "store_sha256": store_digest(staging)}))
            staging.rename(store)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return store, False


def prepare(repo: Path) -> dict | None:
    """Turn expected preparation failures into the runner's unmeasured outcome."""
    try:
        return _prepare(repo, build=False)
    except RuntimeUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeUnavailable(f"Bun preparation failed: {type(exc).__name__}: {exc}") from exc


def configure(repo: Path) -> dict | None:
    """Explicit setup only: no network downloads or lifecycle scripts."""
    try:
        return _prepare(repo, build=True)
    except RuntimeUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeUnavailable(f"Bun configuration failed: {type(exc).__name__}: {exc}") from exc


def identity(repo: Path) -> dict | None:
    """Read-only cache admission: verify the same runtime that a task would use."""
    try:
        receipt = _prepare(repo, build=False, attach=False)
        return receipt["identity"] if receipt else None
    except RuntimeUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeUnavailable(f"Bun identity unavailable: {exc}") from exc


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def store_digest(store):
    """Verify trusted store bytes and link text without following package links."""
    digest = hashlib.sha256()
    for path in sorted(Path(store).rglob("*")):
        relative = path.relative_to(store).as_posix()
        if relative == "provenance.json":
            continue
        if path.is_symlink():
            payload = ("link:" + os.readlink(path)).encode()
        elif path.is_file():
            payload = ("file:" + str(stat.S_IMODE(path.stat().st_mode)) + ":" + file_digest(path)).encode()
        elif path.is_dir():
            payload = ("directory:" + str(stat.S_IMODE(path.stat().st_mode))).encode()
        else:
            raise RuntimeUnavailable("Unsupported file type in prepared Bun store")
        digest.update(relative.encode() + b"\0" + payload + b"\0")
    return digest.hexdigest()


def lookup_store(cache, key):
    store = cache.parent / "bun-dependencies" / key
    if not (store / "provenance.json").is_file():
        raise RuntimeUnavailable("Prepared Bun store missing; run tools/configure_bun_harness.py first")
    provenance = json.loads((store / "provenance.json").read_text())
    if provenance != {"dependency_key": key, "store_sha256": store_digest(store)}:
        raise RuntimeUnavailable("Prepared Bun store identity mismatch; re-run tools/configure_bun_harness.py in a fresh cache")
    return store, provenance["store_sha256"]


def _owned_state(repo: Path) -> Path:
    """Never follow candidate-controlled links when writing a runtime receipt."""
    state = repo / ".state"
    receipt = state / "bun-runtime.json"
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise RuntimeUnavailable("Bun state directory must belong to the candidate")
    if receipt.is_symlink() or (receipt.exists() and
                               (not receipt.is_file() or receipt.stat().st_nlink != 1)):
        raise RuntimeUnavailable("Bun state receipt must not link to another file")
    return state


def _prepare(repo: Path, *, build: bool, attach: bool = True) -> dict | None:
    owned = owned_source(repo)
    if owned is None:
        return None
    repo = repo.resolve()
    _owned_state(repo)
    spec, source, entry, external = owned
    if external:
        # **A declared external source is already installed; preparation is admitting it.**
        # Copying or linking into it would write to a tree the platform bound read-only, and
        # the offline store this function builds exists for candidates that carry their own
        # source and rely on a cache. This tree carries its own `node_modules`, so the receipt
        # names the paths the harness needs and nothing is touched.
        bun = Path(os.environ.get("HG_BUN_BINARY")
                   or os.environ.get("HG_AGENT_OPENCODE_BUN")
                   or shutil.which("bun") or "").resolve()
        if not bun.is_file():
            raise RuntimeUnavailable(
                "An external Bun source needs a Bun binary: set HG_BUN_BINARY or "
                "HG_AGENT_OPENCODE_BUN")
        version = _run([str(bun), "--version"], cwd=bun.parent,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"}).strip()
        binary_sha256 = file_digest(bun)
        key = "external-" + hashlib.sha256(str(source).encode()).hexdigest()[:16]
        identity = {"kind": "bun", "binary_sha256": binary_sha256, "version": version,
                    "dependency_key": key, "store_sha256": None, "declaration": spec,
                    "external_source": str(source), "system": platform.system(),
                    "arch": platform.machine(), "responses_overlay": None}
        return {"kind": "bun", "dependency_key": key, "bun_version": version,
                "identity": identity, "binary": str(bun), "execution_binary": str(bun),
                "source": str(source), "external": True, "offline": True,
                "installed": True, "cache_hit": True, "backend": "external-tree",
                "system": platform.system(), "arch": platform.machine(),
                "runtime_paths": [str(source), str(bun.parent)], "seconds": 0.0}
    started = time.monotonic()
    problems = validate_source(repo) if attach else []
    if problems:
        raise RuntimeUnavailable("; ".join(problems))
    bun, cache = runtime_settings()
    from eval.containers import responses_overlay
    transport = responses_overlay.selected(bun, repo)
    if cache.is_relative_to(repo) or repo.is_relative_to(cache):
        raise RuntimeUnavailable("Bun dependency cache must be outside candidate source")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": "/tmp",
           "BUN_INSTALL_CACHE_DIR": str(cache), "CI": "true"}
    version = _run([str(bun), f"--config={os.devnull}", "--version"], cwd=bun.parent, env=env).strip()
    binary_sha256 = file_digest(bun)
    key = dependency_identity(source, version, platform.system(), platform.machine(),
                              package=spec["package"], binary_sha256=binary_sha256)
    if not (source / "bun.lock").is_file():
        raise RuntimeUnavailable("Bun frozen bun.lock is required")
    hit = True
    if build:
        _, hit = _dependency_layout(source, bun, cache, key, env, package=spec["package"])
    store, store_sha256 = lookup_store(cache, key)
    # Recreate only link directories. Never trust a candidate-written cache stamp.
    if attach:
        attach_layout(source, store)
        for directory in _module_dirs(source):
            for link in directory.rglob("*"):
                if link.is_symlink() and not (link.resolve().is_relative_to(source)
                                             or link.resolve().is_relative_to(store)):
                    raise RuntimeUnavailable("Bun workspace link escapes candidate and prepared store")
    identity = {"kind": "bun", "binary_sha256": binary_sha256, "version": version,
                "dependency_key": key, "store_sha256": store_sha256, "declaration": spec,
                "system": platform.system(), "arch": platform.machine(),
                "responses_overlay": transport["identity"] if transport else None}
    receipt = {"kind": "bun", "dependency_key": key, "bun_version": version,
               "identity": identity, "binary": str(bun),
               "execution_binary": transport["launcher"] if transport else str(bun),
               "responses_overlay": transport,
               "system": platform.system(), "arch": platform.machine(),
               "source": str(source), "cache": str(cache), "offline": True,
               "lifecycle_scripts": False, "cache_hit": hit, "installed": not hit,
               "backend": "shared-third-party-store", "dependency_store": str(store),
               "seconds": round(time.monotonic() - started, 4),
               "runtime_paths": [str(bun.parent), str(store)] + ([transport["path"]] if transport else [])}
    if not attach:
        return receipt
    state = _owned_state(repo)
    state.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".dependencies-", dir=state)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(receipt, indent=2) + "\n")
        temporary.replace(state / "bun-runtime.json")
    finally:
        temporary.unlink(missing_ok=True)
    return receipt
