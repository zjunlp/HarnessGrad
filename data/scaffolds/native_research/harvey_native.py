"""Trusted host Harvey LAB dual-provider scoring of exported output files.

Only the platform's fixed native-benchmark dispatcher calls this module. Provider
keys never occur in VERIFY, public tasks, prepared receipts, actor SETUP, or the
returned verdict. The subprocess executes the unchanged pinned official grader.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
WHEEL_SHA256 = '5bfd70664a99c16a97a35bf61a3845a9890477168d11978d58864f986c9f53ba'
UPSTREAM = 'f93ae2ac2831e65892fea83094cfb50149bc2e59'
STANDARD_MODELS = ('claude-sonnet-4-6', 'gpt-5.5')


def _missing(message):
    from harnessgrad.config.areas import ConfigMissing
    raise ConfigMissing('harvey_lab: ' + message)


def _positive(value, label, *, integer=False):
    if integer:
        if type(value) is not int or value < 1:
            raise ValueError(label + ' must be a positive integer')
    elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(label + ' must be positive and finite')
    return value


def settings(env=None):
    """Resolve trusted host settings; the returned object contains secrets."""
    env = os.environ if env is None else env
    request = {'parallel': int(env.get('HG_JUDGE_HARVEY_PARALLEL', '6')),
               'timeout': float(env.get('HG_JUDGE_HARVEY_TIMEOUT_S', '1800'))}
    _positive(request['parallel'], 'parallel', integer=True)
    _positive(request['timeout'], 'timeout')
    for provider, default_model, default_base in (
        ('anthropic', STANDARD_MODELS[0], 'https://api.anthropic.com'),
        ('openai', STANDARD_MODELS[1], 'https://api.openai.com/v1')):
        prefix = 'HG_JUDGE_HARVEY_' + provider.upper() + '_'
        model = env.get(prefix + 'MODEL') or default_model
        key = env.get(prefix + 'API_KEY')
        base = env.get(prefix + 'BASE_URL')
        if not isinstance(base,str) or not base.strip():
            _missing(prefix + 'BASE_URL must explicitly select a local provider; official paid endpoint defaults are disabled')
        if (not isinstance(model, str) or '/' in model or '\\' in model or
                not model.startswith('claude' if provider == 'anthropic' else ('gpt','o1','o3','o4','o5'))):
            raise ValueError('Harvey judge model must retain its native provider identity')
        if not isinstance(key, str) or not key.strip():
            _missing(prefix + 'API_KEY must be configured independently on the trusted host')
        url = urlsplit(base)
        if (url.scheme not in ('http','https') or not url.hostname or url.username
                or url.password or url.query or url.fragment):
            raise ValueError('native provider base URL must be HTTP(S) without embedded credentials/query')
        if url.hostname != 'localhost':
            try: address=ipaddress.ip_address(url.hostname)
            except ValueError:
                _missing('Harvey native judges require explicit numeric loopback/private provider endpoints')
            if not (address.is_loopback or address.is_private):
                _missing('paid/public Harvey provider endpoints are disabled; configure local providers')
        request[provider] = {'model_name':model, 'api_key':key, 'base_url':base}
    pandoc = env.get('HG_JUDGE_HARVEY_PANDOC') or shutil.which('pandoc', path=env.get('PATH'))
    if not pandoc or not Path(pandoc).is_file():
        _missing('pandoc >=3.5 must be provisioned; set HG_JUDGE_HARVEY_PANDOC if outside PATH')
    from .special_runtime import binding, admit
    python = admit(binding('harvey_lab'))
    return Path(python), Path(pandoc).resolve(), request


def _child_environment(pandoc, env=None):
    env = os.environ if env is None else env
    # Dedicated LAB_ROOT and a fresh cwd prevent operator .env and user-session
    # state from selecting endpoints. Keys arrive only as the worker's stdin.
    keep = ('PATH','SYSTEMROOT','WINDIR','PATHEXT','LANG','SSL_CERT_FILE','SSL_CERT_DIR')
    child = {name:env[name] for name in keep if name in env}
    child['PATH'] = str(pandoc.parent) + os.pathsep + child.get('PATH','/usr/bin:/bin')
    if env.get('HG_JUDGE_HARVEY_WHEEL'):
        child['HG_JUDGE_HARVEY_WHEEL'] = env['HG_JUDGE_HARVEY_WHEEL']
    child['PYTHONDONTWRITEBYTECODE'] = '1'
    child['PYTHONNOUSERSITE'] = '1'
    return child


def readiness(manifest=None, *, timeout_s=30):
    """No network/model call. Refuse only absent/invalid actual prerequisites."""
    python, pandoc, request = settings()
    deadline = time.monotonic() + _positive(float(timeout_s), 'readiness timeout')
    from .special_runtime import binding, admit, fingerprint
    selected = binding('harvey_lab')
    python = admit(selected)
    identity = fingerprint(selected, {'worker': ROOT/'harvey_worker.py', 'bootstrap': ROOT/'special_runtime.py',
        'grader': ROOT/'vendor/flashrsi/integrations/_harvey_lab_grade.py'})
    result = subprocess.run([str(python), '-I', '-B', str(ROOT/'harvey_worker.py'), '--probe'],
        env=_child_environment(pandoc), capture_output=True, text=True, errors='replace', timeout=timeout_s)
    if result.returncode:
        _missing('managed worker probe failed; see data/datasets/harvey_lab/execution-recipe.md for fixed wheel and execution preparation')
    try:
        runtime = json.loads(result.stdout)
    except (ValueError, TypeError):
        _missing('native runtime probe did not return its measured identity')
    version = subprocess.run([str(pandoc),'--version'], capture_output=True, text=True,
                             errors='replace', timeout=_positive(min(10, deadline-time.monotonic()), 'remaining probe timeout'))
    match = re.match(r'pandoc (\d+)\.(\d+)', version.stdout)
    if version.returncode or not match or tuple(map(int,match.groups())) < (3,5):
        _missing('native document processing requires pandoc >=3.5')
    runtime.update(execution_identity=identity, python=str(python), pandoc=str(pandoc), pandoc_version=match.group(0),
        judges=[request[p]['model_name'] for p in ('anthropic','openai')],
        judge_profile='lab-standard-dual-v1' if tuple(request[p]['model_name'] for p in ('anthropic','openai')) == STANDARD_MODELS else 'custom-dual',
        upstream_revision=UPSTREAM, official_wheel_sha256=WHEEL_SHA256)
    return runtime


def export_paths(spec, case):
    return ['output']


def prepare_resources(native, task, output, files):
    """Project only the selected task's public documents during preparation.

    Call after the pinned native `_iter_tasks` populated `_sources_by_id`.
    Mutates the enclosing prepared manifest's file inventory; returns private
    case fields consumed by `setup_files` below. No rubric is staged publicly.
    """
    _directory, _collection, documents = native._sources_by_id[task.task_id]
    documents = Path(documents).resolve()
    from .native import activate
    activate()
    from flashrsi.benchmarks.harvey_lab import _files
    checksum = native._rows_by_id[task.task_id]['documents_checksum']
    if not isinstance(checksum,str) or re.fullmatch(r'[0-9a-f]{64}',checksum) is None:
        raise ValueError('native Harvey public document checksum is malformed')
    prefix = 'documents/' + checksum
    destination = Path(output) / prefix
    destination.mkdir(parents=True, exist_ok=True)
    cached = getattr(native, '_hg_prepared_documents', set())
    for source in ([] if checksum in cached else _files(documents)):
        relative = source.relative_to(documents)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[(Path(prefix) / relative).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    native._hg_prepared_documents = cached | {checksum}
    empty = Path(output) / 'empty-output'
    empty.mkdir(exist_ok=True)
    return {'documents_dir': prefix, 'output_template': 'empty-output'}


def setup_files(case, prepared_root, inventory):
    """Actor SETUP declarations from the platform's prepared public inventory."""
    prepared_root = Path(prepared_root).resolve()
    relative = case['documents_dir']
    path = prepared_root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(prepared_root) or not path.is_dir():
        raise ValueError('prepared Harvey documents are not a private-owned directory')
    prefix = relative.rstrip('/') + '/'
    declared = {name: digest for name, digest in inventory.items() if name.startswith(prefix)}
    actual = {}
    for source in path.rglob('*'):
        if source.is_symlink() or not source.is_dir() and not source.is_file():
            raise ValueError('prepared Harvey documents must be ordinary files')
        if source.is_file():
            actual[source.relative_to(prepared_root).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != declared:
        raise ValueError('selected Harvey public documents changed after preparation')
    template = prepared_root / case.get('output_template', 'empty-output')
    if template.is_symlink() or not template.resolve().is_relative_to(prepared_root) or not template.is_dir() or any(template.iterdir()):
        raise ValueError('prepared Harvey output template must be an owned empty directory')
    return [{'path':'documents', 'from':str(path)}, {'path':'output', 'from':str(template)}]


def _task_path(value):
    if not isinstance(value,str) or len(value.split('/'))<2 or any(
            part in ('','.','..') for part in value.split('/')) or '\\' in value or ':' in value:
        raise ValueError('native Harvey task id must stay inside tasks/')
    return Path(value)


def run(spec, artifact_root, *, task_id, run_id, trial_id, timeout_s):
    """Grade the platform-private export; never read a live actor workdir."""
    root = Path(artifact_root).resolve()
    started = time.monotonic()
    verdict = {'kind':'command', 'passed':False, 'score':None, 'outcome':'unmeasured'}
    try:
        case_raw = (root/'upstream_case.json').read_bytes()
        case = json.loads(case_raw)
        if case.get('task_id') != task_id:
            raise ValueError('private Harvey case belongs to another task')
        declared = spec.get('scorer_input', {})
        if declared and (declared.get('task_id') != task_id or declared.get('sha256') != hashlib.sha256(case_raw).hexdigest()):
            raise ValueError('private Harvey case digest mismatch')
        native_id = case['native_task_id']
        relative = _task_path(native_id)
        task = case['row']['task']
        if (not isinstance(task,dict) or not {'title','instructions','criteria'} <= task.keys()
                or not isinstance(task['criteria'],list) or not task['criteria']):
            raise ValueError('native Harvey task/rubric is malformed')
        runtime = readiness(timeout_s=min(30, _positive(float(timeout_s), 'platform timeout')))
        python, pandoc, request = settings()
        request['task_id'] = native_id
        native_spec = spec.get('native_benchmark') or {}
        if 'parallel' in native_spec:
            request['parallel'] = _positive(native_spec['parallel'],'parallel',integer=True)
        deadline = min(_positive(float(timeout_s) - (time.monotonic()-started), 'remaining platform timeout'), request['timeout'])
        task_dir = root/'tasks'/relative
        task_dir.mkdir(parents=True,exist_ok=True)
        (task_dir/'task.json').write_text(json.dumps(task), encoding='utf-8')
        output = root/'output'
        output.mkdir(exist_ok=True)
        destination = root/'results/run/output'
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(output,destination)
        # -I ignores cwd/PYTHONPATH. The worker adds only the fixed HG native
        # package, then the upstream installed wheel imports its own helpers.
        from .special_runtime import admit, fingerprint
        selected = runtime['execution_identity']['binding']
        python = admit(selected)
        current = fingerprint(selected, {'worker': ROOT/'harvey_worker.py', 'bootstrap': ROOT/'special_runtime.py',
            'grader': ROOT/'vendor/flashrsi/integrations/_harvey_lab_grade.py'})
        if current != runtime['execution_identity']:
            raise ValueError('Harvey worker source identity changed')
        proc = subprocess.Popen([str(python),'-I','-B',str(ROOT/'harvey_worker.py'),str(root)],
            cwd=root, env=_child_environment(pandoc), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', start_new_session=True)
        try:
            stdout,_stderr = proc.communicate(json.dumps(request),timeout=deadline)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            raise TimeoutError('native Harvey dual evaluation exceeded platform deadline') from None
        finally:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        if proc.returncode:
            raise RuntimeError('native Harvey evaluator did not complete both judges')
        result = json.loads(stdout)
        keys = {'all_pass_rate','criterion_pass_rate','all_pass'}
        if (set(result)!=keys or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in result.values())
                or result['all_pass_rate'] not in (0,.5,1)
                or result['all_pass'] != float(result['all_pass_rate']==1)):
            raise ValueError('native Harvey dual aggregate is malformed')
        value = result['all_pass_rate']
        from .special_runtime import public_harvey_runtime
        evidence = {'measured':True,'metrics':result,'native_runtime':public_harvey_runtime(runtime),
                    'task_id':task_id,'run_id':run_id,'trial_id':trial_id,
                    'metric_identity':'harvey-lab-v1.2.0.'+runtime['judge_profile']}
        return {**verdict,'score':value,'passed':value==1.0,'outcome':'measured','evidence':evidence,
                'detail':'official Harvey LAB v1.2.0 dual-provider file rubric completed'}
    except Exception as exc:
        # Official scoring exceptions and diagnostics can contain rubric text,
        # outputs and credentials. Preserve attribution type, never the payload.
        return {**verdict,'unmeasured':'native Harvey LAB unavailable: '+type(exc).__name__,
                'evidence':{'measured':False,'error_type':type(exc).__name__}}
