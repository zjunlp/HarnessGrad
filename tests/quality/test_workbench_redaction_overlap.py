"""Credential substrings/order must not alter the redaction contract."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import assistant_drivers as drivers, workbench
from test_workbench_editor_drivers import installed, make_candidate, helper


@pytest.mark.parametrize('order', ['short-first', 'long-first'])
@pytest.mark.parametrize('short', ['a', '*', 's'])
@pytest.mark.parametrize('operation', ['preflight', 'success', 'failure'])
def test_overlapping_secrets_are_redacted_once(installed, tmp_path, order, short, operation):
    secret = 'AUTHORED_PRIVATE_' + short + '_TOKEN_907'
    pairs = [('OTHER_API_KEY', short), ('EXPLICIT_API_KEY', secret)]
    if order == 'long-first':
        pairs.reverse()
    environment = {**installed.env, **dict(pairs)}
    if operation == 'preflight':
        output = drivers._reason(secret, environment, {'api_key': secret})
        assert output == '***'
        return
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        stdout = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': secret}})
        return SimpleNamespace(returncode=2 if operation == 'failure' else 0,
                               stdout=stdout, stderr=secret if operation == 'failure' else '')
    result = helper(root, installed, process, settings={'api_key': secret}).edit(
        workbench.WorkbenchRequest('candidate', 'edit'), environment=environment)
    assert result[0] == (422 if operation == 'failure' else 200)
    assert result[1]['detail' if operation == 'failure' else 'message'] == '***'
    assert 'AUTHORED_PRIVATE_' not in json.dumps(result)


def test_short_secret_cannot_break_reason_url_identification():
    result = drivers._reason('failed https://user:private-password@provider.invalid/path', {}, {'api_key': 's'})
    assert result == 'failed [endpoint]'
    assert 'private-password' not in result


def test_success_message_retains_ordinary_url_policy():
    value = 'see https://docs.invalid/example for details'
    assert drivers.redact_secrets(value, {'OPENAI_API_KEY': 'unrelated-secret'}) == value


@pytest.mark.parametrize('key_name', ['EDITOR_AUTH', 'a'])
@pytest.mark.parametrize('operation', ['success', 'failure'])
def test_declared_key_environment_is_an_explicit_secret(installed, tmp_path, key_name, operation):
    secret = 'AUTHORED_DECLARED_CREDENTIAL_907'
    installed.resolved['dsh'].update(base_url='https://authored.invalid/v1', provider='authored-provider',
                                    wire='openai-completions', api_key_env=key_name)
    environment = {**installed.env, key_name: secret}
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        assert kwargs['env'][key_name] == secret
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=2 if operation == 'failure' else 0,
                               stdout=secret, stderr=secret if operation == 'failure' else '')
    result = helper(root, installed, process).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=environment)
    assert result[0] == (422 if operation == 'failure' else 200)
    assert result[1]['detail' if operation == 'failure' else 'message'] == '***'
    assert secret not in json.dumps(result)
    thread_file = root.parent / 'threads.json'
    assert not thread_file.exists() or secret not in thread_file.read_text()


@pytest.mark.parametrize('short', ['a', '*'])
def test_preflight_declared_secret_survives_no_intermediate_scrub(installed, short):
    secret = 'AUTHORED_DECLARED_' + short + '_TOKEN'
    installed.resolved['dsh'].update(ok=False, reason=secret, api_key_env='EDITOR_AUTH')
    environment = {**installed.env, 'OTHER_API_KEY': short, 'EDITOR_AUTH': secret}
    result = drivers.capabilities(environment, {}, binding_resolver=installed.resolve)
    row = next(row for row in result['assistants'] if row['kind'] == 'dsh')
    assert row['configured'] is False and row['detail'] == '***'


@pytest.mark.parametrize('operation', ['success', 'failure'])
def test_dsh_stdout_is_redacted_before_display_tail(installed, tmp_path, operation):
    secret = 'AUTHORED_SELECTED_LONG_CREDENTIAL_907'
    installed.resolved['dsh'].update(base_url='https://authored.invalid/v1', provider='authored-provider',
                                    wire='openai-completions', api_key_env='EDITOR_AUTH')
    environment = {**installed.env, 'EDITOR_AUTH': secret}
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=2 if operation == 'failure' else 0,
                               stdout=secret + 'x' * 7990, stderr='')
    result = helper(root, installed, process).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=environment)
    assert result[0] == (422 if operation == 'failure' else 200)
    display = result[1]['detail' if operation == 'failure' else 'message']
    assert display.startswith('***') and secret[-10:] not in display
    assert len(display) <= (800 if operation == 'failure' else 8000)


@pytest.mark.parametrize('operation', ['success', 'failure'])
def test_dsh_display_whitespace_is_applied_only_after_redaction(installed, tmp_path, operation):
    secret = ' AUTHORED_SELECTED_CREDENTIAL_907 '
    installed.resolved['dsh'].update(base_url='https://authored.invalid/v1', provider='authored-provider',
                                    wire='openai-completions', api_key_env='EDITOR_AUTH')
    environment = {**installed.env, 'EDITOR_AUTH': secret}
    root = tmp_path / 'candidates'; make_candidate(root)
    def process(argv, **kwargs):
        (Path(kwargs['cwd']) / 'agent.py').write_text('after=2\n')
        return SimpleNamespace(returncode=2 if operation == 'failure' else 0, stdout=secret, stderr='')
    result = helper(root, installed, process).edit(
        workbench.WorkbenchRequest('candidate', 'edit', kind='dsh'), environment=environment)
    assert result[0] == (422 if operation == 'failure' else 200)
    assert result[1]['detail' if operation == 'failure' else 'message'] == '***'
