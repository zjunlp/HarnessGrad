"""Offline real method/CLI processes: a phase reset cannot buy another session."""
import json
from pathlib import Path
import sys

import pytest

from harnessgrad.experiment.method_attempts import MethodAttempts
from harnessgrad.execution.method_runner import _call_entrypoint
from methods import editor

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('limit,mode,expected,total,terminal', [
    (6, 'known', 1, 6, 'budget_exhausted'),
    (10, 'unknown', 1, None, 'budget_usage_unknown'),
    (10, 'known', 2, 12, 'budget_exhausted'),
    (None, 'known', 3, 18, None),
    (None, 'unknown', 3, None, None),
])
def test_real_method_and_fake_cli_cumulative_phase_resets(tmp_path, monkeypatch,
        limit, mode, expected, total, terminal):
    parent = tmp_path / 'parent'; parent.mkdir()
    (parent / 'harness.json').write_text('{}')
    fake = tmp_path / 'fake_cli.py'
    fake.write_text('''import json,sys
from pathlib import Path
with Path(sys.argv[1]).open('a') as f:f.write('started\\n')
usage={'input_tokens':4,'output_tokens':2,'cache_read_tokens':0}
if sys.argv[2]=='known':usage['total_tokens']=6
print('dsh-usage '+json.dumps(usage),flush=True)
''')
    launches = tmp_path / 'sessions.txt'
    method = tmp_path / 'method.py'
    method.write_text('''import json,sys
from pathlib import Path
sys.path.insert(0, ROOT)
from methods import editor
import cli_improver
request=json.load(sys.stdin)
base=Path(request['fixture_parent'])
cli_improver._cli_argv=lambda *a:[sys.executable,FAKE,LAUNCHES,MODE]
cli_improver._seed_codex_home=lambda *a:None
cli_improver._stage_evidence=lambda *a:None
failure=None
for index in range(3):
 try:
  editor._ask_cli('offline synthetic request',base,base,{},evidence_channel=False)
  editor.reset_spent()
 except Exception as exc:
  failure=getattr(exc,'terminal_stop',None)
  break
print(json.dumps({'method_usage':editor.method_usage(), 'terminal_stop':failure}))
'''.replace('ROOT', repr(str(ROOT))).replace('FAKE', repr(str(fake)))
        .replace('LAUNCHES', repr(str(launches))).replace('MODE', repr(mode)))
    # Ambient user value is never the trusted allowance passed into the child.
    monkeypatch.setenv('HG_META_INVOCATION_TOKEN_LIMIT', '0')
    result = _call_entrypoint(tmp_path, [sys.executable, str(method)],
        {'fixture_parent':str(parent),'meta_invocation_token_limit':limit},
        timeout_s=30, sandboxed=False, cwd=tmp_path)
    assert '_error' not in result, result
    assert len(launches.read_text().splitlines()) == expected
    assert result['method_usage']['total_tokens'] == total
    assert result['method_usage']['complete'] is (total is not None)
    assert (result.get('terminal_stop') or {}).get('code') == terminal


def test_admission_receipt_immutable_minimum_remaining(tmp_path):
    attempts = MethodAttempts(tmp_path, 'offline', round_limit=12, run_limit=20)
    first = attempts.begin(1)
    assert attempts.allowance(first) == 12
    attempts.finish(first, {'method_usage':{'total_tokens':6,'complete':True}})
    second = attempts.begin(1)
    assert attempts.allowance(second) == 6
    assert attempts.allowance(first) == 12
    attempts.finish(second, {'method_usage':{'total_tokens':7,'complete':True}})
    third = attempts.begin(2)
    assert attempts.allowance(third) == 7


@pytest.fixture
def phase_editor(monkeypatch, tmp_path):
    original = dict(editor._SPENT)
    editor.reset_spent()
    monkeypatch.setattr(editor, '_PAST_META_USAGE', {'reported_total':0,'complete':True})
    monkeypatch.setattr(editor, '_TERMINAL_STOP', {})
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    monkeypatch.setattr(editor, '_INVOCATION_TOKEN_LIMIT', '6')
    yield editor
    editor._SPENT.clear(); editor._SPENT.update(original)


