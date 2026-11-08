"""Read-only admission of the fixed author's renderer and copied verifier assets."""
import hashlib
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
PIN = json.loads((ASSETS / 'RENDERER_SOURCE.json').read_bytes())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def admit_source(root):
    root = Path(root)
    if root.is_symlink() or root.resolve(strict=True) != root:
        raise ValueError('JobBench renderer root must be physical')
    for relative, expected in PIN['files'].items():
        path = root / relative
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError('JobBench renderer source contains a symlink')
        if not path.is_file() or digest(path) != expected:
            raise ValueError('JobBench pinned renderer source changed: ' + relative)
    for relative in ('harbor/jobbench_harbor', 'harbor/templates', 'harbor/runtime',
                     'eval/jobbench_eval', 'eval/calc-runtime'):
        directory = root / relative
        actual = {p.relative_to(root).as_posix() for p in directory.rglob('*')
                  if not p.is_dir() or p.is_symlink()}
        expected = {p for p in PIN['files'] if p.startswith(relative + '/')}
        if actual != expected:
            raise ValueError('JobBench renderer input inventory changed: ' + relative)
    return {'source': PIN['source'], 'revision': PIN['revision'], 'files': dict(PIN['files'])}


def platform_identity():
    return {name: digest(ASSETS / name) for name in (
        'prepare.py', 'render_worker.py', 'renderer_identity.py', 'RENDERER_SOURCE.json', 'SOURCE.json')}
