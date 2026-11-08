"""Tiny instrument controls using real Docker and the pinned official scorer.

These local-declared source rows are NOT SWE Verified benchmark measurements.
No model is called. The full driver/relay/patch/pristine-grade path is exercised.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from data.scaffolds.native_research.adapter import Adapter
from data.scaffolds.native_research import swe_native as native
from eval.ckpt.git_state import stage_workspace
from eval.run.runner import run_one
from tools.prepare_native_research import prepare


@pytest.fixture(scope='module')
def native_fixture(tmp_path_factory):
    if not os.environ.get('HG_SWE_SOURCE') or not os.environ.get('HG_DATASET_RUNTIME_ROOT'):
        pytest.skip('pinned official SWE runtime not provisioned (HG_SWE_SOURCE/HG_DATASET_RUNTIME_ROOT)')
    native.official('check')
    context = tmp_path_factory.mktemp('swe-control-image')
    wheels = Path(os.environ.get('HG_SWE_CONTROL_WHEELS', '/tmp/hg-swe-control-wheels'))
    if not wheels.is_dir(): pytest.skip('offline pytest wheels for tiny control image not provisioned')
    shutil.copytree(wheels, context / 'wheels')
    base = subprocess.check_output(['docker', 'image', 'inspect', 'python:3.12-bookworm', '--format', '{{.Id}}'], text=True).strip()
    (context / 'Dockerfile').write_text('FROM python:3.12-bookworm' + '''
COPY wheels /wheels
RUN python3 -m pip install --no-index --find-links=/wheels pytest==8.3.5 && rm -rf /wheels
RUN mkdir /testbed && cd /testbed && git init && git config user.email control@example.invalid && git config user.name control && printf 'def add(a,b): return a-b\\n' > calculator.py && git add . && git commit -m baseline && git rev-parse HEAD > /base.txt && printf 'def add(a,b): return a+b\\n' > calculator.py && git add . && git commit -m private-future-fix && git rev-parse HEAD > /future.txt
RUN printf 'PRIVATE_ROOT_EVALUATOR' > /eval.sh
''')
    # Legacy builder resolves the already inspected local tag without contacting
    # a registry for BuildKit metadata. No network acquisition in this control.
    image = subprocess.check_output(['docker', 'build', '--pull=false', '--network=none', '-q', str(context)],
        text=True, env={**os.environ, 'DOCKER_BUILDKIT': '0'}).strip()
    images = [image]
    try:
        def image_file(name):
            return subprocess.check_output(['docker', 'run', '--rm', '--pull=never', '--network=none', '--entrypoint=cat', image, name], text=True).strip()
        baseline, future = image_file('/base.txt'), image_file('/future.txt')
        # Test code is private native eval_script, absent from the actor's image.
        script = """#!/bin/bash
set -uxo pipefail
cd /testbed
cat > /tmp/test_private.py <<'PY'
import sys
sys.path.insert(0, '/testbed')
from calculator import add
def test_secret_addition():
    assert add(17, 29) == 46
