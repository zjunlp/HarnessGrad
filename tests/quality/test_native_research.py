"""Native source conversion, private scoring and real verifier-only judge transport."""
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
sys.path.insert(0, str(ROOT))
from data.scaffolds.native_research.adapter import Adapter
from data.scaffolds.native_research.native import activate
from tools.prepare_native_research import prepare

ASSETS = ROOT / 'data/scaffolds/native_research'
sys.path.insert(0, str(ASSETS))
from scoring import score


def encrypted(text, password):
    key = hashlib.sha256(password.encode()).digest()
    return base64.b64encode(bytes(v ^ key[i % len(key)] for i, v in enumerate(text.encode()))).decode()


def row(name):
    if name == 'hle':
        return {'id': 'source-1', 'question': 'Return the marker.', 'answer': 'PRIVATE_REFERENCE_MARKER',
                'image': '', 'category': 'test', 'answer_type': 'exactMatch'}
    if name == 'browsecomp':
        return {'problem': encrypted('Return a location.', 'canary-1'),
                'answer': encrypted('PRIVATE_REFERENCE_MARKER', 'canary-1'), 'canary': 'canary-1'}
    if name == 'deepsearchqa':
        return {'problem': 'List the relevant names.', 'answer': 'PRIVATE_REFERENCE_MARKER',
                'problem_category': 'test', 'answer_type': 'Single Answer'}
    raise AssertionError(name)


def fixture(tmp_path, name, monkeypatch):
    source = tmp_path / 'test.jsonl'
    source.write_text(json.dumps(row(name)) + '\n')
    output = tmp_path / 'prepared'
    manifest = prepare(name, output, {'data_path': str(source)}, judge_model='local-scripted-judge')
    monkeypatch.setenv('HG_DATA_' + name.upper(), str(output))
    adapter = Adapter(name)
    return adapter, manifest


@pytest.mark.parametrize('name', ['browsecomp', 'deepsearchqa', 'hle'])
def test_native_questions_and_private_answers_remain_separate(tmp_path, name, monkeypatch):
    adapter, manifest = fixture(tmp_path, name, monkeypatch)
    tasks, _ = adapter.load()
    assert len(tasks) == 1
    assert 'PRIVATE_REFERENCE_MARKER' not in json.dumps(tasks)
    tid = tasks[0]['task_id']
    setup = adapter.setup(tid)
    assert 'PRIVATE_REFERENCE_MARKER' not in json.dumps(setup)
    assert all(entry['path'] != 'upstream_case.json' for entry in setup['files'])
    private = adapter.case(manifest['records'][0])
    assert private['row'] == row(name)
    assert adapter.split() is None
    assert adapter.verifier(tid)['result_contract']['require_exit_zero'] is True


def test_corrupted_private_input_is_rejected_before_measurement(tmp_path, monkeypatch):
    adapter, manifest = fixture(tmp_path, 'hle', monkeypatch)
    record = manifest['records'][0]
    (adapter.root / record['case']).write_text('{}')
    with pytest.raises(ValueError, match='digest mismatch'):
        adapter.load()


def test_hle_keeps_native_schema_and_rejects_truthy_strings():
    case = {'row': row('hle')}
    good = {'extracted_final_answer': 'A', 'reasoning': 'match', 'correct': 'yes', 'confidence': 80, 'strict': True}
    assert score('hle', case, 'A', lambda messages: json.dumps(good))['reward'] == 1
    with pytest.raises(ValueError, match='structured verdict'):
        score('hle', case, 'A', lambda messages: json.dumps({**good, 'strict': 'true'}))


def test_browsecomp_preserves_documented_native_group_fix():
    case = {'row': row('browsecomp')}
    assert score('browsecomp', case, 'location', lambda messages: 'correct: yes')['reward'] == 1
    assert score('browsecomp', case, 'location', lambda messages: 'correct: no')['reward'] == 0


def test_deepsearchqa_uses_native_set_precision_recall():
    case = {'row': row('deepsearchqa')}
    rating = {'Answer Correctness': {'Explanation': 'partial', 'Correctness Details': {'a': True, 'b': False}, 'Excessive Answers': ['extra']}}
    result = score('deepsearchqa', case, 'a, extra', lambda messages: json.dumps(rating))
    assert result['metrics']['precision'] == result['metrics']['recall'] == result['reward'] == .5
    with pytest.raises((KeyError, ValueError)):
        score('deepsearchqa', case, 'a', lambda messages: '{}')


