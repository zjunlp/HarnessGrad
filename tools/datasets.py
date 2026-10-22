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

**DOWNLOADS.md is generated and must never be hand-edited.** A second copy of a
provisioning list in a document forks, and the fork shows up as "someone followed the
docs and got different bytes" -- which is why the renderer is this tool and the
authority is the loader.

The split the whole feature rests on: **data lands under
`/mnt/20t/xubuqiang/datasets/<name>/` and never in git; code -- how to load and how to
score -- stays in git.** A dataset whose `downloads()` names a `dst` inside the platform
tree is refused by `tools/dataset_lint.py` for exactly that reason.

A version must be pinned: a release tag, a commit sha or an exact package version. A
branch name is not a version and is refused by the lint; `--check` cannot verify a
moving target even if one were allowed.

`--fetch` (added with the QA batch)
-----------------------------------
`--check` says what is missing; `--fetch` is the other half, and it lives here rather
than in a shell script so that the same `downloads()` entries are the only list. It
understands the three transports the loaders use:

    http        GET `url` (redirects followed) into `dst`
    hf-dataset  GET `https://<endpoint>/datasets/<repo>/resolve/<revision>/<file>`
    pip         never fetched here -- it is an install, and `--check` reports it

`--file` narrows an entry to a subset of the files it names, which is how a dataset
with an 8 GB repository and a 7 MB config fetches only what it scores. The endpoint
defaults to `https://hf-mirror.com` because huggingface.co is unreachable from this
machine (measured: HTTP 000 after a 30 s timeout); `HG_HF_ENDPOINT` overrides it, so
the choice is visible at the call site instead of being an implicit convenience.
`sha256` is verified whenever the entry declares it, and a mismatch deletes the
partial download rather than leaving bytes on disk that look provisioned.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "DOWNLOADS.md"
SCHEMA_PATH = ROOT / "data" / "dataset.schema.json"

#: huggingface.co is not reachable from this host; the mirror is. A dataset's `version`
#: is the revision (commit) either way, so the pin survives the endpoint swap -- which is
#: the point of pinning a revision rather than a branch.
DEFAULT_HF_ENDPOINT = os.environ.get("HG_HF_ENDPOINT", "https://hf-mirror.com")

sys.path.insert(0, str(ROOT))
import data.scaffolds.registry as registry  # noqa: E402


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def datasets_with_downloads() -> list[tuple[str, list[dict]]]:
    """Every published dataset that declares `downloads()`, in name order."""
    out = []
    for name in registry.published():
        module = importlib.import_module(registry.module_name(name))
        fn = getattr(module, "downloads", None)
        if callable(fn):
            out.append((name, list(fn() or [])))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_root(module) -> Path:
    """Where this dataset's bytes live, for resolving a relative `dst`.

    A relative `dst` is relative to the dataset root, not to the platform -- that is
    the split the whole feature rests on. The root is read from the loader, in the same
    order the rest of the tooling does: an explicit `provision_status()['root']`, then a
    `DATASET_ROOT`/`ROOT` attribute, and only then the platform directory (which is
    right for a dataset whose content really is committed).
    """
    status = getattr(module, "provision_status", None)
    if callable(status):
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


def check_entry(entry: dict, base: Path) -> dict:
    """Is one download present? Never raises: a provisioning tool that dies on the first
    missing byte cannot report the other nine."""
    kind = entry.get("kind")
    result = {"name": entry.get("name"), "kind": kind, "version": entry.get("version"),
              "present": False, "detail": ""}
    if kind == "pip":
        package = entry.get("package")
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
    module = importlib.import_module(registry.module_name(name))
    base = dataset_root(module)
    entries = [check_entry(entry, base) for entry in downloads]
    status = {}
    fn = getattr(module, "provision_status", None)
    if callable(fn):
        try:
            status = fn()
        except Exception as exc:  # noqa: BLE001
            status = {"error": f"{type(exc).__name__}: {exc}"}
    return {"dataset": name, "entries": entries, "status": status,
            "present": all(e["present"] for e in entries) if entries else False}


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
    if kind == "hf-dataset":
        repo, revision = entry.get("repo"), entry.get("version")
        if not (repo and revision):
            return None
        # One URL per file, built per file in `fetch_entry`; this is the shape check.
        return f"{endpoint}/datasets/{repo}/resolve/{revision}"
    return None


def selected_files(entry: dict, only: list[str] | None) -> list[str]:
    """Which files of a multi-file entry to fetch. `only` narrows; empty means all."""
    declared = [f for f in (entry.get("files") or []) if isinstance(f, str)]
    if only:
        wanted = [f for f in only if not declared or f in declared]
        return wanted
    return declared


