"""Offline customization CLI tests with a local scripted endpoint."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def endpoint():
    requests, replies = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            status, payload = replies.pop(0)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests, replies
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        assert not thread.is_alive()


def model_reply(value, *, usage=None):
    return {'choices': [{'message': {'content': value}}], 'model': 'fixture', 'usage': usage}


def run_cli(request, endpoint, tmp_path, *options):
    url, _, _ = endpoint
    request_path = tmp_path / 'request.json'
    request_path.write_text(request if isinstance(request, str) else json.dumps(request), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', HG_METHOD_BASE_URL=url,
               HG_METHOD_API_KEY='fixture-secret-key', HG_METHOD_MODEL='fixture')
    before = request_path.read_bytes()
    proc = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'propose',
                           '--request', str(request_path), *options], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
    assert request_path.read_bytes() == before
    assert 'fixture-secret-key' not in proc.stdout + proc.stderr
    return proc


def example():
    return {'feedback': [{'id': 'one', 'kind': 'failure', 'text': 'Tests were skipped.'}],
            'skills': {'skills/main.md': 'Original\n'},
            'instruction': {'path': 'prompt.md', 'text': 'Baseline\n'}}


def test_combined_proposal_uses_explicit_feedback_without_writing_a_harness(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.extend([(200, model_reply(json.dumps(row), usage=usage)) for row, usage in [
        ({'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]},
         {'prompt_tokens': 10, 'completion_tokens': 4}),
        ({'key_insight': 'Run checks', 'bullet_tags': []}, None),
        ({'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Verify results.'}]},
         {'prompt_tokens': 12, 'completion_tokens': 3})]])
    proc = run_cli(example(), endpoint, tmp_path)
    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert output['status'] == 'unevaluated'
    assert len(output['input_sha256']) == 64
    assert 'Run tests.' in output['proposal']['files']['skills/main.md']
    assert 'Verify results.' in output['proposal']['files']['prompt.md']
    assert output['proposal']['stages'] == ['skillopt-fast', 'ace-reflect-curate']
    assert output['model_usage']['logical_calls'] == 3
    assert output['model_usage']['transport_attempts'] == 3
    assert output['model_usage']['calls'][1]['prompt_tokens'] is None
    assert len(requests) == 3
    assert all('Tests were skipped.' in json.dumps(req) for req in requests[:2])
    assert sorted(p.name for p in tmp_path.iterdir()) == ['request.json']


def test_logical_call_limit_refuses_partial_candidate(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.append((200, model_reply(json.dumps({'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Check.'}]}))))
    proc = run_cli(example(), endpoint, tmp_path, '--max-calls', '1')
    assert proc.returncode == 1 and not proc.stdout
    assert 'logical model call limit reached' in proc.stderr
    assert len(requests) == 1


@pytest.mark.parametrize('payload', [
    '{"feedback":[],"feedback":[]}',
    {'feedback': [], 'skills': {'skills/main.md': 'x'}, 'extra': 'forbidden'},
    {'feedback': [{'id': 'a', 'kind': 'unknown', 'text': 'x'}], 'skills': {'skills/main.md': 'x'}},
    {'feedback': [{'id': 'a', 'kind': 'failure', 'text': 'x'}], 'instruction': {'path': '../x', 'text': 'x'}},
    {'feedback': [{'id': 'a', 'kind': 'failure', 'text': 'x'}], 'skills': {'skills/main.md': 'x'},
     'instruction': {'path': './skills/main.md', 'text': 'other'}},
])
def test_invalid_request_never_calls_endpoint(payload, endpoint, tmp_path):
    _, requests, _ = endpoint
    proc = run_cli(payload, endpoint, tmp_path)
    assert proc.returncode == 1 and not proc.stdout
    assert requests == []


def test_unparseable_model_reply_is_not_echoed_as_a_candidate(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.append((200, model_reply('PRIVATE MODEL OUTPUT: not JSON')))
    proc = run_cli(example(), endpoint, tmp_path)
    assert proc.returncode == 1 and not proc.stdout
    assert 'model reply must be one JSON object' in proc.stderr
    assert 'PRIVATE MODEL OUTPUT' not in proc.stderr
    assert len(requests) == 1


def test_transport_retries_are_reported_separately_from_logical_calls(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.extend([(503, {'error': 'transient'}), (200, model_reply(json.dumps({
        'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Check.'}]})))])
    payload = example()
    payload.pop('instruction')
    proc = run_cli(payload, endpoint, tmp_path)
    assert proc.returncode == 0, proc.stderr
    usage = json.loads(proc.stdout)['model_usage']
    assert usage['logical_calls'] == 1 and usage['transport_attempts'] == 2
    assert usage['calls'][0]['unknown_transport_attempts'] == 1
    assert usage['calls'][0]['prompt_tokens'] is None
    assert len(requests) == 2


def test_model_json_with_duplicate_keys_is_rejected_without_echo(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.append((200, model_reply('{"edits":[],"edits":["PRIVATE"]}')))
    proc = run_cli(example(), endpoint, tmp_path)
    assert proc.returncode == 1 and not proc.stdout
    assert 'model reply must be one JSON object' in proc.stderr
    assert 'PRIVATE' not in proc.stderr and len(requests) == 1


def test_collect_then_propose_uses_selected_source_with_local_endpoint(endpoint, tmp_path):
    _, requests, replies = endpoint
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original skill.\n')
    (root / 'secret.txt').write_text('UNSELECTED PRIVATE VALUE')
    feedback = tmp_path / 'feedback.json'
    feedback.write_text('[{"id":"one","kind":"failure","text":"Tests were skipped."}]')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    collect = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'collect',
        '--root', str(root), '--skill', 'skills/main.md', '--feedback', str(feedback)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert collect.returncode == 0, collect.stderr
    replies.append((200, model_reply(json.dumps({'edits': [
        {'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]}))))
    result = run_cli(collect.stdout, endpoint, tmp_path)
    assert result.returncode == 0, result.stderr
    candidate = json.loads(result.stdout)
    proposal_path = tmp_path / 'proposal.json'
    proposal_path.write_text(result.stdout, encoding='utf-8')
    review = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'review',
        '--request', str(tmp_path / 'request.json'), '--proposal', str(proposal_path),
        '--root', str(root)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert review.returncode == 0, review.stderr
    assert json.loads(review.stdout)['source_check'] == 'selected_files_match'
    assert 'Run tests.' in candidate['proposal']['files']['skills/main.md']
    assert (root / 'skills/main.md').read_text() == 'Original skill.\n'
    assert len(requests) == 1
    assert 'Original skill.' in json.dumps(requests[0])
    assert 'UNSELECTED PRIVATE VALUE' not in json.dumps(requests)


def test_memory_option_composes_candidate_and_review_offline(endpoint, tmp_path):
    _, requests, replies = endpoint
    rows = [
        {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]},
        {'key_insight': 'Check results', 'bullet_tags': []},
        {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Report checks.'}]},
        {'lesson': 'Check tests', 'trigger': 'code change', 'evidence': 'skipped', 'scope_hint': 'coding'},
        {'skills': [{'path': 'skills/verify/SKILL.md', 'name': 'verify', 'description': 'Verify changes',
                     'source_task_ids': ['one'], 'content': 'Run the relevant tests.'}]},
    ]
    replies.extend((200, model_reply(json.dumps(row))) for row in rows)
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original\n')
    (root / 'prompt.md').write_text('Baseline\n')
    (root / 'harness.json').write_text(json.dumps({'name': 'fixture', 'version': '1',
        'entrypoint': 'agent.py', 'env_kinds': ['files'],
        'components': {'context': ['skills/main.md', 'prompt.md']}}))
    (root / 'agent.py').write_text('print("synthetic harness")\n')
    (root / 'private.txt').write_text('UNSELECTED PRIVATE VALUE')
    feedback = tmp_path / 'feedback.json'
    feedback.write_text(json.dumps(example()['feedback']))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    collected = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'collect',
        '--root', str(root), '--skill', 'skills/main.md', '--instruction', 'prompt.md',
        '--feedback', str(feedback)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert collected.returncode == 0, collected.stderr
    assert json.loads(collected.stdout) == example()
    result = run_cli(collected.stdout, endpoint, tmp_path, '--with-memory')
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['status'] == 'unevaluated' and output['model_usage']['logical_calls'] == 5
    assert output['proposal']['stages'] == ['skillopt-fast', 'ace-reflect-curate', 'evo-harness-skill-curation']
    assert output['memories'][0]['task_id'] == 'one'
    assert 'Run tests.' in output['proposal']['files']['skills/main.md']
    assert 'Run the relevant tests.' in output['proposal']['files']['skills/verify/SKILL.md']
    assert 'Report checks.' in output['proposal']['files']['prompt.md']
    assert len(requests) == 5

    proposal = tmp_path / 'proposal.json'
    proposal.write_text(result.stdout)
    review = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'review',
        '--request', str(tmp_path / 'request.json'), '--proposal', str(proposal), '--root', str(root), '--components'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert review.returncode == 0, review.stderr
    reviewed = json.loads(review.stdout)
    assert reviewed['source_check'] == 'selected_files_match'
    assert reviewed['component_attribution']['components'] == ['context']
    assert reviewed['component_attribution']['unattributed'] == ['skills/verify/SKILL.md']
    assert (root / 'skills/main.md').read_text() == 'Original\n'
    assert not (root / 'skills/verify').exists()
    assert len(requests) == 5
    assert 'UNSELECTED PRIVATE VALUE' not in json.dumps(requests)

    patch = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'patch',
        '--request', str(tmp_path / 'request.json'), '--proposal', str(proposal), '--root', str(root)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert patch.returncode == 0, patch.stderr
    if not shutil.which('git'):
        pytest.skip('git is required for the synthetic patch application control')
    candidate = tmp_path / 'candidate-copy'
    shutil.copytree(root, candidate)
    for args in (['--check', '-'], ['-']):
        applied = subprocess.run(['git', 'apply', *args], cwd=candidate, input=patch.stdout,
                                 capture_output=True, text=True, timeout=20)
        assert applied.returncode == 0, applied.stderr
    for path, content in output['proposal']['files'].items():
        assert (candidate / path).read_text() == content
    assert (candidate / 'private.txt').read_text() == 'UNSELECTED PRIVATE VALUE'
    assert (candidate / 'harness.json').read_bytes() == (root / 'harness.json').read_bytes()
    assert (candidate / 'agent.py').read_bytes() == (root / 'agent.py').read_bytes()
    assert (root / 'skills/main.md').read_text() == 'Original\n'
    assert not (root / 'skills/verify').exists() and len(requests) == 5


@pytest.mark.parametrize('payload', [
    {'feedback': [{'id': 'a', 'kind': 'failure', 'text': 'x'}], 'instruction': {'path': 'prompt.md', 'text': 'x'}},
    {'feedback': [{'id': 'a', 'kind': 'success', 'text': 'x'}], 'skills': {'skills/main.md': 'x'}},
])
def test_memory_option_requires_selected_skills_and_failure_before_model(payload, endpoint, tmp_path):
    _, requests, _ = endpoint
    result = run_cli(payload, endpoint, tmp_path, '--with-memory')
    assert result.returncode == 1 and not result.stdout
    assert 'memory proposals require' in result.stderr and not requests


def test_memory_stage_failure_does_not_return_partial_candidate(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.append((200, model_reply(json.dumps({'edits': []}))))
    payload = example()
    payload.pop('instruction')
    result = run_cli(payload, endpoint, tmp_path, '--with-memory', '--max-calls', '1')
    assert result.returncode == 1 and not result.stdout
    assert 'logical model call limit reached' in result.stderr and len(requests) == 1


def test_oversized_proposal_is_refused_before_writing_stdout(endpoint, tmp_path):
    """A successful command must produce JSON that review and patch can read."""
    _, requests, replies = endpoint
    payload = example()
    payload.pop('instruction')
    # The model response fits its transport cap, but candidate text appears in
    # both files and diff, making the resulting proposal larger than 8 MiB.
    edit = {'path': 'skills/main.md', 'op': 'append', 'content': 'x' * (4 * 1024 * 1024)}
    replies.append((200, model_reply(json.dumps({'edits': [edit]}))))
    proc = run_cli(payload, endpoint, tmp_path)
    assert proc.returncode == 1 and proc.stdout == ''
    assert 'proposal exceeds 8 MiB' in proc.stderr
    assert '"logical_calls": 1' in proc.stderr and len(requests) == 1


def test_optional_instruction_refinement_composes_with_memory_and_review(endpoint, tmp_path):
    _, requests, replies = endpoint
    rows = [
        {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run tests.'}]},
        {'key_insight': 'Check results', 'bullet_tags': []},
        {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Report checks.'}]},
        {'instruction': 'Report checks and run tests.\n'},
        {'lesson': 'Check tests', 'trigger': 'code change', 'evidence': 'skipped', 'scope_hint': 'coding'},
        {'skills': [{'path': 'skills/verify/SKILL.md', 'name': 'verify', 'description': 'Verify changes',
                     'source_task_ids': ['one'], 'content': 'Run the relevant tests.'}]},
    ]
    replies.extend((200, model_reply(json.dumps(row))) for row in rows)
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original\n')
    (root / 'prompt.md').write_text('Baseline\n')
    (root / 'private.txt').write_text('UNSELECTED PRIVATE VALUE')
    result = run_cli(example(), endpoint, tmp_path, '--with-memory', '--refine-instruction')
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['proposal']['stages'] == ['skillopt-fast', 'ace-reflect-curate',
                                            'gepa-reflect-propose', 'evo-harness-skill-curation']
    assert output['proposal']['files']['prompt.md'] == 'Report checks and run tests.\n'
    assert output['model_usage']['logical_calls'] == 6 and len(requests) == 6
    assert 'Report checks.' in json.dumps(requests[3])
    assert 'Tests were skipped.' in json.dumps(requests[3])
    assert 'UNSELECTED PRIVATE VALUE' not in json.dumps(requests)
    proposal = tmp_path / 'proposal.json'
    proposal.write_text(result.stdout)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    base_args = ['--request', str(tmp_path / 'request.json'), '--proposal', str(proposal), '--root', str(root)]
    review = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'review', *base_args],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert review.returncode == 0, review.stderr
    assert json.loads(review.stdout)['source_check'] == 'selected_files_match'
    patch = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'patch', *base_args],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert patch.returncode == 0, patch.stderr
    candidate = tmp_path / 'candidate-copy'
    shutil.copytree(root, candidate)
    applied = subprocess.run(['git', 'apply', '-'], cwd=candidate, input=patch.stdout,
                             capture_output=True, text=True, timeout=20)
    assert applied.returncode == 0, applied.stderr
    for path, content in output['proposal']['files'].items():
        assert (candidate / path).read_text() == content
    assert (root / 'prompt.md').read_text() == 'Baseline\n'


@pytest.mark.parametrize('payload', [
    {'feedback': [{'id': 'a', 'kind': 'failure', 'text': 'bad'}], 'skills': {'skills/main.md': 'x'}},
    {'feedback': [{'id': 'a', 'kind': 'success', 'text': 'good'}],
     'instruction': {'path': 'prompt.md', 'text': 'x'}},
])
def test_instruction_refinement_validates_before_model_call(payload, endpoint, tmp_path):
    _, requests, _ = endpoint
    result = run_cli(payload, endpoint, tmp_path, '--refine-instruction')
    assert result.returncode == 1 and not result.stdout
    assert 'requires an instruction and failure feedback' in result.stderr
    assert not requests


def test_instruction_refinement_obeys_logical_call_limit(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.extend((200, model_reply(json.dumps(row))) for row in [
        {'key_insight': 'Check results', 'bullet_tags': []},
        {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Report checks.'}]},
    ])
    payload = {'feedback': [{'id': 'one', 'kind': 'failure', 'text': 'Tests were skipped.'}],
               'instruction': {'path': 'prompt.md', 'text': 'Baseline\n'}}
    result = run_cli(payload, endpoint, tmp_path, '--refine-instruction', '--max-calls', '2')
    assert result.returncode == 1 and not result.stdout
    assert 'logical model call limit reached' in result.stderr and len(requests) == 2


def test_invalid_instruction_refinement_does_not_return_partial_candidate(endpoint, tmp_path):
    _, requests, replies = endpoint
    replies.extend((200, model_reply(json.dumps(row))) for row in [
        {'key_insight': 'Check results', 'bullet_tags': []},
        {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Report checks.'}]},
        {'instruction': ''},
    ])
    payload = {'feedback': [{'id': 'one', 'kind': 'failure', 'text': 'Tests were skipped.'}],
               'instruction': {'path': 'prompt.md', 'text': 'Baseline\n'}}
    result = run_cli(payload, endpoint, tmp_path, '--refine-instruction')
    assert result.returncode == 1 and not result.stdout
    assert 'nonempty instruction' in result.stderr and len(requests) == 3
