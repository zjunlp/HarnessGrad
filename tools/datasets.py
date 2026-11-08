#!/usr/bin/env python3
"""Declarative provisioning: what each dataset needs, and whether it is here.

The design this implements
--------------------------
A dataset's loader is the authority on what the dataset needs. So the needs are
expressed *in the loader*, as a `downloads()` function, and this tool only reads it:

    python3 tools/datasets.py --list      what every dataset asks for
    python3 tools/datasets.py --check     which of those are present locally
    python3 tools/datasets.py --fetch     download what is missing into the data root
    python3 tools/datasets.py --write     regenerate DOWNLOADS.md from the loaders

`--check` reports declared bytes separately from admitted provider and execution
dependency artifacts. Package metadata comes from the selected artifact Python;
adapters without a managed profile retain an explicit legacy platform-package
scope. These are read-only provisioning facts, not loader or model/service health.

**DOWNLOADS.md is generated and must never be hand-edited.** A second copy of a
provisioning list in a document forks, and the fork shows up as "someone followed the
docs and got different bytes" -- which is why the renderer is this tool and the
authority is the loader.

The split the whole feature rests on: **data lands in the adapter's configured
data root outside the checkout; code -- how to load and how to score -- stays in git.**
A dataset whose `downloads()` names a `dst` inside the platform
tree is refused by `tools/dataset_lint.py` for exactly that reason.

A version must be pinned: a release tag, a commit sha or an exact package version. A
branch name is not a version and is refused by the lint; `--check` cannot verify a
moving target even if one were allowed.

`--fetch` (added with the QA batch)
-----------------------------------
`--check` says what is missing; `--fetch` is the other half, and it lives here rather
than in a shell script so that the same `downloads()` entries are the only list. It
understands the four artifact kinds the loaders use:

    http        GET `url` (redirects followed) into `dst`
    hf-dataset  GET `https://<endpoint>/datasets/<repo>/resolve/<revision>/<file>`
    hf-file     GET one pinned repository file into the file-shaped `dst`
    pip         never fetched here -- it is an install, and `--check` reports it

`--file` narrows an entry to a subset of its declared files. The Hugging Face
endpoint defaults to `https://hf-mirror.com`; `HG_HF_ENDPOINT` overrides it.
`HG_GH_RAW_MIRROR` optionally prefixes only raw.githubusercontent.com URLs; the
fetch output records its use. Declared SHA256 checksums are verified, and failed
transfers remove partial files and newly created empty directories.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "DOWNLOADS.md"
SCHEMA_PATH = ROOT / "data" / "dataset.schema.json"

#: The pinned revision remains unchanged when the endpoint is overridden.
DEFAULT_HF_ENDPOINT = os.environ.get("HG_HF_ENDPOINT", "https://hf-mirror.com")

#: Only this host is eligible for the optional raw URL rewrite.
GH_RAW_HOST = "raw.githubusercontent.com"

#: Optional GitHub raw mirror prefix; unset leaves declared URLs unchanged.
GH_RAW_MIRROR_ENV = "HG_GH_RAW_MIRROR"

sys.path.insert(0, str(ROOT))
import data.scaffolds.registry as registry  # noqa: E402
from harnessgrad.paths import env_name  # noqa: E402
from harnessgrad.paths import DataRootNotConfigured, scorer_paths  # noqa: E402
from tools.check_dataset_blockers import published_declarations  # noqa: E402
from harnessgrad import dataset_runtime  # noqa: E402


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _source_downloads(path: Path, *, resolve_root: bool = True):
    """Load downloads() and its source dependencies, excluding eager task-pool code.

    This is only a catalog fallback for trusted adapter code that cannot import on an
    unprovisioned machine. It is not a sandbox or a substitute for runtime imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    providers = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.asname or alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
        else:
            continue
        providers.update((name, node) for name in names)
    if "downloads" not in providers:
        return None
    pending = ["downloads", "DATASET_ROOT", "ROOT"]
    selected = set()
    while pending:
        node = providers.get(pending.pop())
        if node is None or node in selected:
            continue
        selected.add(node)
        pending.extend(child.id for child in ast.walk(node)
                       if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load))
    body = [node for node in tree.body if node in selected
            or isinstance(node, ast.ImportFrom) and node.module == "__future__"]
    namespace = {"__file__": str(path), "__name__": registry.module_name(path.stem),
                 "__package__": "data.datasets"}
    code = compile(ast.Module(body=body, type_ignores=[]), str(path), "exec")
    if resolve_root:
        exec(code, namespace)
    else:
        # A private source namespace, never a cached runtime adapter. Catalogs
        # need declarations without discovering host storage or loading a pool.
        with scorer_paths():
            exec(code, namespace)
    return namespace.get("downloads")


