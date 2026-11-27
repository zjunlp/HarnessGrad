"""Best-effort snapshots of a trusted Python service's startup cache."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


def snapshot_path(spec: dict, runtime_id: str) -> Path:
    root = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    host = os.uname()
    identity = ':'.join((host.nodename, host.machine, runtime_id, spec['directory'], spec['key']))
    return root / 'harnessgrad/relay-startup-v1' / sha256(identity.encode()).hexdigest()


def _ignore_non_cache_files(folder, names):
    paths = [Path(folder) / name for name in names]
    return [path.name for path in paths
            if path.is_symlink() or not (path.is_file() or path.is_dir())]


def copy_snapshot(source: Path, target: Path) -> None:
    """Copy ordinary files into place atomically. Concurrent publishers can race."""
    try:
        if target.exists() or source.is_symlink() or not source.is_dir() or not any(source.iterdir()):
            return
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='.copy-', dir=target.parent) as temporary:
            stage = Path(temporary) / 'snapshot'
            shutil.copytree(source, stage, symlinks=True, ignore=_ignore_non_cache_files)
            if any(stage.iterdir()):
                stage.rename(target)
    except OSError:
        # A missing or unavailable cache just means the service compiles locally.
        # A competing writer may also have published a complete snapshot first.
        pass
