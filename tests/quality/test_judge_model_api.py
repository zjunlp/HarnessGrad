"""Real broker HTTP requests against a local protocol stub; no paid API calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from data.scaffolds.model_api import non_reasoning_profile
from data.scaffolds.runtime_extensions import validate_judge
from eval.service.judge import Judge, JudgeUnavailable


@pytest.fixture
def endpoint(monkeypatch):
    state = {'requests': [], 'message': {'content': 'yes'},
             'usage': {'prompt_tokens': 20, 'completion_tokens': 1}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(body)
            # `served_model` lets a test make the provider answer as a different model than the
            # one the request named, which is the case the receipt has to be able to show.
            response = json.dumps({'model': state.get('served_model') or body['model'],
                'choices': [{'message': state['message'], 'finish_reason': 'stop'}],
                'usage': state['usage']}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HG_JUDGE_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HG_JUDGE_API_KEY', 'test-secret')
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def spec(model, **extra):
    return dict(model=model, max_output_tokens=10,
                parameters={'temperature': 0.1, 'top_p': 0.9, 'seed': 42}, **extra)


def complete(broker):
    return broker._call('complete', {'messages': [{'role': 'user', 'content': 'Reply yes.'}]})


@pytest.mark.parametrize('model,token_key,fields', [
    ('gpt-4o-2024-05-13', 'max_tokens', {}),
    ('gpt-6-sol', 'max_completion_tokens', {'reasoning_effort': 'none'}),
    ('gpt-6-luna', 'max_completion_tokens', {'reasoning_effort': 'none'}),
    ('deepseek-flash', 'max_tokens', {'thinking': {'type': 'disabled'}}),
    ('glm-5.2', 'max_tokens', {'thinking': {'type': 'disabled'}}),
    ('mimo-v2.6-flash', 'max_completion_tokens', {'thinking': {'type': 'disabled'}}),
    ('Ling-3.1-flash', 'max_tokens', {'thinking': {'type': 'disabled'}}),
])
def test_wire_request_and_receipt(endpoint, model, token_key, fields):
    declaration = spec(model, reasoning='disabled')
    with Judge(declaration) as broker:
        assert complete(broker)['content'] == 'yes'
    request, = endpoint['requests']
    expected = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply yes.'}],
                'stream': False, token_key: 10, 'temperature': 0.1, 'top_p': 0.9,
                'seed': 42, **fields}
    if model.startswith('deepseek-') or model in ('glm-5.2', 'mimo-v2.6-flash', 'Ling-3.1-flash'):
        expected.pop('seed')
        if model == 'deepseek-flash':
            expected.pop('top_p')
    assert request == expected
    report = broker.report()
    assert report['configuration'] == declaration  # Original protocol retained.
    assert report['api_options']['fields'] == fields
    assert report['api_options']['parameters'] == {k: expected[k] for k in
                                                   ('temperature', 'top_p', 'seed') if k in expected}
    assert not report['faults']
    assert 'test-secret' not in json.dumps(report)
    if model == 'deepseek-flash':
        assert report['api_options']['omitted_parameters'] == {'seed': 42, 'top_p': 0.9}
        assert report['api_options']['provider_fixed_parameters'] == {'top_p': 1.0}
    elif model in ('glm-5.2', 'mimo-v2.6-flash', 'Ling-3.1-flash'):
        assert report['api_options']['omitted_parameters'] == {'seed': 42}
        assert report['api_options']['provider_fixed_parameters'] == {}


@pytest.mark.parametrize('model', [
    'gpt-6-astra', 'gpt-6.1-sol', 'gpt-5', 'gpt-5-mini', 'gpt-5-nano',
    'gpt-5.4-pro', 'gpt-5.3-codex', 'o3', 'o4-mini', 'gpt-7',
    'gpt-5.1', 'gpt-5.1-custom', 'deepseek-v4-pro', 'deepseek-reasoner',
    'glm-5.3', 'glm-5.3-flash', 'glm-5.2-custom', 'glm-5.2-2026-10-01',
    'mimo-v2.6-pro', 'mimo-v2.6-flash-custom', 'Ling-3.0-flash',
    'MiniMax-M3.1-Flash', 'qwen3.5-9b-local',
])
def test_unsupported_models_fail_before_http(endpoint, model):
    with pytest.raises(ValueError, match='non-reasoning'):
        Judge(spec(model, reasoning='disabled'))
    assert not endpoint['requests']


@pytest.mark.parametrize('policy', ['none', 'low', 'minimal', 'enabled', None, False])
def test_no_ambiguous_mode_or_low_effort_fallback(policy):
    with pytest.raises(ValueError, match='reasoning'):
        validate_judge(spec('gpt-6-sol', reasoning=policy))


@pytest.mark.parametrize('model', ['gpt-4o', 'gpt-4o-2024-05-13', 'gpt-6-sol', 'gpt-6-luna'])
def test_supported_families_and_snapshot_ids(model):
    assert non_reasoning_profile(model)['provider'] == 'openai'


@pytest.mark.parametrize('field', ['reasoning_content', 'reasoning', 'reasoning_tokens', 'top_level_tokens'])
@pytest.mark.parametrize('model', ['gpt-6-sol', 'glm-5.2', 'mimo-v2.6-flash'])
def test_provider_ignoring_off_is_failure_with_usage_retained(endpoint, field, model):
    if field == 'reasoning_tokens':
        endpoint['usage']['completion_tokens_details'] = {'reasoning_tokens': 1}
    elif field == 'top_level_tokens':
        endpoint['usage']['reasoning_tokens'] = 1
    else:
        endpoint['message'][field] = 'Still thinking'
    with Judge(spec(model, reasoning='disabled')) as broker:
        with pytest.raises(JudgeUnavailable, match='reasoning'):
            complete(broker)
    report = broker.report()
    assert report['faults'] and report['calls'][0]['status'] == 'error'
    assert report['usage'] == {'prompt_tokens': 20, 'completion_tokens': 1}
    assert len(endpoint['requests']) == 1


@pytest.mark.parametrize('model', ['gpt-6-sol', 'glm-5.2', 'mimo-v2.6-flash'])
def test_zero_reasoning_usage_is_accepted(endpoint, model):
    endpoint['usage']['completion_tokens_details'] = {'reasoning_tokens': 0}
    with Judge(spec(model, reasoning='disabled')) as broker:
        assert complete(broker)['content'] == 'yes'


def test_legacy_judge_path_does_not_change(endpoint):
    endpoint['message']['reasoning_content'] = 'Existing provider behavior'
    with Judge(spec('arbitrary-existing-model')) as broker:
        assert complete(broker)['content'] == 'yes'
    request, = endpoint['requests']
    assert request['max_tokens'] == 10 and request['seed'] == 42
    assert not {'reasoning_effort', 'thinking', 'max_completion_tokens'} & request.keys()
    assert 'api_options' not in broker.report()


def test_judge_model_mismatch_is_recorded_not_refused(endpoint, monkeypatch):
    """An overridden judge model is allowed, but the receipt must say so.

    The metric identity of two of the stacked datasets depends on which model answered
    (`strongreject.rubric.defense.<model>.v1`; memoryagentbench's profile carries the model plus
    its reasoning policy), and two profiles must be reported separately. The old behaviour let
    `HG_JUDGE_MODEL` replace the declared model with nothing on the record saying so, so two
    points measured under different judges were indistinguishable. It is recorded rather than
    refused because the override is a supported capability -- unlike `environment`, where a
    different model is a different world and stays fatal.
    """
    monkeypatch.setenv('HG_JUDGE_MODEL', 'some-other-judge')
    with Judge(spec('declared-judge-model')) as broker:
        assert complete(broker)['content'] == 'yes'
        report = broker.report()
    assert report['model_mismatch'] == {'declared': 'declared-judge-model',
                                       'actual': 'some-other-judge'}
    # The declaration still governs the wire request: the override is about *recording*, not
    # about silently sending a different model than the one the declaration froze.
    request, = endpoint['requests']
    assert request['model'] == 'declared-judge-model'


def test_matching_judge_model_leaves_the_receipt_unchanged(endpoint, monkeypatch):
    monkeypatch.setenv('HG_JUDGE_MODEL', 'declared-judge-model')
    with Judge(spec('declared-judge-model')) as broker:
        assert 'model_mismatch' not in broker.report()


def test_served_model_mismatch_is_recorded(endpoint):
    """The provider's own model id is compared with the declaration, per call.

    The request always names the declared model, so a provider that answers as something else
    would otherwise leave the receipt carrying the declared id -- and `strongreject`'s metric
    identity bakes that id in (`strongreject.rubric.defense.<model>.v1`). Recorded by default;
    see the next test for the opt-in refusal.
    """
    endpoint['served_model'] = 'some-other-snapshot'
    with Judge(spec('declared-judge-model')) as broker:
        assert complete(broker)['content'] == 'yes'
        report = broker.report()
    assert report['served_model_mismatches'] == [{
        'declared': 'declared-judge-model', 'served': 'some-other-snapshot',
        'request_sha256': report['calls'][0]['request_sha256']}]
    assert report['calls'][0]['served_model_mismatch']['served'] == 'some-other-snapshot'


def test_served_model_mismatch_can_be_made_fatal(endpoint, monkeypatch):
    monkeypatch.setenv('HG_JUDGE_REQUIRE_SERVED_MODEL', '1')
    endpoint['served_model'] = 'some-other-snapshot'
    with Judge(spec('declared-judge-model')) as broker:
        with pytest.raises(JudgeUnavailable):
            complete(broker)
        assert broker.report()['served_model_mismatches'][0]['served'] == 'some-other-snapshot'
