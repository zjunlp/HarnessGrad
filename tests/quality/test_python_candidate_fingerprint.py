"""Source framing must distinguish changed code before measurement reuse."""
import copy
import hashlib
import json
import shutil
import sys
from unittest.mock import Mock

import pytest

from eval.ckpt.git_state import commit_state, init_repo
from eval.run import runner
from harnessgrad import candidate_runtime, config
from harnessgrad import python_harness_runtime as runtime


@pytest.fixture
def source(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    repo = work / 'candidate'
    repo.mkdir(parents=True)
    (repo / 'harness.json').write_text(json.dumps({
        'name': 'source-framing-control', 'version': '1', 'entrypoint': 'agent.py',
        'env_kinds': ['files'], 'runtime': {'kind': 'python',
            'python': f'{sys.version_info.major}.{sys.version_info.minor}',
            'requirements': 'requirements.lock'}}))
    (repo / 'requirements.lock').write_text('# Empty dependency closure\n')
    (repo / 'agent.py').write_text("print('not executed during preparation')\n")
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(tmp_path / 'store'))
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'uv-cache'))
    monkeypatch.setenv('UV_OFFLINE', '1')
    monkeypatch.delenv('HG_HARNESS_WHEELHOUSE', raising=False)
    config.reset()
    try:
        yield repo
    finally:
        config.reset()
        store = tmp_path / 'store'
        if store.exists():
            runtime.remove_instance(store)


def require_preparation():
    if shutil.which('uv') is None or shutil.which('bwrap') is None:
        pytest.skip('real source identity controls need uv and bubblewrap')


def test_binary_contents_cannot_impersonate_another_source_file(source):
    module = b"VALUE = 'changed'\n"
    (source / 'alpha.bin').write_bytes(b'prefix\0beta.py\0-\0' + module)
    before = runtime.source_fingerprint(source)
    (source / 'alpha.bin').write_bytes(b'prefix')
    (source / 'beta.py').write_bytes(module)
    assert runtime.source_fingerprint(source) != before


@pytest.mark.parametrize('change', ['path', 'executable', 'binary-content'])
def test_source_identity_keeps_file_names_modes_and_binary_bytes(source, change):
    path = source / 'data.bin'
    path.write_bytes(b'owned\0binary\xffdata')
    before = runtime.source_fingerprint(source)
    assert runtime.source_fingerprint(source) == before
    if change == 'path':
        path.rename(source / 'renamed.bin')
    elif change == 'executable':
        path.chmod(path.stat().st_mode ^ 0o100)
    else:
        path.write_bytes(path.read_bytes() + b'\0')
    assert runtime.source_fingerprint(source) != before


@pytest.mark.parametrize('version', [1, 2])
def test_obsolete_binding_is_rejected_without_admitting_or_rebuilding_its_artifact(source, monkeypatch, version):
    require_preparation()
    receipt = candidate_runtime.prepare(source)
    assert receipt['identity']['version'] == 3
    legacy = copy.deepcopy(receipt['identity'])
    legacy['version'] = version
    legacy['sha256'] = hashlib.sha256(runtime.canonical({
        key: value for key, value in legacy.items() if key != 'sha256'})).hexdigest()
    touched = Mock(side_effect=AssertionError('obsolete binding reached artifact admission or build'))
    monkeypatch.setattr(runtime, '_read_artifact', touched)
    monkeypatch.setattr(runtime, '_build', touched)
    for action in (runtime.validate_binding,
                   lambda binding: candidate_runtime.admit(source, binding),
                   lambda binding: candidate_runtime.prepare(source, binding=binding)):
        with pytest.raises(ValueError, match='binding version'):
            action(legacy)
    touched.assert_not_called()


def test_ignored_code_change_refuses_the_old_binding_and_never_reuses_its_score(source, monkeypatch):
    require_preparation()
    (source / '.gitignore').write_text('alpha.bin\nbeta.py\n')
    (source / 'agent.py').write_text('''
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
answer='initial'
if (Path(__file__).parent/'beta.py').exists():
    import beta
    answer=beta.VALUE
work=Path(a.workdir)
(work/'answer.txt').write_text(answer)
(work/'trace.jsonl').write_text(json.dumps({'answer':answer})+'\\n')
''')
    module = b"VALUE = 'changed'\n"
    (source / 'alpha.bin').write_bytes(b'prefix\0beta.py\0-\0' + module)
    init_repo(source)
    sha = commit_state(source, 'initial source framing control')
    original = candidate_runtime.prepare(source)['identity']
    calls = Mock(wraps=runner.run_one)
    monkeypatch.setattr(runner, 'run_one', calls)
    cache = {}

    def evaluate(binding):
        return runner._evaluate(source, [{'task_id': 'control'}], {'control': 'initial'},
            cache=cache, harness_sha=sha, harness_runtime_identity=binding,
            envs={'control': {'kind': 'files', 'network': 'none'}},
            verifiers={'control': {'kind': 'answer', 'expected': 'initial'}})

    assert evaluate(original)['per_task'] == {'control': 1.0}
    assert evaluate(original)['cached_tasks'] == ['control']
    assert calls.call_count == 1
    (source / 'alpha.bin').write_bytes(b'prefix')
    (source / 'beta.py').write_bytes(module)
    assert commit_state(source, 'changed ignored source') == sha
    assert runtime.source_fingerprint(source) != original['source_sha256']
    refused = evaluate(original)
    assert refused['invalid'] and not refused['per_task'] and not refused['cached_tasks']
    assert calls.call_count == 1

    prepared = candidate_runtime.prepare(source)
    current = prepared['identity']
    assert prepared['cache_hit'] and current['artifact'] == original['artifact']
    assert current != original
    changed = evaluate(current)
    assert not changed['invalid'] and not changed['cached_tasks']
    assert changed['per_task'] == {'control': 0.0} and calls.call_count == 2
    assert evaluate(current)['cached_tasks'] == ['control']
    assert calls.call_count == 2
