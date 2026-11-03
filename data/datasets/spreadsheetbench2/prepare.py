#!/usr/bin/env python3
"""Explicitly fetch/extract pinned supported SpreadsheetBench2 workbook categories."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import urllib.request
import zipfile

REPOSITORY = 'KAKA22/SpreadsheetBench-v2'
REVISION = '5a2215ed4121945ab09d8723df2995602090b042'
FILENAME = 'spreadsheetbench-v2-09-10.zip'
CATEGORIES = {'Debugging', 'Financial_Model', 'Template'}


def extract(archive: Path, root: Path):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = []
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or ':' in entry.filename:
                raise ValueError('unsafe upstream workbook archive path')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('upstream archive contains a symlink')
            parts = relative.parts
            if len(parts) < 3 or parts[0] != 'spreadsheetbench-v2' or parts[1] not in CATEGORIES or entry.is_dir():
                continue
            target = root.joinpath(*parts)
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError('output path escapes preparation root')
            selected.append((entry, target))
        if not selected:
            raise ValueError('archive has no native supported category files')
        for entry, target in selected:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = source.read(entry)
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f'pre-existing file differs from pinned archive: {target}')
            target.write_bytes(data)
    manifest = {'source': REPOSITORY, 'revision': REVISION,
                'archive': FILENAME, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'files': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for _entry, p in selected},
                'excluded_categories': ['Visualization']}
    (root / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    archive = args.archive or args.root / '_src' / FILENAME
    if not archive.exists():
        endpoint = os.environ.get('HG_HF_ENDPOINT', 'https://hf-mirror.com').rstrip('/')
        url = f'{endpoint}/datasets/{REPOSITORY}/resolve/{REVISION}/{FILENAME}'
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as tmp:
            pending = Path(tmp.name)
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    while block := response.read(1 << 20):
                        tmp.write(block)
                tmp.close()
                pending.replace(archive)
            finally:
                pending.unlink(missing_ok=True)
    manifest = extract(archive, args.root)
    print(json.dumps({'prepared': True, 'root': str(args.root), 'files': len(manifest['files']),
                      'archive_sha256': manifest['archive_sha256']}))


if __name__ == '__main__':
    main()
