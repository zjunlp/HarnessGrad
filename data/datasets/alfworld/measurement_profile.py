"""ALFWorld declared archive sources and conservative implementation identities.

The archive declarations are not verification of any local game. The adapter,
shared replay helper and scorer-input binding participate in both selection and
verification, so they are fingerprinted in both implementation roles.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

from harnessgrad.provenance import measurement_components as identities
from harnessgrad.provenance.source_declarations import literal_assignments

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/alfworld.py'
GYM = 'data/datasets/alfworld/lib/gym_env.py'
LOADER = {'adapter': ADAPTER, 'gym': GYM, 'declarations': 'data/scaffolds/declarations.py',
          'scorer_input': 'data/scaffolds/scorer_input.py', 'native_binding': 'data/scaffolds/public_native.py',
          'dataset_paths': 'harnessgrad/paths.py'}
SCORER = {'adapter': ADAPTER, 'gym': GYM, 'check': 'data/datasets/alfworld/verify/check.py',
          'scorer_input': 'data/scaffolds/scorer_input.py', 'native_binding': 'data/scaffolds/public_native.py'}
_NAMES = frozenset(('json_2.1.1_json.zip', 'json_2.1.1_pddl.zip',
                    'json_2.1.3_tw-pddl.zip', 'alfworld repo tarball'))


def _source_declarations(raw):
    try:
        tree = ast.parse(raw)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'downloads']
        if len(functions) != 1:
            raise ValueError('missing downloads declaration')
        returns = [node for node in functions[0].body if isinstance(node, ast.Return)]
        if len(returns) != 1:
            raise ValueError('invalid downloads declaration')
        declarations = ast.literal_eval(returns[0].value)
        archives = [item for item in declarations if isinstance(item, dict) and item.get('kind') == 'http']
        if len(archives) != len(_NAMES) or {item.get('name') for item in archives} != _NAMES:
            raise ValueError('invalid archive inventory')
        selected = []
        for item in archives:
            name, version, url, digest = (item.get(key) for key in ('name', 'version', 'url', 'sha256'))
            if (not isinstance(version, str) or not version.strip() or not isinstance(url, str)
                    or not url.startswith('https://github.com/alfworld/alfworld/')
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('invalid archive declaration')
            selected.append({'name': name, 'version': version, 'url': url, 'sha256': digest})
        return sorted(selected, key=lambda item: item['name'])
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid ALFWorld provenance declaration') from exc


def _budget(raw):
    try:
        value = literal_assignments(raw, {'STEP_BUDGET'})['STEP_BUDGET']
        if type(value) is not int or value < 1:
            raise ValueError('invalid step budget')
        return value
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid ALFWorld scoring declaration') from exc


def profile():
    adapter = (ROOT / ADAPTER).read_bytes()
    gym = (ROOT / GYM).read_bytes()
    archives = _source_declarations(adapter)
    budget = _budget(gym)
    loaded = sys.modules.get('data.datasets.alfworld')
    if loaded is not None and getattr(loaded, 'STEP_BUDGET', None) != budget:
        raise ValueError('loaded ALFWorld step budget differs from declaration')
    declared = hashlib.sha256(json.dumps(archives, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=False).encode()).hexdigest()
    metric = f'ALFWorld TextWorld replay any-won; max_steps={budget}'
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    scorer = identities.capture_implementation('scorer', ROOT, SCORER, protocol=metric)
    for key, raw in (('adapter', adapter), ('gym', gym)):
        digest = hashlib.sha256(raw).hexdigest()
        if (loader['details']['components'][key] != digest
                or scorer['details']['components'][key] != digest):
            raise ValueError('ALFWorld shared source changed during identity capture')
    return {'data_source': identities.data_source('alfworld',
                origin='ALFWorld declared source and game archives',
                revision='archive-declarations-sha256:' + declared),
            'loader': loader, 'scorer': scorer}
