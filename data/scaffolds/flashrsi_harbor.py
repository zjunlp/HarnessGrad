"""Read pinned native Harbor task trees without importing Harbor or running actors.

A native task is not interchangeable with Terminal-Bench. Separate verifier,
collect hooks, agent network restrictions, judge credentials and fresh patch
replay are explicit admission requirements. This reader preserves all of them.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from harnessgrad.config.areas import ConfigMissing

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 provisioning dependency only.
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


class NativeRuntimeUnavailable(ConfigMissing):
    """A real native declaration requires an execution route not yet supplied."""


def safe_file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if (not relative or rel.is_absolute() or '..' in rel.parts or '\\' in relative
            or ':' in relative):
        raise ValueError(f'unsafe native path: {relative!r}')
    file = root.joinpath(*rel.parts)
    if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()) or not file.is_file():
        raise ValueError(f'native input is missing, linked or outside source tree: {file}')
    return file


def _tree_identity(root: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(root.rglob('*')):
        if file.is_symlink():
            raise ValueError(f'native task contains symlink: {file}')
        if file.is_file():
            digest.update(file.relative_to(root).as_posix().encode() + b'\0')
            digest.update(file.read_bytes())
            digest.update(b'\0')
    return digest.hexdigest()


@dataclass(frozen=True)
class NativeTask:
    dataset: str
    task_id: str
    directory: Path
    instruction: str
    declaration: dict
    source_sha256: str

    def public(self) -> dict:
        # No tests, solution, private task-card/rubrics, collect commands or
        # benchmark declarations can travel in an actor task.json.
        return {'task_id': self.task_id, 'goal': self.instruction}

    def required_capabilities(self) -> list[str]:
        raw = self.declaration
        verifier = raw.get('verifier') or {}
        agent = raw.get('agent') or {}
        required = []
        if verifier.get('environment_mode', 'shared') == 'separate':
            required.append('separate-verifier-image')
        if verifier.get('collect'):
            required.append('native-collect-hooks')
        if agent.get('network_mode') == 'no-network':
            required.append('model-only-container-egress')
        if self.dataset == 'jobbench':
            required += ['private-weighted-rubric-verifier', 'independent-judge-route']
        if self.dataset == 'swe_bench_pro':
            required += ['official-v2-patch-export', 'pristine-patch-replay-and-regrade']
        if not (raw.get('environment') or {}).get('docker_image'):
            required.append('native-dockerfile-build')
        return list(dict.fromkeys(required))

    def refusal(self) -> str:
        return (f'{self.dataset} task {self.task_id}: native execution requires '
                + ', '.join(self.required_capabilities())
                + '; no ordinary HG Terminal-Bench/snapshot route is substituted. '
                  'Use inventory()/provision_status() to inspect pinned native inputs.')


def inventory(root: Path, dataset: str, *, collection: str = 'all') -> list[NativeTask]:
    if tomllib is None:
        raise NativeRuntimeUnavailable('native task.toml inventory needs Python >=3.11 or the tomli package')
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'{dataset}: native source tree not prepared at {root}')
    if dataset == 'jobbench' and collection not in ('main', 'easy', 'all'):
        raise ValueError('jobbench collection must be main/easy/all')
    choices = ([root] if (root / 'task.toml').is_file() else
               [root / 'tasks', root / 'v2/tasks', root / 'harbor/tasks', root])
    directories = []
    for base in choices:
        if base.is_dir():
            directories = ([base] if (base / 'task.toml').is_file() else
                           sorted(p for p in base.iterdir() if p.is_dir() and (p / 'task.toml').is_file()))
            if directories:
                break
    if not directories:
        raise ValueError(f'{dataset}: native source has no task.toml task directories under {root}')
    rows = []
    seen = set()
    for directory in directories:
        if not directory.resolve().is_relative_to(root) or directory.is_symlink():
            raise ValueError(f'{dataset}: native task directory leaves data root')
        task_id = directory.name
        raw = tomllib.loads(safe_file(directory, 'task.toml').read_text(encoding='utf-8'))
        if not isinstance(raw.get('environment'), dict) or not isinstance(raw.get('verifier'), dict):
            raise ValueError(f'{dataset}/{task_id}: missing environment/verifier tables')
        if dataset == 'jobbench':
            native_split = (raw.get('metadata') or {}).get('split')
            if native_split not in ('main', 'easy'):
                raise ValueError(f'jobbench/{task_id}: native metadata.split must be main or easy')
            if collection not in ('all', native_split):
                continue
        if dataset == 'deepswe':
            if raw['verifier'].get('environment_mode') != 'separate' or not raw['verifier'].get('collect'):
                raise ValueError(f'deepswe/{task_id}: v1.1 requires native separate verifier and collect hooks')
            if raw['verifier'].get('network_mode') != 'no-network' or (raw.get('agent') or {}).get('network_mode') != 'no-network':
                raise ValueError(f'deepswe/{task_id}: native actor/verifier no-network contracts must be preserved')
        if dataset == 'swe_bench_pro' and (raw.get('agent') or {}).get('network_mode') != 'no-network':
            raise ValueError(f'swe_bench_pro/{task_id}: V2 requires native actor no-network')
        instruction = safe_file(directory, 'instruction.md').read_text(encoding='utf-8')
        if not instruction.strip():
            raise ValueError(f'{dataset}/{task_id}: empty native instruction')
        tid = f'{dataset}/{task_id}'
        if tid in seen:
            raise ValueError(f'duplicate native task id: {tid}')
        seen.add(tid)
        rows.append(NativeTask(dataset, tid, directory, instruction, raw, _tree_identity(directory)))
    if not rows:
        raise ValueError(f'{dataset}: requested collection {collection!r} has no native tasks')
    return rows


def status(root: Path, dataset: str, *, collection: str = 'all') -> dict:
    result = {'root': str(root), 'present': False, 'execution_ready': False,
              'source_prepared': False, 'tasks': None}
    try:
        rows = inventory(root, dataset, collection=collection)
        result.update(source_prepared=True, tasks=len(rows),
                      source_sha256={r.task_id: r.source_sha256 for r in rows},
                      runtime_requirements=sorted({v for r in rows for v in r.required_capabilities()}),
                      detail=rows[0].refusal())
        for row in rows:
            runtime(row)
        result.update(present=True, execution_ready=True,
                      detail='Selected native runtime manifests, fixed Harbor interpreter and local immutable images are provisioned. Real controls remain separately required.')
    except (OSError, ValueError) as exc:
        result['detail'] = str(exc)
    return result


def require_execution(rows: list[NativeTask]):
    """Fail before actor/model execution, never turn an absent referee into zero."""
    if not rows:
        raise ValueError('native source inventory is empty')
    for row in rows:
        runtime(row)


RUNTIME_ASSETS = Path(__file__).with_name('flashrsi_harbor_runtime')
TRANSPORT = Path(__file__).resolve().parents[2] / 'eval/service/tool_transport.py'


def runtime(row: NativeTask) -> dict:
    import os
    import subprocess
    from data.scaffolds.runtime_extensions import validate_container_runtime
    pointer = os.environ.get('HG_' + row.dataset.upper() + '_RUNTIME')
    path = Path(pointer).expanduser() if pointer else row.directory.parent.parent / 'native-runtime.json'
    if not path.is_file():
        raise NativeRuntimeUnavailable(f'{row.dataset}: prepare selected native-runtime.json; missing {path}')
    data = json.loads(path.read_text())
    if data.get('schema') != 'harnessgrad.native-harbor-runtime.v1' or not isinstance(data.get('tasks'), dict):
        raise ValueError('native Harbor runtime manifest schema mismatch')
    entry = data['tasks'].get(row.task_id)
    if not isinstance(entry, dict) or entry.get('source_sha256') != row.source_sha256:
        raise NativeRuntimeUnavailable(f'{row.dataset}: native runtime does not bind task/source {row.task_id}')
    for key in ('replay_actor_image', 'verifier_image'):
        if not isinstance(entry.get(key), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', entry[key]):
            raise ValueError('native replay/verifier images must be explicit local immutable image IDs')
    actor = entry.get('actor')
    network = (row.declaration.get('agent') or {}).get('network_mode', 'public')
    if network == 'public':
        if not os.environ.get('HG_NATIVE_HARBOR_ALLOW_PUBLIC'):
            raise NativeRuntimeUnavailable('JobBench native public actor requires explicit HG_NATIVE_HARBOR_ALLOW_PUBLIC operator reason')
        if not isinstance(actor, dict) or actor.get('backend') != 'native-public':
            raise ValueError('native public task needs its explicit native-public actor runtime profile')
        from data.scaffolds.flashrsi_harbor_runtime.public_runtime import validate_public_runtime
        validate_public_runtime(actor)
    else:
        validate_container_runtime(actor)
    python = entry.get('harbor_python')
    if not python or not Path(python).is_file():
        raise NativeRuntimeUnavailable(f'{row.dataset}: selected harbor_python interpreter missing')
    probe = subprocess.run([python, '-I', '-c',
                            'import importlib.metadata;print(importlib.metadata.version("harbor"))'],
                           capture_output=True, text=True, errors='replace', timeout=10)
    if probe.returncode or probe.stdout.strip() != '0.23.0':
        raise NativeRuntimeUnavailable('native replay requires prepared harbor==0.23.0 and Python>=3.12')
    for image in {actor['image'], entry['replay_actor_image'], entry['verifier_image']}:
        inspected = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True,
                                    text=True, errors='replace', timeout=10)
        if inspected.returncode:
            raise NativeRuntimeUnavailable(f'{row.dataset}: immutable native image not imported locally: {image}')
    if row.dataset == 'jobbench':
        from urllib.parse import urlsplit
        import ipaddress
        base = os.environ.get('HG_JUDGE_HARBOR_BASE_URL', '')
        model = os.environ.get('HG_JUDGE_HARBOR_MODEL', '')
        try:
            parsed = urlsplit(base)
            valid = parsed.scheme == 'http' and ipaddress.ip_address(parsed.hostname or '').is_loopback and bool(model)
        except ValueError:
            valid = False
        if not valid:
            raise NativeRuntimeUnavailable('JobBench requires independent explicit local HG_JUDGE_HARBOR_BASE_URL / HG_JUDGE_HARBOR_MODEL; no paid provider fallback')
    if row.dataset == 'swe_bench_pro':
        known=json.loads((RUNTIME_ASSETS/'PROVENANCE.json').read_text())['pro_tooling_sha256']
        for key in ('pro_replay_source','pro_capture_source'):
            source = Path(entry.get(key,''))
            filename='patch_replay.py' if key=='pro_replay_source' else 'locked_claude_code.py'
            if (not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != entry.get(key + '_sha256')
                    or entry[key+'_sha256']!=known[filename]):
                raise NativeRuntimeUnavailable('SWE-bench Pro official patch-replay/capture source is not pinned in runtime')
    return entry


def execution_case(row):
    import ast
    selected = runtime(row)
    collect = row.declaration.get('verifier', {}).get('collect', [])
    paths = row.declaration.get('artifacts', [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError('native artifacts must be a list of absolute source paths')
    if row.dataset == 'swe_bench_pro':
        capture=Path(selected['pro_capture_source']).read_bytes()
        if hashlib.sha256(capture).hexdigest()!=selected['pro_capture_source_sha256']:
            raise ValueError('native Pro capture source changed after runtime selection')
        tree = ast.parse(capture)
        literals = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '_CAPTURE' for t in node.targets)]
        if len(literals) != 1 or not isinstance(literals[0], str):
            raise ValueError('official SWE Pro capture command is not a static string')
        collect = [{'command': literals[0], 'timeout_sec': 60}]
        paths = ['/logs/agent/model.patch']
    if row.dataset == 'jobbench' and paths != ['/workspace/output']:
        raise ValueError('JobBench requires native final-deliverable-only artifact export')
    return {'schema':'harnessgrad.native-harbor-case.v1','dataset':row.dataset,
            'task_id':row.task_id,'directory':str(row.directory),'source_sha256':row.source_sha256,
            'declaration':row.declaration,'runtime':selected,'collect':collect,
            'artifact_paths':paths,'actor_timeout_s':row.declaration.get('agent',{}).get('timeout_sec',3000),
            'verify_timeout_s':row.declaration.get('verifier',{}).get('timeout_sec',3000)}


def setup(row):
    case = execution_case(row)
    return {'files':[{'path':'native.py','from':str(RUNTIME_ASSETS/'cli.py')},
                     {'path':'native_transport.py','from':str(TRANSPORT)}],
            'tool_relay':{'program':str(RUNTIME_ASSETS/'relay.py'),'content_flag':'--case-spec',
                          'filename':'native-case.json','content':json.dumps(case),
                          'record':{'path':'native_episode','max_bytes':None},
                          'max_calls':None,'max_line_bytes':None,'deadline_s':case['actor_timeout_s']}}


def verifier(row):
    case = execution_case(row)
    content = json.dumps(case,sort_keys=True,separators=(',',':'))
    sha = hashlib.sha256(content.encode()).hexdigest()
    return {'kind':'command','argv':['harnessgrad-native',row.dataset],
            'inputs':[{'path':'native-case.json','content':content}],
            'scorer_input':{'task_id':row.task_id,'path':'native-case.json','sha256':sha},
            'native_benchmark':{'dataset':row.dataset,'case_sha256':sha,
                                'case_path':'native-case.json','record_path':'native_episode'},
            'reward_file':'/verifier/reward.txt','evidence_file':'/verifier/evidence.json',
            'timeout_s':case['verify_timeout_s'],
            'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}


def public_task(row):
    return {'task_id':row.task_id,'goal':row.instruction + '\n\n'
        'The native task filesystem is a persistent remote world. Use `python3 native.py describe` '
        'and `python3 native.py exec "SHELL COMMAND"` for all native file/tool operations. '
        'Commands run inside the original task environment, never on the Docker host. '
        'When done call `python3 native.py finish` to seal native declared artifacts. '
        'A final answer.txt cannot replace native artifacts. Do not access hidden verifier tests.'}
