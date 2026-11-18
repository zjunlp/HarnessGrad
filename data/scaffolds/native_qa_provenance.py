"""Declared source and implementation roles for the native MATH adapters.

This reads checked-in declarations and code only. A pinned upstream revision is
a declaration, not proof that a prepared local source came from that revision.
"""
import ast
import hashlib
from pathlib import Path

from harnessgrad.provenance import measurement_components as identities

_CATALOG = 'data/scaffolds/native_qa/__init__.py'
_SCORER = {
    'check': 'data/scaffolds/native_qa/check.py',
    'scoring': 'data/scaffolds/native_qa/scoring.py',
    'math_grade': 'data/scaffolds/native_qa/math_grade.py',
    # scoring.py imports these modules even on the MATH branch.
    'metrics': 'data/scaffolds/native_qa/metrics.py',
    'bbeh_official': 'data/scaffolds/native_qa/bbeh_official.py',
    'office_reward': 'data/scaffolds/native_qa/office_reward.py',
}


def _literal_assignment(source, name):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f'invalid native QA declaration for {name}') from exc
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id == name]
    if len(values) != 1:
        raise ValueError(f'native QA declaration requires one {name}')
    try:
        return ast.literal_eval(values[0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f'invalid native QA declaration for {name}') from exc


def math_profile(root, dataset):
    if dataset not in {'math', 'math500'}:
        raise ValueError('unsupported native MATH provenance profile')
    root = Path(root)
    adapter = f'data/datasets/{dataset}.py'
    catalog_bytes = (root / _CATALOG).read_bytes()
    adapter_bytes = (root / adapter).read_bytes()
    catalog = _literal_assignment(catalog_bytes, 'CATALOG')
    spec = catalog.get(dataset) if isinstance(catalog, dict) else None
    if not isinstance(spec, dict):
        raise ValueError('native MATH source declaration is missing')
    metric = _literal_assignment(adapter_bytes, 'METRIC')
    if metric != spec.get('metric'):
        raise ValueError('native MATH metric declarations differ')
    source = spec.get('source')
    if (not isinstance(source, str) or source.count('/') != 1
            or any(not part or not all(c.isalnum() or c in '._-' for c in part)
                   for part in source.split('/'))):
        raise ValueError('invalid native MATH source declaration')
    loader = {'adapter': adapter, 'native_qa': _CATALOG,
              'preparation': 'data/scaffolds/native_qa/prepare.py'}
    loader_identity = identities.capture_implementation('loader', root, loader)
    captured = loader_identity['details']['components']
    if (captured['adapter'] != hashlib.sha256(adapter_bytes).hexdigest()
            or captured['native_qa'] != hashlib.sha256(catalog_bytes).hexdigest()):
        raise ValueError('native MATH declaration changed during capture')
    return {
        'data_source': identities.data_source(dataset, origin='https://huggingface.co/datasets/' + source,
                                               revision=spec.get('revision')),
        'loader': loader_identity,
        'scorer': identities.capture_implementation('scorer', root, _SCORER, protocol=metric),
    }
