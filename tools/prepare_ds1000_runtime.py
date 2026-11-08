#!/usr/bin/env python3
"""Prepare/check the pinned DS-1000 CPU environment without platform dependencies.

Default is a read-only, offline managed-runtime check. --fetch explicitly fetches
SHA256/size-verified public wheels; --prepare uses verified cached wheels only.
Neither mode reads dataset rows, runs a scorer, or installs into platform Python.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "data/datasets/ds1000/cpu-wheels-cp312-linux-x86_64.json"


def _external(path, label):
    from harnessgrad.paths import main_checkout
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute external directory")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(f"{label} must not traverse symlinks")
    path = path.resolve()
    if path in {Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/usr"), Path("/usr/local"),
                Path("/etc"), Path("/opt"), Path.home().resolve()}:
        raise ValueError(f"{label} must be a dedicated private directory")
    protected = {Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"),
                 Path("/etc"), Path("/boot"), Path("/dev"), Path("/proc"), Path("/sys"),
                 Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    if any(path == prefix or path.is_relative_to(prefix) for prefix in protected):
        raise ValueError(f"{label} must be outside system and interpreter trees")
    for source in {ROOT.resolve(), main_checkout(ROOT).resolve()}:
        if path == source or path.is_relative_to(source):
            raise ValueError(f"{label} must be outside platform source trees")
    return path


def _native_store(path):
    root = _external(path, "runtime root")
    if any(root == hidden or root.is_relative_to(hidden) for hidden in (Path("/tmp"), Path("/var/tmp"))):
        raise ValueError("DS-1000 actor/verifier runtime roots must be outside /tmp and /var/tmp; native sandbox isolation hides those trees")
    return root


def _manifest():
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = document["wheels"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty or invalid CPU wheel manifest")
    names, filenames = set(), set()
    for entry in entries:
        name, filename = entry["name"], entry["filename"]
        url = urlsplit(entry["url"])
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or name in names or filename in filenames or Path(filename).name != filename
                or not filename.endswith(".whl") or ".." in filename
                or url.scheme != "https" or not url.hostname or url.username or url.password
                or type(entry["size"]) is not int or entry["size"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("invalid or duplicate CPU wheel manifest entry")
        names.add(name); filenames.add(filename)
    from harnessgrad.dataset_runtime import execution_descriptor
    pins = execution_descriptor("ds1000")["pins"]
    if {entry["name"]: entry["version"] for entry in entries} != pins:
        raise ValueError("CPU wheel manifest does not match the complete locked profile")
    if sum(entry["size"] for entry in entries) != document["total_downloaded_bytes"]:
        raise ValueError("CPU wheel manifest size inventory differs")
    return entries


def _verified(path, entry):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"]


def _wheel(entry, folder, *, fetch, timeout):
    """Publish only verified bytes; retain interrupted/rejected downloads as evidence."""
    target = folder / entry["filename"]
    if _verified(target, entry):
        return {"name": entry["name"], "reused": True, "downloaded_bytes": 0}
    if not fetch:
        raise ValueError(f"missing or invalid cached wheel: {target.name}; use --fetch explicitly")
    if target.exists() or target.is_symlink():
        target.rename(target.with_name(target.name + ".rejected-" + secrets.token_hex(6)))
    partial = target.with_name(target.name + ".part-" + secrets.token_hex(6))
    digest, size = hashlib.sha256(), 0
    try:
        with partial.open("xb") as output, urlopen(entry["url"], timeout=timeout) as source:
            while block := source.read(1024 * 1024):
                output.write(block)
                digest.update(block)
                size += len(block)
                if size > entry["size"]:
                    raise ValueError("wheel exceeds declared size")
        if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError("wheel size/SHA256 verification failed")
        partial.replace(target)
    except Exception as exc:
        raise ValueError(f"{target.name}: download refused ({type(exc).__name__}: {exc}); partial bytes retained at {partial}") from exc
    return {"name": entry["name"], "reused": False, "downloaded_bytes": size}


def _index(entries, folder):
    index = folder / "simple"
    if index.is_symlink():
        raise ValueError("local wheel index must not traverse a symlink")
    index.mkdir(exist_ok=True)
    for entry in entries:
        package = index / entry["name"]
        if package.is_symlink():
            raise ValueError("local wheel index must not traverse a symlink")
        package.mkdir(exist_ok=True)
        target = package / "index.html"
        if target.is_symlink():
            raise ValueError("local wheel index must not overwrite a symlink")
        href = (folder / entry["filename"]).as_uri() + "#sha256=" + entry["sha256"]
        target.write_text(f'<a href="{html.escape(href, quote=True)}">{html.escape(entry["filename"])}</a>\n', encoding="utf-8")
    return index


@contextmanager
def _offline_index(index, folder):
    # Canonical preparation gets a local index and no ambient alternate indexes,
    # downloads or named-index credentials. No new runtime-manager env allowance.
    replacements = {"UV_INDEX_URL": index.as_uri(), "UV_OFFLINE": "1",
                    "UV_PYTHON_DOWNLOADS": "never", "UV_CACHE_DIR": str(folder / "uv-cache")}
    remove = {"UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "UV_INDEX_STRATEGY",
              "UV_FIND_LINKS", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS"}
    remove.update(name for name in os.environ if re.fullmatch(r"UV_INDEX_[A-Z0-9_]+_(?:USERNAME|PASSWORD|TOKEN)", name))
    touched = remove | replacements.keys()
    original = {name: os.environ.get(name) for name in touched}
    try:
        for name in remove:
            os.environ.pop(name, None)
        os.environ.update(replacements)
        yield
    finally:
        for name, value in original.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value


def _python_target(python, timeout):
    proc = subprocess.run([python, "-I", "-S", "-B", "-c",
        "import json,os,platform,sys; print(json.dumps([sys.implementation.name,sys.version_info[:2],platform.system(),platform.machine(),os.confstr('CS_GNU_LIBC_VERSION')]))"],
        capture_output=True, text=True, timeout=timeout,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"})
    if proc.returncode:
        raise ValueError("this wheel profile requires CPython 3.12 Linux x86_64 with glibc >= 2.28")
    target = json.loads(proc.stdout)
    if (not isinstance(target, list) or len(target) != 5 or not isinstance(target[4], str)
            or target[:4] != ["cpython", [3, 12], "Linux", "x86_64"]):
        raise ValueError("this wheel profile requires a CPython 3.12 Linux x86_64 target interpreter")
    libc = target[4].split()
    if len(libc) != 2 or libc[0] != "glibc" or tuple(map(int, libc[1].split("."))) < (2, 28):
        raise ValueError("this exact CPU wheel profile requires glibc >= 2.28; musl/older glibc is unsupported")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime-root", required=True, help="private absolute managed-runtime store")
    ap.add_argument("--wheelhouse", help="private absolute cache (default: RUNTIME_ROOT-wheels)")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--fetch", action="store_true", help="explicitly download verified public CPU wheels, then prepare/check")
    action.add_argument("--prepare", action="store_true", help="prepare/check from verified cached wheels only; no network")
    action.add_argument("--check", action="store_true", help="read-only runtime check (the default)")
    ap.add_argument("--python", default=sys.executable, help="CPython 3.12 interpreter for explicit preparation")
    ap.add_argument("--timeout", type=int, default=600, help="network/preparation timeout in seconds (default 600)")
    args = ap.parse_args(argv)
    try:
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise ValueError("DS-1000 CPU wheels currently support Linux x86_64 only; other architectures are unvalidated")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        root = _native_store(args.runtime_root)
        folder = _external(args.wheelhouse or str(root) + "-wheels", "wheelhouse")
        from harnessgrad import dataset_runtime
        if not (args.fetch or args.prepare):
            status = dataset_runtime.check_execution("ds1000", root=root)
            print(json.dumps({"mode": "check", "downloaded_bytes": 0, **status}, sort_keys=True))
            return 0 if status["present"] else 2
        _python_target(args.python, args.timeout)
        entries = _manifest()
        folder.mkdir(parents=True, exist_ok=True)
        import fcntl
        lock_path = folder / ".prepare.lock"
        if lock_path.is_symlink() or (folder / "prepare-receipt.json").is_symlink():
            raise ValueError("wheelhouse control files must not be symlinks")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(f"DS-1000 CPU: {len(entries)} wheels, {sum(entry["size"] for entry in entries)} compressed bytes; "
                  + ("explicit fetch enabled" if args.fetch else "verified cached wheels only"), file=sys.stderr)
            downloads = []
            for entry in entries:
                row = _wheel(entry, folder, fetch=args.fetch, timeout=args.timeout)
                downloads.append(row)
                print(f"{entry["name"]}: " + ("verified cache" if row["reused"] else f"fetched {row["downloaded_bytes"]} bytes"), file=sys.stderr)
            index = _index(entries, folder)
            with _offline_index(index, folder):
                artifact = dataset_runtime.prepare_execution("ds1000", python=args.python, root=root, timeout_s=args.timeout)
                status = dataset_runtime.check_execution("ds1000", root=root)
            if not status["present"] or status["runtime"]["runtime_id"] != artifact["runtime_id"]:
                raise ValueError("prepared artifact failed canonical check")
            receipt = {"mode": "fetch" if args.fetch else "prepare", "present": True,
                       "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
                       "downloaded_bytes": sum(row["downloaded_bytes"] for row in downloads),
                       "wheels": downloads, "runtime": artifact,
                       "scope": "CPU software dependencies only; no released task or scorer executed"}
            (folder / "prepare-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(receipt, sort_keys=True))
            return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"present": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