def _execution_runtime_name(name: str) -> str | None:
    """Find a shared execution profile without importing the adapter."""
    declared = registry.execution_runtime_name(name)
    if declared is not None:
        return declared
    # Leave validation to check_execution(), which reports malformed declarations
    # as an invalid component instead of crashing the whole check command.
    source = dataset_runtime.ROOT / "data" / "datasets" / name / "execution-runtime.json"
    return name if source.is_file() else None


def _download_adapter(name: str):
    """Read provisioning metadata even before optional runtime/data installation."""
    if (dataset_runtime.descriptor(name) is not None
            or _execution_runtime_name(name) is not None):
        fn = _source_downloads(ROOT / "data" / "datasets" / f"{name}.py")
        values = {key: fn.__globals__[key] for key in ("DATASET_ROOT", "ROOT")
                  if fn and key in fn.__globals__}
        return SimpleNamespace(downloads=fn, **values)
    try:
        return importlib.import_module(registry.module_name(name))
    except Exception as exc:
        try:
            fn = _source_downloads(ROOT / "data" / "datasets" / f"{name}.py")
        except Exception as fallback_exc:
            raise RuntimeError(f"Cannot read {name} downloads(): adapter import failed "
                               f"({exc}); source declaration also failed") from fallback_exc
        values = {key: fn.__globals__[key] for key in ("DATASET_ROOT", "ROOT")
                  if fn and key in fn.__globals__}
        return SimpleNamespace(downloads=fn, **values,
                               _adapter_import_error=f"{type(exc).__name__}: {exc}")


def datasets_with_downloads(*, source_fallback: bool = False) -> list[tuple[str, list[dict]]]:
    """Read catalog declarations even when an adapter's task pool is unavailable.

    Callers can retain registry-only discovery with source_fallback=False.
    """
    out = []
    names = published_declarations(ROOT) if source_fallback else registry.published()
    for name in names:
        try:
            fn = (_source_downloads(ROOT / "data/datasets" / f"{name}.py", resolve_root=False)
                  if source_fallback or dataset_runtime.descriptor(name) is not None
                  else getattr(importlib.import_module(registry.module_name(name)), "downloads", None))
        except Exception as exc:
            raise RuntimeError(f"Cannot read {name} downloads() source declaration") from exc
        if callable(fn):
            out.append((name, list(fn() or [])))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_root(module, *, include_status: bool = True) -> Path:
    """Where this dataset's bytes live, for resolving a relative `dst`.

    A relative `dst` is relative to the dataset root, not to the platform -- that is
    the split the whole feature rests on. The root is read from the loader, in the same
    order the rest of the tooling does: an explicit `provision_status()['root']`, then a
    `DATASET_ROOT`/`ROOT` attribute, and only then the platform directory (which is
    right for a dataset whose content really is committed).
    """
    status = getattr(module, "provision_status", None)
    if include_status and callable(status):
        try:
            root = status().get("root")
            if root:
                return Path(root)
        except Exception:  # noqa: BLE001 - a broken status is reported elsewhere
            pass
    for attribute in ("DATASET_ROOT", "ROOT"):
        value = getattr(module, attribute, None)
        if value:
            return Path(value)
    return ROOT


def _pip_distribution(entry: dict) -> tuple[str | None, str]:
    """Accept only a distribution name or an exact pin matching the version field."""
    package = entry.get("package")
    if not isinstance(package, str):
        return None, "unsupported pip package declaration: expected a distribution name or distribution==version"
    name = package
    if "==" in package:
        name, pin = package.split("==", 1)
        if pin != str(entry.get("version")):
            return None, f"package requirement {package!r} conflicts with declared version {entry.get('version')!r}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", pin):
            return None, f"unsupported exact pip version in {package!r}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        return None, f"unsupported pip package declaration {package!r}: expected a distribution name or distribution==version"
    return name, ""


