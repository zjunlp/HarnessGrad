"""Authored worker/scheduler ownership controls; no model or benchmark execution."""
from contextlib import contextmanager
import json
import threading

from eval.run import runner, scheduling, trial_worker


def test_sequential_schedule_keeps_slot_scope_and_never_builds_executor(monkeypatch):
    events = []

    @contextmanager
    def slot():
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')

    def measure(index, task):
        events.append((index, task['task_id']))
        return {'task_id': task['task_id']}

    monkeypatch.setattr(scheduling.slots, 'slot', slot)
    monkeypatch.setattr(scheduling.futures, 'ThreadPoolExecutor',
                        lambda **kw: (_ for _ in ()).throw(AssertionError('executor used for jobs=1')))
    tasks = [{'task_id': 'a'}, {'task_id': 'b'}]
    assert list(scheduling._in_parallel(tasks, measure, 1)) == ['a', 'b']
    assert events == ['enter', (1, 'a'), 'exit', 'enter', (2, 'b'), 'exit']
    assert runner._in_parallel is scheduling._in_parallel


def test_base_exception_waits_for_running_cleanup_and_reraises_same_object(monkeypatch):
    class AuthoredStop(BaseException):
        pass

    original_executor = scheduling.futures.ThreadPoolExecutor
    started, release, shutting_down = threading.Event(), threading.Event(), threading.Event()
    cleaned, returned = threading.Event(), threading.Event()
    failure = AuthoredStop('authored cancellation')
    observed = []

    class Executor:
        def __init__(self, **kwargs):
            self.pool = original_executor(**kwargs)

        def submit(self, *args):
            return self.pool.submit(*args)

        def shutdown(self, **kwargs):
            observed.append(kwargs)
            shutting_down.set()
            self.pool.shutdown(**kwargs)

    def measure(index, task):
        if index == 1:
            assert started.wait(5)
            raise failure
        started.set()
        try:
            assert release.wait(5)
            return {'task_id': task['task_id']}
        finally:
            cleaned.set()

    def schedule():
        try:
            scheduling._in_parallel([{'task_id': 'a'}, {'task_id': 'b'}], measure, 2)
        except BaseException as exc:
            observed.append(exc)
            assert cleaned.is_set()
        finally:
            returned.set()

    monkeypatch.setattr(scheduling.futures, 'ThreadPoolExecutor', Executor)
    thread = threading.Thread(target=schedule)
    thread.start()
    try:
        assert shutting_down.wait(5)
        assert not returned.is_set()
        assert observed == [{'wait': True, 'cancel_futures': True}]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and cleaned.is_set()
    assert observed[-1] is failure


def test_public_measurement_port_is_late_bound_and_failed_attempt_usage_survives(tmp_path, monkeypatch):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"authored","entrypoint":"agent.py"}')
    calls = []

    def result(tokens, *, failed=False):
        return {'task_id': 't', 'trace': json.dumps({'usage': {
                    'input_tokens': tokens, 'output_tokens': tokens, 'calls': 1}}),
                'exit_code': 1 if failed else 0,
                'stderr': 'APIConnectionError' if failed else '',
                'verdict': {'score': 0 if failed else 1, 'passed': not failed}}

    def second(*args, **kwargs):
        calls.append('second')
        return result(7, failed=True)

    def first(*args, **kwargs):
        calls.append('first')
        monkeypatch.setattr(runner, 'run_one', second)
        return result(3)

    monkeypatch.setattr(runner, 'run_one', first)
    monkeypatch.setattr(runner, 'snapshot', lambda *a: {})
    progress = []
    monkeypatch.setattr(runner, '_write_progress', lambda *a, **kw: progress.append((a, kw)))
    report = runner._evaluate(repo, [{'task_id': 't', 'goal': 'authored'}], {}, trials=2,
                              persist_trial_report=False)
    assert calls == ['first', 'second']
    assert report['per_task'] == {'t': 1.0}
    assert [row['usage']['input_tokens'] for row in progress[0][1]['usage_records']] == [3, 7]
    assert report['tokens']['input'] == 10 and report['tokens']['calls'] == 2


def test_journal_does_not_remove_marker_when_progress_write_fails(tmp_path):
    options = trial_worker.RunOptions(tmp_path, True, 'r', 'seed', 0, 'h', 1, 1, None, None, ())
    journal = trial_worker.TaskEvidenceJournal(options, 't', tmp_path, None, None, {}, False, [])
    marker = tmp_path / 'in-flight'
    marker.write_text('authored marker')
    journal.marker = marker
    state = trial_worker.TrialState(measured=[{'task_id': 't'}], usage_records=[{'usage': None}])
    plan = trial_worker.TaskPlan({'task_id': 't'}, 1, None, None, None, None, {}, None)
    worker = trial_worker.TaskTrialWorker(options, trial_worker.RunBindings(None, {}),
        measure=lambda *a, **kw: None,
        write_progress=lambda *a, **kw: (_ for _ in ()).throw(OSError('authored full disk')))
    inputs = trial_worker.TaskInputPort(lambda kind: None, lambda: False, lambda: None)
    try:
        worker.finish_task(plan, inputs, journal, state)
    except OSError as exc:
        assert str(exc) == 'authored full disk'
    else:
        raise AssertionError('progress failure was swallowed')
    assert marker.read_text() == 'authored marker'


def test_existing_trial_marker_is_not_overwritten_or_remeasured(tmp_path):
    options = trial_worker.RunOptions(tmp_path, True, 'r', 'seed', 0, 'h', 1, 1, None, None, ())
    protocol = {'version': 1, 'metric': 'pass^k', 'trials': 1, 'ks': [1], 'seeds': [7]}
    task = {'task_id': 't', 'goal': 'authored intent'}
    journal = trial_worker.TaskEvidenceJournal(options, 't', tmp_path, protocol, None, {}, False, [])
    journal.start(task)
    before = journal.marker.read_bytes()
    repeated = trial_worker.TaskEvidenceJournal(options, 't', tmp_path, protocol, None, {}, False, [])
    worker = trial_worker.TaskTrialWorker(options, trial_worker.RunBindings(None, {}),
        measure=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('remeasured existing intent')),
        write_progress=lambda *a, **kw: None)
    inputs = trial_worker.TaskInputPort(lambda kind: None, lambda: True, lambda: None)
    plan = trial_worker.TaskPlan(task, 1, None, None, None, None, {}, None)
    try:
        worker.run(plan, inputs, repeated)
    except ValueError as exc:
        assert 'already exists' in str(exc)
    else:
        raise AssertionError('existing in-flight marker was overwritten')
    assert journal.marker.read_bytes() == before
