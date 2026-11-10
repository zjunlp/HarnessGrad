"""Export pinned Loop source releases without changing their files or dependencies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

from harnessgrad.paths import main_checkout
from harnessgrad.python_artifacts import atomic_json, digest, locked

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ('0.2.0', '0.3.0')
ARCHIVE = Path(__file__).with_name('assets') / 'loop-0.2.0.zip'
ARCHIVE_SHA256 = '5249b339c57240db3f644cf246ccf486d192d10d7c7ca54c5aa70f6c9fad5fb2'
LEGACY_TREE = '7a811b3ab930a3f902eb50e18c504288f5eea0dc'
LEGACY_REVISION = 'b87be212e901d26262a1d3cc1b6356847899d19e'
LEGACY_FILES = {'agent.py', 'harness.json', 'requirements.txt'}


def _inventory(root: Path) -> dict:
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Loop release must be an owned directory')
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Loop release contains a link or non-regular entry')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
    return result


def _stage(version: str, target: Path) -> dict:
    if version == '0.2.0':
        if digest(ARCHIVE) != ARCHIVE_SHA256:
            raise ValueError('bundled Loop 0.2.0 archive digest mismatch')
        with zipfile.ZipFile(ARCHIVE) as archive:
            if set(archive.namelist()) != LEGACY_FILES or len(archive.infolist()) != len(LEGACY_FILES):
                raise ValueError('invalid bundled Loop 0.2.0 archive entries')
            target.mkdir()
            for name in sorted(LEGACY_FILES):
                (target / name).write_bytes(archive.read(name))
                (target / name).chmod(0o644)
        provenance = {'kind': 'historical-git-tree', 'revision': LEGACY_REVISION,
                      'tree': LEGACY_TREE, 'archive_sha256': ARCHIVE_SHA256}
    else:
        shutil.copytree(ROOT / 'base_harness/loop', target, symlinks=True,
                        ignore=shutil.ignore_patterns('.git', '.env', '.state', '__pycache__', '*.pyc'))
        provenance = {'kind': 'bundled-template'}
    manifest = json.loads((target / 'harness.json').read_text())
    if manifest['name'] != 'loop' or manifest['version'] != version:
        raise ValueError('Loop template version does not match the selected release')
    return provenance


def install(version: str, root: Path) -> Path:
    if version not in RELEASES:
        raise ValueError('Loop version must be one of: ' + ', '.join(RELEASES))
    root = root.expanduser().resolve()
    if any(root.is_relative_to(path) or path.is_relative_to(root) for path in (ROOT, main_checkout(ROOT))):
        raise ValueError('choose a Loop installation root outside the platform checkout')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(root, timeout_s=120):
        release = root / 'loop' / version
        if release.resolve() != release:
            raise ValueError('Loop release path must not contain symlinks')
        release.mkdir(parents=True, exist_ok=True)
        receipt = release / 'installation.json'
        if receipt.is_symlink():
            raise ValueError('Loop installation receipt must not be a symlink')
        # Source export requires no CLI, model, interpreter discovery or downloads.
        with tempfile.TemporaryDirectory(prefix='.source-', dir=release) as staging:
            source = Path(staging) / 'harness'
            provenance = _stage(version, source)
            files = _inventory(source)
            if receipt.exists():
                recorded = json.loads(receipt.read_text())
                instance = recorded.get('instance')
                if (not isinstance(instance, str) or not instance.startswith('install-')
                        or Path(instance).name != instance):
                    raise ValueError('invalid Loop installation receipt')
                folder = release / instance
                harness = folder / 'harness'
                if folder.is_symlink() or recorded != {'schema': 1, 'name': 'loop', 'version': version,
                        'instance': instance, 'provenance': provenance, 'files': files} or _inventory(harness) != files:
                    raise ValueError('installed Loop source changed; preserve it and use a new installation root')
                return harness
            instance = Path(tempfile.mkdtemp(prefix='install-', dir=release))
            try:
                harness = instance / 'harness'
                source.rename(harness)
                atomic_json(receipt, {'schema': 1, 'name': 'loop', 'version': version,
                                     'instance': instance.name, 'provenance': provenance, 'files': files})
            except BaseException:
                shutil.rmtree(instance)
                raise
        return harness
