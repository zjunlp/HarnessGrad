"""Stdlib controller and first-entry admission for CostBench preparation."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.abc
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

ASSETS = Path(__file__).resolve().parent


def source_hash():
    files = sorted(ASSETS.rglob('*.py')) + sorted((ASSETS.parent / 'costbench_dynamic').rglob('*.py'))
    files.extend([ASSETS / 'source-files.json', ASSETS.parent / 'costbench.py', ASSETS.parent / 'costbench_dynamic.py'])
    h = hashlib.sha256()
    for path in files:
        h.update(str(path.relative_to(ASSETS.parent)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


class SourceGuard(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile checked source directly; never consult ambient env modules or pyc."""
    def __init__(self, bundle):
        self.bundle = Path(bundle)
        self.pins = json.loads((ASSETS / 'source-files.json').read_text())

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'env' and not fullname.startswith('env.'):
            local = ASSETS / 'lib' / (fullname + '.py')
            if '.' not in fullname and local.is_file():
                return importlib.util.spec_from_file_location(fullname, local, loader=self)
            return None
        relative = fullname.replace('.', '/')
        for candidate, package in ((relative + '/__init__.py', True), (relative + '.py', False)):
            if candidate in self.pins:
                file = self.bundle / candidate
                if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != self.pins[candidate]:
                    raise ImportError('CostBench source digest mismatch: ' + candidate)
                return importlib.util.spec_from_file_location(fullname, file, loader=self,
                           submodule_search_locations=[str(file.parent)] if package else None)
        raise ImportError('unlisted CostBench source module: ' + fullname)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path = Path(module.__spec__.origin)
        raw = path.read_bytes()
        if path.parent == ASSETS / 'lib':
            if path.is_symlink():
                raise ImportError('symlinked preparation library')
        else:
            relative = path.relative_to(self.bundle).as_posix()
            if path.is_symlink() or hashlib.sha256(raw).hexdigest() != self.pins[relative]:
                raise ImportError('CostBench source changed: ' + relative)
        exec(compile(raw, str(path), 'exec'), module.__dict__)


def worker(request_path):
    from harnessgrad.dataset_runtime import admit_binding
    request = json.loads(Path(request_path).read_text())
    admit_binding(request['binding'], 'service', python=sys.executable)
    if source_hash() != request['source_sha256']:
        raise RuntimeError('preparation source changed before launch')
    root = Path(request['root'])
    sys.meta_path.insert(0, SourceGuard(root / 'bundle'))
    name = 'costbench_dynamic' if request['dynamic'] else 'costbench'
    file = ASSETS.parent / name / 'prepare_worker.py'
    spec = importlib.util.spec_from_file_location('_costbench_prepare_worker', file)
    module = importlib.util.module_from_spec(spec)
    # Explicit source execution bypasses stale timestamp-valid bytecode.
    exec(compile(file.read_bytes(), str(file), 'exec'), module.__dict__)
    if request['dynamic']:
        module.prepare(root, request['smoke'], output=request['output'])
    else:
        module.prepare(root, request['splits'], source_root=request['source_root'])
    admit_binding(request['binding'], 'service', python=sys.executable)
    if source_hash() != request['source_sha256']:
        raise RuntimeError('preparation source changed during execution')


