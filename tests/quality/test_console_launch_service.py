"""Authored launch ports, persistence and detach lifecycle; no actual driver/model."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import catalogue, launch, server, settings


def form(**overrides):
    return {'dataset': 'demo', 'harness': 'loop', 'task_model': 'mock', 'rounds': 2,
            'jobs': 1, 'trials': 1, 'sampling': 'all', 'improver': 'noop',
            'improver_model': '', **overrides}


@pytest.fixture
def ready(tmp_path):
    platform = tmp_path / 'platform'; platform.mkdir()
    driver = platform / 'driver.py'; driver.write_text('# never execute')
    runs = tmp_path / 'runs'; runs.mkdir()
    budget = tmp_path / 'budget'; budget.mkdir()
    state = tmp_path / 'state'; state.mkdir()
    paths = launch.LaunchPaths(driver, platform, platform / 'methods', runs, state,
                              state / '.launch', state / '.launch-log.jsonl', budget, True, platform)
    catalog = catalogue.LaunchCatalogue(lambda: [{'name': 'demo'}], lambda: ['loop'],
        lambda: [{'name': 'noop', 'kind': 'cli', 'model': ''},
                 {'name': 'endpoint', 'kind': 'openai', 'model': 'suggested'}],
        lambda: ['mock'], lambda: [])
    store = settings.SettingsStore(tmp_path / 'settings.json', tmp_path / 'legacy.json', {})
    environment = lambda data: launch.build_environment(data, load_settings=store.read)
    validate = lambda data, **kwargs: launch.validate_launch_request(data, paths=paths,
        catalogue=catalog, python_supported=lambda: True,
        sandbox_supported=lambda: SimpleNamespace(ok=True, detail='fixture sandbox'),
        budget_capacity=lambda: 3,
        launch_env=environment, **kwargs)
    plan = launch.InvocationPort(validate,
        lambda data, run_id: launch.build_argv(data, run_id, paths=paths, budget_capacity=lambda: 3),
        environment)
    return SimpleNamespace(paths=paths, catalogue=catalog, store=store, plan=plan)


def test_settings_owner_preserves_alias_binding_format_and_permissions(tmp_path):
    path = tmp_path / 'settings.json'
    legacy = tmp_path / 'legacy.json'
    legacy.write_text(json.dumps({'base_url': 'http://legacy.invalid/v1', 'api_key': 'legacy-key'}))
    store = settings.SettingsStore(path, legacy, {'HG_CONSOLE_TASK_BASE_URL': 'http://solver.invalid/v1',
                                                 'HG_CONSOLE_TASK_API_KEY': 'task-key'})
    current = store.read()
    merged = store.write({'chat': {'model': 'editor'}, 'task': {'model': 'solver'}}, current=current)
    assert merged['meta']['api_key'] == 'legacy-key' and merged['meta']['model'] == 'editor'
    assert path.read_text() == json.dumps(merged, indent=1) + '\n'
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(path.read_text())) == {'task', 'meta', 'assistant'}
    with pytest.raises(ValueError, match='explicit API key'):
        store.write({'task': {'base_url': 'http://other.invalid/v1'}}, current=store.read())
    public = settings.redacted(store.read())
    assert 'task-key' not in json.dumps(public) and public['task']['api_key_set']


def test_invalid_field_precedes_capability_storage_and_catalogue(ready, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid field reached later launch stages')
    code, body = launch.validate_launch_request(form(dataset=1), paths=ready.paths,
        catalogue=SimpleNamespace(adapters=forbidden), python_supported=forbidden,
        sandbox_supported=forbidden, budget_capacity=forbidden, launch_env=forbidden)
    assert code == 400 and body['error'] == 'bad-value'
    ready.paths.driver.unlink()
    code, body = launch.validate_launch_request(form(), paths=ready.paths,
        catalogue=ready.catalogue, python_supported=lambda: False,
        sandbox_supported=forbidden, budget_capacity=forbidden, launch_env=forbidden)
    assert code == 400 and body['error'] == 'unsupported-python'


def test_failed_sandbox_probe_blocks_launch_before_storage_or_catalogue(ready):
    def forbidden(*args, **kwargs):
        pytest.fail('sandbox failure reached a later launch stage')
    code, body = launch.validate_launch_request(form(), paths=ready.paths,
        catalogue=SimpleNamespace(adapters=forbidden), python_supported=lambda: True,
        sandbox_supported=lambda: SimpleNamespace(ok=False, detail='fixture namespace failed'),
        budget_capacity=forbidden, launch_env=forbidden)
    assert code == 400
    assert body == {'error': 'sandbox-unavailable', 'detail': 'fixture namespace failed'}


def test_single_measurement_does_not_claim_or_configure_an_editor(ready):
    request = form(method='none', rounds=10, improver='endpoint', improver_model='unused')
    argv = launch.build_argv(request, 'measurement', paths=ready.paths, budget_capacity=lambda: 3)
    assert argv[argv.index('--rounds') + 1] == '0'
    assert argv[argv.index('--improver') + 1] == 'none'
    assert '--improver-model' not in argv
    assert launch.new_run_id(request).split('-')[-2] == 'measurement'
    scope = launch.scope_preview(request, ready.plan.environment(request), catalogue=ready.catalogue)
    assert scope['rounds'] == 0
    assert scope['editor'] == {'method': 'none', 'improver': 'none', 'model': None,
                               'kind': None, 'active': False}


def test_preview_and_budget_preflight_are_read_only_and_do_not_probe(ready, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('preview attempted spawn or endpoint probe')
    monkeypatch.setattr(launch.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(settings.urllib.request, 'build_opener', forbidden)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    payload = launch.preview(form(), plan=ready.plan, catalogue=ready.catalogue)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert payload['preflight'] == {'ok': True, 'status': 200}
    assert payload['scope']['task_count'] is None
    assert before == after and not ready.paths.launch_dir.exists()
    assert list(ready.paths.budget_dir.iterdir()) == []
    assert list(ready.paths.runs.iterdir()) == []


def test_endpoint_override_is_private_and_spawn_owns_only_the_open_log(ready):
    registry = ready.paths.platform / 'harnessgrad/improvers/improvers.json'
    registry.parent.mkdir(parents=True)
    registry.write_text('{"improvers":{"endpoint":{"kind":"openai","model":"old"}}}')
    calls = []
    def spawn(argv, **kwargs):
        assert not kwargs['stdout'].closed and kwargs['start_new_session'] is True
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=123)
    code, result = launch.start(form(improver='endpoint', improver_model='chosen',
        meta_base_url='http://editor.invalid/v1', meta_api_key='synthetic-secret'),
        paths=ready.paths, catalogue=ready.catalogue, plan=ready.plan,
        make_run_id=lambda data: 'authored', spawn=spawn, base_environment={'PATH': '/usr/bin:/bin'})
    assert code == 202 and len(calls) == 1
    argv, options = calls[0]
    assert options['stdout'].closed
    assert options['env']['HG_METHOD_API_KEY'] == 'synthetic-secret'
    override = Path(options['env']['HG_IMPROVER_CONFIG'])
    assert override.stat().st_mode & 0o777 == 0o600
    document = json.loads(override.read_text())['improvers']['endpoint']
    assert document['api_key_env'] == 'HG_METHOD_API_KEY' and document['model'] == 'chosen'
    for path in ready.paths.state_root.rglob('*'):
        if path.is_file():
            assert 'synthetic-secret' not in path.read_text()
    assert json.loads(ready.paths.launch_log.read_text())['argv'] == result['argv'] == argv


def test_lost_http_response_does_not_retry_or_kill_detached_launch(ready, monkeypatch):
    calls = []
    class Detached:
        def terminate(self):
            pytest.fail('response cancellation terminated a detached task')
        kill = terminate
    process = Detached()
    def spawn(argv, **kwargs):
        calls.append(argv)
        return process
    for name, value in {'DRIVER': ready.paths.driver, 'PLATFORM': ready.paths.platform,
        'PLATFORM_ROOT': ready.paths.platform, 'RUNS': ready.paths.runs,
        'STATE_ROOT': ready.paths.state_root, 'LAUNCH_DIR': ready.paths.launch_dir,
        'LAUNCH_LOG': ready.paths.launch_log, 'BUDGET_DIR': ready.paths.budget_dir,
        'BUDGET_CAPACITY': '3', 'SETTINGS': ready.store.path, 'LLM_CONFIG': ready.store.legacy_path}.items():
        monkeypatch.setattr(server, name, value)
    monkeypatch.setattr(server, 'guard_launch', ready.plan.validate)
    monkeypatch.setattr(server, 'new_run_id', lambda data: 'detached')
    monkeypatch.setattr(server, 'improvers', ready.catalogue.improvers)
    monkeypatch.setattr(server.subprocess, 'Popen', spawn)
    handler = object.__new__(server.Handler)
    handler.path = '/api/runs'
    handler._api_guard = lambda: True
    handler._post_framing = lambda **kwargs: True
    handler._json_body = lambda: form()
    def cancelled(*args, **kwargs):
        raise BrokenPipeError('authored client disconnected')
    handler._send = cancelled
    with pytest.raises(BrokenPipeError):
        handler.do_POST()
    assert len(calls) == 1
    assert len(ready.paths.launch_log.read_text().splitlines()) == 1
    assert (ready.paths.launch_dir / 'detached.log').is_file()
