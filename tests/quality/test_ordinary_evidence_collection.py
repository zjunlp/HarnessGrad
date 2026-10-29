"""Bad actor traces must reach durable evidence through the real evaluation path."""
from contextlib import contextmanager
import json
import signal

import pytest

from eval.run import runner, trial_evidence
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


@contextmanager
def collection_watchdog():
    # Test-only liveness bound: a FIFO regression must fail, not hang the suite.
    def expired(*_):
        raise AssertionError('trace collection blocked for 30 seconds')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(30)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def actor(work, kind):
    return harness(work, f'''kind={kind!r}
(w/'answer.txt').write_text('yes')
p=w/'trace.jsonl'
if kind == 'invalid-utf8': p.write_bytes(bytes([255, 10]))
elif kind == 'fifo': os.mkfifo(p)
elif kind == 'symlink':
    (w/'private.txt').write_text('must-not-copy')
    p.symlink_to(w/'private.txt')
elif kind == 'hardlink':
    (w/'private.txt').write_text('must-not-copy')
    os.link(w/'private.txt', p)
elif kind == 'directory': p.mkdir()
elif kind == 'unreadable':
    p.write_text('must-not-copy'); p.chmod(0)
elif kind == 'crlf': p.write_bytes(b'{{"event":"first"}}\\r\\n{{"event":"second"}}\\r')
elif kind == 'missing': pass
else: p.write_text('{{"event":"normal"}}\\n')
print('actor stdout')
print('actor stderr', file=sys.stderr)
''')


@pytest.mark.parametrize('kind', ['invalid-utf8', 'fifo', 'symlink', 'hardlink', 'directory',
                                  'unreadable', 'missing', 'crlf', 'ordinary'])
@pytest.mark.parametrize('protocol', [True, False])
def test_real_evaluation_keeps_all_three_trials_and_archives(tmp_path, monkeypatch, kind, protocol):
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = actor(work, kind)
    spec = {'kind': 'answer', 'expected': 'yes', 'trial_protocol': PROTOCOL, 'trial_evidence': {'version': 1}}
    if not protocol:
        spec.pop('trial_protocol')
    captured = []
    original = trial_evidence.capture_output

    def collect(path, **kw):
        captured.append(path)
        return original(path, **kw)

    monkeypatch.setattr(trial_evidence, 'capture_output', collect)
    with collection_watchdog():
        result = runner.evaluate(repo, [{'task_id': 'fixture/t'}], {}, verifiers={'fixture/t': spec},
            trials=3, run_id='fixture', harness_sha='fixture', progress_dir=tmp_path/'run')
    rows = result['repeated_trials' if protocol else 'trial_evidence']['results']
    assert len(rows) == 3
    if protocol:
        assert result['repeated_trials']['complete']
    else:
        assert 'repeated_trials' not in result
    assert result['per_task']['fixture/t'] == 1  # The existing verifier still grades the answer.
    if protocol:
        assert [row['score'] for row in rows] == [1] * 3
    else:
        # The answer verifier returns passed=True without a raw score. Archival
        # observes that result and leaves the existing runner to derive its score.
        assert [row['raw_verdict_score'] for row in rows] == [None] * 3
    assert len(captured) == 3 and all(not path.exists() for path in captured)
    for row in rows:
        archive = json.loads((tmp_path/'run'/row['trial_evidence']['path']).read_text())
        record = archive['result']
        output = record['trial_evidence_output']
        assert output['exit_code'] == 0 and output['launch_attempted']
        assert output['stdout'] == 'actor stdout\n' and output['stderr'] == 'actor stderr\n'
        assert output['trace_status'] == ('captured' if kind in ('invalid-utf8', 'crlf', 'ordinary')
                                          else 'missing' if kind == 'missing' else 'unreadable')
        assert 'must-not-copy' not in json.dumps(archive)
        if kind == 'invalid-utf8':
            assert output['trace_bytes_base64'] == '/wo='
        if kind == 'crlf':
            assert '\r' in output['trace'] and '\r' not in record['trace']
