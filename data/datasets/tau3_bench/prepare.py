#!/usr/bin/env python3
"""Prepare the pinned τ³ text base split; never execute a model request."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from harnessgrad.paths import dataset_root

REVISION = 'fc0055dc4e0a316c3f83133267fbd6faaa770992'
ARCHIVE_SHA256 = '7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e'
DOMAINS = {'airline': 50, 'retail': 114, 'telecom': 114, 'banking_knowledge': 97}
ASSETS = Path(__file__).resolve().parent
import hashlib as _source_hashlib
if '__executed_source_sha256__' not in globals():
    __executed_source_sha256__ = _source_hashlib.sha256(ASSETS.joinpath('prepare.py').read_bytes()).hexdigest()


def _controller():
    import hashlib
    import types
    path = ROOT / 'tools/_tau_prepare.py'
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ValueError('Tau preparation controller requires a physical source file')
    raw = path.read_bytes()
    identity = globals().get('__tau_source_identity__')
    if identity and hashlib.sha256(raw).hexdigest() != identity['tools/_tau_prepare.py']:
        error = ValueError('Tau preparation controller source changed')
        error.preparation_source_changed = True
        raise error
    module = types.ModuleType('_tau_preparation_controller')
    module.__file__ = str(path)
    module.__dict__['__tau_source_identity__'] = identity
    module.__dict__['__executed_source_sha256__'] = hashlib.sha256(raw).hexdigest()
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    if identity is None:
        identity = module.source_identity()
        if identity['data/datasets/tau3_bench/prepare.py'] != __executed_source_sha256__:
            raise module.PreparationSourceChanged('Tau preparation entry source changed')
        globals()['__tau_source_identity__'] = identity
        module.__dict__['__tau_source_identity__'] = identity
    module.verify_sources(identity)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n')


def validate_native_splits(source):
    """Check the frozen train/test IDs against the pinned upstream files."""
    spec = json.loads((ASSETS / 'specs/task-splits.json').read_text())
    if spec['revision'] != REVISION:
        raise ValueError('tau3 native split revision mismatch')
    for domain, expected in spec['domains'].items():
        path = source / expected['path']
        actual = json.loads(path.read_text())
        if (digest(path) != expected['sha256']
                or any(actual[side] != expected[side] for side in ('train', 'test'))
                or set(actual['train']) & set(actual['test'])
                or set(actual['train']) | set(actual['test']) != set(actual['base'])):
            raise ValueError(f'{domain}: official train/test split mismatch')


def prepare(source, target):
    """source is extracted from the digest-checked official archive."""
    validate_native_splits(source)
    if target.exists() or target.is_symlink():
        raise FileExistsError('use a fresh preparation root')
    target.mkdir(parents=True)
    bundle = target / 'bundle'
    if bundle.exists():
        raise FileExistsError('use a fresh preparation root')
    shutil.copytree(source / 'src', bundle / 'src')
    shutil.copy2(source / 'LICENSE', bundle / 'LICENSE')
    # Runtime contains business databases/policy and user guidelines, never task
    # sets, reference trajectories, release results or other benchmark solutions.
    shutil.copytree(source / 'data/tau2/user_simulator', bundle / 'data/tau2/user_simulator')
    for domain in DOMAINS:
        src = source / 'data/tau2/domains' / domain
        dst = bundle / 'data/tau2/domains' / domain
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns('*tasks*', '*trajectory*', '*trajectories*', '__pycache__'))
    files = {str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
    write(bundle / 'manifest.json', {'revision': REVISION, 'files': files})
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['TAU2_DATA_DIR'] = str(bundle / 'data')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    sys.path.insert(0, str(bundle / 'src'))
    sys.path.insert(0, str(ASSETS / 'lib'))
    from loguru import logger
    logger.remove()
    from tau2.data_model.tasks import Task
    _get_tau2_domain_constructor = _controller().owned_module(ASSETS / 'lib/runtime.py',
        '_tau3_preparation_runtime', globals().get('__tau_source_identity__'))._get_tau2_domain_constructor
    rows = []
    for domain, count in DOMAINS.items():
        src = source / 'data/tau2/domains' / domain
        if domain == 'banking_knowledge':
            tasks = [json.loads(p.read_text()) for p in sorted((src / 'tasks').glob('task_*.json'))]
        else:
            all_tasks = json.loads((src / 'tasks.json').read_text())
            by_id = {str(t['id']): t for t in all_tasks}
            selected = json.loads((src / 'split_tasks.json').read_text())['base']
            if len(set(selected)) != len(selected):
                raise ValueError('duplicate official split task')
            tasks = [by_id[str(tid)] for tid in selected]  # missing IDs fail closed
        if len(tasks) != count:
            raise ValueError(f'{domain}: expected {count} official base tasks, got {len(tasks)}')
        for raw in tasks:
            task = Task.model_validate(raw)
            tid = f'tau3_bench/{domain}/{task.id}'
            config = {'revision': REVISION, 'task_id': tid, 'domain': domain,
                      'task': raw, 'retrieval_variant': 'bm25', 'max_steps': 200, 'max_errors': 10}
            constructor, kwargs = _get_tau2_domain_constructor(domain, config, task)
            env = constructor(solo_mode=False, **kwargs)
            public = {'task_id': tid, 'domain': domain, 'policy': env.get_policy(),
                      'tools': [t.openai_schema for t in env.get_tools()]}
            users = (env.get_user_tools(include=task.user_tools) or []) if env.user_tools is not None else []
            relative = f'cases/{domain}/{task.id}'
            write(target / relative / 'private.json', config)
            write(target / relative / 'public.json', public)
            rows.append({'task_id': tid, 'domain': domain, 'path': relative,
                         'private_sha256': digest(target / relative / 'private.json'),
                         'public_sha256': digest(target / relative / 'public.json'),
                         'user_tools': [t.openai_schema for t in users],
                         'needs_judge': 'NL_ASSERTION' in [str(x).upper() for x in raw['evaluation_criteria']['reward_basis']]})
    write(target / 'manifest.json', {'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256,
                                    'counts': DOMAINS, 'tasks': rows})
    print(json.dumps({'tasks': len(rows), 'manifest_sha256': digest(target / 'manifest.json')}))


def _check_payload(target):
    controller = _controller()
    identity = globals().get('__tau_source_identity__')
    module = controller.owned_module(ASSETS / 'lib/bootstrap.py', 'tau3_preparation_bootstrap', identity)
    module.bootstrap(target / 'bundle')
    dataset = controller.owned_module(ROOT / 'data/datasets/tau3_bench.py', '_tau3_prepare_dataset', identity)
    dataset.DATASET_ROOT = target
    dataset.roster.cache_clear()
    rows = dataset.roster()
    for row in rows.values():
        for half in ('public', 'private'):
            path = target / row['path'] / (half + '.json')
            if path.is_symlink() or digest(path) != row[half + '_sha256']:
                raise ValueError('tau3 prepared case digest mismatch')
    return {'tasks': len(rows), 'manifest_sha256': digest(target / 'manifest.json')}


def _managed_operation(operation, options, runtime):
    target = Path(options['target'])
    if operation == 'check' or options.get('reuse'):
        return {**_check_payload(target), 'check_only': True}
    with tempfile.TemporaryDirectory(prefix='tau3-prepare-') as tmp:
        if options.get('fetch'):
            raise ValueError('source fetch must be completed by the standard-library controller')
        archive = Path(options['archive'])
        if digest(archive) != ARCHIVE_SHA256:
            raise ValueError('official source archive digest mismatch')
        with tarfile.open(archive) as tar:
            members = [m for m in tar if '/data/tau2/results/' not in m.name and '/data/voice/' not in m.name]
            tar.extractall(tmp, members=members, filter='data')
        prepare(Path(tmp) / ('tau2-bench-' + REVISION), target)
    return _check_payload(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=dataset_root('tau3_bench'))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--runtime', help='must name the selected managed execution artifact')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--reuse-bundle', action='store_true', help='validate an existing payload; never repair it')
    parser.add_argument('--prepare-runtime', action='store_true', help='explicitly build the managed execution artifact')
    args = parser.parse_args()
    from harnessgrad import dataset_runtime
    if args.prepare_runtime and (args.check_only or args.reuse_bundle or args.runtime or args.archive or args.fetch):
        parser.error("--prepare-runtime is a separate action")
    if args.prepare_runtime:
        print(json.dumps(dataset_runtime.prepare_execution('tau3_bench'), sort_keys=True))
        return 0
    if args.archive is None and not args.fetch and not (args.check_only or args.reuse_bundle):
        parser.error('supply --archive or --fetch')
    run = _controller().run
    try:
        result = run('tau3_bench', 'check' if args.check_only else 'prepare',
                     {'target': str(args.root), 'archive': str(args.archive) if args.archive else None,
                      'fetch': args.fetch, 'reuse': args.reuse_bundle}, runtime_override=args.runtime)
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        print('tau3 preparation failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
