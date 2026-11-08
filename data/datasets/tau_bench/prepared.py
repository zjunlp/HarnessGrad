"""Offline, pinned import of legacy tau assets into external storage."""
from __future__ import annotations

import argparse
import ctypes
import errno
import re
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ASSETS = Path(__file__).resolve().parent


def inventory(manifest: Path = ASSETS / 'PROVENANCE.json') -> dict:
    value = json.loads(manifest.read_bytes())
    files = value['files']
    if not files or not value['checkout_commit']:
        raise ValueError('tau prepared source manifest is incomplete')
    for name, digest in files.items():
        path = Path(name)
        if (path.is_absolute() or '..' in path.parts or path.as_posix() != name
                or not path.parts or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('tau prepared source manifest has an unsafe entry')
    return value


def verify(root: Path, manifest: Path = ASSETS / 'PROVENANCE.json') -> dict:
    expected = inventory(manifest)
    if (root / 'PROVENANCE.json').read_bytes() != manifest.read_bytes():
        raise ValueError('tau prepared provenance differs from the pinned source manifest')
    payloads = {}
    for name, digest in expected['files'].items():
        path = root / name
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent) or not path.is_file():
            raise ValueError(f'tau prepared missing or unsafe pinned file: {name}')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(f'tau prepared checksum mismatch: {name}')
        payloads[name] = payload
    actual = {p.relative_to(root).as_posix() for p in (root / 'gt').glob('*.json')}
    pinned = {name for name in expected['files'] if name.startswith('gt/')}
    if actual != pinned:
        raise ValueError('tau prepared case inventory differs from pinned source')
    return payloads


def import_assets(source: Path, destination: Path,
                  manifest: Path = ASSETS / 'PROVENANCE.json') -> Path:
    """Copy exact pinned assets, retaining existing destinations on every failure."""
    source, destination = source.resolve(), destination.absolute()
    payloads = verify(source, manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / ('.' + destination.name + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists() or destination.is_symlink():
            verify(destination, manifest)
            return destination
        temporary = Path(tempfile.mkdtemp(prefix='.' + destination.name + '-', dir=destination.parent))
        try:
            for name, payload in payloads.items():
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            shutil.copyfile(manifest, temporary / 'PROVENANCE.json')
            verify(temporary, manifest)
            # Linux RENAME_NOREPLACE also protects against publishers that do not
            # cooperate with our lock (rename alone can replace an empty directory).
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1):
                error = ctypes.get_errno()
                if error == errno.EEXIST:
                    verify(destination, manifest)
                else:
                    raise OSError(error, os.strerror(error), str(destination))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    import_assets(args.source, args.destination)
    print('Verified tau-bench prepared assets imported.')


if __name__ == '__main__':
    main()
