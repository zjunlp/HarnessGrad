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

The same wall has a second door, and it is off by default. `HG_GH_RAW_MIRROR`, set to a
proxy prefix, rewrites `https://raw.githubusercontent.com/<path>` to
`<mirror>/https://raw.githubusercontent.com/<path>` before the transfer -- and only that
host. Measured 2026-11-02: `raw.githubusercontent.com` is reset-blocked from this machine
(`curl: (35) Recv failure` on every A record) while `github.com` and `hf-mirror.com`
answer, so a dataset whose provenance *is* a raw file could not be provisioned at all:
`strongreject` landed 8 of 155 files, and `memoryagentbench`'s pinned `punkt_tab.zip`
failed the same way. Unset, every URL is exactly what the loader declared; the `--fetch`
line names the mirror when one was used, so provenance is not lost.

A failed transfer also leaves no directory behind. `_download` records which parents it
had to create and removes them again, deepest first and only while empty, so an
"already prepared?" guard is not fooled by a fetch that produced nothing -- measured:
kumo's `prepare.py` skips its clone when `<root>/upstream` exists, and the empty tree
left by a blocked fetch made the next sanctioned command die on a missing `.gitignore`.
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
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "DOWNLOADS.md"
SCHEMA_PATH = ROOT / "data" / "dataset.schema.json"

#: huggingface.co is not reachable from this host; the mirror is. A dataset's `version`
#: is the revision (commit) either way, so the pin survives the endpoint swap -- which is
#: the point of pinning a revision rather than a branch.
DEFAULT_HF_ENDPOINT = os.environ.get("HG_HF_ENDPOINT", "https://hf-mirror.com")

#: The one host `HG_GH_RAW_MIRROR` is allowed to stand in front of. Nothing else: a
#: mirror that silently rewrote a checksummed URL on another host would surface as a hash
#: mismatch, which reads like corrupt data rather than the transport choice it is.
GH_RAW_HOST = "raw.githubusercontent.com"

#: The opt-in GitHub-raw mirror. `raw.githubusercontent.com` is reset-blocked from this
#: host (measured 2026-11-02: `curl: (35) Recv failure` on every A record) while
#: `github.com` answers. Unset means off, and off means the URL is the loader's URL byte
#: for byte -- the only URL its `sha256` was taken from.
GH_RAW_MIRROR_ENV = "HG_GH_RAW_MIRROR"

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
    if kind == "pip":
        return True, f"[pip] {entry.get('package')}=={entry.get('version')} (install separately)"
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
        f"**Egress on a walled host.** With `{GH_RAW_MIRROR_ENV}` unset, `--fetch` asks for",
        "the URL above byte for byte. Set it to a proxy prefix (for example",
        "`https://ghproxy.example/`) and a `raw.githubusercontent.com` URL is fetched as",
        "`<mirror>/https://raw.githubusercontent.com/<path>` instead; no other host is",
        "rewritten, because a mirror in front of a checksummed upstream would turn a",
        "transport choice into a hash mismatch. Measured 2026-11-02: that host is",
        "reset-blocked from this machine while `github.com` and `hf-mirror.com` work, which",
        "is why `strongreject` (155 of 155 URLs) and `memoryagentbench`'s pinned",
        "`punkt_tab.zip` could not be fetched without one. Re-measured 2026-11-03:",
        f"`{GH_RAW_MIRROR_ENV}=https://ghproxy.net` served that `punkt_tab.zip` byte for byte",
        "(`sha256 ok`), so a mirror is the supported route for that host, not a workaround;",
        "a dataset that also pins preparation packages needs an interpreter of its own, which",
        "is documented in `docs/dataset-onboarding.md` under the download contract.",
        "The `--fetch` line names the mirror it used,",
        "and a failed transfer removes the directories it had to create, so a half-fetched",
        "dataset does not read as prepared.",
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
    # An adapter this interpreter cannot import is missing from `rows`, and saying so is part
    # of the answer: a catalogue that is silently one entry shorter is how a fresh clone looks
    # broken. (A wheel has no `specs/*.json`; `chess` and `numpy` are optional dependencies.)
    if args.json:
        # Shape kept as a list of rows: it is a machine interface, and the diagnostics below
        # go to stderr rather than into a payload a caller already parses.
        print(json.dumps([{"dataset": n, "downloads": e} for n, e in rows],
                         indent=1, sort_keys=True))
        _report_unimportable()
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
    _report_unimportable()
    return 0


def _report_unimportable() -> None:
    """Name the adapters the catalogue had to drop, and why."""
    broken = registry.broken_adapters()
    if not broken:
        return
    print(f"\n{len(broken)} adapter(s) could not be imported and are not listed above:",
          file=sys.stderr)
    for name, reason in sorted(broken.items()):
        print(f"  {name}: {reason[:160]}", file=sys.stderr)
    print("Install the named module or provision the missing asset; `load()` names the "
          "same reason when the adapter is used directly.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
