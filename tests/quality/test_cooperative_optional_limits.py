"""Mailbox policy is enforced by the platform even if actors swallow errors."""
import pytest

from eval.run import runner
from test_shared_dataset_runtime import harness, TRANSPORT


@pytest.mark.parametrize('policy,accepted', [
    ({'max_messages': None, 'max_message_bytes': None, 'max_calls': None, 'max_line_bytes': None}, True),
    ({'max_messages': None, 'max_message_bytes': 32, 'max_calls': None, 'max_line_bytes': None}, False),
    ({'max_messages': None, 'max_message_bytes': None, 'max_calls': 1, 'max_line_bytes': None}, False),
    (None, False),  # Existing declarations retain the old 64 KiB message limit.
])
def test_real_sandbox_mailbox_limits_cannot_be_swallowed(tmp_path, monkeypatch, policy, accepted):
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = harness(tmp_path, '''import json
from transport import call
t=json.loads(Path(a.task).read_text())
peer=next(p for p in t['peers'] if p != t['worker_id'])
call('/run/harnessgrad-tool/tool.sock','receive',{},timeout_s=None,max_line_bytes=None)
try:
 call('/run/harnessgrad-tool/tool.sock','send',{'to':peer,'message':'λ'*40000},
      timeout_s=None,max_line_bytes=None)
except RuntimeError:
 pass
# A deliberately valid-looking submission must not conceal relay failures.
(w/'patch.diff').write_text('candidate completed')
''')
    setup = {'workers': [
        {'id': name, 'goal': 'send a message', 'artifacts': ['patch.diff'],
         'files': [{'path': 'transport.py', 'from': str(TRANSPORT)}],
         'env': {'kind': 'files', 'network': 'none'}, 'timeout_s': 10}
        for name in ('alice', 'bob')]}
    if policy is not None:
        setup['mailbox'] = policy
    verifier = {'kind': 'command', 'argv': ['python', 'check.py'],
                'reward_file': '/verifier/reward.txt',
                'result_contract': {'version': 1, 'score_range': [0, 1]},
                'inputs': [{'path': 'check.py', 'content': '''from pathlib import Path
assert Path('workers/alice/patch.diff').read_text() == 'candidate completed'
assert Path('workers/bob/patch.diff').read_text() == 'candidate completed'
Path('verifier/reward.txt').write_text('1')
'''}]}
    result = runner.run_one(repo, {'task_id': 'mailbox-policy'}, setup=setup, verifier=verifier)
    group = result['worker_group']
    assert all(worker['exit_code'] == 0 for worker in group['workers'].values()), result
    assert result['runtime_services']['workers']['cleanup']['complete']
    if accepted:
        assert result['verdict']['score'] == 1, result
        assert len(group['events']) == 2
        assert all(len(event['message'].encode()) == 80000 for event in group['events'])
        assert all(not relay['errors'] and not relay['refused']
                   for relay in group['mailbox_transport'].values())
    else:
        assert result.get('verdict', {}).get('score') is None, result
        assert result['invalid']['stage'] == 'worker mailbox', result
        assert not group['events']
        assert all(relay['errors'] or relay['refused']
                   for relay in group['mailbox_transport'].values())
    if policy is None:
        assert group['mailbox_limits']['max_message_bytes'] == 65536
        assert group['mailbox_limits']['max_messages'] == 4096
