"""RoboCasa365 kitchen tasks and the official leaderboard episode pool."""
from __future__ import annotations

from argparse import ArgumentParser
import ctypes.util
from hashlib import sha256
from itertools import chain
import json
import os
from pathlib import Path
from statistics import mean

from data.scaffolds.asset_module import load as load_asset
from data.scaffolds.declarations import TaskDeclarations, once
from harnessgrad.datasets.runtime import bind, check_execution, resolve_execution
from harnessgrad.paths import DataRootNotConfigured, dataset_root

KIND = 'dataset'
VERIFIER_KIND = 'command'
METRIC = 'robocasa365.multitask.task_success.v1'
ORACLE_SOURCE = 'authored'
ORACLE_VALIDATED_BY = 'RoboCasa365 Gym success protocol and sealed scorer controls'
MANAGED_EXECUTION_RUNTIME = 'robocasa365'
SPLIT_SOURCE = 'official-eval'
SPLIT = None
DATASET_ROOT = dataset_root('robocasa365')
ASSETS = Path(__file__).resolve().with_name('robocasa365')
PLATFORM = ASSETS.parents[2]
PROTOCOL = load_asset(ASSETS / 'protocol.py')
BUNDLE = load_asset(ASSETS / 'bundle.py')
_POOL = os.environ.get('HG_ROBOCASA365_POOL', 'leaderboard')
if _POOL not in ('leaderboard', 'all'):
    raise ValueError("HG_ROBOCASA365_POOL must be 'leaderboard' or 'all'")
_ADDITIONAL_CASES = PROTOCOL.catalog_cases() if _POOL == 'all' else ()
CASES = {case['task_id']: case for case in chain(PROTOCOL.cases(), _ADDITIONAL_CASES)}
ORACLE_COVERAGE = f'Protocol controls only. 0 of {len(CASES)} procedural episodes have paired reference actions.'
OFFICIAL_EVAL = [task_id for task_id, case in CASES.items() if case['pool'] == PROTOCOL.LEADERBOARD_POOL]
GOAL = (
    'Control a robot in a RoboCasa365 kitchen to complete the instruction returned by the environment. '
    'Start with `python3 robocasa365_client.py observe --image-dir views` to see the goal, '
    'robot state, and three RGB views. Read `robocasa365_controls.json` for the official '
    'action fields. Use `step ACTION.json` for one action or `step_many CHUNK.json` for up to '
    '32 actions, followed by `--image-dir views` to save the new camera frames. '
    'End your harness when you are done. The simulator scores the movements you actually execute.'
)


def load():
    return ([{'task_id': task_id, 'goal': GOAL, 'inputs': []} for task_id in CASES],
            dict.fromkeys(CASES, METRIC))


def _prepared():
    path = Path(DATASET_ROOT) / BUNDLE.BUNDLE_DIR
    return once(('robocasa365-bundle', path), lambda: (path, BUNDLE.identify(path)))


def _osmesa():
    local = Path(DATASET_ROOT) / 'native/libOSMesa.so.8'
    if local.is_file():
        return str(local.resolve())
    if ctypes.util.find_library('OSMesa') is None:
        raise FileNotFoundError(f'OSMesa is unavailable. Install libosmesa6 or place libOSMesa.so.8 at {local}')
    return None


def _setup(case):
    prepared, bundle = _prepared()
    osmesa = _osmesa()
    runtime = bind(resolve_execution(MANAGED_EXECUTION_RUNTIME), 'service')
    read_paths = [ASSETS, prepared, PLATFORM / 'eval/service/tool_transport.py']
    for name in ('eval/__init__.py', 'eval/service/__init__.py'):
        if (PLATFORM / name).is_file():
            read_paths.append(PLATFORM / name)
    if osmesa:
        read_paths.append(Path(osmesa))
    return {
        'files': [
            {'path': 'robocasa365_client.py', 'from': str(ASSETS / 'cli/client.py')},
            {'path': 'robocasa365_controls.json', 'content': json.dumps({
                'action_shapes': PROTOCOL.ACTION_SIZES, 'range': [-1, 1],
                'gripper_close': 'values >= 0.5 close the gripper',
                'control_mode': 'values >= 0.5 select base-motion mode',
                'action_chunk_max': PROTOCOL.MAX_CHUNK, 'horizon': case['horizon'],
            }, indent=2)},
        ],
        'tool_relay': {
            'program': str(ASSETS / 'relay.py'), 'python': runtime['python'],
            'python_runtime': runtime, 'read_paths': [str(path.resolve()) for path in read_paths],
            'filename': 'case.json', 'content_flag': '--case-spec',
            'content': json.dumps({'case': case, 'prepared': str(prepared), 'bundle': bundle, 'osmesa': osmesa}),
            'ready_timeout_s': 120,
            'max_line_bytes': 1024**2,
            'record': {'path': 'trusted_episode', 'max_bytes': 16 * 1024},
        },
    }