PY
cd /tmp
echo '>>>>> Start Test Output'
python3 -m pytest -rA test_private.py
echo '>>>>> End Test Output'
"""
        row = {'instance_id': 'control__calculator-1', 'repo': 'control/calculator', 'base_commit': baseline,
            'environment_setup_commit': baseline, 'problem_statement': 'Correct addition.', 'image': image,
            'version': 'control', 'patch': 'private reference repair', 'test_patch': 'private test patch',
            'eval_script': script, 'log_parser': 'parse_log_pytest', 'eval_type': 'pass_and_fail',
            'FAIL_TO_PASS': ['test_private.py::test_secret_addition'], 'PASS_TO_PASS': []}
        source = context / 'test.jsonl'
        source.write_text(json.dumps(row) + '\n')
        prepared = context / 'prepared'
        manifest = prepare('swe_bench', prepared, {'data_path': str(source)}, fetch=False)
        case = json.loads((prepared / manifest['records'][0]['case']).read_text())
        sha = manifest['files'][manifest['records'][0]['case']]
        entry = native.prepare_image(case, sha)
        images.append(entry['actor_image_id'])
        entry.update(workspace_bytes=128 << 20, workspace_inodes=16384, cpus=1, memory_mb=512)
        runtime = context / 'native-runtime.json'
        runtime.write_text(json.dumps({'schema': 'harnessgrad.swe-native-runtime.v1',
            'evaluator_revision': native.REVISION, 'instances': {case['native_task_id']: entry}}))
        yield prepared, runtime, future
    finally:
        for item in reversed(images):
            subprocess.run(['docker', 'image', 'rm', item], capture_output=True)


@pytest.mark.parametrize('mode,expected', [('repair', 1.), ('wrong', 0.), ('empty', 0.)])
def test_actual_platform_native_patch_grade(native_fixture, mode, expected, tmp_path, monkeypatch):
    prepared, runtime, future = native_fixture
    monkeypatch.setenv('HG_DATA_SWE_BENCH', str(prepared))
    monkeypatch.setenv('HG_SWE_BENCH_RUNTIME', str(runtime))
    adapter = Adapter('swe_bench')
    native_logs = []
    original_official = native.official
    def with_control_diagnostics(action, root=None, **kwargs):
        if action == 'grade':
            native_logs.append((root / 'test_output.txt').read_text())
            (tmp_path / 'native-control-test-output.txt').write_text(native_logs[-1])
        return original_official(action, root, **kwargs)
    monkeypatch.setattr(native, 'official', with_control_diagnostics)
    task = adapter.load()[0][0]
    source = tmp_path / 'control'
    source.mkdir()
    (source / 'harness.json').write_text(json.dumps({'name': 'native-swe-instrument-control',
        'entrypoint': 'agent.py', 'version': '1', 'env_kinds': ['files'], 'role': 'control'}))
    command = {'repair': "printf 'def add(a,b): return a+b\\n' > calculator.py",
        'wrong': "printf 'def add(a,b): return a*b\\n' > calculator.py", 'empty': 'true'}[mode]
    actor = '''import json,subprocess,sys
from pathlib import Path
assert not Path(PRIVATE_SOURCE).exists()
assert not Path('upstream_case.json').exists()
assert not Path('swe_episode').exists()
def run(command):
 p=subprocess.run([sys.executable,'swe.py','exec',command],capture_output=True,text=True)
 assert p.returncode==0,p.stderr+' '+p.stdout
 return json.loads(p.stdout)
probe=run('test ! -f /eval.sh && test ! -f /tmp/test_private.py && ! git cat-file -e FUTURE_HASH && cat calculator.py')
assert 'return a-b' in probe['stdout']
run(COMMAND)
Path('answer.txt').write_text('resolved=true; deliberately ignored')
Path('trace.jsonl').write_text(json.dumps({'command':'native patch control','observation':{'kind':'api','returncode':0,'output':'completed'}})+'\\n')
'''.replace('PRIVATE_SOURCE', repr(str(prepared))).replace('FUTURE_HASH', future).replace('COMMAND', repr(command))
    (source / 'agent.py').write_text(actor)
    work = tmp_path / 'work'
    repo = work / 'run/workspace'
    stage_workspace(source, repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    outcome = run_one(repo, task, sandbox=True, setup=adapter.setup(task['task_id']),
        env=adapter.environment(task['task_id']), verifier=adapter.verifier(task['task_id']),
        timeout_s=60, verify_timeout_s=120, run_id='native-swe-control-' + mode, trial_id='0')
    assert not outcome.get('invalid'), json.dumps(outcome, default=str)
    assert outcome['exit_code'] == 0, outcome.get('stderr')
    verdict = outcome['verdict']
    assert verdict['score'] == expected, (verdict.get('unmeasured'), native_logs)
    assert verdict['evidence']['measured'] is True
    assert verdict['evidence']['official_evaluation_performed'] is True
    assert verdict['evidence']['runtime']['cleanup']['complete'] is True
    assert verdict['evidence']['runtime']['observed']['network'] == 'none'
    assert 'private reference repair' not in json.dumps(outcome)
    assert 'test_secret_addition' not in json.dumps(outcome)


def test_actual_official_grader_reports_missing_execution_without_fabricated_pass(tmp_path):
    """Pinned SWE grading.py:365-375 has no generic missing-runner infra signature.

    This real evaluator control pins the boundary swe_native.run must check:
    an applied patch with no parseable native test execution is unmeasured, not
    a model failure scored zero. It requires the installed pinned evaluator but
    no Docker, model, dataset download, or benchmark task.
    """
    if not os.environ.get('HG_SWE_SOURCE') or not os.environ.get('HG_DATASET_RUNTIME_ROOT'):
        pytest.skip('pinned official evaluator not provisioned')
    native.official('check')
    row = {
        'instance_id': 'control__missing-runner-1', 'repo': 'control/missing-runner',
        'version': 'control', 'image': 'sha256:' + '1' * 64,
        'eval_script': '#!/bin/bash\nmissing_test_runner /tmp/test_private.py\n',
        'log_parser': 'parse_log_pytest', 'eval_type': 'pass_and_fail',
        'FAIL_TO_PASS': ['test_private.py::test_secret_addition'], 'PASS_TO_PASS': [],
    }
    (tmp_path / 'request.json').write_text(json.dumps({'row': row}))
    (tmp_path / 'model.patch').write_text('diff --git a/calculator.py b/calculator.py\n')
    (tmp_path / 'test_output.txt').write_text('/bin/bash: missing_test_runner: command not found\n')
    result = native.official('grade', tmp_path)
    assert result == {
        'resolved': False, 'patch_successfully_applied': False,
        'tests': {'FAIL_TO_PASS': {'passed': 0, 'total': 1},
                  'PASS_TO_PASS': {'passed': 0, 'total': 0}},
    }


def test_applied_patch_without_parseable_official_execution_is_unmeasured(tmp_path, monkeypatch):
    """A real official-grader failure shape must not become a platform zero."""
    from types import SimpleNamespace
    task = 'swe_bench/test/control'
    case = {'task_id': task, 'native_task_id': 'control__missing-runner-1',
        'row': {key: [] if key in ('FAIL_TO_PASS', 'PASS_TO_PASS') else 'control'
            for key in ('instance_id', 'image', 'repo', 'version', 'eval_script',
                        'log_parser', 'eval_type', 'FAIL_TO_PASS', 'PASS_TO_PASS')}}
    raw = json.dumps(case).encode()
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / 'case.json').write_bytes(raw)
    entry = {'base_commit': 'a' * 40, 'image_id': 'sha256:' + '1' * 64,
             'actor_image_id': 'sha256:' + '2' * 64}
    payload = {'task_id': task, 'case_sha256': sha, **entry, 'model_patch': 'nonempty patch', 'failure': None}
    (tmp_path / 'swe_episode').mkdir()
    (tmp_path / 'swe_episode/episode.json').write_text(json.dumps({'payload': payload}))
    monkeypatch.setattr(native, 'selected', lambda *a, **k: entry)
    monkeypatch.setattr(native, 'runtime_spec', lambda *a, **k: {})
    class Runtime:
        name = 'control'
        receipt = {'cleanup': {'complete': True}}
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def run(self, *a, **k): return SimpleNamespace(returncode=0)
    monkeypatch.setattr(native, 'Runtime', Runtime)
    monkeypatch.setattr(native, 'checked', lambda *a, **k: b'/bin/bash: missing_test_runner: command not found\n')
    def official(action, *a, **k):
        if action == 'prepare':
            return {'apply_commands': ['git apply'], 'apply_patch_fail': '>>>>> Patch Apply Failed'}
        return {'resolved': False, 'patch_successfully_applied': False,
                'tests': {'FAIL_TO_PASS': {'passed': 0, 'total': 1}, 'PASS_TO_PASS': {'passed': 0, 'total': 0}}}
    monkeypatch.setattr(native, 'official', official)
    result = native.run({'scorer_input': {'path': 'case.json'}}, tmp_path,
                       task_id=task, run_id='control', trial_id='0', timeout_s=10)
    assert result['score'] is None and result['evidence']['measured'] is False
    assert result['outcome'] == 'unmeasured'


def test_authenticated_source_executes_snapshot_not_mutable_cache(tmp_path, monkeypatch):
    import importlib.util
    import py_compile
    from data.scaffolds.native_research import swe_official
    source = tmp_path / 'upstream'
    package = source / 'swebench'
    package.mkdir(parents=True)
    path = package / '__init__.py'
    evil, good = b'VALUE="evil"\n', b'VALUE="good"\n'
    stamp = 1234567890
    path.write_bytes(evil)
    os.utime(path, (stamp, stamp))
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(good)
    os.utime(path, (stamp, stamp))  # A stale timestamp/size-valid cache remains.
    inventory = tmp_path / 'trusted'
    inventory.mkdir()
    (inventory / 'SWE_EVALUATOR_SOURCE.json').write_text(json.dumps({
        'revision': native.REVISION, 'files': {'swebench/__init__.py': hashlib.sha256(good).hexdigest()}}))
    monkeypatch.setattr(swe_official, 'HERE', inventory)
    monkeypatch.setenv('HG_SWE_SOURCE', str(source))
    # activate mutates sys.path, so restore it after this isolated loader control.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    original_flag = sys.dont_write_bytecode
    try:
        swe_official.activate()
        snapshot = Path(swe_official.SOURCE_COPY.name) / 'swebench/__init__.py'
        path.write_bytes(evil)  # Changes after admission cannot alter execution.
        loaded = importlib.util.spec_from_file_location('fixed_source_control', snapshot)
        module = importlib.util.module_from_spec(loaded)
        loaded.loader.exec_module(module)
        assert module.VALUE == 'good'
        assert not (snapshot.parent / '__pycache__').exists()
    finally:
        sys.dont_write_bytecode = original_flag
        if swe_official.SOURCE_COPY is not None:
            swe_official.SOURCE_COPY.cleanup()
            swe_official.SOURCE_COPY = None
