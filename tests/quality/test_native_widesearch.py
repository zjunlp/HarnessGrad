"""Real WideSearch native tables and HG private-broker protocol controls.

All task/gold rows here are explicitly authored controls, not benchmark samples.
The real pinned evaluator runs with a localhost scripted judge, never paid APIs.
"""
from contextlib import contextmanager
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'data/scaffolds/native_research')]
from scoring import score
from data.scaffolds.native_research.adapter import Adapter
from tools.prepare_native_research import prepare
from data.scaffolds.native_research import wide_runtime
from harnessgrad.config.areas import ConfigMissing

needs_native = pytest.mark.skipif(importlib.util.find_spec('dateparser') is None,
    reason='native WideSearch dateparser is not provisioned in this interpreter')


def test_explicit_runtime_requires_both_fields():
    with pytest.raises(ConfigMissing, match='together'):
        wide_runtime.settings({'HG_JUDGE_WIDESEARCH_PYTHON': sys.executable})


def test_explicit_runtime_cannot_reexpose_hidden_tmp(tmp_path):
    with pytest.raises(ValueError, match='outside hidden platform/tmp'):
        wide_runtime.settings({'HG_JUDGE_WIDESEARCH_PYTHON': sys.executable,
            'HG_JUDGE_WIDESEARCH_RUNTIME_PATHS': json.dumps([str(tmp_path)])})


def row():
    return {'instance_id': 'native_control', 'query': 'Return entity and description as a Markdown table.',
        'language': 'en', 'evaluation': {'required': ['entity', 'description'],
        'unique_columns': ['entity'], 'eval_pipeline': {
            'entity': {'metric': ['exact_match']},
            'description': {'metric': ['llm_judge'], 'criterion': 'Match the full description.'}}}}


def prediction(correct=True):
    return '| entity | description |\n| --- | --- |\n| row1 | ' + (
        'PRIVATE_GOLD_DESCRIPTION' if correct else 'WRONG_DESCRIPTION') + ' |'


def case():
    value = row()
    raw = b'entity,description\nrow1,PRIVATE_GOLD_DESCRIPTION\n'
    return {'row': {**value, '_gold_sha256': hashlib.sha256(raw).hexdigest()},
        'wide_query': {'schema': 'harnessgrad.widesearch-query.csv.v1',
        'instance_id': value['instance_id'], 'query': value['query'],
        'language': 'en', 'evaluation': value['evaluation'],
        'gold_csv_base64': base64.b64encode(raw).decode('ascii')}}


def scripted(messages, correct=True):
    text = messages[0]['content']
    if text.startswith('Your task is to align two vocabularies.'):
        return '```json\n{"row1":"row1"}\n```'
    assert text.startswith('You are an expert in grading answers.')
    return '```json\n{"idx_0":' + str(int(correct)) + '}\n```'


@needs_native
@pytest.mark.parametrize('correct', [True, False])
def test_native_key_alignment_and_column_evaluator(correct):
    requests = []
    def complete(messages):
        requests.append(messages)
        return scripted(messages, correct)
    result = score('widesearch', case(), prediction(correct), complete)
    assert result['reward'] == float(correct)
    assert result['metrics']['f1_by_row'] == float(correct)
    assert len(requests) == 2
    assert 'PRIVATE_GOLD_DESCRIPTION' in json.dumps(requests)


@needs_native
def test_provider_failure_escapes_native_zero_fallback():
    def unavailable(_messages):
        raise ConnectionError('local scripted provider unavailable')
    with pytest.raises(ValueError, match='WideSearch judge request failed'):
        score('widesearch', case(), prediction(), unavailable)


@needs_native
@pytest.mark.parametrize('response', ['not json', '```json\n{"row1":true}\n```'])
def test_malformed_judge_alignment_is_not_native_wrong_answer_zero(response):
    with pytest.raises(ValueError, match='WideSearch judge request failed'):
        score('widesearch', case(), prediction(), lambda _messages: response)


@needs_native
@pytest.mark.parametrize('prediction_text', [prediction(), ''])
def test_legacy_lossy_json_cases_require_explicit_repreparation(prediction_text):
    value = case()
    value['wide_query'].pop('schema')
    value['wide_query']['answer'] = '{"columns":["entity"],"index":[0],"data":[["row1"]]}'
    with pytest.raises(ValueError, match='re-prepare legacy JSON cases'):
        score('widesearch', value, prediction_text, scripted)


@needs_native
def test_private_csv_bytes_are_bound_to_native_row_hash():
    value = case()
    value['wide_query']['gold_csv_base64'] = base64.b64encode(
        b'entity,description\nrow1,TAMPERED_REFERENCE\n').decode('ascii')
    with pytest.raises(ValueError, match='gold CSV digest mismatch'):
        score('widesearch', value, prediction(), scripted)


@needs_native
@pytest.mark.parametrize('precise', ['0.12345678901234567', '1.2345678901234567',
    '0.9999999999999999', '1.2345678901234567e-100'])
