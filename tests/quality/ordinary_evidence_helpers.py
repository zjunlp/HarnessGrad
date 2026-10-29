"""Shared actor fixture for ordinary trial evidence regressions."""
import copy

import pytest

from eval.run import runner
from test_declared_trials_continue import PROTOCOL


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    attempts = []

    def execute(outcomes, *, enabled=True, directory='run', protocol=True, **kwargs):
        calls = []

        def actor(_repo, task, **kw):
            i = len(calls)
            calls.append(kw)
            if outcomes[i] == 'interrupt':
                raise KeyboardInterrupt('fixture interruption')
            result = {'task_id': task['task_id'], 'answer': 'answer', 'exit_code': 0,
                      'stderr': f'stderr {i}', 'trace': f'private trace {i}',
                      'verdict': {'score': int(i != 1), 'passed': i != 1,
                                  'evidence': {'details': f'private grading {i}'}},
                      'runtime_services': {'tool_relay': {'recording': {'archive': f'episode-{i}.json'}}}}
            if outcomes[i] == 'success':
                result['verdict'].update(score=1, passed=True)
            elif outcomes[i] == 'fractional':
                result['verdict'].update(score=.25, passed=False)
            if outcomes[i] == 'invalid':
                result['invalid'] = {'stage': 'tool relay', 'detail': f'fault {i}'}
            elif outcomes[i] == 'unmeasured':
                result['verdict'].update(unmeasured='scorer failed', score=None)
            elif outcomes[i] == 'provider':
                result.update(exit_code=1, stderr='APIConnectionError: Connection error',
                              verdict={'score': 0, 'passed': False})
            attempts.append(copy.deepcopy(result))
            return result

        monkeypatch.setattr(runner, 'run_one', actor)
        spec = {'kind': 'answer', 'expected': 'answer'}
        if protocol:
            spec['trial_protocol'] = PROTOCOL
        if enabled:
            spec['trial_evidence'] = {'version': 1}
        cache = {}
        args = dict(trials=3, run_id='fixture', harness_sha='harness', round_no=2,
                    progress_dir=tmp_path / directory, verifiers={'ordinary/task': spec}, cache=cache)
        args.update(kwargs)
        result = runner._evaluate(work, [{'task_id': 'ordinary/task'}], {}, **args)
        return result, calls, cache

    return execute, attempts
