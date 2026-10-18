"""CooperBench: fixed two-peer coding, pinned Harbor merged-pair success.

All 652 released pairs have stable identities. Images are prepared explicitly per
base task; missing images are provisioning failures, never silently dropped tasks.
Related feature pairs share a grouped study/eval assignment.
"""
from __future__ import annotations
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

KIND = 'dataset'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT_SOURCE = 'hash'
ASSETS = Path(__file__).with_suffix('')
TRANSPORT = Path(__file__).resolve().parents[2] / 'eval/service/tool_transport.py'
DATASET_ROOT = Path(os.environ.get('HG_COOPERBENCH_DATA', Path(__file__).resolve().parents[3] / 'datasets/cooperbench'))
MANIFEST_SHA256 = '3accea49615abcc01f65d366e5717fe27d39626bd59e3f8c331297fc60d5b072'
REVISION = '63b9d44d9f39a02fccf5bf0052db48a917a011fd'
HARBOR_REVISION = '3e9f14a7c1033896cc600d27de69d6948bcb3410'
METRIC = 'cooperbench.harbor.merged_pair_success.v1'
DEFAULT_ENV = {'kind': 'files', 'agent_timeout_s': 1800, 'verify_timeout_s': 1800}
GOAL = '''Implement your assigned feature in /workspace/repo. A second independent
peer implements another feature against the same base. Coordinate using:
  python3 /workspace/cooperbench.py send_message {peer} 'message'
  python3 /workspace/cooperbench.py check_messages
Use the other worker ID as recipient (agent1 or agent2). Messages are ordered;
check_messages drains your inbox. There is no shared filesystem or git server.
Both peers execute the candidate harness. Do not edit tests to change scoring.
When done run `python3 /workspace/cooperbench.py submit` to export agent.patch.
Only that patch is collected; text answers and unsubmitted repository changes
are not graded. The verifier filters test-file diffs, merges both patches using
Harbor naive/union rules, then runs both private feature test suites. Reward is
1 only if both pass. Communication is optional; the fixed platform bound is
4096 messages per pair, 64 KiB each, 8192 mailbox calls per worker.
'''


def checked(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'CooperBench prepared bytes changed: {path}')
    return data.decode()


@lru_cache(maxsize=1)
def roster():
    manifest = json.loads(checked(DATASET_ROOT / 'prepared-v1/manifest.json', MANIFEST_SHA256))
    if manifest['source_revision'] != REVISION or manifest['harbor_revision'] != HARBOR_REVISION:
        raise ValueError('CooperBench source identity mismatch')
    return {r['task_id']: r for r in manifest['tasks']}


def task_file(row, rel):
    return checked(DATASET_ROOT / 'prepared-v1' / row['path'] / rel, row['files_sha256'][rel])


def runtime(row):
    folder = DATASET_ROOT / 'runtimes' / row['group'].replace('/', '-')
    config = json.loads((folder / 'runtime.json').read_text())
    if config['group'] != row['group'] or config['identity']['base_commit'] != row['base_commit']:
        raise ValueError('CooperBench runtime group/base mismatch')
    checked(folder / 'requirements.freeze.txt', config['dependencies_sha256'])
    checked(folder / 'Dockerfile', config['dockerfile_sha256'])
    if config.get('preparation_sha256'):
        checked(folder / 'prepare-dependencies.sh', config['preparation_sha256'])
    return config, folder


def runtime_spec(row):
    config, _ = runtime(row)
    return {'kind': 'exec', 'backend': 'observed-v1', 'image': config['image'],
            'python': config.get('python', '/usr/local/bin/python3'), 'workdir': '/workspace',
            'image_workspace': '/workspace', 'network': 'none', 'cpus': 2,
            'memory_mb': config.get('resources', {}).get('memory_mb', 4096), 'pids': 512,
            'filesystems': {'/workspace': {'bytes': config.get('resources', {}).get('workspace_bytes', 2 << 30),
                                          'inodes': config.get('resources', {}).get('workspace_inodes', 200000)}}}


def control_task(task_id):
    row = roster()[task_id]
    return {'task_id': task_id, 'goal': f"CooperBench merged pair {row['group']}", 'inputs': []}


def load():
    return [control_task(t) for t in roster()], {t: METRIC for t in roster()}