def main(argv=None, *, dynamic=False):
    from harnessgrad.paths import dataset_root
    from harnessgrad.dataset_runtime import prepare_execution, resolve_execution, bind, admit_binding
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=dataset_root('costbench'))
    parser.add_argument('--install', action='store_true')
    if dynamic:
        parser.add_argument('--smoke', action='store_true')
    else:
        parser.add_argument('--splits', nargs='+', choices=['test', 'train'], default=['train', 'test'])
    args = parser.parse_args(argv)
    if not dynamic and len(set(args.splits)) != len(args.splits):
        parser.error('--splits must not contain duplicates')
    runtime = prepare_execution('costbench') if args.install else resolve_execution('costbench')
    binding = bind(runtime, 'service')
    admit_binding(binding, 'service')
    root = args.root.resolve()
    full_static = not dynamic and set(args.splits) == {'train', 'test'}
    final = root / ('dynamic-smoke-v1' if args.smoke else 'dynamic-prepared-v1') if dynamic else root / ('prepared-v1' if full_static else 'prepared-diagnostic-' + '-'.join(args.splits))
    final.parent.mkdir(parents=True, exist_ok=True)
    # A permanent exclusive claim prevents late concurrent claimants, including
    # after failure. Attempts and all previous output remain available for audit.
    claim = final.parent / ('.' + final.name + '.prepare-reservation')
    if final.exists() or final.is_symlink():
        raise FileExistsError('prepared output exists; choose a fresh root: ' + str(final))
    claim.mkdir()
    stage = Path(tempfile.mkdtemp(prefix='attempt-', dir=claim))
    work = root if dynamic else stage / 'data'
    work.mkdir(parents=True, exist_ok=True)
    if dynamic:
        # Worker output is redirected, leaving static inputs at their original root.
        output = work / final.name
        if output.exists():
            raise FileExistsError(output)
    request = {'binding': binding, 'source_sha256': source_hash(), 'root': str(work),
               'source_root': str(root), 'dynamic': dynamic, 'smoke': getattr(args, 'smoke', False),
               'splits': getattr(args, 'splits', None), 'output': str(stage / 'output')}
    request_path = stage / 'request.json'
    request_path.write_text(json.dumps(request))
    env = child_environment(stage)
    launch = "import sys,pathlib;sys.path.insert(0,sys.argv[1]);p=pathlib.Path(sys.argv[1])/'data/datasets/costbench/prepare_controller.py';d={'__file__':str(p),'__name__':'_prepare_entry'};exec(compile(p.read_bytes(),str(p),'exec'),d);d['worker'](sys.argv[2])"
    result = subprocess.run([binding['python'], '-I', '-B', '-c', launch,
                             str(ASSETS.parents[2]), str(request_path)], env=env, cwd=stage,
                            capture_output=True, text=True)
    for value, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        if value:
            print(redact_network_credentials(value, env), end='', file=stream)
    if result.returncode:
        return result.returncode
    admit_binding(binding, 'service')
    if source_hash() != request['source_sha256']:
        raise RuntimeError('preparation source changed before publication')
    pending = stage / 'output' if dynamic else work
    if not (pending / ('manifest.json' if dynamic else 'prepared-v1/manifest.json')).is_file():
        raise RuntimeError('worker did not produce a manifest')
    validate_output(pending, root, dynamic=dynamic, smoke=getattr(args, 'smoke', False),
                    splits=getattr(args, 'splits', None))
    if not dynamic and not full_static:
        print(json.dumps({'diagnostic': True, 'attempt': str(stage), 'published': False}))
        return 0
    if not dynamic:
        for config in (pending / 'prepared-v1').glob('*-runtime.yaml'):
            config.write_text(config.read_text().replace(str(work), str(root)))
    # Linux renameat2 supplies the no-replace publication boundary, even if a
    # non-cooperating process creates final after our initial check.
    if not dynamic:
        # Dependencies are published first; prepared-v1 is the final readiness gate.
        # Refuse all conflicts up front, then retain each no-replace publication
        # and its permanent claim if a non-cooperating late writer intervenes.
        dependencies = [path for path in pending.iterdir() if path.name != 'prepared-v1']
        for path in dependencies:
            if (root / path.name).exists() or (root / path.name).is_symlink():
                raise FileExistsError(root / path.name)
        for path in dependencies:
            publish(path, root / path.name)
        pending = pending / 'prepared-v1'
    publish(pending, final)
    return 0


def publish(source, destination):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def adapter_constants(dynamic=False):
    """Read independent release pins without importing an adapter or benchmark."""
    name = 'costbench_dynamic.py' if dynamic else 'costbench.py'
    tree = ast.parse((ASSETS.parent / name).read_bytes())
    wanted = {'REVISION', 'ARCHIVE_SHA256', 'MANIFEST_SHA256', 'MODES'}
    return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted}