def _verify(case):
    _, bundle = _prepared()
    runtime = bind(resolve_execution(MANAGED_EXECUTION_RUNTIME), 'verifier')
    config = json.dumps({
        'case': case, 'bundle': bundle, 'episode_schema': PROTOCOL.EPISODE_SCHEMA,
        'metric': PROTOCOL.METRIC, 'revision': PROTOCOL.REVISION, 'scene_split': PROTOCOL.SCENE_SPLIT,
    }, sort_keys=True)
    return {
        'kind': 'command', 'python_runtime': runtime, 'runtime_paths': runtime['runtime_paths'],
        'argv': [runtime['python'], '-I', '-B', 'robocasa365_check.py',
                 '--config-sha256', sha256(config.encode()).hexdigest()],
        'inputs': [
            {'path': 'robocasa365_check.py', 'from': str(ASSETS / 'verify/check.py')},
            {'path': 'robocasa365_case.json', 'content': config},
        ],
        'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
        'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True},
        'scorer_bundle': {'isolation_class': 'actor-workdir'},
        'trial_evidence': {'version': 1},
    }


SETUP = TaskDeclarations(CASES.values(), _setup)
VERIFY = TaskDeclarations(CASES.values(), _verify)
DEFAULT_ENV = {'kind': 'files', 'network': 'none'}


def downloads():
    return [
        *[{key: value for key, value in entry.items() if key not in ('package', 'extract_into')}
          for entry in BUNDLE.ARCHIVES],
        {'kind': 'generated', 'name': 'Prepared RoboCasa365 simulator and assets', 'version': BUNDLE.BUNDLE_DIR,
         'dst': f'{BUNDLE.BUNDLE_DIR}/{BUNDLE.RECEIPT}', 'sha256': BUNDLE.RECEIPT_SHA256,
         'prepare': 'python3 data/datasets/robocasa365/prepare.py', 'size': 'manifest and extracted kitchen assets',
         'note': 'OSMesa is also required. See the adapter README for local setup.'},
    ]


def provision_status():
    try:
        root = str(DATASET_ROOT)
    except DataRootNotConfigured as exc:
        return {'present': False, 'status': 'config_missing', 'detail': str(exc)}
    execution = check_execution(MANAGED_EXECUTION_RUNTIME)
    result = {'root': root, 'present': False, 'execution_runtime': execution}
    try:
        _, bundle = _prepared()
        _osmesa()
    except (OSError, ValueError, KeyError) as exc:
        return {**result, 'status': 'data_missing' if isinstance(exc, FileNotFoundError) else 'invalid',
                'detail': str(exc)}
    return {**result, 'present': execution['present'], 'status': execution['status'],
            'detail': execution['detail'], 'bundle': bundle, 'tasks': len(CASES)}


def aggregate(per_task):
    """Leaderboard macro and group scores from a curve point's measured rewards."""
    grouped = {group: {} for group in PROTOCOL.GROUPS}
    for task_id, score in per_task.items():
        case = CASES[task_id]
        if case['pool'] != PROTOCOL.LEADERBOARD_POOL or type(score) not in (float, int) or not 0 <= score <= 1:
            raise ValueError(f'expected a measured leaderboard reward for {task_id}')
        grouped[case['group']].setdefault(case['task'], []).append(score)
    by_group = {}
    task_means = []
    for group, tasks in grouped.items():
        means = [mean(scores) for scores in tasks.values()]
        task_means.extend(means)
        by_group[group] = {
            'success_rate': mean(means) if means else None,
            'classes_measured': len(means), 'classes_expected': len(PROTOCOL.GROUPS[group]),
            'rollouts_measured': sum(map(len, tasks.values())),
        }
    return {
        'success_rate': mean(task_means) if task_means else None, 'groups': by_group,
        'rollouts_measured': len(per_task), 'rollouts_expected': len(OFFICIAL_EVAL),
        'complete': len(per_task) == len(OFFICIAL_EVAL),
    }


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument('--pool', choices=(PROTOCOL.LEADERBOARD_POOL, PROTOCOL.CATALOG_POOL))
    commands.add_argument('--aggregate', type=Path, metavar='CURVE_POINT_JSON')
    args = parser.parse_args()
    if args.pool == PROTOCOL.CATALOG_POOL:
        for case in PROTOCOL.catalog_cases():
            print(case['task_id'])
    elif args.pool:
        for task_id, case in CASES.items():
            if case['pool'] == args.pool:
                print(task_id)
    else:
        point = json.loads(args.aggregate.read_text())
        print(json.dumps(aggregate(point['per_task']), indent=2))
