"""Portable, digest-bound native scorer packages; never discover a host interpreter.

The reviewed manifest names sandbox destinations and content-addressed blobs.
Only the external store location is configurable. Imports publish a complete
package atomically; admission still seals and verifies every byte separately.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATASETS = frozenset(('bird_minidev', 'jericho', 'alfworld', 'scienceworld', 'chessbench_puzzles'))


class StoreError(ValueError):
    """A selected package is absent, incomplete or differs from its reviewed pin."""


class MissingBlobs(StoreError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__(f'incomplete native runtime: {len(missing)} pinned blobs missing')


def blob_name(digest: str) -> str:
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise StoreError('invalid native runtime SHA256')
    return 'blobs/' + digest


def entries(manifest: dict) -> dict[str, dict]:
    """Validate portable locators, deduplicating identical payloads and launcher."""
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int
            or manifest['schema'] != 2 or not isinstance(manifest.get('dataset'), str)
            or manifest['dataset'] not in DATASETS):
        raise StoreError('native runtime package requires schema 2 and a supported dataset')
    if not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('launcher'), dict):
        raise StoreError('invalid native runtime file inventory')
    blobs = {}
    for entry in [*manifest['files'].values(), manifest['launcher']]:
        if not isinstance(entry, dict):
            raise StoreError('invalid native runtime file entry')
        name = blob_name(entry['sha256'])
        if entry.get('source') != name or 'path' in entry:
            raise StoreError('native runtime source must name its digest-bound blob')
        size = entry.get('size')
        if type(size) is not int or size < 0:
            raise StoreError('invalid native runtime payload size')
        if name in blobs and blobs[name]['size'] != size:
            raise StoreError('inconsistent native runtime blob sizes')
        blobs[name] = {'sha256': entry['sha256'], 'size': size}
    for target in manifest['files']:
        p = Path(target)
        if (not p.is_absolute() or '..' in p.parts or len(p.parts) < 2
                or p.parts[1] in {'task', 'tmp', 'proc', 'dev', 'etc'}):
            raise StoreError(f'unsafe native runtime destination: {target}')
    for target in (manifest['interpreter'], manifest['loader'], '/scorer/check.py'):
        if target not in manifest['files']:
            raise StoreError(f'native runtime missing entry: {target}')
    return blobs


def portable_manifest(legacy: dict, launcher_size: int) -> dict:
    """Change host locators only; retain sandbox layout, byte pins and provenance."""
    if legacy.get('schema') != 1:
        raise StoreError('migration requires a schema 1 source manifest')
    manifest = deepcopy(legacy)
    manifest['schema'] = 2
    for entry in manifest['files'].values():
        entry['source'] = blob_name(entry['sha256'])
    launcher = manifest['launcher']
    launcher.pop('path')
    launcher.update(source=blob_name(launcher['sha256']), size=launcher_size)
    entries(manifest)
    return manifest


def load_manifest(binding: dict) -> tuple[bytes, dict]:
    if binding['dataset'] not in DATASETS:
        raise StoreError('unsupported native runtime dataset')
    blob_name(binding['sha256'])
    path = Path(binding['manifest'])
    raw = (path if path.is_absolute() else ROOT / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise StoreError('native runtime manifest digest mismatch')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get('dataset') != binding['dataset']:
        raise StoreError('native runtime manifest dataset mismatch')
    entries(manifest)
    return raw, manifest


def store_root() -> Path:
    from harnessgrad.paths import dataset_root
    # Reuse the existing .env/worktree/relative-path rules and external-storage
    # guard, including the optional HG_DATA_NATIVE_RUNTIMES override.
    return Path(dataset_root('native-runtimes')).resolve()


def package_path(manifest: dict, digest: str, root: Path | None = None) -> Path:
    from harnessgrad.paths import main_checkout

    blob_name(digest)
    if manifest['dataset'] not in DATASETS:
        raise StoreError('unsupported native runtime dataset')
    base = store_root() if root is None else Path(root).expanduser().resolve()
    if any(base.is_relative_to(checkout) for checkout in {ROOT, main_checkout()}):
        raise StoreError('native runtime store must be outside the platform checkout')
    return base / manifest['dataset'] / digest


def source_path(package: Path, name: str) -> Path:
    if name != 'manifest.json' and name != blob_name(name.removeprefix('blobs/')):
        raise StoreError('unsafe native runtime package member')
    path = package
    for part in Path(name).parts:
        path = path / part
        if stat.S_ISLNK(path.lstat().st_mode):
            raise StoreError(f'linked native runtime package member: {name}')
    if not stat.S_ISREG(path.stat().st_mode):
        raise StoreError(f'native runtime package member is not a regular file: {name}')
    return path


def selected_package(manifest: dict, digest: str, root: Path | None = None,
                     *, manifest_size: int) -> Path:
    package = package_path(manifest, digest, root)
    for path in (package.parent, package):
        if path.is_symlink():
            raise StoreError('linked native runtime package directory')
    marker = source_path(package, 'manifest.json')
    with marker.open('rb') as stream:
        raw = stream.read(manifest_size + 1)
    if len(raw) != manifest_size or hashlib.sha256(raw).hexdigest() != digest:
        raise StoreError('installed native runtime manifest digest mismatch')
    return package


def _copy_checked(source, output, entry: dict) -> None:
    digest, remaining = hashlib.sha256(), entry['size']
    while remaining:
        block = source.read(min(1024 * 1024, remaining))
        if not block:
            raise StoreError('native runtime payload size mismatch')
        output.write(block)
        digest.update(block)
        remaining -= len(block)
    if source.read(1) or digest.hexdigest() != entry['sha256']:
        raise StoreError('native runtime payload digest/size mismatch')


def verify_package(binding: dict, root: Path | None = None) -> dict:
    raw, manifest = load_manifest(binding)
    package = selected_package(manifest, binding['sha256'], root, manifest_size=len(raw))
    for name, entry in entries(manifest).items():
        with source_path(package, name).open('rb') as source:
            _copy_checked(source, _Discard(), entry)
    return {'dataset': manifest['dataset'], 'sha256': binding['sha256'],
            'package': str(package), 'files': len(manifest['files']), 'verified': True}


class _Discard:
    def write(self, value):
        pass


@contextmanager
def _staging(manifest: dict, digest: str, root: Path | None):
    final = package_path(manifest, digest, root)
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if final.parent.is_symlink():
        raise StoreError('linked native runtime dataset directory')
    # Serialize preparations of this exact identity, without overwriting a
    # previously published package or exposing partial data as installed.
    fd = os.open(final.parent / ('.' + digest + '.lock'),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='.' + digest + '-', dir=final.parent) as temporary:
            stage = Path(temporary) / 'package'
            (stage / 'blobs').mkdir(parents=True, mode=0o700)
            yield stage, final


def _publish(stage: Path, final: Path, raw: bytes, binding: dict, root: Path | None) -> dict:
    (stage / 'manifest.json').write_bytes(raw)
    for path in stage.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    if final.exists() or final.is_symlink():
        # A corrupt existing installation requires explicit operator repair.
        return verify_package(binding, root)
    stage.rename(final)
    return verify_package(binding, root)


def import_package(binding: dict, archive: Path, root: Path | None = None) -> dict:
    raw, manifest = load_manifest(binding)
    allowed = {**entries(manifest), 'manifest.json': {
        'sha256': binding['sha256'], 'size': len(raw)}}
    with _staging(manifest, binding['sha256'], root) as (stage, final):
        seen = set()
        with tarfile.open(archive, 'r|*') as tar:
            for member in tar:
                name = member.name
                if (name not in allowed or name in seen or not member.isfile()
                        or member.sparse is not None or member.size != allowed[name]['size']):
                    raise StoreError(f'unexpected, linked or duplicate native package member: {name}')
                seen.add(name)
                # Names come from the authenticated allowlist, never extractall.
                with tar.extractfile(member) as source, (stage / name).open('wb') as output:
                    _copy_checked(source, output, allowed[name])
        if seen != allowed.keys():
            raise StoreError('native runtime archive is incomplete')
        return _publish(stage, final, raw, binding, root)


def write_archive(raw: bytes, sources: dict[str, Path], output: Path) -> Path:
    """Export exact checked bytes, including launcher, before publishing a new file."""
    manifest = json.loads(raw)
    blobs = entries(manifest)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + output.name + '-', dir=output.parent)
    try:
        with os.fdopen(fd, 'wb') as destination, tarfile.open(fileobj=destination, mode='w:gz') as tar:
            marker = tarfile.TarInfo('manifest.json')
            marker.size, marker.mode = len(raw), 0o444
            tar.addfile(marker, io.BytesIO(raw))
            for name, entry in sorted(blobs.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = entry['size'], 0o444
                with Path(sources[name]).open('rb') as source:
                    reader = _HashingReader(source)
                    tar.addfile(info, reader)
                    if (source.read(1) or reader.size != entry['size']
                            or reader.digest.hexdigest() != entry['sha256']):
                        raise StoreError('native runtime payload changed during export')
        # No clobber: an existing archive may be unrelated evidence.
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


class _HashingReader:
    def __init__(self, stream):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def read(self, count):
        block = self.stream.read(count)
        self.digest.update(block)
        self.size += len(block)
        return block


def export_package(binding: dict, output: Path, root: Path | None = None) -> Path:
    raw, manifest = load_manifest(binding)
    package = selected_package(manifest, binding['sha256'], root, manifest_size=len(raw))
    return write_archive(raw, {name: source_path(package, name) for name in entries(manifest)}, output)


def assemble_package(binding: dict, *, source_manifests=(), archives=(), remaps=(),
                     source_root: Path = ROOT, root: Path | None = None) -> dict:
    """Recover pinned bytes from explicit legacy sources; never install substitutes."""
    raw, manifest = load_manifest(binding)
    wanted = entries(manifest)
    by_hash = {entry['sha256']: name for name, entry in wanted.items()}
    with _staging(manifest, binding['sha256'], root) as (stage, final):
        found = set()
        for declaration in source_manifests:
            legacy = json.loads(Path(declaration).read_text())
            if (not isinstance(legacy, dict) or legacy.get('schema') != 1
                    or not isinstance(legacy.get('files'), dict)
                    or not isinstance(legacy.get('launcher'), dict)):
                raise StoreError('assembly source manifests must be schema 1 ledgers')
            rows = [*legacy['files'].values(), legacy['launcher']]
            for entry in rows:
                if not isinstance(entry, dict):
                    raise StoreError('invalid native runtime source ledger entry')
                name = by_hash.get(entry['sha256'])
                if name is None or name in found:
                    continue
                path = Path(entry.get('source', entry.get('path')))
                if not path.is_absolute():
                    path = Path(source_root) / path
                for old, new in sorted(remaps, key=lambda pair: len(str(pair[0])), reverse=True):
                    if path.is_relative_to(old):
                        path = Path(new) / path.relative_to(old)
                        break
                try:
                    with path.open('rb') as source, (stage / name).open('wb') as output:
                        _copy_checked(source, output, wanted[name])
                except (OSError, StoreError):
                    (stage / name).unlink(missing_ok=True)
                    continue
                found.add(name)
        for archive in archives:
            with tarfile.open(archive, 'r|*') as tar:
                for member in tar:
                    # Legacy archives are only scanned for byte matches. Linked
                    # members are never followed, executed or extracted by name.
                    if not member.isfile() or member.sparse is not None:
                        continue
                    candidates = {name for name in wanted if name not in found
                                  and wanted[name]['size'] == member.size}
                    if not candidates:
                        continue
                    with tar.extractfile(member) as source, tempfile.TemporaryFile(dir=stage) as saved:
                        digest = hashlib.sha256()
                        while block := source.read(1024 * 1024):
                            digest.update(block)
                            saved.write(block)
                        name = by_hash.get(digest.hexdigest())
                        if name in candidates:
                            saved.seek(0)
                            with (stage / name).open('wb') as output:
                                shutil.copyfileobj(saved, output)
                            found.add(name)
        if found != wanted.keys():
            missing = [{'sha256': wanted[name]['sha256'], 'size': wanted[name]['size'],
                        'targets': [target for target, entry in manifest['files'].items()
                                    if entry['source'] == name]}
                       for name in sorted(wanted.keys() - found)]
            raise MissingBlobs(missing)
        return _publish(stage, final, raw, binding, root)