def checked_file(root, relative, expected=None):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe preparation reference: ' + str(relative))
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('missing or unsafe preparation reference: ' + str(path))
    raw = path.read_bytes()
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('preparation reference digest mismatch: ' + str(path))
    return raw


def validate_output(pending, source_root, *, dynamic, smoke=False, splits=None):
    """Certify release identity and every payload required by the prepared catalog."""
    pending, source_root = Path(pending), Path(source_root)
    folder = pending if dynamic else pending / 'prepared-v1'
    raw = checked_file(folder, 'manifest.json')
    manifest = json.loads(raw)
    constants = adapter_constants(dynamic)
    full = dynamic and not smoke or not dynamic and set(splits) == {'train', 'test'}
    if full and hashlib.sha256(raw).hexdigest() != constants['MANIFEST_SHA256']:
        raise ValueError('independent released preparation manifest digest mismatch')
    counts = expected_counts(dynamic=dynamic, smoke=smoke, splits=splits)
    if (manifest['revision'] != constants['REVISION'] or manifest['archive_sha256'] != constants['ARCHIVE_SHA256']
            or manifest['counts'] != counts):
        raise ValueError('preparation source or inventory mismatch')
    multiplier = len(constants['MODES']) if dynamic else 1
    if dynamic and (manifest['modes'] != list(constants['MODES'])
                    or manifest['static_manifest_sha256'] != adapter_constants()['MANIFEST_SHA256']):
        raise ValueError('dynamic preparation protocol mismatch')
    if dynamic:
        static = json.loads(checked_file(source_root, 'prepared-v1/manifest.json', adapter_constants()['MANIFEST_SHA256']))
        if manifest['sources'] != static['sources']:
            raise ValueError('dynamic sources differ from pinned static preparation')
    tasks = manifest['tasks']
    if len(tasks) != sum(counts.values()) * multiplier or len({row['task_id'] for row in tasks}) != len(tasks):
        raise ValueError('preparation task inventory mismatch')
    audit_root = folder if dynamic else pending
    audit = json.loads(checked_file(audit_root, 'gold-audit.json', manifest['gold_audit_sha256']))
    if (audit['total'] != len(tasks) or audit['passed'] != len(tasks)
            or len(audit['results']) != len(tasks) or any(not row['passed'] for row in audit['results'])
            or {row['task_id'] for row in audit['results']} != {row['task_id'] for row in tasks}):
        raise ValueError('preparation audit is incomplete or failed')
    if audit['revision'] != constants['REVISION']:
        raise ValueError('audit source revision mismatch')
    for split, count in counts.items():
        modes = constants['MODES'] if dynamic else (None,)
        for mode in modes:
            selected = [row for row in tasks if row['split'] == split and (not dynamic or row['mode'] == mode)]
            if len(selected) != count:
                raise ValueError('preparation split/mode inventory mismatch')
            if dynamic and smoke and {row['domain'] for row in selected} != {'location', 'transportation', 'accommodation', 'attraction', 'dining', 'shopping'}:
                raise ValueError('smoke domain inventory mismatch')
    if not dynamic and 'raw_training_audit' in manifest:
        training = json.loads(checked_file(pending, 'raw-train-audit.json'))
        if {key: value for key, value in training.items() if key != 'results'} != manifest['raw_training_audit']:
            raise ValueError('raw training audit receipt mismatch')
    if dynamic and audit.get('smoke') != smoke:
        raise ValueError('dynamic audit diagnostic identity mismatch')
    for row in tasks:
        if row['split'] not in counts:
            raise ValueError('unexpected task split')
        for name in ('public', 'private'):
            checked_file(folder, row['path'] + '/' + name + '.json', row[name + '_sha256'])
        oracle = json.loads(checked_file(folder, row['path'] + '/oracle.json'))
        if not oracle['passed'] or oracle['task_id'] != row['task_id']:
            raise ValueError('missing successful task oracle receipt')
    for split, source in manifest['sources'].items():
        if split not in counts:
            raise ValueError('unexpected source split')
        relative = 'CostBench-' + constants['REVISION'] + '/' + source['path']
        raw_root = source_root if (source_root / relative).exists() else pending
        checked_file(raw_root, relative, source['sha256'])
        filtered = relative.replace('queries.json', 'filtered_queries_with_requirements.json')
        checked_file(raw_root, filtered, source['filtered_sha256'])
        data_root = source_root if dynamic else pending
        index_relative = 'search-spaces/' + split + '/index.json'
        index = json.loads(checked_file(data_root, index_relative, source['search_space_sha256']))
        if index.get('schema') != 'costbench.search-index.v1':
            raise ValueError('unknown search index schema')
        for entry in [index['locations'], index['empty'], *index['location_information'].values()]:
            checked_file(data_root / 'search-spaces' / split, entry['path'], entry['sha256'])
    if set(manifest['sources']) != set(counts):
        raise ValueError('missing preparation source split')
    bundle_root = source_root if dynamic else pending
    for relative, digest in json.loads((ASSETS / 'source-files.json').read_text()).items():
        checked_file(bundle_root / 'bundle', relative, digest)


