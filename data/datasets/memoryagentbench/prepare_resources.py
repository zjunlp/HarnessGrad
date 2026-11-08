"""Check pinned tokenizer resources, or explicitly restore missing/corrupt files.

Default operation is offline and read only. --archive restores the four English
Punkt tables from a local pinned zip. --download permits the locked URLs only.
--from-resources imports individually verified files from an explicit local root.
The tracked lock is never regenerated. Python packages are installed separately.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets.memoryagentbench.lib.inputs import read_spec

_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_ENGLISH_MEMBERS = {"punkt_tab/english/" + name for name in (
    "abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt")}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _path(resources, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe resource path")
    target = (resources / relative).resolve()
    if not target.is_relative_to(resources):
        raise ValueError("resource path escapes destination")
    return target


def _verified(data, entry):
    if len(data) != entry["bytes"] or _sha(data) != entry["sha256"]:
        raise ValueError("resource_hash_mismatch:" + entry["path"])
    return data


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "HarnessGrad-MemoryAgentBench-resource-provision"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(data) > _MAX_DOWNLOAD_BYTES:
        raise ValueError("resource download exceeds maximum size")
    return data


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".mab-resource-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def provision(resources, *, download=False, archive=None, from_resources=None):
    """Verify all five files; opt-in restoration publishes only verified bytes."""
    resources = Path(resources).resolve()
    lock = read_spec("resources.lock.json")
    entries = lock["files"]
    members = [entry["archive_member"] for entry in entries if "archive_member" in entry]
    if len(members) != 4 or set(members) != _ENGLISH_MEMBERS:
        raise ValueError("lock must identify exactly the four English Punkt tables")
    missing, paths = [], {}
    for entry in entries:
        path = _path(resources, Path(entry["path"]).relative_to("resources"))
        paths[entry["path"]] = path
        if not path.is_file():
            missing.append(entry)
        else:
            try:
                _verified(path.read_bytes(), entry)
            except ValueError:
                missing.append(entry)
    replacements, local_sources = {}, []
    if from_resources is not None:
        source = Path(from_resources).resolve()
        for entry in list(missing):
            candidate = _path(source, Path(entry["path"]).relative_to("resources"))
            if candidate.is_file():
                replacements[entry["path"]] = _verified(candidate.read_bytes(), entry)
                local_sources.append({"path": entry["path"], "source": str(candidate),
                                      "sha256": entry["sha256"], "bytes": entry["bytes"]})
                missing.remove(entry)
    if missing and not download and archive is None:
        raise ValueError("missing or corrupt resources: " + ", ".join(entry["path"] for entry in missing))
    archive_bytes = None
    archive_source = None
    if any("archive_member" in entry for entry in missing):
        archive_path = Path(archive) if archive is not None else _path(resources, "downloads/punkt_tab.zip")
        source_kind = "local_archive"
        if archive_path.is_file():
            archive_bytes = archive_path.read_bytes()
            if _sha(archive_bytes) != lock["nltk_archive_sha256"]:
                if archive is not None or not download:
                    raise ValueError("punkt archive checksum mismatch")
                archive_bytes = None
        if archive_bytes is None:
            if not download:
                raise ValueError("pinned local Punkt archive missing")
            archive_bytes = _fetch(lock["nltk_archive_url"])
            source_kind = "download"
        if _sha(archive_bytes) != lock["nltk_archive_sha256"]:
            raise ValueError("punkt archive checksum mismatch")
        archive_source = {"kind": source_kind, "declared_source_url": lock["nltk_archive_url"],
                          "sha256": _sha(archive_bytes), "bytes": len(archive_bytes), "members": []}
        if source_kind == "local_archive":
            archive_source["path"] = str(archive_path.resolve())
        else:
            archive_source["acquisition_url"] = lock["nltk_archive_url"]
        if download:
            archive_source["cache_destination"] = str(_path(resources, "downloads/punkt_tab.zip"))
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zipped:
            for entry in missing:
                if "archive_member" not in entry:
                    continue
                name = entry["archive_member"]
                if zipped.namelist().count(name) != 1:
                    raise ValueError("Punkt archive member missing or duplicated: " + name)
                info = zipped.getinfo(name)
                if info.file_size != entry["bytes"]:
                    raise ValueError("Punkt archive member size mismatch: " + name)
                with zipped.open(info) as stream:
                    data = stream.read(entry["bytes"] + 1)
                replacements[entry["path"]] = _verified(data, entry)
                archive_source["members"].append({"archive_member": name, "path": entry["path"],
                                                   "sha256": entry["sha256"], "bytes": entry["bytes"]})
    for entry in missing:
        if "archive_member" in entry:
            continue
        if not download:
            raise ValueError("encoding missing or corrupt; provision the pinned file or use --download")
        replacements[entry["path"]] = _verified(_fetch(entry["source_url"]), entry)
    # No write occurs until every required payload has passed its pin checks.
    for relative, data in replacements.items():
        _atomic_write(paths[relative], data)
    if archive_bytes is not None and download:
        _atomic_write(_path(resources, "downloads/punkt_tab.zip"), archive_bytes)
    for entry in entries:
        _verified(paths[entry["path"]].read_bytes(), entry)
    return {"schema": "hg-mab-resource-provision-v1", "verified_files": len(entries),
            "verified_bytes": sum(entry["bytes"] for entry in entries),
            "restored_files": len(replacements), "download_allowed": bool(download),
            "nltk_data_revision": lock["nltk_data_revision"], "verified_local_sources": local_sources,
            "verified_archive_source": archive_source}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True, help="Directory containing nltk/ and tiktoken/")
    parser.add_argument("--download", action="store_true", help="Allow downloads from the pinned resource URLs")
    parser.add_argument("--archive", type=Path, help="Pinned local punkt_tab.zip for offline English-table restoration")
    parser.add_argument("--from-resources", type=Path, help="Explicit local resources root; import only original-lock-verified files")
    args = parser.parse_args(argv)
    print(json.dumps(provision(args.resources, download=args.download, archive=args.archive, from_resources=args.from_resources), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
