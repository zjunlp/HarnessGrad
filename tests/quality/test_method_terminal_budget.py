"""Real offline driver loop: terminal controls stop retries; ordinary failures do not."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from harnessgrad.experiment.method_attempts import MethodAttempts
from methods.stop_status import SCHEMA, TerminalStop, terminal_stop

ROOT = Path(__file__).resolve().parents[2]


def test_only_structured_terminal_controls_stop():
    assert terminal_stop({'_error': 'budget exhausted in an example'}) is None
    assert terminal_stop({'terminal_stop': {'code': 'budget_exhausted'}})['code'] == 'terminal_status_invalid'
    status = {'schema': SCHEMA, 'code': 'budget_exhausted', 'scope': 'meta_round', 'retryable': False}
    assert terminal_stop({'method_reported': {'terminal_stop': status}}) == status


@pytest.mark.parametrize('field', ['code', 'scope'])
@pytest.mark.parametrize('value', [[], {}, 7, None, True])
def test_malformed_terminal_fields_are_structured_terminal_failures(field, value):
    status = {'schema': SCHEMA, 'code': 'budget_exhausted', 'scope': 'meta_round', 'retryable': False}
    status[field] = value
    result = terminal_stop({'terminal_stop': status})
    assert result == {'schema': SCHEMA, 'code': 'terminal_status_invalid',
                      'retryable': False, 'scope': 'meta_session'}


def test_attempts_cross_session_total_unknown_and_overshoot(tmp_path):
    a = MethodAttempts(tmp_path, 'run', round_limit=10)
    first = a.begin(1)
    a.finish(first, {'method_usage': {'total_tokens': 6}})
    # Re-open like a different session/process; no in-memory quota reset.
    b = MethodAttempts(tmp_path, 'run', round_limit=10)
    second = b.begin(1)
    b.finish(second, {'method_usage': {'total_tokens': 6}})
    with pytest.raises(TerminalStop, match='budget_exhausted'):
        b.begin(1)
    data = json.loads((tmp_path / 'method_attempts.json').read_text())
    assert [x['observed_total_tokens'] for x in data['attempts']] == [6, 6]
    assert b.round_usage(1) == {'generation_tokens_lower_bound': 12, 'complete': True, 'attempt_count': 2}
    assert data['attempts'][1]['round_observed_overshoot_tokens'] == 2
    assert b.begin(2)  # separate round budget; run cap would cover both


def test_unknown_and_inflight_refuse_another_session(tmp_path):
    a = MethodAttempts(tmp_path, 'run', run_limit=100)
    admitted = a.begin(1)
    with pytest.raises(TerminalStop, match='budget_usage_unknown'):
        MethodAttempts(tmp_path, 'run', run_limit=100).begin(1)
    a.finish(admitted, {'_error': 'connection ended'})
    with pytest.raises(TerminalStop, match='budget_usage_unknown'):
        a.begin(1)
    data = json.loads(a.path.read_text())
    assert data['attempts'][0]['observed_total_tokens'] is None
    assert a.round_usage(1) == {'generation_tokens_lower_bound': 0, 'complete': False, 'attempt_count': 1}


def test_zero_is_observed_not_missing(tmp_path):
    a = MethodAttempts(tmp_path, 'run', run_limit=10)
    a.finish(a.begin(1), {'method_usage': {'total_tokens': 0}})
    assert a.begin(1)


def test_changed_limits_or_symlink_ledger_refuse(tmp_path):
    a = MethodAttempts(tmp_path, 'run', run_limit=10)
    a.finish(a.begin(1), {'method_usage': {'total_tokens': 1}})
    with pytest.raises(TerminalStop):
        MethodAttempts(tmp_path, 'run', run_limit=100).begin(1)
    target = tmp_path / 'elsewhere'
    a.path.rename(target)
    a.path.symlink_to(target)
    original = target.read_bytes()
    with pytest.raises(TerminalStop):
        a.begin(1)
    assert target.read_bytes() == original


@pytest.mark.parametrize('kind,limit,total,expected,terminal,exit_code', [
    ('ordinary', None, 1, 3, None, 0),
    ('budget', None, 16_000_000, 1, 'budget_exhausted', 0),
    ('budget', None, 16_000_000, 1, 'budget_exhausted', 7),
    ('ordinary', '10', 10, 1, 'budget_exhausted', 0),
    ('ordinary', '10', None, 1, 'budget_usage_unknown', 0),
    ('ordinary', '10', 6, 2, 'budget_exhausted', 0),
    ('archive', None, 8, 1, 'artifact_persistence_failed', 0),
    ('copy_failure', None, 8, 1, 'artifact_persistence_failed', 0),
    ('step_budget', None, 8, 1, 'budget_exhausted', 0),
    ('batch_budget', None, 8, 1, 'budget_exhausted', 0),
    ('invalid_code_list', None, 8, 1, 'terminal_status_invalid', 0),
    ('invalid_scope_map', None, 8, 1, 'terminal_status_invalid', 0),
])
def test_real_driver_retry_admission(tmp_path, kind, limit, total, expected, terminal, exit_code):
    script = tmp_path / 'method.py'
    script.write_text('''import json,sys
from pathlib import Path
req=json.load(sys.stdin)
p=Path(req['workspace'])/'launches.log'
with p.open('a') as f:f.write('provider-session-started\\n')
if KIND=='copy_failure':
 target=p.parent/'kept.txt';target.write_text('keep')
 (p.parent/'linked.log').symlink_to(target)
reply={'_error':'ordinary temporary error','method_usage':{'input':2,'output':1,'total_tokens':TOTAL}}
if KIND in ('budget','archive'):
 reply['terminal_stop']={'schema':'harnessgrad.terminal-stop.v1','code':('budget_exhausted' if KIND=='budget' else 'artifact_persistence_failed'),'scope':'meta_round','retryable':False}
if KIND in ('invalid_code_list','invalid_scope_map'):
 reply['terminal_stop']={'schema':'harnessgrad.terminal-stop.v1','code':([] if KIND=='invalid_code_list' else 'budget_exhausted'),'scope':({} if KIND=='invalid_scope_map' else 'meta_round'),'retryable':False}
if KIND in ('step_budget','batch_budget'):
 step={'harness_dir':req['base_harness'],'method_reported':{'terminal_stop':{'schema':'harnessgrad.terminal-stop.v1','code':'budget_exhausted','scope':'meta_round','retryable':False}}}
 steps=[{'harness_dir':req['base_harness']},step] if KIND=='batch_budget' else [step]
 Path(req['trajectory_out']).write_text(json.dumps({'steps':steps}))
 reply.pop('_error')
 reply.update(changed=False,stop=False)
print(json.dumps(reply))
sys.exit(EXIT)
'''.replace('TOTAL', repr(total)).replace('KIND', repr(kind)).replace('EXIT', str(exit_code)))
    env = {k: v for k, v in os.environ.items() if not k.startswith('HG_META_')}
    env['HG_AGENT_BACKEND'] = 'mock'
    if limit is not None:
        env['HG_META_ROUND_TOKEN_LIMIT'] = limit
    run = tmp_path / 'runs/check'
    proc = subprocess.run([sys.executable, 'driver.py', '--harness', 'loop_plain',
        '--mode', 'A', '--rounds', '1', '--run-id', 'check', '--dataset', 'demo',
        '--sampling', 'all', '--side', 'train', '--improver', 'none', '--runs-root', str(tmp_path/'runs'),
        '--work-root', str(tmp_path/'work'), '--method-entrypoint', f'{sys.executable} {script}'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-2500:]
    ledger = json.loads((run/'method_attempts.json').read_text())
    assert len(ledger['attempts']) == expected
    log = run/'method_logs/round-1/launches.log'
    assert len(log.read_text().splitlines()) == expected
    assert [r['observed_total_tokens'] for r in ledger['attempts']] == [total] * expected
    meta = json.loads((run/'run_meta.json').read_text())
    assert (meta.get('method_terminal_stop') or {}).get('code') == terminal
    assert [json.loads(x)['round'] for x in (run/'curve.jsonl').read_text().splitlines()] == [0]


def test_log_copy_is_private_and_rejects_symlink(tmp_path):
    from harnessgrad.experiment.records import _keep_method_logs
    import stat
    work = tmp_path / 'workspace'
    work.mkdir()
    run = tmp_path / 'run'
    (work / 'full.log').write_text('complete observed output')
    _keep_method_logs(work, run, 1)
    saved = run / 'method_logs/round-1/full.log'
    assert saved.read_text() == 'complete observed output'
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    target = tmp_path / 'secret.txt'
    target.write_text('do not copy')
    (work / 'linked.log').symlink_to(target)
    with pytest.raises(OSError):
        _keep_method_logs(work, run, 2)
    assert not (run / 'method_logs/round-2/linked.log').exists()
    assert target.read_text() == 'do not copy'


def test_editor_copy_failure_keeps_usage_and_cannot_dispatch_again(tmp_path, monkeypatch):
    from methods import editor
    import cli_improver
    from types import SimpleNamespace
    monkeypatch.setattr(editor, '_TERMINAL_STOP', {})
    original_spend = dict(editor._SPENT)
    editor.reset_spent()
    calls = []
    def propose(*args, **kwargs):
        calls.append(1)
        return {'files': [], 'candidate': tmp_path, 'reported': {'exit_code': 0},
                'transcript': 'dsh-usage {"input_tokens":12,"output_tokens":3,"cache_read_tokens":0}\n',
                'exit_code': 0, 'note': ''}
    def failed_archive(*args, **kwargs):
        raise editor.archive_io.ArchiveFailure('synthetic write failure')
    monkeypatch.setattr(cli_improver, 'propose', propose)
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    monkeypatch.setattr(editor, '_keep_improver_transcript', failed_archive)
    try:
        with pytest.raises(RuntimeError, match='synthetic write failure'):
            editor._ask_cli('synthetic request', tmp_path, tmp_path, {}, evidence_channel=False)
        assert editor.spent()['input'] == 12 and editor.spent()['output'] == 3
        with pytest.raises(RuntimeError, match='artifact_persistence_failed'):
            editor.ask('do not dispatch')
        assert len(calls) == 1
    finally:
        editor._SPENT.clear()
        editor._SPENT.update(original_spend)