def test_prepare_csv_preserves_native_float_nan_and_integer_differential(precise, tmp_path, monkeypatch):
    import io
    import pandas as pd
    from data.scaffolds.native_research.native import activate, benchmark
    activate()
    from upstream.benchmarks.widesearch import METRICS
    from upstream.benchmarks.widesearch._vendor import evaluation
    from upstream.benchmarks.widesearch._vendor.data_loader import WideSearchResponse

    native_row = {'instance_id': 'numeric_control', 'query': 'Return the numeric table.',
        'language': 'en', 'evaluation': {'required': ['entity', 'whole', 'precise', 'missing'],
        'unique_columns': ['entity'], 'eval_pipeline': {
            col: {'metric': ['exact_match']} for col in ('whole', 'precise', 'missing')}}}
    raw = (' Precise ,Whole,Missing,Entity,Ignored\r\n'
        + precise + ',9223372036854775806,,row1,private-extra\r\n'
        + '0.31415926535897931,2,2.5,row2,private-extra\r\n').encode()
    source = tmp_path / 'native'
    (source / 'widesearch_gold').mkdir(parents=True)
    (source / 'widesearch.jsonl').write_text(json.dumps(native_row) + '\n')
    (source / 'widesearch_gold/numeric_control.csv').write_bytes(raw)
    native = benchmark('widesearch', {'data_path': str(source)})
    list(native._iter_tasks())
    original_query = native._queries_by_id['numeric_control']
    expected = original_query.answer.copy(deep=True)
    assert str(expected['whole'].dtype) == 'int64'
    assert str(expected['precise'].dtype) == str(expected['missing'].dtype) == 'float64'
    assert pd.isna(expected.loc[0, 'missing'])
    lossy = pd.read_json(io.StringIO(expected.to_json(orient='split')), orient='split')
    assert any(float(a).hex() != float(b).hex()
        for a, b in zip(expected['precise'], lossy['precise']))

    prepared = tmp_path / 'prepared'
    manifest = prepare('widesearch', prepared, {'data_path': str(source)}, judge_model='local-control')
    record = manifest['records'][0]
    case_path = prepared / record['case']
    private = json.loads(case_path.read_text())
    assert base64.b64decode(private['wide_query']['gold_csv_base64'], validate=True) == raw
    assert private['row']['_gold_sha256'] == hashlib.sha256(raw).hexdigest()
    assert manifest['files'][record['case']] == hashlib.sha256(case_path.read_bytes()).hexdigest()
    assert 'answer' not in private['wide_query']

    response = ('|entity|whole|precise|missing|\n|---|---|---|---|\n'
        '|row1|9223372036854775806|' + precise + '||\n'
        '|row2|2|0.31415926535897931|2.5|')
    native_evaluate = evaluation.evaluate_single_query
    native_result = native_evaluate(original_query, WideSearchResponse('numeric_control', response))
    observed = []
    def checked_evaluate(query, answer):
        pd.testing.assert_frame_equal(query.answer, expected, check_exact=True, check_dtype=True)
        assert query.answer.loc[0, 'precise'].hex() == expected.loc[0, 'precise'].hex()
        observed.append(True)
        return native_evaluate(query, answer)
    monkeypatch.setattr(evaluation, 'evaluate_single_query', checked_evaluate)
    def no_judge(_messages):
        pytest.fail('numeric exact-match control must not invoke a judge')
    result = score('widesearch', private, response, no_judge)
    assert observed == [True]
    assert result['metrics'] == {key: float(getattr(native_result, key)) for key in METRICS}

    # Changing even a private optional CSV column invalidates the entire bound
    # original byte sequence; modifying the sealed case also fails outer binding.
    private['wide_query']['gold_csv_base64'] = base64.b64encode(raw.replace(b'private-extra', b'changed')).decode()
    with pytest.raises(ValueError, match='gold CSV digest mismatch'):
        score('widesearch', private, response, no_judge)
    case_path.write_text(json.dumps(private))
    monkeypatch.setenv('HG_WIDESEARCH_DATA', str(prepared))
    with pytest.raises(ValueError, match='private native case digest mismatch'):
        Adapter('widesearch').case(record, manifest)


@needs_native
def test_explicit_fetch_captures_original_csv_without_second_download(tmp_path, monkeypatch):
    import huggingface_hub
    from data.scaffolds.native_research.native import activate
    activate()
    from upstream.benchmarks.widesearch import DATA_REVISION
    source = tmp_path / 'cached-authored-control'
    (source / 'widesearch_gold').mkdir(parents=True)
    (source / 'widesearch.jsonl').write_text(json.dumps(row()) + '\n')
    raw = b'entity,description\r\nrow1,PRIVATE_GOLD_DESCRIPTION\r\n'
    (source / 'widesearch_gold/native_control.csv').write_bytes(raw)
    requests = []
    def snapshot(**kwargs):
        requests.append(kwargs)
        return str(source)
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', snapshot)
    output = tmp_path / 'prepared'
    manifest = prepare('widesearch', output, {}, fetch=True, judge_model='local-control')
    assert len(requests) == 1
    assert requests[0]['revision'] == DATA_REVISION
    private = json.loads((output / manifest['records'][0]['case']).read_text())
    assert base64.b64decode(private['wide_query']['gold_csv_base64']) == raw


