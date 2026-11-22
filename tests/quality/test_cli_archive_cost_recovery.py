"""Offline post-provider evidence failures retain costs without authorizing retries."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from methods import editor
import cli_improver


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(editor, '_TERMINAL_STOP', {})
    monkeypatch.setattr(editor, '_PAST_META_USAGE', {'reported_total':0,'complete':True})
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    original = dict(editor._SPENT)
    editor.reset_spent()
    editor._PAST_META_USAGE.update(reported_total=0, complete=True)
    yield
    editor._SPENT.clear()
    editor._SPENT.update(original)


@pytest.mark.parametrize('transcript,capture,expected,complete', [
    ('dsh-usage {"input_tokens":12,"output_tokens":3,"total_tokens":19}', True, 19, True),
    ('dsh-usage {"input_tokens":12,"output_tokens":3}', True, None, False),
    ('dsh-usage {}', True, None, False),
    ('dsh-usage {"input_tokens":-12,"output_tokens":3,"total_tokens":0}', True, None, False),
    ('dsh-usage {"input_tokens":12,"output_tokens":3,"total_tokens":19}', False, None, False),
    ('', False, None, False),
])
def test_propose_failure_accounts_once_and_stays_terminal(tmp_path, monkeypatch,
        transcript, capture, expected, complete):
    calls = []
    failure = editor.archive_io.ArchiveFailure('synthetic post-call persistence failure')
    failure.observed_transcript = transcript
    failure.capture_complete = capture
    def fail(*args, **kwargs):
        calls.append(1)
        raise failure
    monkeypatch.setattr(cli_improver, 'propose', fail)
    with pytest.raises(RuntimeError):
        editor._ask_cli('offline', tmp_path, tmp_path, {}, evidence_channel=False)
    usage = editor.method_usage()
    assert usage['total_tokens'] == expected and usage['complete'] is complete
    if not transcript or not capture or '"input_tokens":-12' in transcript or transcript.endswith('{}'):
        assert editor.spent_tokens() is None
    with pytest.raises(RuntimeError, match='artifact_persistence_failed'):
        editor.ask('no second session')
    assert calls == [1]


def test_endpoint_total_must_be_explicit_not_sum():
    editor._record_usage(SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=21), 'fake')
    assert editor.spent_tokens() is None
    assert editor.method_usage()['total_tokens'] == 21
    editor._record_usage(SimpleNamespace(prompt_tokens=2, completion_tokens=1), 'fake')
    assert editor.spent_tokens() is None
    assert editor.method_usage()['total_tokens'] is None and not editor.method_usage()['complete']


def test_private_home_cannot_be_named_by_export_manifest(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'; parent.mkdir()
    (parent / 'harness.json').write_text('{}')
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    archive = tmp_path / 'host'; archive.mkdir()
    private = b'{"token":"synthetic-token-absent-from-env"}'
    def seed(work, env):
        home = work / '.codex-home'; home.mkdir()
        (home / 'auth.json').write_bytes(private)
    monkeypatch.setattr(cli_improver, '_seed_codex_home', seed)
    monkeypatch.setattr(cli_improver, '_stage_evidence', lambda *a: None)
    code = """import os,json
from pathlib import Path
root=Path(os.environ['HG_IMPROVER_ARTIFACT_ROOT'])
assert root.name == 'evidence-artifacts'
manifest={'version':1,'session_id':'offline','artifacts':[{'path':'../.codex-home/auth.json','sha256':HASH}]}
Path(os.environ['HG_IMPROVER_ARTIFACT_MANIFEST']).write_text(json.dumps(manifest))
print('dsh-usage {"input_tokens":12,"output_tokens":3,"total_tokens":19}',flush=True)
""".replace('HASH', repr(hashlib.sha256(private).hexdigest()))
    monkeypatch.setattr(cli_improver, '_cli_argv', lambda *a: [sys.executable, '-c', code])
    with pytest.raises(RuntimeError) as caught:
        cli_improver.propose({'workspace':str(workspace),'archive_directory':str(archive)}, parent)
    assert caught.value.capture_complete is True
    assert 'total_tokens' in caught.value.observed_transcript
    assert not any(private in file.read_bytes() for file in archive.glob('*.log'))
    assert (workspace / '.codex-home/auth.json').read_bytes() == private


def test_derived_cli_total_cannot_fill_missing_wire_total():
    line = {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15,
        'usage_evidence': {'source':'pi-ai', 'raw':{'input':12,'output':3},
                           'fields':{'totalTokens':'derived'}}}
    editor._account_cli_observation('dsh-usage ' + json.dumps(line))
    assert editor.spent_tokens() is None
    assert editor.method_usage()['total_tokens'] is None
    assert not editor.method_usage()['complete']


def test_explicit_raw_cli_total_preserves_inconsistency():
    line = {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15,
        'usage_evidence': {'source':'pi-ai', 'raw':{'total':21},
                           'fields':{'totalTokens':'observed'}}}
    editor._account_cli_observation('dsh-usage ' + json.dumps(line))
    assert editor.spent_tokens() is None
    assert editor.method_usage()['total_tokens'] == 21


def test_partial_endpoint_is_unknown_with_positive_lower_bound():
    editor._record_usage(SimpleNamespace(prompt_tokens=12, total_tokens=12), 'fake')
    assert editor.spent_tokens() is None
    assert editor.spent()['input'] is None
    assert editor.method_usage()['total_tokens'] == 12