def check_entry(entry: dict, base: Path) -> dict:
    """Is one download present? Never raises: a provisioning tool that dies on the first
    missing byte cannot report the other nine."""
    kind = entry.get("kind")
    result = {"name": entry.get("name"), "kind": kind, "version": entry.get("version"),
              "present": False, "detail": ""}
    if kind == "authored":
        from data.scaffolds.authored import download_entry
        declared = Path(str(entry.get("path", "")))
        if declared.is_absolute() or ".." in declared.parts or declared.parts[:2] != ("data", "authored") or len(declared.parts) != 3:
            result.update(status="invalid", detail="unsafe or missing authored fixture path")
            return result
        current = download_entry(declared.name, authored_root=ROOT / "data/authored")
        state = current["availability"]
        if state != "ready":
            result.update(status=state, detail=current["detail"])
            return result
        if entry.get("sha256") and entry["sha256"] != current["sha256"]:
            result.update(status="invalid", detail="authored bundle checksum changed")
            return result
        result.update(present=True, status="ready", detail=str(ROOT / declared) + " (authored fixture verified)")
        return result
    if kind == "pip":
        result["scope"] = "legacy-platform-package-metadata"
        package, error = _pip_distribution(entry)
        if error:
            result.update(detail=error, status="invalid")
            return result
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["detail"] = f"{package} is not installed"
            return result
        if installed == str(entry.get("version")):
            result.update(present=True, detail=f"{package}=={installed}")
        else:
            result["detail"] = (f"{package}=={installed} is installed, but the dataset "
                                f"pins {entry.get('version')}")
        return result

    dst = entry.get("dst")
    if not dst:
        result["detail"] = "no `dst` to check"
        return result
    path = Path(str(dst))
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        result["detail"] = f"missing: {path}"
        return result
    if kind == "generated" and not path.is_file():
        result["detail"] = f"not a generated data file: {path}"
        return result
    expected = entry.get("sha256")
    if expected and path.is_file():
        actual = _sha256(path)
        if actual != expected:
            result["detail"] = f"{path} exists but sha256 {actual[:12]}... != {expected[:12]}..."
            return result
        result.update(present=True, detail=f"{path} (sha256 ok)")
        return result
    if isinstance(expected, dict) and path.is_dir():
        bad = [tail for tail, want in expected.items()
               if not (path / tail).is_file() or _sha256(path / tail) != want]
        if bad:
            result["detail"] = f"{path} exists but {len(bad)} file(s) are missing or " \
                               f"changed: {bad[:3]}"
            return result
        result.update(present=True, detail=f"{path} ({len(expected)} file(s), sha256 ok)")
        return result
    result.update(present=True, detail=f"{path} exists")
    return result


def check_dataset(name: str, downloads: list[dict]) -> dict:
    provider = dataset_runtime.descriptor(name) is not None
    execution = _execution_runtime_name(name)
    if provider or execution is not None:
        return _check_component_downloads(name, downloads, provider=provider, execution=execution)
    module = _download_adapter(name)
    base = dataset_root(module, include_status=False)
    entries = [check_entry(entry, base) for entry in downloads]
    data = [entry for entry in entries if entry["kind"] != "pip"]
    # An adapter's status hook can include model/judge configuration or contact a
    # service. Download checking never invokes it or calls its result data readiness.
    status = {"scope": "legacy-platform-declared-downloads",
              "detail": "Declared bytes and legacy platform package metadata only; "
                        "no managed Python profile is declared; container/native, loader "
                        "and model health not checked"}
    if hasattr(module, "_adapter_import_error"):
        status["adapter_import_error"] = module._adapter_import_error
    return {"dataset": name, "root": str(base), "entries": entries,
            "data_present": bool(data) and all(entry["present"] for entry in data),
            "loader_ready": None, "status": status,
            "present": bool(entries) and all(entry["present"] for entry in entries)}


def _runtime_package_versions(runtime_status: dict, packages: list[str]) -> tuple[dict, str]:
    """Read metadata only in the admitted interpreter, without caller credentials."""
    if not packages or not runtime_status["present"]:
        return {}, ""
    code = ("import importlib.metadata as m,json,sys\nvalues={}\n"
            "for name in json.loads(sys.argv[1]):\n"
            " try: values[name]=m.version(name)\n"
            " except m.PackageNotFoundError: values[name]=None\n"
            "print(json.dumps(values))")
    try:
        process = subprocess.run([runtime_status["runtime"]["python"], "-B", "-I", "-c", code,
                                  json.dumps(packages)], capture_output=True, text=True,
                                 errors="replace", timeout=30,
                                 env={"PATH": os.defpath, "LANG": "C.UTF-8",
                                      "PYTHONDONTWRITEBYTECODE": "1"})
        if process.returncode:
            return {}, process.stderr.strip() or "runtime package check failed"
        versions = json.loads(process.stdout)
        if (not isinstance(versions, dict) or set(versions) != set(packages)
                or any(value is not None and not isinstance(value, str) for value in versions.values())):
            raise ValueError("runtime package check returned an invalid package map")
        return versions, ""
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {}, str(exc)


