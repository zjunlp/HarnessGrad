"""CostBench sequence-5 with one official dynamic intervention per episode."""
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys

KIND = 'dataset'
ORACLE_SOURCE = 'released'
SPLIT_SOURCE = 'declared'
ASSETS = Path(__file__).resolve().parent / 'costbench'
#: Absolute, like every other adapter's: `parents[3]` resolved to the directory *above* the
#: repository, so the default only worked in the checkout that wrote it.
DATASET_ROOT = Path(os.environ.get('HG_COSTBENCH_DATA',
                                   Path('/mnt/20t/xubuqiang/datasets/costbench')))
PREPARED = DATASET_ROOT / 'dynamic-prepared-v1'
RUNTIME = Path(os.environ.get('HG_COSTBENCH_RUNTIME', DATASET_ROOT / 'runtime'))
REVISION = '6424069f3f773ade076bcedbed302ed47cfb1837'
ARCHIVE_SHA256 = 'b61cc4923662e63b0756a309aa2988487350036cecea93d674e3defc62404844'
MANIFEST_SHA256 = 'a424801c3d156bfc9527534e715e40ed02cd812889ae0d1c711c9d8c792e4c14'
MODES = ('cost_change', 'steplen_change', 'ban_tool', 'preference_change')

def metric(mode):
    if mode not in MODES:
        raise ValueError('unknown CostBench dynamic mode')
    return f'costbench.dynamic.sequence5.{mode}.block1.per_query_path_exact_match.v1'

TRANSPORT = Path(__file__).resolve().parents[2] / 'eval/service/tool_transport.py'
DEFAULT_ENV = {'kind': 'files'}
GOAL = '''Complete the CostBench travel task using a minimum-total-cost tool path.
Read `python3 costbench.py show` for the official task, instructions and tool schemas.
Invoke a tool with `python3 costbench.py call TOOL_NAME '{"ParameterType":"value"}'`.
This is a dynamic episode: costs, tool availability, or user preferences may change.
The tool response includes its execution cost and the current tools after changes.
Run `python3 costbench.py show` again to inspect the current instructions and schemas.
Follow any new user message returned by the service; earlier preferences can be superseded.
Send your final answer through `python3 costbench.py submit '<answer> <CandidateID> </answer>'`.
For a full assistant turn, `python3 costbench.py turn '{"content":null,"calls":[{"name":"TOOL","arguments":{}}]}'`
preserves simultaneous text/tool submission. Only the first tool in a turn executes.
Use `python3 costbench.py status` to inspect the official 20-turn limit and termination.
Showing schemas and checking status do not consume a turn. Submitting an answer does.
Use IDs and parameter values exactly as given. The primary metric compares your
valid executed tool path with the minimum-cost reference path; answer correctness,
tool costs and invalid calls are reported separately. answer.txt cannot replace
tool execution or final submission through the service. Finish the harness after submission.
'''


