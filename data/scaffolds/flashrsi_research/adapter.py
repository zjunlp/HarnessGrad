"""Translate native public tasks and private records into SETUP / ENV / VERIFY.

Preparation owns native data loading. Ordinary imports and measurements never fetch,
repartition or execute FlashRSI's agent/algorithm. Every private case is hash-checked.
"""
from collections.abc import Mapping
import hashlib
import importlib.util
import json
import os
from pathlib import Path

from harnessgrad.config.areas import ConfigMissing
from .profiles import COMMIT, PROFILES, SOURCES

ROOT = Path(__file__).resolve().parents[3]
ASSETS = Path(__file__).resolve().parent


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('prepared path must be relative and inside the dataset')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('prepared source is not an owned regular file')
    return path


class Adapter:
    def __init__(self, name, root=None):
        self.name = name
        self.profile = PROFILES[name]
        # The adapter module above passes the root its own `roots.dataset_root()` call
        # resolved; the fallback keeps `Adapter('x')` usable in a test or a tool without
        # re-spelling the operator's absolute path here.
        self._root = None if root is None else Path(root)

    @property
    def root(self):
        if self._root is not None:
            return self._root
        from data.scaffolds import roots
        # No legacy tier here for the same reason as the QA family: this adapter arrived on
        # 2026-11-02. A module that keeps an old default spells it in its own
        # `roots.dataset_root(..., legacy=...)` call and passes the result in.
        return roots.dataset_root(self.name)

    def manifest(self):
        if self.root.resolve() == ROOT or self.root.resolve().is_relative_to(ROOT):
            raise ValueError('native dataset payloads must live outside the platform tree')
        path = self.root / 'manifest.json'
        if not path.is_file():
            raise ConfigMissing(f'{self.name}: prepare pinned native data with tools/prepare_flashrsi_research.py; missing {path}')
        value = json.loads(path.read_text())
        if (value.get('schema') != 'harnessgrad.flashrsi-research.v1'
                or value.get('dataset') != self.name or value.get('flashrsi_commit') != COMMIT
                or not isinstance(value.get('source'), dict) or not isinstance(value.get('records'), list)
                or not value['records'] or not isinstance(value.get('files'), dict)):
            raise ValueError(f'{self.name}: malformed or mismatched prepared manifest')
        ids = [r.get('task_id') for r in value['records']]
        if any(not isinstance(t, str) or not t for t in ids) or len(ids) != len(set(ids)):
            raise ValueError('prepared task IDs must be nonempty and unique')
        for tid in ids:
            if (not tid.startswith(self.name + '/') or Path(tid).is_absolute()
                    or any(part in ('', '.', '..') for part in tid.split('/')) or '\\' in tid):
                raise ValueError('prepared task IDs must be safe namespaced artifact paths')
        return value

    def case(self, record, manifest=None):
        manifest = self.manifest() if manifest is None else manifest
        path = owned(self.root, record['case'])
        if digest(path) != manifest['files'].get(record['case']):
            raise ValueError('private native case digest mismatch')
        case = json.loads(path.read_text())
        if case.get('task_id') != record['task_id'] or not isinstance(case.get('question'), str) or not case['question'].strip():
            raise ValueError('prepared native case task binding mismatch')
        return case

    def readiness(self, task_id=None):
        m = self.manifest()
        if self.name == 'harvey_lab':
            from .harvey_native import readiness, setup_files
            readiness(m)
            for record in m['records']:
                if task_id is not None and record['task_id'] != task_id:
                    continue
                setup_files(self.case(record, m), self.root, m['files'])
            return m
        if self.name == 'swe_bench':
            from .swe_native import readiness
            return readiness(self, m, task_id=task_id)
        if self.name == 'widesearch':
            from .wide_runtime import readiness as wide_readiness
            wide_readiness()
        required = {'travelplanner': ('pandas', 'numpy'),
                    'workbench': ('pandas', 'numpy')}.get(self.name, ())
        absent = [name for name in required if importlib.util.find_spec(name) is None]
        if absent:
            raise ConfigMissing(f'{self.name}: native dependency missing: {", ".join(absent)}')
        if self.profile['judge']:
            self.judge(m)  # No endpoint call; validate the explicit frozen model/profile before actor work.
        if self.name == 'travelplanner':
            if not (self.root / 'database').is_dir():
                raise ConfigMissing('travelplanner: complete native database is not prepared')
            self.checked_world(m)
        if self.name == 'workbench':
            if not any(path.startswith('world/src/') for path in m['files']):
                raise ConfigMissing('workbench: preparation must include the native src and full initial database')
            self.checked_world(m)
        return m

    def checked_world(self, manifest):
        for relative, expected in manifest['files'].items():
            if relative.startswith(('world/', 'database/')) and digest(owned(self.root, relative)) != expected:
                raise ValueError('native world/database dependency digest mismatch')

    def records(self):
        return {r['task_id']: r for r in self.manifest()['records']}

    def load(self):
        # The public inventory is independent of model/image provisioning.
        # Runtime admission occurs on selected SETUP / VERIFY values, so an
        # unrelated unprovisioned instance cannot block a requested task.
        m = self.manifest() if self.name in ('swe_bench', 'harvey_lab') else self.readiness()
        tasks = []
        for r in m['records']:
            c = self.case(r, m)
            if self.name == 'harvey_lab':
                goal = (c['question'] + '\n\nPublic source documents are in documents/. '
                    'Write the requested deliverable files into output/. Only those files are '
                    'exported for independent native dual-provider rubric evaluation; '
                    'a final answer.txt does not replace the deliverables.')
            elif self.name == 'swe_bench':
                goal = (c['question'] + '\n\nThe native repository is at /testbed behind '
                    '`python3 swe.py exec "SHELL COMMAND"`. Use that interface to inspect, edit and test '
                    'the repository. The platform exports the complete diff against the baseline '
                    'after exit and grades it independently. Writing answer.txt does not repair code.')
            elif self.name == 'workbench':
                goal = ("Today's date is Thursday, 2023-11-30 and the current time is 00:00:00.\n"
                    + c['question'] + '\n\nUse native workplace tools with `python3 workbench.py describe` '
                    'and `python3 workbench.py call TOOL_NAME JSON_OBJECT_OF_STRING_KEYWORD_ARGUMENTS`. '
                    'Tool calls change the persistent task world. A final answer.txt does not perform actions.')
            else:
                goal = c['question'] + '\n\nWrite the complete final answer to answer.txt.'
                if self.profile['web']:
                    goal += '\nUse `python3 web.py call search {JSON}` and `python3 web.py call fetch {JSON}` for web research. Search JSON has query/num, fetch JSON has url/query. Provider endpoints and keys are fixed by the platform.'
            tasks.append({'task_id': r['task_id'], 'goal': goal})
        return tasks, {r['task_id']: self.metric(m) for r in m['records']}

    def metric(self, manifest=None):
        m = manifest or self.manifest()
        if self.name == 'harvey_lab':
            return 'harvey_lab.flashrsi.all_pass_rate.dual-provider;models-in-native-receipt'
        profile = m.get('judge_model')
        # A custom model is part of metric identity; it never silently inherits
        # an official score, even when using the native prompt and formula.
        suffix = '.single-sample-label;GPass-requires-complete-report' if self.name == 'livemathbench' else ''
        return f'{self.name}.flashrsi.{self.profile["metric"]}.judge-{profile or "none"}' + suffix

    def setup(self, tid):
        if self.name == 'harvey_lab':
            from .harvey_native import setup_files
            m = self.readiness(tid)
            return {'files': setup_files(self.case(self.records()[tid], m), self.root, m['files'])}
        if self.name == 'swe_bench':
            m = self.readiness(tid)
            record = self.records()[tid]
            case = self.case(record, m)
            return {'files': [
                {'path': 'swe.py', 'from': str(ASSETS / 'cli/swe.py')},
                {'path': 'swe_transport.py', 'from': str(ROOT / 'eval/service/tool_transport.py')}],
                'tool_relay': {'program': str(ASSETS / 'swe_relay.py'),
                    'content_flag': '--case-spec', 'content': canonical({
                        'case': case, 'case_sha256': m['files'][record['case']]}),
                    'filename': 'native.json', 'max_calls': None, 'deadline_s': None,
                    'max_line_bytes': None, 'record': {'path': 'swe_episode', 'max_bytes': 32 << 20}}}
        if self.profile['web']:
            return {'files': [
                {'path': 'web.py', 'from': str(ASSETS / 'cli/web.py')},
                {'path': 'web_transport.py', 'from': str(ROOT / 'eval/service/tool_transport.py')}],
                'tool_relay': {'program': str(ASSETS / 'web_relay.py'),
                    'content_flag': '--case-spec', 'content': canonical({'dataset': self.name, 'task_id': tid}),
                    'filename': 'web.json', 'max_calls': None, 'deadline_s': None, 'max_line_bytes': None,
                    'record': {'path': 'trusted_web', 'max_bytes': None}}}
        if self.name == 'workbench':
            m = self.readiness()
            record = self.records()[tid]
            case = self.case(record, m)
            spec = {'task_id': tid, 'world': str(self.root / 'world'),
                'world_sha256': case['world_sha256'],
                'files': {k.removeprefix('world/'): v for k, v in m['files'].items() if k.startswith('world/')}}
            return {'files': [
                {'path': 'workbench.py', 'from': str(ASSETS / 'cli/workbench_env.py')},
                {'path': 'workbench_transport.py', 'from': str(ROOT / 'eval/service/tool_transport.py')}],
                'tool_relay': {'program': str(ASSETS / 'workbench_relay.py'),
                    'content_flag': '--case-spec', 'content': canonical(spec), 'filename': 'world.json',
                    'max_calls': None, 'deadline_s': None, 'max_line_bytes': None,
                    'record': {'path': 'trusted_episode', 'max_bytes': None}}}
        return {'files': []}

    def environment(self, tid):
        env = {'kind': 'files', 'network': 'none'}
        return env

    def judge(self, manifest):
        model = manifest.get('judge_model')
        if not isinstance(model, str) or not model.strip():
            raise ConfigMissing(f'{self.name}: explicit judge_model is required in preparation, never selected by the actor')
        spec = {'model': model, 'max_calls': None if self.name == 'widesearch' else 1,
                'max_output_tokens': None if self.name == 'travelplanner' else 4096,
                'timeout_s': 300, 'parameters': {}, 'max_retries': 0}
        # Match the pinned clients' defaults, including omitted temperature.
        if self.name in ('hle', 'browsecomp'):
            spec['token_parameter'] = 'max_completion_tokens'
        if self.name in ('travelplanner', 'livemathbench'):
            spec['parameters']['temperature'] = 0
        if self.name == 'livemathbench':
            spec['max_output_tokens'] = 8192
        if self.name == 'widesearch':
            spec['max_output_tokens'] = 10240
        if self.name == 'hle':
            spec['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'ExtractedAnswer', 'strict': True, 'schema': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {'extracted_final_answer': {'type': 'string'}, 'reasoning': {'type': 'string'},
                        'correct': {'type': 'string', 'enum': ['yes', 'no']},
                        'confidence': {'type': 'integer'}, 'strict': {'type': 'boolean', 'enum': [True]}},
                    'required': ['extracted_final_answer', 'reasoning', 'correct', 'confidence', 'strict']}}}
        return spec

    def verifier(self, tid):
        m = self.readiness(tid)
        r = self.records()[tid]
        self.case(r, m)
        if self.name in ('swe_bench', 'harvey_lab'):
            return {'kind': 'command', 'argv': ['harnessgrad-native', self.name],
                'native_benchmark': {'dataset': self.name, 'case_sha256': m['files'][r['case']]},
                'inputs': [{'path': 'flashrsi_case.json', 'from': str(owned(self.root, r['case']))}],
                'scorer_input': {'task_id': tid, 'path': 'flashrsi_case.json', 'sha256': m['files'][r['case']]},
                'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
        profile = self.profile
        spec = {'kind': 'command', 'argv': ['python3', '-I', 'flashrsi_research/verify.py',
                    '--dataset', self.name, '--task-id', tid, '--case-sha256', m['files'][r['case']]],
                'inputs': [{'path': 'flashrsi_research', 'from': str(ASSETS), 'immutable': True},
                           {'path': 'flashrsi_case.json', 'from': str(owned(self.root, r['case']))},
                           {'path': 'judge_transport', 'from': str(ROOT / 'eval/service'), 'immutable': True}],
                'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
                'scorer_input': {'task_id': tid, 'path': 'flashrsi_case.json', 'sha256': m['files'][r['case']]},
                'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True}}
        if profile['judge']:
            spec['judge'] = self.judge(m)
        if self.name == 'travelplanner':
            self.checked_world(m)
            database = self.root / 'database'
            if not database.is_dir():
                raise ConfigMissing('travelplanner: the complete native database is required')
            spec['inputs'].append({'path': 'travelplanner_database', 'from': str(database), 'immutable': True})
        if self.name == 'workbench':
            spec['inputs'].append({'path': 'workbench_world', 'from': str(self.root / 'world'), 'immutable': True})
        if self.name == 'widesearch':
            from .wide_runtime import apply as wide_runtime
            wide_runtime(spec)
        return spec

    def split(self):
        rows = self.records()
        train = [t for t, r in rows.items() if r.get('source_split') == 'train']
        return {'train': train, 'eval': [t for t in rows if t not in train]} if train else None

    def provision_status(self):
        try:
            m = self.readiness()
            for r in m['records']:
                self.case(r, m)
            return {'present': True, 'root': str(self.root), 'tasks': len(m['records']),
                    'flashrsi_commit': COMMIT, 'metric': self.metric(m)}
        except (OSError, ValueError) as exc:
            return {'present': False, 'root': str(self.root), 'detail': str(exc)}


class TaskMapping(Mapping):
    def __init__(self, adapter, fn): self.adapter, self.fn = adapter, fn
    def __iter__(self): return iter(self.adapter.records())
    def __len__(self): return len(self.adapter.records())
    def __getitem__(self, tid):
        if tid not in self.adapter.records(): raise KeyError(tid)
        return self.fn(tid)


def downloads(name):
    kind, source, revision = SOURCES[name]
    entry = {'kind': kind, 'name': name + ' native source', 'version': revision,
        'size': 'source-dependent', 'dst': 'native-source',
        'note': 'Pinned native source; use prepare_flashrsi_research.py to preserve private grading inputs and source split membership. Downloading alone does not establish scorer/runtime readiness.'}
    if kind == 'git':
        entry.update(kind='http', url='https://codeload.github.com/' + source + '/tar.gz/' + revision,
                     dst='native-source.tar.gz')
    elif kind == 'http':
        entry.update(url=source, dst='browse_comp_test_set.csv', sha256=revision)
    else:
        entry['repo'] = source
        if name == 'deepsearchqa':
            entry.update(file='DSQA-full.csv', dst='DSQA-full.csv')
    return [entry]