def _check_component_downloads(name: str, downloads: list[dict], *, provider: bool,
                               execution: str | None) -> dict:
    """Keep downloaded bytes, provider and service/verifier artifacts separate."""
    module = _download_adapter(name)
    base = dataset_root(module, include_status=False)
    components = {}
    if provider:
        components["runtime"] = dataset_runtime.check(name)
    if execution is not None:
        components["execution_runtime"] = dataset_runtime.check_execution(execution)
    # Existing provider requirements describe its loading environment; execution-
    # only adapters describe the service/verifier environment instead.
    runtime_status = components["runtime" if provider else "execution_runtime"]
    packages = [_pip_distribution(entry)[0] for entry in downloads
                if entry.get("kind") == "pip" and not _pip_distribution(entry)[1]]
    versions, package_error = _runtime_package_versions(runtime_status, packages)
    entries = []
    for entry in downloads:
        if entry.get("kind") != "pip":
            entries.append(check_entry(entry, base))
            continue
        package, requirement_error = _pip_distribution(entry)
        expected = str(entry.get("version"))
        installed = versions.get(package)
        scope = runtime_status["scope"]
        detail = (f"{package}=={installed} in {scope}" if installed == expected else
                  f"{package}: expected {expected}, found {installed or 'not installed'} in {scope}")
        if requirement_error:
            detail = requirement_error
        elif not runtime_status["present"]:
            detail = runtime_status["detail"]
        elif package_error:
            detail = package_error
        checked = {"name": entry.get("name"), "kind": "pip", "version": entry.get("version"),
                   "scope": scope,
                   "present": bool(runtime_status["present"] and not package_error
                                   and not requirement_error and installed == expected),
                   "detail": detail}
        if requirement_error:
            checked["status"] = "invalid"
        entries.append(checked)
    data = [entry for entry in entries if entry["kind"] != "pip"]
    return {"dataset": name, "root": str(base), "entries": entries, **components,
            "data_present": bool(data) and all(entry["present"] for entry in data),
            "loader_ready": None,
            "status": {"scope": "declared-downloads-and-component-runtimes",
                       "detail": "Declared bytes and admitted dependency artifacts only; "
                                 "loader readiness and model/service health not checked"},
            "present": bool(entries) and all(state["present"] for state in components.values())
                       and all(entry["present"] for entry in entries)}


def _check_provider_downloads(name: str, downloads: list[dict]) -> dict:
    """Compatibility helper for callers checking opted-in provider declarations."""
    return _check_component_downloads(name, downloads, provider=True,
                                      execution=_execution_runtime_name(name))


# ------------------------------------------------------------------- fetching ---


def entry_url(entry: dict, endpoint: str = DEFAULT_HF_ENDPOINT) -> str | None:
    """The URL to GET for one entry, or None when there is nothing to GET.

    `hf-dataset` entries carry `repo` + `file` + `version`, where `version` **is** the
    revision sha -- the same value the lint pins. Spelling the URL here (rather than in
    every loader) is what keeps the pin and the fetch from drifting apart: a loader that
    hand-wrote a `main` URL would look pinned in `--list` and move under `--fetch`.
    """
    kind = entry.get("kind")
    if kind == "http":
        return entry.get("url")
    if kind in ("hf-dataset", "hf-file"):
        repo, revision = entry.get("repo"), entry.get("version")
        if not (repo and revision):
            return None
        # One URL per file, built per file in `fetch_entry`; this is the shape check.
        # `hf-file` differs only in carrying one `file` and a file-shaped `dst`: the
        # documented contract already had it (`docs/dataset-onboarding.md`), the transport
        # did not, so a declared `hf-file` used to report "no fetch transport" while
        # `DOWNLOADS.md` advertised `--fetch` for it.
        return f"{endpoint}/datasets/{repo}/resolve/{revision}"
    return None


def selected_files(entry: dict, only: list[str] | None) -> list[str]:
    """Which files of a multi-file entry to fetch. `only` narrows; empty means all."""
    declared = [f for f in (entry.get("files") or []) if isinstance(f, str)]
    if only:
        wanted = [f for f in only if not declared or f in declared]
        return wanted
    return declared


def gh_raw_mirror() -> str:
    """The configured GitHub-raw mirror prefix, or `""` (off, the default).

    Read at call time rather than at import: the variable is an operator's answer to "this
    host cannot reach that one", so a wrapper, a test or a second fetch in the same process
    must be able to change it. A trailing slash is dropped so the joined URL has exactly
    one, whichever spelling the operator used.
    """
    return os.environ.get(GH_RAW_MIRROR_ENV, "").strip().rstrip("/")


def gh_raw_url(url: str, mirror: str | None = None) -> str:
    """`url` fetched through the GitHub-raw mirror, or unchanged when it does not apply.

    The `netloc` test is the whole safety property: `hf-mirror.com`, `github.com`,
    `codeload.github.com`, a release asset or any other host is returned byte-identical, so
    a mirror can never be the reason a declared checksum fails.
    """
    prefix = gh_raw_mirror() if mirror is None else mirror.strip().rstrip("/")
    if not prefix:
        return url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != GH_RAW_HOST:
        return url
    return f"{prefix}/{url}"