@lru_cache(maxsize=1)
def manifest():
    raw = (PREPARED / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('CostBench manifest digest mismatch; run pinned preparation')
    result = json.loads(raw)
    if (result['revision'] != REVISION or result['archive_sha256'] != ARCHIVE_SHA256
            or result['counts'] != {'train': 1912, 'test': 381}
            or result.get('modes') != list(MODES)
            or result.get('static_manifest_sha256') != '7bcc4ba0bc653229b5536f625d797217b3b48f31764314d9d2a397e2e8332587'):
        raise ValueError('CostBench source or split inventory mismatch')
    return result


@lru_cache(maxsize=1)
def roster():
    rows = manifest()['tasks']
    indexed = {row['task_id']: row for row in rows}
    if len(rows) != 9172 or len(indexed) != len(rows):
        raise ValueError('CostBench duplicate or missing task identities')
    return indexed


def control_task(tid):
    if tid not in roster():
        raise KeyError(tid)
    return {'task_id': tid, 'goal': GOAL, 'inputs': []}


def load():
    return [control_task(tid) for tid in roster()], {tid: metric(row['mode']) for tid, row in roster().items()}


def _setup(tid, row):
    folder = PREPARED / row['path']
    public = (folder / 'public.json').read_bytes()
    if hashlib.sha256(public).hexdigest() != row['public_sha256']:
        raise ValueError('CostBench public task digest mismatch')
    return {'files': [{'path': 'costbench.py', 'from': str(ASSETS / 'cli/dynamic.py')},
                      {'path': 'costbench_transport.py', 'from': str(TRANSPORT.resolve())},
                      {'path': 'costbench_public.json', 'content': public.decode()}],
            'tool_relay': {'program': str(ASSETS / 'relay.py'), 'python': str(RUNTIME / 'bin/python'),
                           'content_flag': '--case-spec', 'filename': 'case-spec.json',
                           'content': json.dumps({'case': str(folder / 'private.json'),
                                                 'sha256': row['private_sha256'],
                                                 'public': str(folder / 'public.json'),
                                                 'public_sha256': row['public_sha256'],
                                                 'bundle': str(DATASET_ROOT / 'bundle'),
                                                 'dataset_root': str(DATASET_ROOT)}),
                           'record': {'path': 'trusted_episode', 'max_bytes': None},
                           'max_calls': None, 'deadline_s': None, 'max_line_bytes': None}}


def _verify(tid, row):
    return {'kind': 'command',
            'argv': [sys.executable, '-I', 'costbench_code/verify/check.py', '--task-id', tid,
                     '--case-sha256', row['private_sha256']],
            'inputs': [{'path': 'costbench_code', 'from': str(ASSETS), 'immutable': True},
                       {'path': 'costbench_private.json', 'from': str(PREPARED / row['path'] / 'private.json')},
                       {'path': 'costbench_bundle', 'from': str(DATASET_ROOT / 'bundle'), 'immutable': True},
                       {'path': 'costbench_runtime', 'from': str(RUNTIME), 'immutable': True}],
            'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
            'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}


class _Tasks(Mapping):
    def __init__(self, build):
        self.build = build

    def __iter__(self):
        return iter(roster())

    def __len__(self):
        return len(roster())

    def __getitem__(self, tid):
        return self.build(tid, roster()[tid])


SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verify)
ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))


def downloads():
    return [{'kind': 'http', 'name': 'CostBench dynamic sequence5: four one-event protocols',
             'version': REVISION, 'url': f'https://codeload.github.com/JiayuJeff/CostBench/tar.gz/{REVISION}',
             'sha256': ARCHIVE_SHA256, 'size': 'same pinned source; 9172 mode/query cases across train and test',
             'dst': 'source.tar.gz',
             'note': 'Prepare static costbench first, then run costbench_dynamic/prepare.py. '
                     'Four fixed modes, one intervention, sequence5, seed42; same-split preference '
                     'replacement and declared training compatibility. No model calls during preparation.'}]


def provision_status():
    try:
        rows = roster()
        present = (RUNTIME / 'bin/python').is_file() and all(
            (PREPARED / row['path'] / 'private.json').is_file()
            and (PREPARED / row['path'] / 'public.json').is_file() for row in rows.values())
        return {'root': str(DATASET_ROOT), 'present': present, 'tasks': len(rows),
                'detail': 'dynamic sequence5; four one-event modes; training compatibility declared separately'}
    except (OSError, ValueError, KeyError) as exc:
        return {'root': str(DATASET_ROOT), 'present': False, 'detail': str(exc)}


def __getattr__(name):
    """`SPLIT` and `OFFICIAL_EVAL` come from the prepared manifest, lazily.

    Absent payload -> `AttributeError`, not the raw read error: discovery and the split
    audits must work before `--fetch`, and `load()` is where the absence is reported.
    """
    if name not in ('SPLIT', 'OFFICIAL_EVAL'):
        raise AttributeError(name)
    try:
        rows = roster()
    except (OSError, ValueError) as exc:
        raise AttributeError(
            f'costbench_dynamic: {name} needs the prepared payload under {PREPARED}: {exc}'
        ) from exc
    if name == 'SPLIT':
        from data.scaffolds.registry import validate_split
        return validate_split('costbench_dynamic', load()[0], {
            side: [tid for tid, row in rows.items() if row['split'] == source]
            for side, source in (('train', 'train'), ('eval', 'test'))})
    return [tid for tid, row in rows.items() if row['split'] == 'test']
