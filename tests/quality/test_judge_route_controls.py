"""Synthetic local HTTP controls through author cases, real actor and judge sandbox.

No model or benchmark run: scripted verdicts exercise native prompts and parsers.
All five routes are stdlib projections and need no third-party runtime installer.
Run this file through runpy with the checkout on sys.path using an empty platform
interpreter to exercise the same controls without installing pytest.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import base64
import hashlib
from pathlib import Path

from harnessgrad import config
from data.scaffolds.native_research.adapter import Adapter
from tools.prepare_native_research import prepare
from eval.run.runner import run_one

PRIVATE = 'PRIVATE_REFERENCE_MARKER'


def row(name):
    if name == 'browsecomp':
        key = hashlib.sha256(b'canary-control').digest()
        def encrypt(text):
            return base64.b64encode(bytes(v ^ key[i % len(key)] for i, v in enumerate(text.encode()))).decode()
        return {'problem': encrypt('Return a marker.'), 'answer': encrypt(PRIVATE), 'canary': 'canary-control'}
    if name == 'hle':
        return dict(id='synthetic', question='Return a marker.', answer=PRIVATE,
                    image='', category='test', answer_type='exactMatch')
    return dict(problem='Return a marker.', answer=PRIVATE, problem_category='test', answer_type='Single Answer')


def harness(tmp_path, body):
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
    (repo / 'agent.py').write_text("import argparse,os\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()\nw=Path(a.workdir)\n" + body)
    return repo


def reply(name, correct):
    if name == 'browsecomp':
        return 'correct: ' + ('yes' if correct else 'no')
    if name == 'livemathbench':
        return r'\boxed{' + ('yes' if correct else 'no') + '}'
    if name == 'hle':
        return json.dumps(dict(extracted_final_answer='submitted', reasoning='synthetic',
                              correct='yes' if correct else 'no', confidence=99, strict=True))
    if name == 'deepsearchqa':
        return json.dumps({'Answer Correctness': {'Explanation': 'synthetic',
            'Correctness Details': {'part': correct}, 'Excessive Answers': []}})
    return '1.b ' + ('1' if correct else '0') + '\n2.b 5\n3.b 5'


def authored(tmp_path, name, monkeypatch):
    if name == 'strongreject':
        from data.datasets import strongreject as dataset
        # Authored templates are deliberately synthetic; pinned official assets are untouched.
        monkeypatch.setattr(dataset, 'checked_bytes', lambda entry: json.dumps({
            'strongreject_rubric': '{forbidden_prompt}/{response}',
            'strongreject_rubric_system': 'Synthetic rubric control only'}).encode())
        tid = 'strongreject/authored-control'
        spec = dataset._verify(tid, dict(forbidden_prompt=PRIVATE, source_sha256='authored',
            attack_unchanged=False, jailbreak_method='synthetic'))
        return {'task_id': tid, 'goal': 'Save a synthetic answer to answer.txt'}, {'files': []}, spec
    source = tmp_path / 'test.jsonl'
    record = dict(question='Return a marker.', answer=PRIVATE, question_type='single') if name == 'livemathbench' else row(name)
    source.write_text(json.dumps(record) + '\n')
    options = {'data_path': str(source)}
    if name == 'livemathbench':
        options.update(dataset_splits=['AMC'], dataset_languages=['en'])
    output = tmp_path / 'prepared'
    prepare(name, output, options, judge_model='synthetic-route-control')
    adapter = Adapter(name, output)
    tasks, _ = adapter.load()
    tid = tasks[0]['task_id']
    return tasks[0], adapter.setup(tid), adapter.verifier(tid)


def test_author_case_runs_through_real_judge_route(tmp_path, monkeypatch, name, mode):
    state = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            state.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = {'model': 'synthetic-route-control', 'choices': [{'message': {
                'content': 'malformed' if mode == 'invalid' else reply(name, mode != 'wrong')},
                'finish_reason': 'length' if mode == 'truncated' else 'stop'}]}
            if mode != 'missing-usage':
                body['usage'] = {'prompt_tokens': 9, 'completion_tokens': 4}
            raw = json.dumps(body).encode()
            self.send_response(503 if mode == 'http-failure' else 200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
        monkeypatch.setenv('HG_JUDGE_BASE_URL', '' if mode == 'missing-config' else f'http://127.0.0.1:{server.server_port}/v1')
        if mode == 'invalid-endpoint':
            monkeypatch.setenv('HG_JUDGE_BASE_URL', 'ftp://synthetic-user:synthetic-route-secret@127.0.0.1')
        monkeypatch.setenv('HG_JUDGE_MODEL', 'synthetic-route-control')
        monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'synthetic-route-control')
        monkeypatch.setenv('HG_JUDGE_API_KEY', 'synthetic-route-secret')
        (tmp_path / 'work').mkdir()
        monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path / 'work'))
        config.reset()
        task, setup, verifier = authored(tmp_path, name, monkeypatch)
        if mode == 'invalid-model':
            verifier['judge']['model'] = ''
        if mode == 'invalid-parameters':
            verifier['judge']['parameters'] = {'temperature': 9}
        repo = harness(tmp_path, '''
assert not (w/'upstream_case.json').exists()
assert not (w/'strongreject_case.json').exists()
assert 'HG_JUDGE_API_KEY' not in os.environ
assert 'HG_JUDGE_SOCKET' not in os.environ
(w/'actor-sentinel').write_text('actor started')
(w/'trace.jsonl').write_text('{"event":"actor-sentinel"}\\n')
(w/'answer.txt').write_text('Synthetic actor response')
# An actor-authored stale reward must never become a verified result.
(w/'verifier').mkdir(exist_ok=True)
(w/'verifier/reward.txt').write_text('1')
''')
        result = run_one(repo, task, sandbox=True, setup=setup, verifier=verifier,
                         env={'kind': 'files', 'network': 'none'}, timeout_s=30, verify_timeout_s=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        config.reset()
    if mode in ('missing-config', 'invalid-endpoint', 'invalid-model', 'invalid-parameters'):
        assert state == []
        assert result['exit_code'] is None
        assert result['invalid']['stage'] == 'judge configuration admission'
        diagnostic = {'missing-config': 'HG_JUDGE_BASE_URL must be configured',
                      'invalid-endpoint': 'judge base URL must be HTTP(S)',
                      'invalid-model': 'judge.model must be nonempty',
                      'invalid-parameters': 'judge temperature must be between 0 and 2'}[mode]
        assert diagnostic in result['invalid']['detail']
        assert 'synthetic-route-secret' not in json.dumps(result)
        assert result['answer'] == result['trace'] == ''
        return
    assert 'actor-sentinel' in result['trace'], result
    assert len(state) == 1, json.dumps(result, indent=2)
    assert PRIVATE in json.dumps(state[0])
    assert PRIVATE not in json.dumps(task)
    assert PRIVATE not in json.dumps(setup)
    assert PRIVATE not in json.dumps(result)
    assert 'synthetic-route-secret' not in json.dumps(result)
    verdict = result['verdict']
    judge = verdict['judge']
    assert len(judge['attempts']) == 1
    if mode in ('missing-usage', 'http-failure'):
        assert judge['usage_complete'] is False
        assert judge['attempts'][0]['usage'] is None
        assert judge['attempts'][0]['usage_known'] is False
    else:
        assert judge['usage_complete'] is True
        assert judge['usage'] == {'prompt_tokens': 9, 'completion_tokens': 4}
    if mode in ('correct', 'wrong'):
        assert verdict['score'] == float(mode == 'correct'), result
        assert verdict['evidence']['measured'] is True
    else:
        assert verdict.get('score') is None, result
        assert verdict.get('unmeasured') or not verdict.get('passed'), result
        assert verdict.get('evidence', {}).get('measured') is not True


NAMES = ['browsecomp', 'deepsearchqa', 'hle', 'livemathbench', 'strongreject']
MODES = ['correct', 'wrong', 'invalid', 'http-failure', 'missing-usage', 'truncated', 'missing-config',
         'invalid-endpoint', 'invalid-model', 'invalid-parameters']

if __name__ == '__main__':
    # Run with an empty platform interpreter: no pytest or third-party SDK needed.
    import os
    import tempfile
    from contextlib import ExitStack
    from unittest.mock import patch
    class Patches:
        def __init__(self, stack): self.stack = stack
        def setenv(self, key, value): self.stack.enter_context(patch.dict(os.environ, {key: value}))
        def setattr(self, obj, key, value): self.stack.enter_context(patch.object(obj, key, value))
    for name in NAMES:
        for mode in MODES:
            with tempfile.TemporaryDirectory(prefix='hg-judge-author-') as directory, ExitStack() as stack:
                test_author_case_runs_through_real_judge_route(Path(directory), Patches(stack), name, mode)
            print(name + '/' + mode + ': synthetic control passed', flush=True)
else:
    import pytest
    test_author_case_runs_through_real_judge_route = pytest.mark.parametrize('name', NAMES)(
        pytest.mark.parametrize('mode', MODES)(test_author_case_runs_through_real_judge_route))
