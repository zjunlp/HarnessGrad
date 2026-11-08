"""Run default Loop and actual spreadsheet scorers from a stdlib-only platform."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import threading

ROOT, WORK = map(Path, sys.argv[1:3])
sys.path.insert(0, str(ROOT))
for package in ('openpyxl', 'openai', 'httpx', 'pip'):
    assert importlib.util.find_spec(package) is None, ('platform dependency', package)

from harnessgrad import candidate_runtime, dataset_runtime, dataset_runtime_identity, verification_context
from eval.run import runner


def adapter(name):
    spec = importlib.util.spec_from_file_location('authored_' + name, ROOT / ('data/datasets/' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workbook(path, a, b=7, dataset="spreadsheetbench"):
    # Only the explicitly managed artifact may create these authored fixtures.
    import subprocess
    selected = dataset_runtime.resolve_execution(dataset)
    subprocess.run([selected['python'], '-I', '-B', '-c',
        'import openpyxl,sys;w=openpyxl.Workbook();w.active.title="Sheet";'
        'w.active["A1"]=int(sys.argv[2]);w.active["B1"]=int(sys.argv[3]);w.save(sys.argv[1])',
        str(path), str(a), str(b)], check=True)


def run_case(name, wrong):
    case = WORK / (name + ('-wrong' if wrong else '-correct'))
    case.mkdir()
    os.environ['HG_DATASET_RUNTIME_ROOT'] = os.environ['HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT' if name == 'spreadsheetbench' else 'HG_SPREADSHEETBENCH2_TEST_RUNTIME_ROOT']
    module = adapter(name)
    private_data = WORK.parent / 'private-data' / case.name
    private_data.mkdir(parents=True)
    initial, golden = private_data / 'initial.xlsx', private_data / 'reference.xlsx'
    workbook(initial, 0, dataset=name)
    workbook(golden, 5, dataset=name)
    if name == 'spreadsheetbench':
        record = {'task_id': 'authored-v1', 'init_path': initial, 'golden_path': golden,
                  'instruction': 'Set A1 to the sum of two and three.', 'instruction_type': '',
                  'answer_position': 'Sheet!A1'}
        module._RECORDS, module._LOAD_ERROR = [record], None
        output = 'result.xlsx'
    else:
        record = {'task_id': 'spreadsheetbench2/Template/authored-v2', 'category': 'Template',
                  'row': {'id': 'authored-v2', 'instruction': 'Set A1 to the sum of two and three.',
                          'spreadsheet_path': 'initial.xlsx', 'golden_response_path': 'reference.xlsx',
                          'answer_position': 'Sheet!A1:B1'},
                  'input': initial, 'golden': golden, 'output': 'authored-v2_output.xlsx'}
        module.records = lambda: [record]
        output = record['output']
    tasks, scorable = module.load()
    task = tasks[0]
    tid = task['task_id']
    setup, verifier, env = module.SETUP[tid], module.VERIFY[tid], module.ENV[tid]
    # Capture all private identities without placing them into runtime_task/model content.
    identity = dataset_runtime_identity.for_evaluation(None, tasks, {tid: setup}, {tid: verifier}, {tid: env})
    context = verification_context.capture(tasks, scorable, {tid: setup}, {tid: verifier}, {tid: env})
    assert 'actor' in identity['tasks'][tid]
    source = case / 'loop'
    shutil.copytree(ROOT / 'base_harness/loop', source)
    binding = candidate_runtime.prepare(source, envs={tid: env}, setups={tid: setup})['identity']
    # The command uses exactly the default shell interface, without modifying Loop.
    code = '''import importlib.util,json,os,sys
from pathlib import Path
import openpyxl
assert openpyxl.__version__ == '3.1.2'
assert importlib.util.find_spec('openai') is None
assert os.statvfs(sys.prefix).f_flag & os.ST_RDONLY
try:
 Path(openpyxl.__file__).write_text('actor cannot replace scorer dependency')
except OSError:
 pass
else:
 raise AssertionError('task artifact writable')
for name in ['golden.xlsx','native-runtime.json','runtime-context.json','check.py','evaluation.py','open_spreadsheet.py','native.py']:
 assert not Path(name).exists(),name
assert not (Path(sys.prefix).parent/'artifact.json').exists()
assert not (Path(PLATFORM)/'driver.py').exists()
assert not (Path(PLATFORM)/'data/datasets/spreadsheetbench/verify/check.py').exists()
assert not Path(GOLDEN_SOURCE).exists()
assert not Path(NATIVE_SOURCE).exists()
w=openpyxl.load_workbook(INPUT)
w.active['A1']=FORMULA
w.save(OUTPUT)
Path('answer.txt').write_text(OUTPUT)
print('ACTOR_EDITING_READY')
'''.replace('PLATFORM', repr(str(ROOT))).replace('GOLDEN_SOURCE', repr(str(golden))).replace('NATIVE_SOURCE', repr(os.environ['HG_SPREADSHEET_NATIVE_MANIFEST'])).replace('INPUT', repr('workbook.xlsx' if name == 'spreadsheetbench' else 'input.xlsx')).replace('FORMULA', repr('=SUM(2,4)' if wrong else '=SUM(2,3)')).replace('OUTPUT', repr(output))
    command = 'python -I -B -c ' + shlex.quote(code)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            assert 'native-runtime.json' not in json.dumps(body['messages'][:2])
            action = {'tool': 'bash', 'command': command} if len(seen) == 1 else {'answer': output}
            data = json.dumps({'id': 'authored', 'object': 'chat.completion', 'created': 0,
                'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(action)}}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ.update(HG_AGENT_BACKEND='openai', HG_AGENT_API_KEY='authored-local-key',
                      HG_AGENT_MODEL='authored-local-model', HG_AGENT_MAX_STEPS='3',
                      HG_AGENT_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
                      HARNESSGRAD_WORK_ROOT=str(case))
    try:
        result = runner.run_one(source, task, setup=setup, verifier=verifier, env=env,
            timeout_s=60, verify_timeout_s=120, harness_runtime_identity=binding,
            dataset_runtime_identity=identity)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    (case / 'result.json').write_text(json.dumps(result, indent=2))
    assert not result.get('invalid'), result
    assert result['exit_code'] == 0, result
    assert len(seen) == 2, seen
    commands = [json.loads(line) for line in result['trace'].splitlines() if 'command' in json.loads(line)]
    assert len(commands) == 1 and commands[0]['exit'] == 0, result['trace']
    assert 'ACTOR_EDITING_READY' in commands[0]['output'], result['trace']
    assert result['verdict']['score'] == (0 if wrong else 1), result['verdict']
    assert result['verdict']['evidence']['measured'] is True
    summary = result['runtime_services']['task_python']
    assert set(summary) == {'dataset', 'runtime_id', 'role', 'readonly'}
    assert summary['role'] == 'actor' and summary['readonly']
    for package in ('openpyxl', 'openai', 'httpx', 'pip'):
        assert importlib.util.find_spec(package) is None, package
    print(json.dumps({'dataset': name, 'score': result['verdict']['score'],
                      'platform_has_openpyxl': False, 'actor_runtime': summary,
                      'verification_context': context['sha256']}), flush=True)


for name in ('spreadsheetbench', 'spreadsheetbench2'):
    for wrong in (False, True):
        run_case(name, wrong)
