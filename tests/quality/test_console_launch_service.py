"""Authored launch ports, persistence and detach lifecycle; no actual driver/model."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import catalogue, custom_methods, launch, server, settings


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


def test_invalid_sampling_is_refused_before_launch(ready):
    code, body = ready.plan.validate(form(sampling='default'))
    assert code == 400 and body['error'] == 'bad-sampling'
    assert '--fail-on-incomplete' in launch.build_argv(form(), 'fixture', paths=ready.paths,
                                                        budget_capacity=lambda: 3)


def test_method_selection_cannot_escape_the_catalogue(ready):
    target = ready.paths.platform / 'outside'; target.mkdir()
    (target / 'run.py').write_text('print(1)')
    code, body = ready.plan.validate(form(method='../outside'))
    assert code == 400 and body['error'] == 'unknown-method'


def test_failed_sandbox_probe_blocks_launch_before_storage_or_catalogue(ready):
    def forbidden(*args, **kwargs):
        pytest.fail('sandbox failure reached a later launch stage')
    code, body = launch.validate_launch_request(form(), paths=ready.paths,
        catalogue=SimpleNamespace(adapters=forbidden), python_supported=lambda: True,
        sandbox_supported=lambda: SimpleNamespace(ok=False, detail='fixture namespace failed'),
        budget_capacity=forbidden, launch_env=forbidden)
    assert code == 400
    assert body['error'] == 'sandbox-unavailable'
    assert 'fixture namespace failed' in body['detail'] and 'bubblewrap' in body['detail']


def test_non_linux_launch_has_actionable_guidance(ready, monkeypatch):
    monkeypatch.setattr(launch.platform, 'system', lambda: 'Darwin')
    code, body = ready.plan.validate(form())
    assert code == 400 and body['error'] == 'unsupported-platform'
    assert 'Linux' in body['detail'] and 'bubblewrap' in body['detail']


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


@pytest.mark.parametrize('mode', [{'method': 'none', 'rounds': 10}, {'rounds': 0}])
def test_single_measurement_ignores_stale_editor_configuration(ready, mode):
    request = form(improver='endpoint', improver_model='unused',
                   meta_base_url='http://editor.invalid/v1', meta_api_key='unused-key', **mode)
    calls = []
    code, result = launch.start(request, paths=ready.paths, catalogue=ready.catalogue,
        plan=ready.plan, make_run_id=lambda data: 'measurement',
        spawn=lambda argv, **kwargs: calls.append((argv, kwargs)), base_environment={})
    assert code == 202 and len(calls) == 1
    assert calls[0][0][calls[0][0].index('--improver') + 1] == 'none'
    assert not any(key.startswith('HG_METHOD_') for key in calls[0][1]['env'])
    assert 'HG_IMPROVER_CONFIG' not in calls[0][1]['env']
    assert list(ready.paths.launch_dir.glob('*.improvers.json')) == []


def test_candidate_preview_binds_both_identities_and_refuses_changes(ready, tmp_path):
    root = tmp_path / 'candidates'; candidate = root / 'mine'; candidate.mkdir(parents=True)
    (candidate / 'harness.json').write_text(json.dumps({'name': 'mine', 'version': '1', 'entrypoint': 'agent.py'}))
    (candidate / 'agent.py').write_text('print("hello")\n')
    paths = replace(ready.paths, candidates_root=root)
    request = form(harness='candidate:mine', method='none', rounds=0)
    argv = launch.build_argv(request, 'candidate-run', paths=paths, budget_capacity=lambda: 3)
    assert argv[argv.index('--harness-path') + 1] == str(candidate)
    assert '--harness' not in argv
    for flag, key in (('--expected-harness-sha256', 'candidate_sha256'),
                      ('--expected-harness-filesystem-sha256', 'candidate_filesystem_sha256')):
        request[key] = argv[argv.index(flag) + 1]
    assert launch._candidate_spec(request, paths)[1] == request['candidate_sha256']
    (candidate / 'agent.py').write_text('print("changed")\n')
    with pytest.raises(ValueError, match='changed since preview'):
        launch._candidate_spec(request, paths)
    assert launch._catalogue_selection(request, ready.catalogue, paths)[1]['error'] == 'candidate-unavailable'


def test_candidate_start_requires_reviewed_identity(ready):
    code, body = launch.start(form(harness='candidate:mine'), paths=ready.paths,
        catalogue=ready.catalogue, plan=ready.plan, make_run_id=lambda _: 'x',
        spawn=lambda *args, **kwargs: pytest.fail('must not spawn'), base_environment={})
    assert code == 400 and body['error'] == 'candidate-preview-required'


def test_launch_does_not_append_to_an_existing_launch_log(ready):
    ready.paths.launch_dir.mkdir()
    old = ready.paths.launch_dir / 'collision.log'; old.write_text('old evidence')
    code, body = launch.start(form(), paths=ready.paths, catalogue=ready.catalogue,
        plan=ready.plan, make_run_id=lambda _: 'collision',
        spawn=lambda *args, **kwargs: pytest.fail('must not spawn'), base_environment={})
    assert code == 409 and body['error'] == 'launch-id-exists'
    assert old.read_text() == 'old evidence'


def test_custom_method_is_copied_into_a_dedicated_run_directory(ready, tmp_path):
    methods = tmp_path / 'methods'
    source = 'print("hello")\n'
    digest = custom_methods.create(methods, 'my_method', source)
    paths = replace(ready.paths, custom_methods_root=methods)
    request = form(method='custom:my_method', rounds=2, custom_method_sha256=digest)
    plan = launch.InvocationPort(lambda data: (200, {}),
        lambda data, run_id: launch.build_argv(data, run_id, paths=paths, budget_capacity=lambda: 3),
        ready.plan.environment)
    calls = []
    code, result = launch.start(request, paths=paths, catalogue=ready.catalogue, plan=plan,
        make_run_id=lambda _: 'console-test', spawn=lambda argv, **kw: calls.append(argv), base_environment={})
    assert code == 202 and len(calls) == 1
    staged = paths.launch_dir / 'method-sources/console-test/run.py'
    assert staged.read_text() == source
    assert list(staged.parent.iterdir()) == [staged]
    assert str(staged) in result['argv'][result['argv'].index('--method-entrypoint') + 1]
    (methods / 'my_method.py').write_text('print("changed")')
    assert staged.read_text() == source
    assert launch._host_capabilities(request, paths, ready.catalogue, lambda: True,
        lambda: SimpleNamespace(ok=True)) [1]['error'] == 'custom-method-unavailable'


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
    assert json.loads(ready.paths.launch_log.read_text())['argv'] == result['argv']
    assert argv[argv.index('--') + 1:] == result['argv']


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