@contextmanager
def local_provider(fail=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            if fail:
                self.send_response(400)
                raw = b'{"error":{"message":"scripted provider unavailable"}}'
            else:
                content = scripted(body['messages'], 'WRONG_DESCRIPTION' not in json.dumps(body['messages']))
                raw = json.dumps({'model': body['model'], 'choices': [{'message': {
                    'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}).encode()
                self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally: server.shutdown(); server.server_close(); thread.join()


@needs_native
@pytest.mark.parametrize('mode', ['positive', 'wrong', 'provider-failure'])
def test_prepare_native_gold_and_hg_private_judge_broker(mode, tmp_path, monkeypatch):
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import run_one
    from harnessgrad import config
    source = tmp_path / 'native'
    source.mkdir()
    (source / 'widesearch_gold').mkdir()
    (source / 'widesearch.jsonl').write_text(json.dumps(row()) + '\n')
    (source / 'widesearch_gold/native_control.csv').write_text('entity,description\nrow1,PRIVATE_GOLD_DESCRIPTION\n')
    prepared = tmp_path / 'prepared'
    prepare('widesearch', prepared, {'data_path': str(source)}, judge_model='local-scripted-widejudge')
    monkeypatch.setenv('HG_WIDESEARCH_DATA', str(prepared))
    if sys.prefix != sys.base_prefix:
        monkeypatch.setenv('HG_JUDGE_WIDESEARCH_PYTHON', sys.executable)
        monkeypatch.setenv('HG_JUDGE_WIDESEARCH_RUNTIME_PATHS', json.dumps(list(dict.fromkeys([sys.prefix, sys.base_prefix]))))
    adapter = Adapter('widesearch')
    task = adapter.load()[0][0]
    assert 'PRIVATE_GOLD_DESCRIPTION' not in json.dumps(task)
    candidate = tmp_path / 'harness'
    candidate.mkdir()
    (candidate / 'harness.json').write_text(json.dumps({'name': 'wide-native-protocol-control',
        'version': '1', 'entrypoint': 'agent.py', 'env_kinds': ['files'], 'role': 'control'}))
    actor = """import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args();w=Path(a.workdir)
assert not (w/'upstream_case.json').exists()
assert not (w/'widesearch_gold').exists()
assert not os.environ.get('HG_JUDGE_API_KEY')
assert not os.environ.get('HG_JUDGE_WIDESEARCH_PYTHON')
(w/'answer.txt').write_text(ANSWER)
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
""".replace('ANSWER', repr(prediction(mode != 'wrong')))
    (candidate / 'agent.py').write_text(actor)
    work = tmp_path / 'work'
    repo = work / 'run/workspace'
    stage_workspace(candidate, repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    with local_provider(mode == 'provider-failure') as (base, requests):
        monkeypatch.setenv('HG_JUDGE_BASE_URL', base)
        monkeypatch.setenv('HG_JUDGE_API_KEY', 'PRIVATE_LOCAL_JUDGE_KEY')
        monkeypatch.setenv('HG_JUDGE_MODEL', 'local-scripted-widejudge')
        config.reset()
        verifier = adapter.verifier(task['task_id'])
        if sys.prefix != sys.base_prefix:
            assert verifier['argv'][0] == sys.executable
            assert verifier['runtime_paths'] == list(dict.fromkeys([sys.prefix, sys.base_prefix]))
        # This is an authored output grading control. No public web research
        # was performed or claimed; the actor's output is intentionally fixed.
        result = run_one(repo, task, sandbox=True, setup=adapter.setup(task['task_id']),
            verifier=verifier, env={'kind': 'files', 'network': 'none'},
            timeout_s=30, verify_timeout_s=30, run_id='wide-native-control', trial_id='0')
    config.reset()
    assert not result.get('invalid'), result
    assert result['exit_code'] == 0, result.get('stderr')
    verdict = result['verdict']
    if mode == 'provider-failure':
        assert verdict['score'] is None and verdict.get('unmeasured'), verdict
        assert requests, 'failure must originate in the real provider, not a missing native import'
    else:
        assert verdict['score'] == float(mode == 'positive'), verdict
        assert verdict['evidence']['measured'] is True
        assert verdict['evidence']['metrics']['score'] == float(mode == 'positive')
        assert len(requests) == 2
    assert 'PRIVATE_LOCAL_JUDGE_KEY' not in result['trace']
    assert 'PRIVATE_GOLD_DESCRIPTION' not in json.dumps(verdict.get('evidence', {}))