def _absent_parents(target: Path) -> list[Path]:
    """The directories `target.parent.mkdir(parents=True)` would create, deepest first.

    Read *before* creating them: this is the set a failed transfer may remove, and a
    directory that already existed is deliberately not in it -- cleanup must never delete
    something this call did not create.
    """
    absent, parent = [], target.parent
    while not parent.exists() and parent != parent.parent:
        absent.append(parent)
        parent = parent.parent
    return absent


def _remove_created(dirs: list[Path]) -> None:
    """Remove the directories a failed call created, bottom-up, only while empty.

    `rmdir` refuses a non-empty directory; stopping at the first refusal is what keeps a
    tree that another entry already wrote into (or that the operator filled) intact, and a
    pre-existing directory is never in `dirs` in the first place.
    """
    for directory in dirs:
        try:
            directory.rmdir()
        except OSError:
            break


def _download(url: str, target: Path, *, timeout: int = 600) -> None:
    """GET `url` into `target`, atomically enough that a failure leaves no half file.

    `curl` when it is available (it follows the mirror's redirects and reports a real
    exit code on a truncated transfer); `urllib` otherwise, so the tool still works on a
    host without curl.

    A failure also removes the directories this call created, so a fetch that never
    transferred a byte leaves no empty tree for a dataset's "already prepared?" guard to
    read as prepared.
    """
    created = _absent_parents(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        if shutil.which("curl"):
            proc = subprocess.run(
                ["curl", "-sSL", "--fail", "--retry", "2", "--max-time", str(timeout),
                 "-o", str(tmp), url],
                capture_output=True, text=True, errors="replace")
            if proc.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"curl exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        else:
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response, \
                        tmp.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
        tmp.replace(target)
    except BaseException:
        _remove_created(created)
        raise


def fetch_entry(entry: dict, base: Path, *, only: list[str] | None = None,
                endpoint: str = DEFAULT_HF_ENDPOINT,
                force: bool = False) -> tuple[bool, str]:
    """Fetch one entry. Returns `(ok, message)`; never raises for a missing transport.

    The order is deliberate: a `pip` entry is reported, not installed, because an
    install has side effects a data fetch does not and `--fetch` is about bytes under
    the dataset root. A `sha256` mismatch removes the file it just wrote -- otherwise
    the next `--check` would call a corrupted download provisioned, which is exactly
    the failure the checksum exists to catch.
    """
    kind = entry.get("kind")
    name = entry.get("name")
    if kind == "authored":
        checked = check_entry(entry, base)
        return checked["present"], "[authored] " + checked["detail"] + " (read only; no fetch)"
    if kind == "pip":
        return True, f"[pip] {entry.get('package')}=={entry.get('version')} (install separately)"
    if kind == "generated":
        return True, f"[generated] {name}: {entry['prepare']}"
    if kind not in ("http", "hf-dataset", "hf-file"):
        return False, f"[{kind}] {name}: no fetch transport"

    dst = entry.get("dst")
    if not dst:
        return False, f"[{kind}] {name}: no `dst`"
    target = Path(str(dst))
    if not target.is_absolute():
        target = base / target

    url = entry_url(entry, endpoint)
    if url is None:
        return False, f"[{kind}] {name}: cannot build a URL (needs repo/file/version)"
    # `hf-file` names ONE file and `dst` is that file; `hf-dataset` names a tree and `dst`
    # is its directory. The distinction is why the loop below cannot share one `out`.
    single_file = kind == "hf-file"
    if single_file:
        declared_file = str(entry.get("file") or "")
        if not declared_file:
            return False, f"[{kind}] {name}: no `file`"
        if only and declared_file not in only:
            return False, (f"[{kind}] {name}: --file names {only}, "
                           f"this entry declares {declared_file}")
        files = [declared_file]
    else:
        files = selected_files(entry, only)
        if not files:
            files = [""]

    mirror = gh_raw_mirror()
    mirrored = False
    written = []
    for relative in files:
        if kind in ("hf-dataset", "hf-file"):
            full = f"{endpoint}{_hf_tail(entry, relative, endpoint)}"
        else:
            full = url if not relative else f"{url.rstrip('/')}/{relative}"
        out = target if (single_file or not relative) else target / relative
        # The rewrite happens on the finished URL, once per file, so the mirror and the
        # `--fetch` message cannot disagree about what was actually requested.
        fetch_url = gh_raw_url(full, mirror)
        mirrored = mirrored or fetch_url != full
        via = f" (via mirror {mirror})" if fetch_url != full else ""
        # `sha256` is a string for a one-file entry and a `{tail: digest}` map for a
        # multi-file one; the map is what lets a single `--check` verify every file
        # rather than just the first.
        checksums = entry.get("sha256")
        expected = (checksums.get(relative) if isinstance(checksums, dict)
                    else checksums)
        if out.is_file() and not force and not expected:
            written.append(f"{relative or target.name} (present)")
            continue
        if out.is_file() and not force and expected and _sha256(out) == expected:
            written.append(f"{relative or out.name} (present, sha256 ok)")
            continue
        created = _absent_parents(out)
        try:
            _download(fetch_url, out)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, f"[{kind}] {name}: {relative or out.name}: {exc}{via}"
        if expected:
            actual = _sha256(out)
            if actual != expected:
                out.unlink(missing_ok=True)
                # Same rule as a failed transfer: a file the checksum rejected must not
                # leave an empty tree that looks provisioned.
                _remove_created(created)
                return False, (f"[{kind}] {name}: {relative or out.name}: sha256 "
                               f"{actual[:12]}... != {expected[:12]}...; removed the "
                               f"download{via}")
            written.append(f"{relative or out.name} (sha256 ok)")
        else:
            written.append(f"{relative or out.name} "
                           f"({out.stat().st_size / 1e6:.1f} MB, sha256 "
                           f"{_sha256(out)[:12]}...)")

    extract_to = entry.get("extract_to")
    if extract_to:
        dest = Path(str(extract_to))
        if not dest.is_absolute():
            dest = base / dest
        dest.mkdir(parents=True, exist_ok=True)
        for relative in files:
            src = target if not relative else target / relative
            if src.is_file():
                shutil.unpack_archive(str(src), str(dest))
        written.append(f"extracted -> {dest}")
    # The mirror is part of provenance, like the sha256: a reader who has to re-fetch has
    # to know which URL these bytes came from, and `--fetch` prints this line verbatim.
    return True, (f"[{kind}] {name}: " + "; ".join(written)
                  + (f" (via mirror {mirror})" if mirrored else ""))


def _hf_tail(entry: dict, file: str, endpoint: str) -> str:
    """`/datasets/<repo>/resolve/<revision>/<file>` -- the path after the endpoint."""
    return f"/datasets/{entry.get('repo')}/resolve/{entry.get('version')}/{file}"


# These are document layout limits, independent of download selection or verification.
INLINE_FILE_LIMIT = 8
INLINE_FILE_CHARS = 600


def _short_file_list(files: list[str]) -> bool:
    return len(files) <= INLINE_FILE_LIMIT and sum(map(len, files)) <= INLINE_FILE_CHARS


def _logical_data_root(dataset: str) -> tuple[str, str | None]:
    """Describe the adapter's declared layout without resolving machine settings.

    Read the shared resolver call rather than assuming a directory from the adapter
    name: two adapters may share storage, and some use version subdirectories.
    """
    source = ROOT / "data" / "datasets" / f"{dataset}.py"
    if source.is_file():
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            targets = (node.targets if isinstance(node, ast.Assign) else
                       [node.target] if isinstance(node, ast.AnnAssign) else [])
            if not any(isinstance(target, ast.Name) and target.id in {"DATASET_ROOT", "ROOT"}
                       for target in targets):
                continue
            call = node.value
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "dataset_root" and call.args):
                continue
            try:
                directory = ast.literal_eval(call.args[0])
            except (ValueError, TypeError):
                continue
            if not isinstance(directory, str):
                continue
            path = PurePosixPath(directory)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                continue
            return f"<HG_DATA_ROOT>/{path}", env_name(directory)
    return "<DATASET_ROOT>", None


