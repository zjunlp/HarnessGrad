"""Separate declarations for ChessBench source, loader and scorer provenance.

The shared adapter and QA base contain both loading and scoring code, so each is
deliberately included in both roles. No benchmark data is opened here.
"""
import ast
from pathlib import Path

from harnessgrad.provenance import measurement_components as identities

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/chessbench_puzzles.py'
LOADER = {
    'adapter': ADAPTER,
    'qa_base': 'data/scaffolds/qa/base.py',
    'qa_read': 'data/scaffolds/qa/read.py',
    'provider_worker': 'data/scaffolds/provider_worker.py',
    'provider': 'harnessgrad/datasets/provider.py',
}
SCORER = {
    'adapter': ADAPTER,
    'qa_base': 'data/scaffolds/qa/base.py',
    'qa_verifier': 'data/scaffolds/qa/verify/check.py',
    'stepwise': 'eval/run/chess_stepwise.py',
    'stepwise_worker': 'eval/run/chess_stepwise_worker.py',
    'native_runtime': 'data/runtimes/chessbench_puzzles-native.json',
}


def _source_declaration():
    """Read literal declarations without importing the adapter or its dependency."""
    tree = ast.parse((ROOT / ADAPTER).read_bytes())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {'REVISION', 'CSV_SHA256', 'STRICT_METRIC'}:
                if name in constants:
                    raise ValueError(f'duplicate ChessBench declaration: {name}')
                constants[name] = ast.literal_eval(node.value)
    downloads = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'downloads']
    if len(downloads) != 1:
        raise ValueError('ChessBench downloads declaration unavailable')
    origins = []
    for node in ast.walk(downloads[0]):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {key.value: val for key, val in zip(node.keys, node.values)
                 if isinstance(key, ast.Constant) and isinstance(key.value, str)}
        if isinstance(pairs.get('name'), ast.Constant) and pairs['name'].value == 'ChessBench puzzles.csv':
            if (not isinstance(pairs.get('version'), ast.Name) or pairs['version'].id != 'REVISION'
                    or not isinstance(pairs.get('sha256'), ast.Name) or pairs['sha256'].id != 'CSV_SHA256'
                    or not isinstance(pairs.get('dst'), ast.Constant) or pairs['dst'].value != '_src/puzzles.csv'):
                raise ValueError('ChessBench data artifact declaration changed')
            origins.append(ast.literal_eval(pairs['url']))
    if len(origins) != 1 or set(constants) != {'REVISION', 'CSV_SHA256', 'STRICT_METRIC'}:
        raise ValueError('ChessBench source declaration unavailable')
    return constants, origins[0]


def profile():
    constants, origin = _source_declaration()
    return {
        'data_source': identities.data_source('chessbench_puzzles', origin=origin,
            revision=constants['REVISION'], declared_artifact_sha256=constants['CSV_SHA256']),
        'loader': identities.capture_implementation('loader', ROOT, LOADER),
        'scorer': identities.capture_implementation('scorer', ROOT, SCORER,
            protocol=constants['STRICT_METRIC']),
    }