@contextmanager
def local_judge(*, truncate=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            content = json.dumps({'extracted_final_answer': 'submitted', 'reasoning': 'scripted',
                                  'correct': 'yes', 'confidence': 100, 'strict': True})
            response = {'model': 'local-scripted-judge', 'choices': [{'message': {'role': 'assistant', 'content': content},
                'finish_reason': 'length' if truncate else 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7}}
            raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('truncate', [False, True])
def test_real_sandbox_runs_native_structured_judge_after_actor_exit(tmp_path, monkeypatch, truncate):
    from eval.run.runner import run_one
    from harnessgrad import config
    adapter, manifest = fixture(tmp_path, 'hle', monkeypatch)
    tasks, _ = adapter.load()
    task = tasks[0]
    workroot = tmp_path / 'work'
    repo = workroot / 'run/workspace'
    repo.mkdir(parents=True)
    (repo / 'harness.json').write_text(json.dumps({'name': 'selected-probe', 'version': '1', 'entrypoint': 'agent.py'}))
    (repo / 'agent.py').write_text('''import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir)
assert not (w/'upstream_case.json').exists()
assert not os.environ.get('HG_JUDGE_API_KEY')
(w/'answer.txt').write_text('actor answer')
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
''')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(workroot))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    with local_judge(truncate=truncate) as (base, requests):
        monkeypatch.setenv('HG_JUDGE_BASE_URL', base)
        monkeypatch.setenv('HG_JUDGE_API_KEY', 'PRIVATE_JUDGE_KEY')
        monkeypatch.setenv('HG_JUDGE_MODEL', 'local-scripted-judge')
        config.reset()
        result = run_one(repo, task, sandbox=True, setup=adapter.setup(task['task_id']),
                         verifier=adapter.verifier(task['task_id']), env={'kind': 'files', 'network': 'none'},
                         timeout_s=30, verify_timeout_s=30)
    config.reset()
    assert len(requests) == 1
    assert requests[0]['response_format']['json_schema']['strict'] is True
    assert requests[0]['max_completion_tokens'] == 4096
    assert 'max_tokens' not in requests[0] and 'temperature' not in requests[0]
    assert 'PRIVATE_REFERENCE_MARKER' in requests[0]['messages'][0]['content']
    assert 'PRIVATE_REFERENCE_MARKER' not in result['trace']
    verdict = result['verdict']
    if truncate:
        assert verdict.get('unmeasured') or verdict.get('score') is None
    else:
        assert result['exit_code'] == 0, result.get('stderr')
        assert verdict['passed'] is True, verdict


def test_pinned_vendor_bytes_and_attributed_import_projection():
    provenance = json.loads((ASSETS / 'PROVENANCE.json').read_text())
    for name, sha in provenance['files'].items():
        assert hashlib.sha256((ASSETS / 'vendor' / name).read_bytes()).hexdigest() == sha
    assert (ASSETS / 'LICENSE.upstream').is_file()


def test_gpass_is_native_hypergeometric_tail_and_requires_complete_labels():
    from tools.report_livemathbench import aggregate
    point = {'per_task': {'task': .5}, 'per_task_trials': {'task': [1, 1, 0, 0]}, 'n_invalid': 0}
    report = aggregate(point, n=4, ks=(2,))
    assert report['complete'] is True
    assert report['metrics']['accuracy'] == .5
    assert report['metrics']['G-Pass@2_0.0'] == pytest.approx(5 / 6)
    assert report['metrics']['G-Pass@2_1.0'] == pytest.approx(1 / 6)
    assert report['metrics']['mG-Pass@2'] == pytest.approx(1 / 6)
    point['per_task_trials']['task'].pop()
    report = aggregate(point, n=4, ks=(2,))
    assert report['complete'] is False and report['metrics'] is None


def test_offline_files_actor_can_use_platform_web_without_docker(tmp_path, monkeypatch):
    from eval.run.runner import run_one
    adapter, manifest = fixture(tmp_path, 'browsecomp', monkeypatch)
    task = adapter.load()[0][0]
    seen = []
    class Searx(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.path)
            raw = json.dumps({'results': [{'title': 'fixture', 'url': 'https://example.org/native',
                                          'content': 'PUBLIC_SEARCH_SNIPPET'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Searx)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv('HG_WEB_SEARCH_PROVIDER', 'searxng')
    monkeypatch.setenv('HG_WEB_FETCH_PROVIDER', 'http')
    monkeypatch.setenv('HG_WEB_SEARXNG_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    workroot = tmp_path / 'work'
    repo = workroot / 'run/workspace'; repo.mkdir(parents=True)
    (repo / 'harness.json').write_text(json.dumps({'name': 'native-web-probe', 'version': '1', 'entrypoint': 'agent.py'}))
    (repo / 'agent.py').write_text('''import json,subprocess,sys
from pathlib import Path
w=Path.cwd()
assert not (w/'upstream_case.json').exists()
assert not (w/'trusted_web').exists()
r=subprocess.run([sys.executable,'web.py','call','search',json.dumps({'query':'native fixture'})],capture_output=True,text=True,errors='replace')
assert r.returncode==0,r.stderr
assert 'PUBLIC_SEARCH_SNIPPET' in r.stdout,r.stdout
(w/'answer.txt').write_text('web worked')
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
''')
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(workroot))
    try:
        result = run_one(repo, task, sandbox=True, setup=adapter.setup(task['task_id']),
                         verifier={'kind': 'command', 'argv': ['python3', '-c',
                             "from pathlib import Path; assert Path('answer.txt').read_text() == 'web worked'"]},
                         env=adapter.environment(task['task_id']), timeout_s=30)
    finally:
        server.shutdown(); server.server_close(); thread.join()
    assert not result.get('invalid'), result
    assert result['exit_code'] == 0, result.get('stderr')
    assert result['verdict']['passed'] is True
    assert len(seen) == 1 and 'native' in seen[0]


@pytest.mark.parametrize('change', [
    {'token_parameter': 'unbounded'},
    {'response_format': {'type': 'text'}},
    {'response_format': {'type': 'json_schema', 'json_schema': {'name': 'x', 'strict': False, 'schema': {}}}},
])
def test_structured_judge_configuration_fails_closed(change):
    from data.scaffolds.runtime_extensions import validate_judge
    with pytest.raises(ValueError):
        validate_judge({'model': 'local', **change})
