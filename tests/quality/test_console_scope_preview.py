"""Offline preview contract: no datasets, Docker, model, or launcher are invoked."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def console():
    spec = importlib.util.spec_from_file_location('scope_preview_console', ROOT / 'console/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_describes_all_tasks_and_unknown_count_without_execution(monkeypatch):
    server = console()
    def forbidden(*args, **kwargs):
        raise AssertionError('preview must not execute or access a model')
    monkeypatch.setattr(server.subprocess, 'run', forbidden)
    monkeypatch.setattr(server.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(server, 'guard_launch', lambda *a, **kw: (200, {}))
    monkeypatch.setattr(server, 'launch_argv', lambda *a: ['synthetic-command'])
    monkeypatch.setattr(server, 'launch_env', lambda *a: {'HG_AGENT_BACKEND': 'mock', 'HG_AGENT_MODEL': 'synthetic-solver'})
    monkeypatch.setattr(server, 'improvers', lambda: [{'name': 'synthetic-editor', 'kind': 'cli', 'model': 'registry-model'}])
    form = {'dataset': 'synthetic', 'harness': 'synthetic', 'sampling': 'all',
            'rounds': 0, 'trials': 1, 'jobs': 3, 'task_backend': 'mock',
            'task_model': 'synthetic-solver', 'method': 'synthetic-method',
            'improver': 'synthetic-editor', 'improver_model': 'synthetic-model'}
    result = server.launch_preview(form)
    scope = result['scope']
    assert scope['dataset'] == 'synthetic' and scope['side'] == 'train'
    assert scope['sampling'] == 'all' and scope['task_count'] is None
    assert (scope['rounds'], scope['trials_per_task'], scope['jobs']) == (0, 1, 3)
    assert scope['solver']['model'] == 'synthetic-solver'
    assert scope['editor']['improver'] == 'synthetic-editor'
    assert scope['editor']['model'] == 'synthetic-model'
    assert scope['editor']['active'] is False and scope['editor']['kind'] == 'cli'
    assert scope['environment'] is None and scope['judge'] is None
    assert result['preflight']['ok']


def test_preview_and_candidate_page_explain_round_zero_and_untallied_scope():
    launch = (ROOT / 'console/static/launch.js').read_text()
    candidate = (ROOT / 'console/static/harness.js').read_text()
    assert 'rounds=0 means no improvement rounds, not one task' in launch
    assert 'rounds=0 means no improvement rounds, not one task' in candidate
    assert 'the count is not determined' in candidate
    assert 'Environment and judge' in launch
    assert 'No editor improvement runs.' in candidate
    assert 'id="scope"' in (ROOT / 'console/static/launch.html').read_text()
    assert 'id="trial-preview"' in (ROOT / 'console/static/harness.html').read_text()


def test_launch_scope_uses_effective_solver_and_editor_registry_fallback(monkeypatch):
    server = console()
    monkeypatch.setattr(server, 'improvers', lambda: [
        {'name': 'fixture-cli', 'kind': 'cli', 'model': 'registry-default'}])
    form = {'dataset': 'fixture', 'harness': 'fixture', 'sampling': 'all', 'rounds': 0,
            'trials': 1, 'jobs': 1, 'task_model': '', 'task_backend': '',
            'improver': 'fixture-cli', 'improver_model': '', 'method': 'none'}
    effective = {'HG_AGENT_BACKEND': 'mock', 'HG_AGENT_MODEL': 'effective-solver'}
    scope = server.launch_scope_preview(form, effective)
    assert scope['solver'] == {'backend': 'mock', 'model': 'effective-solver'}
    assert scope['editor']['model'] == 'registry-default'
    assert scope['editor']['kind'] == 'cli' and scope['editor']['active'] is False
    monkeypatch.setattr(server, 'budget_capacity', lambda: 1)
    argv = server.launch_argv(form, 'fixture-run')
    assert argv[argv.index('--side') + 1] == scope['side'] == 'train'
    assert argv[argv.index('--split') + 1] == scope['split'] == 'declared'
    assert argv[argv.index('--sampling') + 1] == scope['sampling'] == 'all'
    assert argv[argv.index('--rounds') + 1] == '0'
