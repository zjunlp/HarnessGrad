"""ScienceWorld source declarations and conservative implementation identities.

The public fold and study files are projections, not verified upstream downloads.
Selected task inputs, the world JAR and actual runtimes are bound separately.
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
ADAPTER = 'data/datasets/scienceworld.py'
ASSETS = 'data/datasets/scienceworld/'
ENVIRONMENT = ASSETS + 'lib/scw_env.py'
WORLD = ASSETS + 'runtime.py'
FOLD = ASSETS + 'test_fold.json'
STUDY = ASSETS + 'study_300.json'
REQUIREMENTS = ASSETS + 'requirements.lock'
LOADER = {'adapter': ADAPTER, 'environment': ENVIRONMENT, 'world': WORLD,
          'declarations': 'data/scaffolds/declarations.py', 'dataset_paths': 'harnessgrad/paths.py',
          'scorer_input': 'data/scaffolds/scorer_input.py', 'native_binding': 'data/scaffolds/public_native.py'}
SCORER = {'adapter': ADAPTER, 'environment': ENVIRONMENT, 'world': WORLD,
          'check': ASSETS + 'verify/check.py', 'scorer_input': 'data/scaffolds/scorer_input.py',
          'native_binding': 'data/scaffolds/public_native.py'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate ScienceWorld declaration key')
        result[key] = value
    return result


def _constant(raw, name):
    try:
        return literal_assignments(raw, {name})[name]
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid ScienceWorld source declaration') from exc


def _adapter_package_version(raw):
    try:
        tree = ast.parse(raw)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'downloads']
        if len(functions) != 1:
            raise ValueError('missing downloads declaration')
        returns = [node for node in functions[0].body if isinstance(node, ast.Return)]
        if len(returns) != 1 or not isinstance(returns[0].value, ast.List):
            raise ValueError('invalid downloads declaration')
        versions = []
        for item in returns[0].value.elts:
            if not isinstance(item, ast.Dict):
                continue
            pairs = {key.value: value for key, value in zip(item.keys, item.values)
                     if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            package = pairs.get('package')
            if isinstance(package, ast.Constant) and package.value == 'scienceworld':
                versions.append(ast.literal_eval(pairs['version']))
        if len(versions) != 1 or not isinstance(versions[0], str) or not versions[0]:
            raise ValueError('invalid package version')
        return versions[0]
    except (KeyError, SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid ScienceWorld package declaration') from exc


def _rows(value):
    if not isinstance(value, list) or not value:
        raise ValueError('invalid ScienceWorld task inventory')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get('task_name'), str) or not row['task_name']:
            raise ValueError('invalid ScienceWorld task declaration')
        variation, moves = row.get('variation'), row.get('gold_moves')
        if type(variation) is not int or variation < 0 or type(moves) is not int or moves < 0:
            raise ValueError('invalid ScienceWorld task declaration')
        key = (row['task_name'], variation)
        if key in seen:
            raise ValueError('duplicate ScienceWorld task declaration')
        seen.add(key)
        result.append({'task_name': key[0], 'variation': variation, 'gold_moves': moves})
    return result


def _declarations(fold_raw, study_raw, jar, package_version):
    try:
        fold = json.loads(fold_raw, object_pairs_hook=_unique)
        study = json.loads(study_raw, object_pairs_hook=_unique)
        archive, study_archive = fold['archive'], study['archive']
        origin, digest = archive['url'], archive['sha256']
        if (not isinstance(origin, str) or not origin.startswith('https://github.com/allenai/ScienceWorld/')
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or study_archive['sha256'] != digest or fold['eval_fold'] != 'test'
                or archive['package_version'] != package_version
                or study_archive['package_version'] != package_version):
            raise ValueError('inconsistent source declaration')
        pools = study['pools']
        if not isinstance(pools, dict) or set(pools) != {'train', 'dev', 'test'}:
            raise ValueError('invalid study pools')
        selected = {'archive_sha256': digest, 'package_version': package_version, 'world_jar_sha256': jar,
                    'eval_fold': fold['eval_fold'], 'tasks': _rows(fold['tasks']),
                    'study_pools': {name: _rows(pools[name]) for name in sorted(pools)}}
        return origin, digest, selected
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid ScienceWorld provenance declaration') from exc


def profile():
    adapter = (ROOT / ADAPTER).read_bytes()
    environment = (ROOT / ENVIRONMENT).read_bytes()
    world = (ROOT / WORLD).read_bytes()
    fold_raw = (ROOT / FOLD).read_bytes()
    study_raw = (ROOT / STUDY).read_bytes()
    requirements = (ROOT / REQUIREMENTS).read_text().splitlines()
    versions = [line.split('==', 1)[1].strip() for line in requirements if line.startswith('scienceworld==')]
    jar = _constant(world, 'JAR_SHA256')
    budget = _constant(environment, 'STEP_BUDGET')
    if (len(versions) != 1 or not versions[0] or _adapter_package_version(adapter) != versions[0]
            or not isinstance(jar, str)
            or not re.fullmatch(r'[0-9a-f]{64}', jar) or type(budget) is not int or budget < 1):
        raise ValueError('invalid ScienceWorld runtime declaration')
    origin, archive_sha, declaration = _declarations(fold_raw, study_raw, jar, versions[0])
    loaded = sys.modules.get('data.datasets.scienceworld')
    if loaded is not None and getattr(loaded, 'STEP_BUDGET', None) != budget:
        raise ValueError('loaded ScienceWorld step budget differs from declaration')
    if loaded is not None and getattr(loaded, '_FOLD_CACHE', None) is not None:
        try:
            cached = [{key: row[key] for key in ('task_name', 'variation', 'gold_moves')}
                      for row in loaded._FOLD_CACHE]
        except (TypeError, KeyError) as exc:
            raise ValueError('invalid loaded ScienceWorld fold') from exc
        if cached != declaration['tasks']:
            raise ValueError('loaded ScienceWorld fold differs from declaration')
    if loaded is not None and getattr(loaded, '_STUDY_CACHE', None) is not None:
        cache = loaded._STUDY_CACHE
        pool = getattr(loaded, '_STUDY_POOL', None)
        if not isinstance(cache, dict) or pool not in ('dev', 'test') or cache.get('eval_fold') != pool:
            raise ValueError('loaded ScienceWorld study selection differs from declaration')
        for side, fold in (('train', 'train'), ('eval', pool)):
            expected = [{'task_id': f"{row['task_name']}/{row['variation']}", **row}
                        for row in declaration['study_pools'][fold]]
            if cache.get(side) != expected:
                raise ValueError('loaded ScienceWorld study selection differs from declaration')
    encoded = json.dumps(declaration, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    metric = f'ScienceWorld final score clamped to 0..100 / 100; max_steps={budget}'
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    scorer = identities.capture_implementation('scorer', ROOT, SCORER, protocol=metric)
    for key, raw in (('adapter', adapter), ('environment', environment), ('world', world)):
        digest = hashlib.sha256(raw).hexdigest()
        if loader['details']['components'][key] != digest or scorer['details']['components'][key] != digest:
            raise ValueError('ScienceWorld shared source changed during identity capture')
    if ((ROOT / FOLD).read_bytes() != fold_raw or (ROOT / STUDY).read_bytes() != study_raw
            or (ROOT / REQUIREMENTS).read_text().splitlines() != requirements):
        raise ValueError('ScienceWorld declaration changed during identity capture')
    return {'data_source': identities.data_source('scienceworld', origin=origin,
                revision='declared-inventory-sha256:' + hashlib.sha256(encoded).hexdigest(),
                declared_artifact_sha256=archive_sha), 'loader': loader, 'scorer': scorer}