def _logical_destination(destination: str, root: str) -> str:
    path = PurePosixPath(destination)
    if path.is_absolute():
        return "<ABSOLUTE_DESTINATION>"
    return str(PurePosixPath(root) / path)


def _render_inventory(entry: dict, dataset: str, root: str) -> list[str]:
    """Summarize large inventories without changing the provisioning declaration."""
    lines = []
    files = sorted(entry.get("files") or [])
    checksums = entry.get("sha256")
    summarize_files = bool(files) and not _short_file_list(files)
    summarize_checksums = isinstance(checksums, dict) and not _short_file_list(list(checksums))
    if files:
        if summarize_files:
            examples = [path if len(path) <= 120 else path[:57] + "..." + path[-60:]
                        for path in files[:3]]
            preview = ", ".join(f"`{path}`" for path in examples)
            label = "file" if len(files) == 1 else "files"
            lines.append(f"- **Files:** {len(files):,} {label} (examples: {preview})")
        else:
            lines.append("- **Files:** " + ", ".join(f"`{path}`" for path in files))
    if entry.get("dst"):
        lines.append(f"- **Destination:** `{_logical_destination(entry['dst'], root)}`")
    if entry.get("extract_to"):
        lines.append(f"- **Extract to:** `{_logical_destination(entry['extract_to'], root)}`")
    absolute_destination = any(PurePosixPath(entry[key]).is_absolute()
                               for key in ("dst", "extract_to") if entry.get(key))
    if entry.get("manifest"):
        target = quote(entry["manifest"], safe="/")
        lines.append(f"- **Manifest:** [Full file list and checksums]({target})")
    if absolute_destination or (not entry.get("manifest") and (summarize_files or summarize_checksums)):
        lines.append("- **Full declaration:** "
                     f"`python3 tools/datasets.py --list --json --dataset {dataset}`")
    if isinstance(checksums, dict) and checksums:
        if summarize_checksums:
            label = "checksum" if len(checksums) == 1 else "checksums"
            lines.append(f"- **SHA256:** {len(checksums):,} per-file {label} (see full inventory above)")
        else:
            lines.extend(["- **SHA256:** Per-file checksums:", "",
                          "| File | SHA256 |", "| --- | --- |"])
            for path, digest in sorted(checksums.items()):
                cell = path.replace("|", r"\|")
                lines.append(f"| `{cell}` | `{digest}` |")
    elif checksums:
        lines.append(f"- **SHA256:** `{checksums}`")
    return lines


