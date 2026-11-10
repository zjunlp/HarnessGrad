"""Bind public scorer source and dependency recipes, not a historical machine image."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'public-native-local-v1'
DATASETS = frozenset(('alfworld', 'bird_minidev', 'chessbench_puzzles', 'jericho', 'scienceworld'))


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def bind(dataset: str, spec: dict) -> dict:
    """Keep original argv/metric/inputs; identify only the public Python scorer files."""
    if dataset not in DATASETS:
        raise ValueError('unsupported public native scorer')
    sources = {}
    for entry in spec.get('inputs', []):
        name = entry.get('path', '')
        if not name.endswith('.py'):
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name:
            raise ValueError('public scorer code path must be normalized and relative')
        if 'from' in entry:
            source = Path(entry['from']).resolve(strict=True)
            if not source.is_relative_to(ROOT) or not source.is_file():
                raise ValueError('public scorer code must come from the installed platform')
            content = source.read_bytes()
            value = {'source': source.relative_to(ROOT).as_posix()}
        else:
            content = entry['content'].encode('utf-8')
            value = {'content': entry['content']}
        value['sha256'] = hashlib.sha256(content).hexdigest()
        if name in sources and sources[name] != value:
            raise ValueError('conflicting public scorer code entries')
        sources[name] = value
    if not sources:
        raise ValueError('public native scorer requires declared scoring code')
    binding = {'dataset': dataset, 'profile': PROFILE, 'sources': sources}
    return {**spec, 'native_runtime': {**binding, 'sha256': identity(binding)}}