@pytest.mark.parametrize('raw_total,code', [(6,'budget_exhausted'), (None,'budget_usage_unknown')])
def test_structured_role_retry_cannot_start_another_session_at_cap(
        phase_editor, tmp_path, monkeypatch, raw_total, code):
    import cli_improver

    base = tmp_path / 'base'
    channel = base / '_harnessgrad'
    channel.mkdir(parents=True)
    declaration = {'kind': 'cli', 'name': 'authored', 'path': str(tmp_path / 'authored-cli')}
    (channel / 'round.json').write_text(json.dumps({'identity': {'improver': declaration}}))
    monkeypatch.delenv('HG_IMPROVER', raising=False)
    calls = []

    def propose(request, parent, **kwargs):
        calls.append(request['improver']['name'])
        assert request['structured_role']
        (Path(request['workspace']) / 'artifacts' / 'role-result.json').write_text('invalid JSON')
        usage = {'input_tokens': 4, 'output_tokens': 2, 'cache_read_tokens': 0}
        if raw_total is not None:
            usage['total_tokens'] = raw_total
        return {'files': [], 'candidate': request['candidate'], 'reported': {}, 'exit_code': 0,
                'transcript': 'dsh-usage ' + json.dumps(usage)}

    monkeypatch.setattr(cli_improver, 'propose', propose)
    with pytest.raises(RuntimeError, match=code):
        editor.ask_for_json(lambda problems: 'return a JSON verdict',
                            system='authored critic', base=base, structured_cli=True, attempts=3)
    assert calls == ['authored']
    assert editor.method_usage()['total_tokens'] == raw_total


def test_unknown_transport_survives_phase_reset(phase_editor):
    editor._SPENT['unknown_transport_attempts'] = 1
    editor.reset_spent()
    assert editor.spent_tokens() == 0  # native phase-local API stays unchanged
    assert editor.method_usage()['total_tokens'] is None
    with pytest.raises(RuntimeError,match='budget_usage_unknown'):
        editor._meta_admit()


def test_pre_dispatch_configuration_failure_is_not_terminal_without_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_INVOCATION_TOKEN_LIMIT', None)
    monkeypatch.setattr(editor, '_TERMINAL_STOP', {})
    import cli_improver
    monkeypatch.setattr(cli_improver, 'propose', lambda *a, **k: (_ for _ in ()).throw(ValueError('config')))
    with pytest.raises(ValueError,match='config'):
        editor._ask_cli('offline',tmp_path,tmp_path,{},evidence_channel=False)
    assert not editor._TERMINAL_STOP


def test_post_cli_generic_audit_failure_is_terminal_and_preserves_cost(phase_editor, tmp_path, monkeypatch):
    import cli_improver
    monkeypatch.setattr(cli_improver,'propose',lambda *a,**k:{'files':[],
        'candidate':tmp_path,'reported':{},'exit_code':0,'note':'',
        'transcript':'dsh-usage {"input_tokens":4,"output_tokens":2,"total_tokens":6}'})
    def failed_identity(*a):
        raise ValueError('synthetic post-call audit failure')
    monkeypatch.setattr(editor,'_editor_audit_identity',failed_identity)
    with pytest.raises(RuntimeError,match='Cannot process completed CLI evidence'):
        editor._ask_cli('offline',tmp_path,tmp_path,{},evidence_channel=False)
    assert editor.method_usage()['total_tokens'] == 6
    assert editor._TERMINAL_STOP['code'] == 'artifact_persistence_failed'
    with pytest.raises(RuntimeError,match='artifact_persistence_failed'):
        editor._ask_cli('no second session',tmp_path,tmp_path,{},evidence_channel=False)


def test_pre_dispatch_configuration_failure_keeps_configured_allowance(phase_editor,tmp_path,monkeypatch):
    import cli_improver
    def failed_config(*a,**k):
        raise ValueError('synthetic config failure')
    monkeypatch.setattr(cli_improver,'propose',failed_config)
    for _ in range(2):
        with pytest.raises(ValueError,match='synthetic config failure'):
            editor._ask_cli('offline',tmp_path,tmp_path,{},evidence_channel=False)
    assert editor.method_usage()['total_tokens'] == 0
    assert editor.method_usage()['complete']
    assert not editor._TERMINAL_STOP


def test_partial_cli_totals_keep_known_lower_bound_across_reset(phase_editor,tmp_path):
    transcript = ('dsh-usage {"input_tokens":4,"output_tokens":2,"total_tokens":6}\n'
                  'dsh-usage {"input_tokens":1,"output_tokens":1}')
    editor._account_cli_observation(transcript)
    editor.reset_spent()
    usage = editor.method_usage()
    assert usage['total_tokens'] is None and not usage['complete']
    assert usage['observed_total_tokens_lower_bound'] == 6
    attempts = MethodAttempts(tmp_path,'offline',run_limit=5)
    attempt = attempts.begin(1)
    attempts.finish(attempt,{'method_usage':usage})
    row = json.loads(attempts.path.read_text())['attempts'][0]
    assert row['observed_total_tokens'] is None
    assert row['observed_total_tokens_lower_bound'] == 6
    assert row['run_observed_overshoot_tokens'] is None
    assert row['run_observed_overshoot_tokens_lower_bound'] == 1