def render_index(rows: list[dict]) -> str:
    """DOWNLOADS.md, from the loaders. See the module docstring for why it is generated."""
    lines = [
        "<!-- GENERATED BY tools/datasets.py --write. DO NOT EDIT BY HAND. -->",
        "",
        "# Dataset downloads",
        "",
        "This human-readable index is generated from each dataset adapter's `downloads()`",
        "declaration by `python3 tools/datasets.py --write`. Destinations show portable logical",
        "paths under `<HG_DATA_ROOT>`, using each adapter's declared directory and download `dst`.",
        "Set `HG_DATA_ROOT` in `.env` or the environment before accessing external data; there is no implicit directory.",
        "per-dataset overrides take precedence. See [data directory configuration](README.md#dataset-storage).",
        "Each override replaces that dataset's entire data root; the relative download path stays the same.",
        "`<DATASET_ROOT>` denotes an adapter-defined root; `<ABSOLUTE_DESTINATION>` denotes an explicit",
        "absolute destination available in the full declaration. Manifest links are repository-relative.",
        "",
        "Large file lists and checksum maps are summarized; follow the linked manifests",
        "or run `python3 tools/datasets.py --list --json --dataset <name>` for full declarations.",
        "",
        "`python3 tools/datasets.py --check` reports the current server's resolved absolute roots",
        "and which resources are present locally.",
        "",
        f"With `{GH_RAW_MIRROR_ENV}` unset, `--fetch` uses declared raw GitHub URLs.",
        "Set it to a mirror prefix to fetch `raw.githubusercontent.com` URLs through",
        "`<mirror>/https://raw.githubusercontent.com/<path>`; other hosts are unchanged.",
        "The fetch output identifies any mirror used. Declared checksums are verified;",
        "failed transfers remove partial files and newly created empty directories.",
        "",
    ]
    for row in rows:
        name = row["dataset"]
        lines.append(f"## `{name}`")
        lines.append("")
        if not row["entries"]:
            lines.append("_Declares no downloads._")
            lines.append("")
            continue
        root, override = _logical_data_root(name)
        lines.append(f"- **Data root:** `{root}`")
        if override:
            lines.append(f"- **Root override:** `{override}`")
        lines.append("")
        for entry in row["entries"]:
            size = entry.get("size") or "size not stated"
            lines.append(f"### {entry.get('name')}")
            lines.append("")
            lines.append(f"- **Type:** `{entry['kind']}`")
            lines.append(f"- **Version (pinned):** `{entry.get('version')}`")
            lines.append(f"- **Size:** {size}")
            if entry.get("url"):
                lines.append(f"- **URL:** {entry['url']}")
            if entry.get("repo"):
                lines.append(f"- **Repo:** `{entry['repo']}`")
            if entry.get("file"):
                lines.append(f"- **File:** `{entry['file']}`")
            if entry.get("package"):
                lines.append(f"- **Package:** `{entry['package']}=={entry.get('version')}`")
            lines.extend(_render_inventory(entry, name, root))
            lines.append("")
            lines.append(f"> {entry.get('note', '').strip()}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="print each dataset's needs")
    mode.add_argument("--check", action="store_true",
                      help="report which datasets are present locally")
    mode.add_argument("--fetch", action="store_true",
                      help="download what is missing into each dataset's own root")
    mode.add_argument("--write", action="store_true",
                      help="regenerate DOWNLOADS.md from the loaders")
    parser.add_argument("--dataset", action="append", help="only this dataset")
    parser.add_argument("--file", action="append",
                        help="with --fetch, only this file of a multi-file entry; "
                             "repeatable")
    parser.add_argument("--force", action="store_true",
                        help="with --fetch, re-download files that are already present")
    parser.add_argument("--endpoint", default=DEFAULT_HF_ENDPOINT,
                        help=f"hf-dataset endpoint (default {DEFAULT_HF_ENDPOINT})")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = datasets_with_downloads(source_fallback=True)
    if args.dataset:
        rows = [r for r in rows if r[0] in set(args.dataset)]

    if args.write:
        INDEX.write_text(render_index([{"dataset": n, "entries": e} for n, e in rows]))
        print(f"wrote {INDEX.relative_to(ROOT)} ({len(rows)} dataset(s))")
        return 0

    if args.fetch:
        failed = 0
        for name, entries in rows:
            module = _download_adapter(name)
            base = dataset_root(module)
            execution = _execution_runtime_name(name)
            print(f"== {name}  (root {base})")
            for entry in entries:
                if execution is not None and entry.get("kind") == "pip":
                    print(f"       [pip] {entry['name']} (managed dataset runtime)")
                    continue
                ok, message = fetch_entry(entry, base, only=args.file,
                                          endpoint=args.endpoint, force=args.force)
                print(f"  {'ok  ' if ok else 'FAIL'} {message}")
                if not ok:
                    failed += 1
            if dataset_runtime.descriptor(name) is not None:
                print(f"  next: python tools/prepare_dataset.py --dataset {name}")
            if execution is not None:
                print(f"  next: python tools/prepare_dataset.py --dataset {execution} --runtime execution")
        return 1 if failed else 0

    if args.check:
        checked = [check_dataset(name, entries) for name, entries in rows]
        if args.json:
            print(json.dumps({"schema_version": _schema()["schema_version"],
                              "datasets": checked}, indent=1, sort_keys=True))
            return 1 if any(not row["present"] for row in checked) else 0
        failed = 0
        for row in checked:
            mark = "ok  " if row["present"] else "MISS"
            if not row["present"]:
                failed += 1
            status = row["status"]
            extra = ""
            if status and status.get("tasks") is not None:
                extra = f" ({status.get('tasks')} task(s) loadable)"
            print(f"{mark} {row['dataset']}{extra} (root: {row['root']})")
            print(f"  scope: {status['scope']}; loader readiness: not checked")
            for component in ("runtime", "execution_runtime"):
                if component in row:
                    state = row[component]
                    print(f"  {component}: {state['status']} ({state['scope']}) -- {state['detail']}")
            for entry in row["entries"]:
                flag = "  ok " if entry["present"] else "  -- "
                print(f"{flag} [{entry['kind']}] {entry['name']} @ {entry['version']}"
                      f" -- {entry['detail']}")
        return 1 if failed else 0

    # Default and --list: what each dataset asks for.
    # Report omitted source declarations without importing runtime adapters or
    # mixing diagnostics into the machine-readable catalog payload.
    if args.json:
        # Shape kept as a list of rows: it is a machine interface, and the diagnostics below
        # go to stderr rather than into a payload a caller already parses.
        print(json.dumps([{"dataset": n, "downloads": e} for n, e in rows],
                         indent=1, sort_keys=True))
        _report_unreadable()
        return 0
    for name, entries in rows:
        print(f"{name}: {len(entries)} item(s)")
        for entry in entries:
            print(f"  [{entry['kind']}] {entry['name']} @ {entry['version']} "
                  f"({entry.get('size', '?')})")
            if entry.get("url"):
                print(f"      {entry['url']}")
            print(f"      {entry.get('note', '').strip()}")
    if not rows:
        print("no published dataset declares downloads()")
    _report_unreadable()
    return 0


def _report_unreadable() -> None:
    """Name the adapters the catalogue had to drop, and why."""
    broken = registry.broken_adapters()
    if not broken:
        return
    print(f"\n{len(broken)} adapter declaration(s) could not be read:",
          file=sys.stderr)
    for name, reason in sorted(broken.items()):
        print(f"  {name}: {reason[:160]}", file=sys.stderr)
    print("Repair the named source declaration; runtime and storage errors are "
          "reported when loading the dataset.", file=sys.stderr)


def main(argv=None) -> int:
    try:
        return _main(argv)
    except DataRootNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
