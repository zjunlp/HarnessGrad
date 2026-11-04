"""Launch model choices reach the driver without leaking credentials into previews."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def console():
    spec = importlib.util.spec_from_file_location('launch_models_server', ROOT / 'console/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(tmp_path, monkeypatch):
    m = console()
    runs = tmp_path / 'runs'; runs.mkdir()
    budget = tmp_path / 'budget'; budget.mkdir()
    driver = tmp_path / 'driver.py'; driver.write_text('# synthetic')
    monkeypatch.setattr(m, 'RUNS', runs)
    monkeypatch.setattr(m, 'BUDGET_DIR', budget)
    monkeypatch.setattr(m, 'DRIVER', driver)
    monkeypatch.setattr(m, 'SETTINGS', tmp_path / 'settings.json')
    monkeypatch.setattr(m, 'LLM_CONFIG', tmp_path / 'absent.json')
    monkeypatch.setattr(m, 'python_supported', lambda: True)
    monkeypatch.setattr(m, 'procfs_readable', lambda: True)
    monkeypatch.setattr(m, 'live_drivers_by_run', lambda: {})
    monkeypatch.setattr(m, 'adapters', lambda: [{'name': 'demo'}])
    monkeypatch.setattr(m, 'base_harnesses', lambda: ['loop'])
    monkeypatch.setattr(m, 'improvers', lambda: [{'name': 'dsh', 'kind': 'cli', 'model': 'suggested'},
                                                  {'name': 'local', 'kind': 'openai-compatible', 'model': ''}])
    return m


def form(**kwargs):
    value = {'dataset': 'demo', 'harness': 'loop', 'task_backend': 'mock', 'task_model': 'mock',
             'rounds': 1, 'jobs': 1, 'trials': 1, 'sampling': 'all', 'improver': 'dsh',
             'improver_model': ''}
    value.update(kwargs)
    return value


def test_live_task_model_propagates_explicit_backend_and_key(setup):
    f = form(task_backend='openai', task_model='my-model', task_base_url='http://127.0.0.1:8002/v1', task_api_key='synthetic-secret')
    assert setup.guard_launch(f) == (200, {})
    env = setup.launch_env(f)
    assert env['HG_AGENT_BACKEND'] == 'openai' and env['HG_AGENT_MODEL'] == 'my-model'
    assert env['HG_AGENT_BASE_URL'] == f['task_base_url'] and env['HG_AGENT_API_KEY'] == 'synthetic-secret'
    assert 'synthetic-secret' not in repr(setup.launch_preview(f))


def test_mock_never_inherits_live_endpoint_and_backend_mismatch_is_rejected(setup):
    setup.save_settings({'task': {'model': 'live', 'base_url': 'https://example.invalid/v1', 'api_key': 'secret'}})
    env = setup.launch_env(form())
    assert env['HG_AGENT_BACKEND'] == 'mock'
    assert env['HG_AGENT_BASE_URL'] == env['HG_AGENT_API_KEY'] == ''
    assert setup.guard_launch(form(task_model='live'))[1]['error'] == 'task-backend-mismatch'


def test_preview_key_hint_never_bypasses_real_post_guard(setup):
    f = form(task_backend='openai', task_model='local', task_base_url='http://127.0.0.1:8002/v1',
             task_key_present='true', _preview_key_pending=True)
    assert setup.launch_preview(f)['preflight']['ok'] is True
    assert setup.guard_launch(f)[1]['error'] == 'no-task-key'


def test_embedded_endpoint_credentials_rejected(setup):
    f = form(task_backend='openai', task_model='local', task_base_url='https://user:pass@example.invalid/v1', task_api_key='x')
    assert setup.guard_launch(f)[1]['error'] == 'bad-endpoint'


def test_cli_improver_keeps_its_transport_separate_from_method_endpoint(setup):
    f = form(improver='dsh', improver_model='meta-model', meta_base_url='https://example.invalid/v1', meta_api_key='meta-secret')
    assert setup.guard_launch(f) == (200, {})
    env = setup.launch_env(f)
    assert env['HG_METHOD_MODEL'] == 'meta-model' and env['HG_METHOD_API_KEY'] == 'meta-secret'
    assert 'HG_IMPROVER_CONFIG' not in env