def _setup(tid, row):
    features = json.loads(task_file(row, 'public.json'))
    spec = runtime_spec(row)
    workers = []
    for i, feature in enumerate(features, 1):
        workers.append({'id': f'agent{i}', 'goal': f'You are agent{i}; your peer is agent{3-i}.\n' + GOAL.format(peer=f'agent{3-i}') + '\n' + feature['description'],
                        'env': dict(spec), 'artifacts': ['agent.patch'], 'timeout_s': 1800,
                        'files': [{'path': 'cooperbench.py', 'from': str(ASSETS / 'cli/cooperbench.py')},
                                  {'path': 'cooperbench_transport.py', 'from': str(TRANSPORT)},
                                  {'path': 'public.json', 'content': json.dumps({**feature, 'base_commit': row['base_commit']})}]})
    return {'workers': workers}


def _verify(tid, row):
    config, folder = runtime(row)
    inputs = [{'path': 'check.py', 'from': str(ASSETS / 'verify/check.py')}]
    private = {'test.sh': task_file(row, 'test.sh'),
               'runner.sh': checked(folder / 'runner.sh', config['runner_sha256']),
               'tests/tests1.patch': task_file(row, 'tests/tests1.patch'),
               'tests/tests2.patch': task_file(row, 'tests/tests2.patch')}
    if 'tests/testfix.patch' in row['files_sha256']:
        private['tests/testfix.patch'] = task_file(row, 'tests/testfix.patch')
    case = {'task_id': tid, 'base_commit': row['base_commit'],
            'private_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in private.items()},
            'provenance': {'source_revision': REVISION, 'harbor_revision': HARBOR_REVISION,
                           'manifest_sha256': MANIFEST_SHA256, 'runtime': config}}
    inputs += [{'path': 'private/' + name, 'content': text} for name, text in private.items()]
    inputs += [{'path': 'private/case.json', 'content': json.dumps(case)}]
    return {'kind': 'command', 'argv': [config.get('python', '/usr/local/bin/python3'), 'check.py'], 'inputs': inputs,
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'timeout_s': 1800, 'container_runtime': runtime_spec(row),
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}


class _Tasks(Mapping):
    def __init__(self, build):
        self.build = build

    def __iter__(self):
        return iter(roster())

    def __len__(self):
        return len(roster())

    def __getitem__(self, task_id):
        return self.build(task_id, roster()[task_id])


ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))
SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verify)


def oracle_files(task):
    row = roster()[task['task_id']]
    combined = task_file(row, 'solution/combined.patch')
    return {f'workers/{peer}/agent.patch': combined for peer in ('agent1', 'agent2')}


def downloads():
    return [{'kind': 'http', 'name': 'CooperBench pinned source and released feature pairs',
             'version': REVISION, 'url': f'https://codeload.github.com/cooperbench/CooperBench/tar.gz/{REVISION}',
             'sha256': 'f2af4c7d721987e008e01a03a1f69500a448f7c273cc2bd91e6e093f09c6a8e6',
             'size': '1693222 bytes; 199 features, 652 pairs', 'dst': 'source.tar.gz',
             'note': 'Prepare with the pinned Harbor checkout, then import explicit per-group Docker images. Runtime digests, package versions and transformed runner hashes are recorded outside the source tree; measurements never download dependencies or pull; editable builds remain offline.'}]


def provision_status():
    try:
        rows = roster()
        groups = {r['group'] for r in rows.values()}
        ready = sum((DATASET_ROOT / 'runtimes' / g.replace('/', '-') / 'runtime.json').is_file() for g in groups)
        return {'root': str(DATASET_ROOT), 'present': ready == len(groups), 'tasks': len(rows),
                'prepared_groups': sorted(g for g in groups if (DATASET_ROOT / 'runtimes' / g.replace('/', '-') / 'runtime.json').is_file()),
                'unprepared_groups': sorted(g for g in groups if not (DATASET_ROOT / 'runtimes' / g.replace('/', '-') / 'runtime.json').is_file()),
                'validation': 'Each prepared image still requires explicit real oracle/noop controls; preparation alone is not validation',
                'detail': f'{ready}/{len(groups)} base-task runtimes prepared; all pair identities retained'}
    except (OSError, ValueError) as exc:
        return {'root': str(DATASET_ROOT), 'present': False, 'detail': str(exc)}


def __getattr__(name):
    if name == 'SPLIT':
        # Group related pairs before hashing; this is an adapter split, not an
        # upstream heldout designation. The official flash subset is dev-only.
        result = {'train': [], 'eval': []}
        for tid, row in roster().items():
            side = 'eval' if int(hashlib.sha256(row['group'].encode()).hexdigest(), 16) % 5 == 0 else 'train'
            result[side].append(tid)
        return result
    raise AttributeError(name)
