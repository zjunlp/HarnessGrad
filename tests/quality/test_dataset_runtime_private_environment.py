"""Synthetic child environments and diagnostics; never contact a network or model."""
import json
import sys

import pytest

from harnessgrad import dataset_runtime as runtime


def test_real_child_environment_is_phase_limited(monkeypatch, tmp_path):
    planted = {'HG_AGENT_API_KEY': 'agent-synthetic-secret', 'HG_METHOD_API_KEY': 'method-synthetic-secret',
               'OPENAI_API_KEY': 'openai-synthetic-secret', 'ANTHROPIC_API_KEY': 'anthropic-synthetic-secret',
               'UNRELATED_TOKEN': 'other-synthetic-secret', 'HG_DATA_ROOT': '/private/data',
               'PYTHONPATH': '/private/python', 'PYTHONHOME': '/private/home',
               'HTTP_PROXY': 'http://proxy-user:proxy-pass@proxy.invalid:1234',
               'UV_INDEX_URL': 'https://index-user:index-pass@index.invalid/simple',
               'UV_INDEX_PRIVATE_USERNAME': 'synthetic-index-user',
               'UV_INDEX_PRIVATE_PASSWORD': 'synthetic-index-password',
               'UV_NATIVE_TLS': '1', 'UV_OFFLINE': '1', 'UV_CACHE_DIR': str(tmp_path / 'cache')}
    for key, value in planted.items():
        monkeypatch.setenv(key, value)
    code = 'import json,os; print(json.dumps(dict(os.environ)))'
    for allow in (False, True):
        child = json.loads(runtime._run([sys.executable, '-I', '-c', code], allow_downloads=allow))
        for key in ('HG_AGENT_API_KEY', 'HG_METHOD_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                    'UNRELATED_TOKEN', 'HG_DATA_ROOT', 'PYTHONPATH', 'PYTHONHOME'):
            assert key not in child
        assert child['UV_OFFLINE'] == '1' and child['UV_CACHE_DIR'] == str(tmp_path / 'cache')
        assert child['UV_NO_CONFIG'] == '1'
        for key in ('HTTP_PROXY', 'UV_INDEX_URL', 'UV_INDEX_PRIVATE_USERNAME',
                    'UV_INDEX_PRIVATE_PASSWORD', 'UV_NATIVE_TLS'):
            assert (child.get(key) == planted[key]) if allow else (key not in child)


def test_failed_installer_diagnostic_redacts_environment_and_url_credentials(monkeypatch):
    values = {'HG_AGENT_API_KEY': 'actor-secret-example',
              'RANDOM_TOKEN': 'unrelated-secret-example',
              'HTTPS_PROXY': 'http://proxyuser:proxypass@proxy.invalid:9000',
              'UV_INDEX_URL': 'https://indexuser:indexpass@index.invalid/simple',
              'UV_INDEX_PRIVATE_PASSWORD': 'named-index-password'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    code = '''import os,sys
sys.stderr.write(" ".join([os.environ.get("HTTPS_PROXY", ""), os.environ.get("UV_INDEX_URL", ""),
  os.environ.get("UV_INDEX_PRIVATE_PASSWORD", ""), "actor-secret-example", "unrelated-secret-example",
  "https://unknown-user:unknown-password@other.invalid/path"]))
sys.exit(7)
'''
    with pytest.raises(ValueError) as exc:
        runtime._run([sys.executable, '-I', '-c', code], allow_downloads=True)
    message = str(exc.value)
    assert 'runtime command failed (7)' in message and '[redacted]' in message
    for value in (*values.values(), 'actor-secret-example', 'unrelated-secret-example',
                  'unknown-user', 'unknown-password', 'named-index-password'):
        assert value not in message
