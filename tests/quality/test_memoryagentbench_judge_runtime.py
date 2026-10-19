"""Both model judges through the real relay, verifier sandbox and model broker."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from data.datasets import memoryagentbench as dataset
from data.datasets.memoryagentbench import control
from data.datasets.memoryagentbench.lib import judge, storage
from data.datasets.memoryagentbench.lib.reporting import summarize
from test_memoryagentbench_dataset import prepared


@pytest.fixture
def judge_pool(prepared):
    tid, service, gold = prepared

    def configure(source, count=1, broken_metadata=False):
        service['source'] = gold['source'] = source
        service['questions'] = [{'id': storage.question_id(tid, i), 'text': f'Question {i}?'}
                                for i in range(count)]
        service.pop('input_sha256')
        service['input_sha256'] = storage.canonical_sha256(service)
        gold['input_sha256'] = service['input_sha256']
        gold['gold_by_id'] = {q['id']: [f'Private correct answer {i}.']
                             for i, q in enumerate(service['questions'])}
        gold['judge_metadata_by_id'] = {q['id']: dict(question=q['text'],
            question_type='single-session-user', question_id=f'original-{i}', qa_pair_id=None,
            keypoints=['Fact one', 'Fact two']) for i, q in enumerate(service['questions'])}
        if broken_metadata:
            for meta in gold['judge_metadata_by_id'].values():
                meta['keypoints'] = None
                meta['question_id'] = None
        root = dataset.PREPARED_ROOT
        pool = json.loads((root / 'pool.json').read_text())
        pool['tasks'][0].update(source=source, input_sha256=service['input_sha256'], question_count=count)
        changed = {'pool.json': pool, f'service/{tid}.json': service, f'gold/{tid}.json': gold}
        manifest = json.loads((root / 'manifest.json').read_text())
        for name, value in changed.items():
            (root / name).write_text(json.dumps(value))
            manifest['artifacts'][name] = storage.file_sha256(root / name)
        (root / 'manifest.json').write_text(json.dumps(manifest))
        dataset._rows.cache_clear()
        return tid
    return configure


@pytest.fixture
def judge_server(monkeypatch):
    state = {'requests': [], 'fault': None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(body)
            if state['fault'] == 'http' or (state['fault'] == 'late-http' and len(state['requests']) > 1):
                self.send_response(503)
                self.end_headers()
                return
            prompt = body['messages'][0]['content']
            wrong = 'ZXQVMABWRONGSENTINEL' in prompt
            if body['max_tokens'] in (10, 512):
                response = 'no' if wrong else 'yes'
            else:
                index = (len(state['requests']) - 1) % 3
                response = json.dumps([{'fluency': 1}, {'recall': 0 if wrong else 2},
                    {'sentence_count': 2, 'precision': 0 if wrong else 2}][index])
            if state['fault'] == 'malformed':
                response = 'unparseable response'
            if state['fault'] == 'out-of-range':
                response = '{"fluency":2,"recall":99,"sentence_count":1,"precision":99}'
            out = json.dumps({'model': 'fixture-judge', 'choices': [{'message': {'content': response}}],
                              'usage': {'prompt_tokens': 11, 'completion_tokens': 3}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HG_JUDGE_BASE_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HG_JUDGE_MODEL', 'fixture-judge')
    monkeypatch.setenv('HG_MAB_JUDGE_MODEL', 'fixture-judge')
    monkeypatch.setenv('HG_JUDGE_API_KEY', 'fixture-secret')
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def run_control(tmp_path, monkeypatch, tid, mode, platform=True):
    work = tmp_path / mode
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    return control.run(mode, platform, tid, work)


@pytest.mark.parametrize('source,count,calls', [(judge.SUMMARY, 1, 3), (judge.LONGMEM, 60, 60)])
@pytest.mark.parametrize('mode,score', [('oracle', 1), ('wrong', 0), ('noop', None)])
def test_judges_complete_episode_in_real_sandbox(judge_pool, judge_server, tmp_path, monkeypatch,
                                                source, count, calls, mode, score):
    tid = judge_pool(source, count)
    result = run_control(tmp_path, monkeypatch, tid, mode)
    assert result['score'] == score, json.dumps(result)
    assert len(judge_server['requests']) == (0 if mode == 'noop' else calls)
    spec = dataset.VERIFY[tid]['judge']
    assert spec['max_calls'] == calls
    if score is not None:
        verdict = result['detail']['verdict']
        assert verdict['judge']['usage']['prompt_tokens'] == calls * 11
        assert 'fixture-secret' not in json.dumps(result)
        report = summarize([result['evidence']], [dataset._rows()[tid]])
        assert report['sources'][source]['score'] == score
        assert report['sources'][source]['judge'] == judge.profile(source, 'fixture-judge')
        assert report['coverage']['measured_questions'] == count
        prompts = [r['messages'][0]['content'] for r in judge_server['requests']]
        if source == judge.LONGMEM:
            assert all(f'Question {i}?' in prompt and f"['Private correct answer {i}.']" in prompt
                       for i, prompt in enumerate(prompts))
        for corrupt in ('episode', 'question'):
            damaged = copy.deepcopy(result['evidence'])
            if corrupt == 'episode':
                damaged['judge']['model'] = 'different-model'
            else:
                damaged['result']['questions'][0]['judge']['profile']['identity'] = 'wrong'
            with pytest.raises(ValueError, match='judge profile'):
                summarize([damaged], [dataset._rows()[tid]])
        other = copy.deepcopy(result['evidence'])
        other_row = dict(dataset._rows()[tid], task_id=storage.task_id('Conflict_Resolution', 1),
                         episode_id=storage.task_id('Conflict_Resolution', 1))
        other['task_id'] = other_row['task_id']
        other['result']['episode_id'] = other_row['episode_id']
        different = judge.profile(source, 'different-valid-model')
        other['judge'] = other['result']['judge'] = different
        for index, question in enumerate(other['result']['questions']):
            question['question_id'] = storage.question_id(other_row['task_id'], index)
            question['judge']['profile'] = different
        with pytest.raises(ValueError, match='mixed judge profiles'):
            summarize([result['evidence'], other], [dataset._rows()[tid], other_row])
    else:
        assert result['evidence']['reason'] == 'incomplete_episode'


@pytest.mark.parametrize('source', [judge.SUMMARY, judge.LONGMEM])
@pytest.mark.parametrize('fault', ['http', 'late-http', 'malformed', 'metadata'])
def test_judge_failure_never_becomes_zero(judge_pool, judge_server, tmp_path, monkeypatch, source, fault):
    tid = judge_pool(source, count=2, broken_metadata=fault == 'metadata')
    judge_server['fault'] = fault
    result = run_control(tmp_path, monkeypatch, tid, 'oracle')
    assert result['score'] is None, result
    assert result['detail']['verdict']['outcome'] == 'unmeasured'
    if fault == 'metadata':
        assert not judge_server['requests']
    if fault in ('http', 'late-http'):
        assert len(judge_server['requests']) == (1 if fault == 'http' else 2)


def test_manual_control_uses_judge_broker(judge_pool, judge_server, tmp_path, monkeypatch):
    tid = judge_pool(judge.SUMMARY)
    result = run_control(tmp_path, monkeypatch, tid, 'oracle', platform=False)
    assert result['score'] == 1, result
    assert len(judge_server['requests']) == 3


def test_out_of_range_judge_score_is_unmeasured(judge_pool, judge_server, tmp_path, monkeypatch):
    tid = judge_pool(judge.SUMMARY)
    judge_server['fault'] = 'out-of-range'
    result = run_control(tmp_path, monkeypatch, tid, 'oracle')
    assert result['score'] is None
    assert result['detail']['verdict']['exit_code'] == 3


def test_output_budget_override_reaches_broker_and_evidence(judge_pool, judge_server, tmp_path, monkeypatch):
    tid = judge_pool(judge.LONGMEM)
    monkeypatch.setenv('HG_MAB_JUDGE_MAX_OUTPUT_TOKENS', '512')
    result = run_control(tmp_path, monkeypatch, tid, 'oracle')
    assert result['score'] == 1, result
    assert judge_server['requests'][0]['max_tokens'] == 512
    selected = judge.profile(judge.LONGMEM, 'fixture-judge', max_output_tokens=512)
    assert result['evidence']['judge'] == selected
    report = summarize([result['evidence']], [dataset._rows()[tid]])
    assert report['sources'][judge.LONGMEM]['judge'] == selected
