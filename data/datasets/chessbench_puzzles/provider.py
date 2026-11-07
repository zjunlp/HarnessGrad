"""Private provider-side board transitions; the pinned scorer stays unchanged."""
import os
import sys

from data.datasets import chessbench_puzzles as adapter


def resolve(task_ids):
    if task_ids is None:
        records = adapter._SPEC.ensure()
    else:
        records = adapter._selected_records(task_ids) if task_ids else []
    return {
        'tasks': [{'task_id': row['task_id'],
                   'goal': adapter._goal(row['question'], row['gold']), 'inputs': []}
                  for row in records],
        'setups': {row['task_id']: adapter.setup_for(row) for row in records},
        'verifiers': {row['task_id']: adapter.verify_for(row) for row in records},
        'envs': {row['task_id']: {'kind': 'files'} for row in records}}


def stepwise(payload):
    if payload['contract'] != adapter.STEPWISE_CONTRACT:
        raise ValueError('unsupported stepwise contract')
    try:
        session = adapter.StepwisePuzzle(payload['gold'])
        for answer in payload['answers']:
            session.submit(answer)
        public = session.public_task()
    except Exception:
        raise ValueError('invalid trusted stepwise input') from None
    return {'observation': public, 'provider_observation': {
        'python': sys.executable, 'prefix': sys.prefix,
        'isolated': bool(sys.flags.isolated), 'no_bytecode': sys.dont_write_bytecode,
        'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
        'package_versions': {'chess': adapter.chess.__version__},
        'package_origins': {'chess': adapter.chess.__file__}}}
