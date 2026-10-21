#!/usr/bin/env python3
"""Prepare fixed data without executing upstream code or generating attacks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets import strongreject


def prepare(root, source_directory=None, fetch=False):
    root = root.resolve()
    platform = Path(__file__).resolve().parents[3]
    if root == platform or platform in root.parents:
        raise ValueError("StrongREJECT data must be outside the platform checkout")
    written = 0
    for entry in strongreject.SOURCES["files"]:
        destination = root / entry["path"]
        if destination.is_file():
            raw = destination.read_bytes()
        elif source_directory is not None and (source_directory / entry["path"]).is_file():
            raw = (source_directory / entry["path"]).read_bytes()
        elif source_directory is not None and entry["path"].startswith("tasks/") and (source_directory / "parity-metadata" / Path(entry["path"]).name).is_file():
            raw = (source_directory / "parity-metadata" / Path(entry["path"]).name).read_bytes()
        elif fetch:
            with urllib.request.urlopen(entry["url"], timeout=60) as response:
                raw = response.read()
        else:
            raise FileNotFoundError(f"Missing {entry['path']}; provide source directory or --fetch")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"Digest mismatch for {entry['path']}; refusing to replace data")
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents a concurrent preparation from overwriting bytes.
            with destination.open("xb") as output:
                output.write(raw)
            written += 1
    strongreject.DATASET_ROOT = root
    strongreject.rows.cache_clear()
    status = strongreject.provision_status()
    if not status["present"]:
        raise ValueError(status)
    return {**status, "files_written": written, "source_files": len(strongreject.SOURCES["files"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=strongreject.DATASET_ROOT)
    parser.add_argument("--source-directory", type=Path)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.source_directory, args.fetch), indent=2))


if __name__ == "__main__":
    main()
