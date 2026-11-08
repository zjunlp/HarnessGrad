"""Real loopback probes never borrow an undeclared credential from another role."""
import http.server
import json
import os
import threading
from types import SimpleNamespace

import pytest


class ProbeServer(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), ProbeHandler)
        self.requests = []
        self.responses_support_tools = True

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_port}/v1'


class ProbeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, self.headers.get('Authorization'), payload['model']))
        if self.path.endswith('/responses'):
            body = {'output': ([{'type': 'function_call', 'name': 'write_note'}]
                               if self.server.responses_support_tools else [])}
        else:
            body = {'choices': [{'message': {'tool_calls': [{'function': {'name': 'write_note'}}]}}]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def control(tmp_path, monkeypatch):
    # No personal config, proxy or real role credentials enter this control.
    monkeypatch.setattr(os, 'environ', {'PATH': os.defpath, 'HOME': str(tmp_path),
        'CODEX_HOME': str(tmp_path), 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
        'HG_AGENT_BACKEND': 'openai', 'HG_AGENT_MODEL': 'authored-agent',
        'HG_AGENT_API_KEY': 'fake-agent-A'})
    import driver
    from harnessgrad import config
    from harnessgrad.orchestration import improver_readiness
    config.reset()
    agent, improver = ProbeServer(), ProbeServer()
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (agent, improver)]
    for thread in threads:
        thread.start()
    os.environ['HG_AGENT_BASE_URL'] = agent.url
    events = []
    declaration = dict(ok=True, name='authored-cli', kind='cli', model='authored-improver',
                       version='1', sha256='f' * 64)

    def run(*, env_key=None, key=None, same_origin=False):
        text = ('model_provider="authored"\n[model_providers.authored]\n'
                f'base_url={json.dumps(agent.url if same_origin else improver.url)}\n'
                'requires_openai_auth=false\n')
        if env_key:
            text += f'env_key={json.dumps(env_key)}\n'
            if key is not None:
                os.environ[env_key] = key
        (tmp_path / 'config.toml').write_text(text)
        return improver_readiness.resolve_readiness(
            improver_readiness.ImproverRequest('authored-cli', None, None),
            reporter=SimpleNamespace(note=lambda message, **kw: events.append((message, kw))),
            load_config=lambda: {}, resolve_config=lambda *a, **kw: declaration,
            provider_url=driver._cli_provider_base_url, probe=driver._probe_tool_calls)
    try:
        yield SimpleNamespace(run=run, agent=agent, improver=improver,
                              declaration=declaration, events=events)
    finally:
        for server in (agent, improver):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)
        config.reset()


@pytest.mark.parametrize('env_key,key,expected,same_origin', [
    (None, None, 'local', False),
    ('SELECTED_CLI_KEY', 'fake-improver-B', 'fake-improver-B', False),
    ('HG_AGENT_API_KEY', None, 'fake-agent-A', True),
])
def test_only_explicit_cli_key_or_nonsecret_dummy_reaches_selected_origin(
        control, env_key, key, expected, same_origin):
    result = control.run(env_key=env_key, key=key, same_origin=same_origin)
    selected = control.agent if same_origin else control.improver
    other = control.improver if same_origin else control.agent
    assert result.tool_calls is True and not result.refused
    assert result.declaration is control.declaration
    assert selected.requests == [('/v1/responses', f'Bearer {expected}', 'authored-improver'),
                                 ('/v1/chat/completions', f'Bearer {expected}', 'authored-improver')]
    assert not other.requests


@pytest.mark.parametrize('value', [None, ''])
def test_missing_declared_credential_is_unprobed_and_refused_without_requests(control, value):
    result = control.run(env_key='UNSET_CLI_KEY', key=value)
    assert result.tool_calls is None and result.refused
    assert result.declaration is control.declaration
    assert 'credential-unset' in result.detail and 'UNSET_CLI_KEY' in result.detail
    assert not control.agent.requests and not control.improver.requests
    assert any('unprobed' in message for message, _ in control.events)
    assert not any('does not return structured tool_calls' in message for message, _ in control.events)


def test_successful_chat_cannot_override_failed_responses_tool_gate(control):
    control.improver.responses_support_tools = False
    result = control.run(env_key='SELECTED_CLI_KEY', key='fake-improver-B')
    assert result.tool_calls is False and result.refused
    assert result.declaration is control.declaration
    assert len(control.improver.requests) == 2 and not control.agent.requests
    assert all(header == 'Bearer fake-improver-B' for _, header, _ in control.improver.requests)
