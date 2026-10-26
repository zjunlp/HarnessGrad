"""τ³ v1.0.1 text half-duplex, all 375 base tasks and complete three-trial pass^k.

The official sierra-research/tau2-bench v1.0.1 release contains τ³; this adapter
is distinct from the refused tau2_bench v0.1 adapter. Business state, user
scenario and full scorer criteria remain in trusted services/verifiers.
"""
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess

KIND = 'dataset'
ORACLE_SOURCE = 'released'
# Upstream's full-base reporting protocol, distinct from the held-out split.
OFFICIAL_EVAL = None
SPLIT_SOURCE = 'declared'
ASSETS = Path(__file__).resolve().parent / 'tau3_bench'
#: Where the prepared payload lives. The default used to be `parents[3]/datasets/tau3_bench`,
#: which from `data/datasets/` resolves to `<checkout>/../datasets` -- a path that exists on the
#: author's machine and nowhere else. Every other adapter on this host uses the same absolute
#: `/mnt/20t/xubuqiang/datasets/<name>` convention, so this one does too; `HG_TAU3_DATA` still
#: overrides it.
DATASET_ROOT = Path(os.environ.get('HG_TAU3_DATA', '/mnt/20t/xubuqiang/datasets/tau3_bench'))
RUNTIME = Path(os.environ.get('HG_TAU3_RUNTIME', DATASET_ROOT / 'runtime'))
REVISION = 'fc0055dc4e0a316c3f83133267fbd6faaa770992'
ARCHIVE_SHA256 = '7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e'
METRIC = 'tau3.v1.0.1.ALL.binary'
_rng = random.Random(300)
TRIAL_PROTOCOL = {'version': 1, 'metric': 'pass^k', 'trials': 3, 'ks': [1, 2, 3],
                  'seeds': [_rng.randint(0, 1000000) for _ in range(3)]}
DEFAULT_ENV = {'kind': 'files', 'network': 'none'}
TRANSPORT = Path(__file__).resolve().parents[2] / 'eval/service/tool_transport.py'
GOAL = '''Help the customer complete their service request while following the domain policy.
Read `python3 tau3.py show` for the policy and business-tool schemas. Start with
`python3 tau3.py start`. Converse using `python3 tau3.py say 'MESSAGE'`; call a
business tool using `python3 tau3.py call TOOL_NAME '{"argument":"value"}'`.
Use `python3 tau3.py calls '[{"id":"a","name":"TOOL","arguments":{}}]'` for
multiple business calls in one assistant turn (preserving the official step budget).
The simulated user has independent tools; their private scenario is not available.
Use `python3 tau3.py status` to inspect the conversation's termination status.
Finish by sending the user your final response and `###STOP###`, unless the user
has already stopped. The service fixes 200 steps and 10 tool errors per episode.
Scoring replays the trusted conversation and applies every official reward basis.
answer.txt is optional bookkeeping; it cannot replace conversation/tool actions.
'''


@lru_cache(maxsize=1)
def roster():
    raw = (DATASET_ROOT / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'f14897ce5d8d3589b00b3d9f03fd79a4a40e0842d6075c84d3b58aca4e4572aa':
        raise ValueError('tau3 prepared manifest digest mismatch')
    manifest = json.loads(raw)
    if (manifest['revision'] != REVISION or manifest['archive_sha256'] != ARCHIVE_SHA256
            or manifest['counts'] != {'airline': 50, 'retail': 114, 'telecom': 114, 'banking_knowledge': 97}):
        raise ValueError('tau3 manifest identity mismatch')
    rows = {r['task_id']: r for r in manifest['tasks']}
    if len(rows) != 375 or len(manifest['tasks']) != 375:
        raise ValueError('tau3 base inventory mismatch')
    return rows


def control_task(tid):
    if tid not in roster():
        raise KeyError(tid)
    return {'task_id': tid, 'goal': GOAL, 'inputs': []}


def load():
    return [control_task(tid) for tid in roster()], {tid: METRIC for tid in roster()}


def model_spec(role, tools=None):
    from harnessgrad.config import current
    if role not in ('environment', 'judge'):
        raise ValueError('unsupported model role')
    cfg = current()
    model = cfg.environment.model if role == 'environment' else cfg.judge.resolve_model(override=cfg.judge.tau3_model)
    if not model:
        raise ValueError(f'configure explicit HG_{"ENV" if role == "environment" else "JUDGE"}_MODEL')
    # Official evaluator_nl_assertions.evaluate_nl_assertions generates once for
    # the whole assertion list. HTTP retry attempts are a separate budget.
    spec = {'model': model, 'max_calls': None if role == 'environment' else 1,
            'max_output_tokens': None, 'timeout_s': None, 'max_retries': 0, 'max_attempts': None,
            'response_max_bytes': None, 'max_line_bytes': None, 'parameters': {'temperature': 0}}
    if role == 'environment':
        spec['seed_source'] = 'trial'
    if tools:
        spec.update(tools=tools, tool_choice='auto')
    return spec


def _setup(tid, row):
    public = (DATASET_ROOT / row['path'] / 'public.json').read_bytes()
    if hashlib.sha256(public).hexdigest() != row['public_sha256']:
        raise ValueError('public task digest mismatch')
    model = model_spec('environment', row['user_tools'])
    return {'files': [{'path': 'tau3.py', 'from': str(ASSETS / 'cli/tau3.py')},
                      {'path': 'tau3_transport.py', 'from': str(TRANSPORT.resolve())},
                      {'path': 'tau3_public.json', 'content': public.decode()}],
            'tool_relay': {'program': str(ASSETS / 'relay.py'), 'python': str(RUNTIME / 'bin/python'),
                           'content_flag': '--case-spec', 'filename': 'case-spec.json',
                           'content': json.dumps({'case': str(DATASET_ROOT / row['path'] / 'private.json'),
                                                 'sha256': row['private_sha256'], 'bundle': str(DATASET_ROOT / 'bundle'),
                                                 'user_model': model['model']}),
                           'record': {'path': 'trusted_episode', 'max_bytes': None}, 'model': model,
                           'max_calls': None, 'deadline_s': None, 'max_line_bytes': None}}


@lru_cache(maxsize=1)
def _python_runtime(runtime):
    """Inspect the selected scorer Python once; the driver may use another runtime."""
    runtime = Path(runtime)
    if not runtime.is_absolute():
        raise ValueError('tau3 runtime must be an absolute directory')
    python = runtime / 'bin/python'
    probe = ('import json, sys; print(json.dumps({"version": list(sys.version_info[:2]), '
             '"prefix": sys.prefix, "base_prefix": sys.base_prefix, '
             '"base_exec_prefix": sys.base_exec_prefix}))')
    try:
        # Startup inspection only, not an episode/model/verification budget.
        proc = subprocess.run([str(python), '-I', '-c', probe], capture_output=True,
                              text=True, check=True, timeout=10)
        info = json.loads(proc.stdout)
        if info['version'] != [3, 12]:
            raise ValueError('tau3 requires Python 3.12 in its selected runtime')
        prefixes = [info[key] for key in ('prefix', 'base_prefix', 'base_exec_prefix')]
        if any(not isinstance(p, str) or not Path(p).is_absolute() or not Path(p).is_dir()
               for p in prefixes) or Path(prefixes[0]).resolve() != runtime.resolve():
            raise ValueError('tau3 interpreter prefixes do not match the selected runtime')
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f'cannot bind tau3 Python runtime {python}: {exc}') from exc
    return str(python), tuple(dict.fromkeys([str(runtime), *prefixes]))


