"""Host output collection must not undo the actor's private archive boundary."""
import json

import pytest

from eval.run import runner, trial_evidence
from harnessgrad.channel import _stage_for_method
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


@pytest.mark.parametrize('filename', ['trace.jsonl', 'answer.txt'])
@pytest.mark.parametrize('reader_archives', [False, True])
@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('protocol', [True, False])
def test_output_link_cannot_import_another_tasks_archive(
        tmp_path, monkeypatch, filename, reader_archives, resume, protocol):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    progress = tmp_path / 'run'
    binding = {'run_id': 'fixture', 'round': 0, 'harness_sha': 'fixture',
               'task_id': 'a', 'trial_id': 'round-0.harness-fixture.trial-1', 'seed': '10'}
    target = progress / trial_evidence._relative_path(binding)
    secret = 'private-A-evidence'
    actor = harness(work, f'''import json
task = json.loads(Path(a.task).read_text())
(w/'answer.txt').write_text('yes')
(w/'trace.jsonl').write_text(json.dumps({{'text': {secret!r} if task['task_id']=='a' else 'public-B'}}))
if task['task_id']=='b':
    assert not Path({str(target)!r}).exists(), 'archive visible in actor sandbox'
    (w/{filename!r}).unlink()
    (w/{filename!r}).symlink_to({str(target)!r})
''')
    specs = {tid: {'kind': 'answer', 'expected': 'yes', 'trial_protocol': dict(PROTOCOL)}
             for tid in ('a', 'b')}
    if not protocol:
        for spec in specs.values():
            spec.pop('trial_protocol')
    specs['a']['trial_evidence'] = {'version': 1}
    if reader_archives:
        specs['b']['trial_evidence'] = {'version': 1}
    captured = []
    original = runner.run_one

    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(runner, 'run_one', record)
    options = dict(verifiers=specs, trials=3, jobs=1, run_id='fixture',
                   harness_sha='fixture', progress_dir=progress)
    saved = {}
    if resume:
        runner.evaluate(actor, [{'task_id': 'a'}], {}, **options, persist_trial_report=False)
        saved = {p: p.read_bytes() for p in (progress/'trial-evidence').rglob('*.json')}
        tasks = [{'task_id': 'b'}]
    else:
        tasks = [{'task_id': 'a'}, {'task_id': 'b'}]
    result = runner.evaluate(actor, tasks, {}, **options, persist_trial_report=not resume)
    assert target.is_file() and secret in target.read_text()
    assert all(p.read_bytes() == data for p, data in saved.items())
    if protocol:
        assert result['repeated_trials']['complete']
    else:
        assert 'repeated_trials' not in result
    assert result['per_task']['b'] == (1 if filename == 'trace.jsonl' else 0)
    reader_results = [r for r in captured if r['task_id'] == 'b']
    assert len(reader_results) == 3 and all(r['exit_code'] == 0 for r in reader_results)
    assert secret not in json.dumps(reader_results)
    assert secret not in result['traces']['b']
    assert secret not in json.dumps(result['trial_traces']['b'])
    assert all(('trial_evidence_output' in r) == reader_archives for r in reader_results)
    if reader_archives:
        field = 'trace' if filename == 'trace.jsonl' else 'answer'
        assert all(r['trial_evidence_output'][field + '_status'] == 'unreadable' for r in reader_results)
    records = [json.loads(p.read_text()) for p in (progress/'trial-evidence').rglob('*.json')]
    reader_records = [r for r in records if r.get('schema') == 'harnessgrad.trial-evidence.v1'
                      and r['binding']['task_id'] == 'b']
    assert len(reader_records) == (3 if reader_archives else 0)
    assert secret not in json.dumps(reader_records)

    method = tmp_path / 'method'
    _stage_for_method(method, {'round': 0}, result['traces'], task_ids=['b'], result=result)
    assert all(secret not in p.read_text() for p in (method/'_harnessgrad').rglob('*') if p.is_file())
    for path in (progress/'progress/round-0').rglob('b*'):
        if path.is_file():
            assert secret not in path.read_text()