def _download(url: str, target: Path, *, timeout: int = 600) -> None:
    """GET `url` into `target`, atomically enough that a failure leaves no half file.

    `curl` when it is available (it follows the mirror's redirects and reports a real
    exit code on a truncated transfer); `urllib` otherwise, so the tool still works on a
    host without curl.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
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
    if kind == "pip":
        return True, f"[pip] {entry.get('package')}=={entry.get('version')} (install separately)"
    if kind not in ("http", "hf-dataset"):
        return False, f"[{kind}] {name}: no fetch transport"

    dst = entry.get("dst")
    if not dst:
        return False, f"[{kind}] {name}: no `dst`"
    target = Path(str(dst))
    if not target.is_absolute():
        target = base / target

    files = selected_files(entry, only)
    url = entry_url(entry, endpoint)
    if url is None:
        return False, f"[{kind}] {name}: cannot build a URL (needs repo/file/version)"
    if not files:
        files = [""]

    written = []
    for relative in files:
        if kind == "hf-dataset":
            full = f"{endpoint}{_hf_tail(entry, relative, endpoint)}"
        else:
            full = url if not relative else f"{url.rstrip('/')}/{relative}"
        out = target if not relative else target / relative
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
        try:
            _download(full, out)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, f"[{kind}] {name}: {relative or out.name}: {exc}"
        if expected:
            actual = _sha256(out)
            if actual != expected:
                out.unlink(missing_ok=True)
                return False, (f"[{kind}] {name}: {relative or out.name}: sha256 "
                               f"{actual[:12]}... != {expected[:12]}...; removed the "
                               f"download")
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
    return True, f"[{kind}] {name}: " + "; ".join(written)


def _hf_tail(entry: dict, file: str, endpoint: str) -> str:
    """`/datasets/<repo>/resolve/<revision>/<file>` -- the path after the endpoint."""
    return f"/datasets/{entry.get('repo')}/resolve/{entry.get('version')}/{file}"


def render_index(rows: list[dict]) -> str:
    """DOWNLOADS.md, from the loaders. See the module docstring for why it is generated."""
    lines = [
        "<!-- GENERATED BY tools/datasets.py --write. DO NOT EDIT BY HAND. -->",
        "",
        "# Dataset downloads",
        "",
        "The authority for every line below is the named dataset's own `downloads()`",
        "function; this file is rendered from it by `python3 tools/datasets.py --write`.",
        "Editing this file changes a copy, not the loader -- and a copy forks. Data lands",
        "under `/mnt/20t/xubuqiang/datasets/<name>/` and is never committed; the code that",
        "loads and scores it stays in git.",
        "",
        "`python3 tools/datasets.py --check` reports which of these are present locally.",
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
        for entry in row["entries"]:
            size = entry.get("size") or "size not stated"
            lines.append(f"### {entry.get('name')} ({entry['kind']}, {entry.get('version')})")
            lines.append("")
            lines.append(f"- **Version (pinned):** `{entry.get('version')}`")
            lines.append(f"- **Size:** {size}")
            if entry.get("url"):
                lines.append(f"- **URL:** {entry['url']}")
            if entry.get("repo"):
                lines.append(f"- **Repo:** `{entry['repo']}`")
            if entry.get("file"):
                lines.append(f"- **File:** `{entry['file']}`")
            if entry.get("files"):
                lines.append("- **Files:** "
                             + ", ".join(f"`{f}`" for f in entry["files"]))
            if entry.get("package"):
                lines.append(f"- **Package:** `{entry['package']}=={entry.get('version')}`")
            if entry.get("dst"):
                lines.append(f"- **Destination:** `{entry['dst']}`")
            if entry.get("sha256"):
                lines.append(f"- **sha256:** `{entry['sha256']}`")
            lines.append("")
            lines.append(f"> {entry.get('note', '').strip()}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
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

    rows = datasets_with_downloads()
    if args.dataset:
        rows = [r for r in rows if r[0] in set(args.dataset)]

    if args.write:
        INDEX.write_text(render_index([{"dataset": n, "entries": e} for n, e in rows]))
        print(f"wrote {INDEX.relative_to(ROOT)} ({len(rows)} dataset(s))")
        return 0

    if args.fetch:
        failed = 0
        for name, entries in rows:
            module = importlib.import_module(registry.module_name(name))
            base = dataset_root(module)
            print(f"== {name}  (root {base})")
            for entry in entries:
                ok, message = fetch_entry(entry, base, only=args.file,
                                          endpoint=args.endpoint, force=args.force)
                print(f"  {'ok  ' if ok else 'FAIL'} {message}")
                if not ok:
                    failed += 1
        return 1 if failed else 0

    if args.check:
        checked = [check_dataset(name, entries) for name, entries in rows]
        if args.json:
            print(json.dumps({"schema_version": _schema()["schema_version"],
                              "datasets": checked}, indent=1, sort_keys=True))
            return 0
        failed = 0
        for row in checked:
            mark = "ok  " if row["present"] else "MISS"
            if not row["present"]:
                failed += 1
            status = row["status"]
            extra = ""
            if status and status.get("tasks") is not None:
                extra = f" ({status.get('tasks')} task(s) loadable)"
            print(f"{mark} {row['dataset']}{extra}")
            for entry in row["entries"]:
                flag = "  ok " if entry["present"] else "  -- "
                print(f"{flag} [{entry['kind']}] {entry['name']} @ {entry['version']}"
                      f" -- {entry['detail']}")
        return 1 if failed else 0

    # Default and --list: what each dataset asks for.
    if args.json:
        print(json.dumps([{"dataset": n, "downloads": e} for n, e in rows],
                         indent=1, sort_keys=True))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
