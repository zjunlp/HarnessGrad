"""End-to-end adapter delivery controls with synthetic CLI processes."""
import importlib.util
import json
from pathlib import Path

import pytest

from eval.run.task_contract import cli_task_text, runtime_task
from eval.run.task_workspace import TaskWorkspace

ROOT = Path(__file__).resolve().parents[2]


def adapter(name):
    spec = importlib.util.spec_from_file_location('_delivery_' + name.replace('-', '_'),
                                                   ROOT / 'base_harness' / name / 'agent.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_platform_renders_public_content_once_without_private_metadata(tmp_path):
    task = {'goal': 'Solve the problem.', 'inputs': {'value': 0},
            'submission': {'kind': 'files', 'paths': ['nested/result.json']},
            'private_verifier': 'PRIVATE-VERIFIER'}
    rendered = runtime_task(task, {'runtime_prompt': 'Use the public tool.'}, verdict_kind='answer')
    public = cli_task_text(rendered)
    assert 'Use the public tool.' in public and 'nested/result.json' in public
    assert '"value": 0' in public and 'PRIVATE-VERIFIER' not in public
    assert 'Write the answer, and nothing else' not in public
    workspace = TaskWorkspace()
    try:
        saved = json.loads(workspace.write_public_task({**rendered, 'cli_task': 'forged'}).read_text())
        assert saved['cli_task'] == public
        for name in ('codex', 'deepseek-harness', 'openclaw', 'opencode'):
            mod = adapter(name)
            prompt = mod.task_prompt(saved, {'prompt': {'preamble': 'Editable'}}) if name == 'deepseek-harness' else mod.task_prompt(saved)
            assert public in prompt
            assert prompt.count('"value": 0') == 1
    finally:
        workspace.remove_task()


@pytest.mark.parametrize('name', ['codex', 'deepseek-harness'])
@pytest.mark.parametrize('mode', ['complete', 'missing', 'symlink'])
def test_file_delivery_through_adapter_run(name, mode, tmp_path, monkeypatch):
    mod = adapter(name)
    work = tmp_path / 'work'; work.mkdir()
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    task = {'goal': 'Make result.', 'submission': {'kind': 'files', 'paths': ['result.json']}}
    class FakeCLI:
        returncode = 0
        def __init__(self, *args, **kwargs): pass
        def communicate(self, *args, **kwargs):
            if mode == 'complete':
                (work / 'result.json').write_text('{}')
            elif mode == 'symlink':
                (work / 'result.json').symlink_to(tmp_path / 'external.json')
                (tmp_path / 'external.json').write_text('{}')
            return '', ''
    monkeypatch.setattr(mod.subprocess, 'Popen', FakeCLI)
    if name == 'codex':
        monkeypatch.setattr(mod, '_runtime', lambda: {'path': [], 'argv': ['fake-codex']})
    else:
        monkeypatch.setattr(mod, 'runtime', lambda env: {'layout': 'npm', 'root': None, 'node': None})
        monkeypatch.setattr(mod, '_model_patch', lambda *args: None)
        monkeypatch.setattr(mod, 'facts', lambda *args: {'prompt_sha': '', 'config_sha': '', 'profile_sha': '', 'knobs_sha': ''})
        monkeypatch.setattr(mod, 'command', lambda *args, **kwargs: ['fake-dsh'])
        monkeypatch.setattr(mod, 'find_session_log', lambda *args: None)
    result = mod.run(task, work)
    records = [json.loads(line) for line in (work / 'trace.jsonl').read_text().splitlines()]
    if mode == 'complete':
        assert result == 0
        row = next(row for row in records if row.get('submitted'))
        assert row['answer_source'] == 'files' and row['submitted_files'] == ['result.json']
        assert not (work / 'answer.txt').exists()
    else:
        assert result != 0 and not any(row.get('submitted') for row in records)
        assert any('unsafe submission' in row.get('error', '').lower() for row in records)


@pytest.mark.parametrize('value', [None, {}, {'kind':'files','paths':['../outside']},
                                     {'kind':'files','paths':['x'],'expected':'secret'}])
def test_invalid_contract_rejected(value):
    with pytest.raises(ValueError, match='submission'):
        cli_task_text({'goal': 'fixture', 'submission': value})


@pytest.mark.parametrize('name', ['codex', 'deepseek-harness'])
def test_files_allow_empty_and_reject_symlinked_parent(name, tmp_path):
    mod = adapter(name)
    work = tmp_path / 'work'; work.mkdir()
    (work / 'empty.bin').write_bytes(b'')
    task = {'submission': {'kind': 'files', 'paths': ['empty.bin']}}
    assert mod.file_submission(work, task)['answer_source'] == 'files'
    actual = work / 'real'; actual.mkdir()
    (actual / 'file').write_text('content')
    (work / 'alias').symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match='unsafe submission'):
        mod.file_submission(work, {'submission': {'kind': 'files', 'paths': ['alias/file']}})