def expected_counts(*, dynamic, smoke=False, splits=None):
    if dynamic and smoke:
        return {'train': 6, 'test': 6}
    return {split: {'train': 1912, 'test': 381}[split]
            for split in (('train', 'test') if dynamic else splits)}


PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
              'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')


def child_environment(stage):
    keys = ('LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR', *PROXY_KEYS)
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update(PYTHON_DOTENV_DISABLED='1', HOME=str(stage))
    return env


def redact_network_credentials(value, env):
    from urllib.parse import urlsplit, unquote
    secrets = set()
    for key in PROXY_KEYS:
        proxy = env.get(key)
        if not proxy or key.lower() == 'no_proxy':
            continue
        try:
            parsed = urlsplit(proxy if '://' in proxy else '//' + proxy)
            if parsed.username is not None or parsed.password is not None:
                secrets.add(proxy)
                for component in (parsed.username, parsed.password):
                    if component:
                        secrets.update((component, unquote(component)))
        except ValueError:
            # Malformed network settings still must not echo through errors.
            secrets.add(proxy)
    def redact_text(text):
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, '[redacted proxy credential]')
        return text

    lines = []
    for line in value.splitlines(keepends=True):
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            record = None
        # These values are public counters/protocol facts, not credential sinks.
        # Preserve complete validated progress records before matching secrets:
        # a one-character password must never rewrite a counter or public SHA.
        lines.append(line if public_progress(record) else redact_text(line))
    return ''.join(lines)


def public_progress(record):
    if not isinstance(record, dict) or 'total' not in record:
        return False
    counters = {'completed', 'total', 'passed', 'tcr_passed'}
    allowed = counters | {'split', 'mode', 'manifest_sha256', 'revision', 'protocol', 'counts'}
    if not record.keys() <= allowed:
        return False
    for key, value in record.items():
        if key in counters:
            if not (type(value) is int and value >= 0 or key == 'passed' and type(value) is bool):
                return False
        elif key == 'split':
            if value not in ('train', 'test'):
                return False
        elif key == 'mode':
            if value not in ('cost_change', 'steplen_change', 'ban_tool', 'preference_change'):
                return False
        elif key in ('manifest_sha256', 'revision'):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{1,64}', value):
                return False
        elif key == 'counts':
            if (not isinstance(value, dict) or not value.keys() <= {'train', 'test'}
                    or any(type(count) is not int or count < 0 for count in value.values())):
                return False
        elif key == 'protocol':
            expected = {'refinement_level': 2, 'ban_longest_tool': True, 'min_atomic_cost': 15,
                        'max_atomic_cost': 25, 'noise_std': 0.1, 'max_tool_steps': 20,
                        'tool_creation_seed': 42, 'use_blocker': False}
            if value != expected:
                return False
    return True
