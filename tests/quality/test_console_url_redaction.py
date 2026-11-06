"""Synthetic offline coverage for secret-bearing endpoint display."""
import json
import pytest
from test_console_credential_binding import console, ready, form, http_console, post


@pytest.mark.parametrize('url,secret', [
    ('https://user:password@host.invalid/v1', 'password'),
    ('https://host.invalid/v1?key=querysecret', 'querysecret'),
    ('https://host.invalid/v1#fragmentsecret', 'fragmentsecret'),
    ('https://host.invalid/opaque-pathsecret', 'pathsecret'),
    ('https://host.invalid/v1\ncontrolsecret', 'controlsecret'),
])
def test_redaction_and_preservation(ready, url, secret):
    ready.save_settings({'task': {'base_url': url, 'api_key': 'newkey'}})
    before = ready.SETTINGS.read_bytes()
    settings = ready.settings_for_ui()
    defaults = ready.launch_form()['defaults']
    preview = ready.launch_preview(form(task_base_url=url))
    for payload in (settings, defaults, preview):
        assert secret not in json.dumps(payload)
    assert settings['task']['base_url_redacted']
    assert defaults['task_base_url_redacted']
    assert ready.launch_env(form())['HG_AGENT_BASE_URL'] == url
    ready.save_settings({'task': {'base_url': '', 'model': 'changed'}})
    assert ready.load_settings()['task']['base_url'] == url
    if '?' in url or '#' in url or '@' in url or '\n' in url:
        assert ready.guard_launch(form())[1]['error'] == 'bad-endpoint'
    assert before


def test_http_endpoints(ready, http_console):
    import http.client
    server, _ = http_console
    secret_url = 'https://name:secretpass@host.invalid/pathsecret?token=querysecret'
    ready.save_settings({'task': {'base_url': secret_url, 'api_key': 'key'}})
    for path in ('/api/settings', '/api/launch/form', '/api/launch/preview'):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        conn.request('GET', path)
        data = conn.getresponse().read().decode()
        conn.close()
        for secret in ('secretpass', 'pathsecret', 'querysecret'):
            assert secret not in data
    status, result = post(server, '/api/launch/preview', form(task_base_url=secret_url, task_api_key='hidden-key'))
    assert status == 200
    for secret in ('secretpass', 'pathsecret', 'querysecret', 'hidden-key'):
        assert secret not in json.dumps(result)
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
    conn.request('GET', '/api/launch/preview')
    assert conn.getresponse().status == 405
    conn.close()


@pytest.mark.parametrize('field,value', [('dataset', []), ('harness', {}), ('rounds', True),
                                           ('task_key_present', {}), ('meta_key_present', 'maybe')])
def test_preview_rejects_malformed_field_types(ready, http_console, field, value):
    server, _ = http_console
    status, result = post(server, '/api/launch/preview', {field: value})
    assert status == 400 and result['error'] == 'bad-preview-fields'
    if field in ('dataset', 'harness'):
        status, result = post(server, '/api/runs', form(**{field: value}))
        assert status == 400 and result['error'] == 'bad-value'


def test_preview_pending_meta_key_requires_url(ready, http_console):
    server, _ = http_console
    launch = form(meta_key_present=True)
    status, result = post(server, '/api/launch/preview', launch)
    assert status == 200 and result['preflight']['error'] == 'no-meta-url'
    assert ready.guard_launch(form(meta_api_key='synthetic'))[1]['error'] == 'no-meta-url'


@pytest.mark.parametrize('value', [True, 1.9, float('inf'), '1.9', '1e999'])
def test_launch_numeric_inputs_are_strict(ready, http_console, value):
    server, _ = http_console
    launch = form(rounds=value)
    assert ready.guard_launch(launch)[1]['error'] == 'bad-value'
    status, result = post(server, '/api/launch/preview', launch)
    assert (status == 400 and result['error'] == 'bad-preview-fields') or (status == 200 and result['preflight']['error'] == 'bad-value')
    status, result = post(server, '/api/runs', launch)
    assert status == 400 and result['error'] == 'bad-value'


def test_probe_exception_does_not_echo(ready, monkeypatch):
    secret_url = 'https://name:secretpass@host.invalid/pathsecret?token=querysecret'
    def fail(*args, **kwargs):
        raise ValueError(secret_url)
    monkeypatch.setattr(ready.urllib.request, 'Request', fail)
    assert 'secretpass' not in repr(ready.probe_endpoint(secret_url, 'key'))


def test_hidden_platform_fallback_requires_reentry(ready, monkeypatch):
    from types import SimpleNamespace
    import harnessgrad.config
    ready.SETTINGS.unlink()
    monkeypatch.setattr(harnessgrad.config, 'current', lambda: SimpleNamespace(agent=SimpleNamespace(
        model='synthetic', base_url='https://host.invalid/pathsecret', backend='openai')))
    defaults = ready.launch_form()['defaults']
    assert defaults['task_base_url_reenter'] and defaults['task_base_url_redacted']
    assert not defaults['task_base_url']
    assert ready.launch_env(form())['HG_AGENT_BASE_URL'] == ''


def test_safe_and_custom_paths(ready):
    assert ready.display_base_url('https://host.invalid/v1/') == 'https://host.invalid/v1/'
    custom = 'https://host.invalid/custom/path'
    ready.save_settings({'task': {'base_url': custom, 'api_key': 'newkey'}})
    assert ready.settings_for_ui()['task']['base_url'] == ''
    assert ready.guard_launch(form()) == (200, {})
    assert ready.launch_env(form(task_base_url='https://other.invalid/custom'))['HG_AGENT_API_KEY'] == ''