def _verify(tid, row):
    python, runtime_paths = _python_runtime(RUNTIME)
    inputs = [{'path': 'tau3_code', 'from': str(ASSETS), 'immutable': True},
              {'path': 'tau3_private.json', 'from': str(DATASET_ROOT / row['path'] / 'private.json')},
              {'path': 'tau3_transport_runtime', 'from': str(TRANSPORT.resolve().parent), 'immutable': True},
              {'path': 'tau3_bundle', 'from': str(DATASET_ROOT / 'bundle'), 'immutable': True},
              {'path': 'tau3_runtime', 'from': str(RUNTIME), 'immutable': True}]
    result = {'kind': 'command', 'argv': [python, '-I', 'tau3_code/verify/check.py', '--task-id', tid,
                                       '--case-sha256', row['private_sha256']],
              'runtime_paths': list(runtime_paths),
              'inputs': inputs, 'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
              'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True},
              'trial_protocol': dict(TRIAL_PROTOCOL),
              'trial_evidence': {'version': 1},
              'model_failure_policy': {'version': 1, 'invalidate_on_http_status': [429, *range(500, 600)]}}
    if row['needs_judge']:
        result['judge'] = model_spec('judge')
    return result


class _Tasks(Mapping):
    def __init__(self, build): self.build = build
    def __iter__(self): return iter(roster())
    def __len__(self): return len(roster())
    def __getitem__(self, tid): return self.build(tid, roster()[tid])


SETUP = _Tasks(_setup)
VERIFY = _Tasks(_verify)
ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))


def downloads():
    return [{'kind': 'http', 'name': 'τ³ official v1.0.1 text base tasks and runtime source',
             'version': REVISION, 'url': f'https://codeload.github.com/sierra-research/tau2-bench/tar.gz/{REVISION}',
             'sha256': ARCHIVE_SHA256, 'size': '92 MB archive', 'dst': 'source.tar.gz',
             'note': 'Run tau3_bench/prepare.py with Python 3.12 and isolated pinned dependencies. All four base domains retained; text half-duplex, BM25 banking, no voice.'}]


def provision_status():
    try:
        return {'root': str(DATASET_ROOT), 'present': (RUNTIME / 'bin/python').is_file(), 'tasks': len(roster())}
    except (OSError, ValueError, KeyError) as exc:
        return {'root': str(DATASET_ROOT), 'present': False, 'detail': str(exc)}


def __getattr__(name):
    if name == 'SPLIT':
        from data.scaffolds.registry import default_split, validate_split
        raw = (ASSETS / 'specs/task-splits.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != '7c54f64946e9398341707eee3979726bf620278b72b1e9daf8a0b42f994f872a':
            raise ValueError('tau3 native split digest mismatch')
        spec = json.loads(raw)
        split = {side: [f'tau3_bench/{domain}/{tid}'
                        for domain, parts in spec['domains'].items() for tid in parts[source]]
                 for side, source in (('train', 'train'), ('eval', 'test'))}
        # Banking has no upstream division; apply the platform's hash rule only here.
        banking = default_split([{'task_id': tid} for tid, row in roster().items()
                                 if row['domain'] == 'banking_knowledge'])
        for side in split:
            split[side].extend(banking[side])
        return validate_split('tau3_bench', load()[0], split)
    raise AttributeError(name)
