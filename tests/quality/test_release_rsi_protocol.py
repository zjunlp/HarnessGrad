"""A real DGM/driver/native-scorer recipe cycle with scripted local transport.

All rows are author-written BBEH-shaped instrument tasks. The solver is mock;
the HTTP optimizer is scripted; no learned model, paid API or BBEH capability
result is claimed. The actual DGM policy proposes the source edit, and only the
platform executes tasks and computes study/held-out scores.
"""
from __future__ import annotations

import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds.native_qa.prepare import prepare


def test_real_dgm_proposal_platform_measurement_and_frozen_checkpoint_eval(tmp_path):
    rows = [{'task': 'release-protocol-control',
             'input': f'Instrument task {index}. Return the public marker: TOKEN_{index}.',
             'target': f'TOKEN_{index}'} for index in range(30)]
    native_file = tmp_path / 'authored-bbeh-controls.json'
    native_file.write_text(json.dumps(rows))
    data = tmp_path / 'native-data'
    prepare('bbeh', data, ['test=' + str(native_file)], duplicate_questions='exclude')

    # The proposal changes the real loop's mock completion function only. It
    # derives each response from that task's public question, not a gold table,
    # and supplies neither an evaluator nor a score in the method response.
    original = (ROOT / 'base_harness/loop/agent.py').read_text()
    tree = ast.parse(original)
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == '_call_mock')
    lines = original.splitlines(keepends=True)
    replacement = '''def _call_mock(messages: list[dict]) -> str:
    """Scripted instrument control: follow an explicitly stated public marker."""
    import re
    text = messages[1]["content"] if len(messages) > 1 else ""
    match = re.search(r"public marker: (TOKEN_[0-9]+)", text)
    return json.dumps({"answer": match.group(1) if match else "NO_PUBLIC_MARKER"})
'''
    proposal = ''.join(lines[:function.lineno-1]) + replacement + ''.join(lines[function.end_lineno:])
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append({'path': self.path, 'request': request})
            edit = {'files': [{'path': 'agent.py', 'content': proposal}],
                    'hypothesis': 'Instrument control: follow the public marker in each task.'}
            response = {'id': 'scripted-release-control', 'object': 'chat.completion',
                'model': 'local-scripted-editor', 'choices': [{'index': 0, 'message': {
                    'role': 'assistant', 'content': json.dumps(edit)}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}}
            raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(('HG_METHOD_', 'HG_AGENT_', 'HG_IMPROVER_', 'HARNESSGRAD_'))}
    environment.update(PYTHONDONTWRITEBYTECODE='1', HG_DATA_BBEH=str(data),
        HG_AGENT_BACKEND='mock', HG_AGENT_MODEL='mock', HG_AGENT_MAX_STEPS='1',
        HG_METHOD_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
        HG_METHOD_MODEL='local-scripted-editor', HG_METHOD_API_KEY='private-local-control-key',
        HG_SLOT_GUARD_TIMEOUT='10', HG_SLOT_MIN_FREE_GB='1')
    split = tmp_path / 'bbeh-split.json'
    runs, work = tmp_path / 'runs', tmp_path / 'work'

    def run(*args):
        return subprocess.run([sys.executable, '-m', 'harnessgrad.cli', *args], cwd=ROOT,
            env=environment, capture_output=True, text=True, errors='replace', timeout=180)

    try:
        frozen = run('split', 'create', '--dataset', 'bbeh', '--source', 'official-eval',
                     '--train-ratio', '0.2', '--seed', '42', '--output', str(split))
        assert frozen.returncode == 0, frozen.stdout + frozen.stderr
        common = ['--set', f'split={split}', '--set', f'runs_root={runs}', '--set',
                  f'work_root={work}', '--improver', 'none']
        study = run('run', '--config', 'experiments/rsi-bbeh.json', *common)
        assert study.returncode == 0, study.stdout + study.stderr
        train_root = runs / 'bbeh-dgm-study'
        meta = json.loads((train_root / 'run_meta.json').read_text())
        train_points = [json.loads(line) for line in (train_root / 'curve.jsonl').read_text().splitlines()]
        assert [point['score'] for point in train_points] == [0.0, 1.0], study.stdout + study.stderr
        assert meta['completion']['status'] == 'complete'
        assert meta['completion']['completed_rounds'] == meta['completion']['requested_rounds'] == 1
        assert 'methods/dgm/run.py' in meta['method_entrypoint']
        assert len(calls) == 1 and calls[0]['path'] == '/v1/chat/completions'
        assert calls[0]['request']['model'] == 'local-scripted-editor'
        assert train_points[-1]['measured_by_platform'] is True
        assert train_points[-1]['n_scored'] == 5
        assert set(train_points[-1]['per_task'].values()) == {1.0}
        assert train_points[-1]['method_reported']['dgm_parent'] == 0
        assert (train_root / 'state_after_round1/agent.py').read_text() == proposal
        verdict_files = list((train_root / 'verdicts/round-1').rglob('*.json'))
        assert len(verdict_files) == 5
        for path in verdict_files:
            verdict = json.loads(path.read_text())
            evidence = verdict['evidence']
            assert verdict['kind'] == 'command' and evidence['measured'] is True
            assert evidence['scorer_input']['verified'] is True
            assert evidence['official']['metric_identity'] == 'bbeh-per-answer-correctness-80d12ca9'

        manifest = json.loads(split.read_text())
        study_ids, eval_ids = set(manifest['split']['train']), set(manifest['split']['eval'])
        assert study_ids.isdisjoint(eval_ids)
        channel = sorted((train_root / 'method_channel').glob('round*'))[-1]
        staged = json.loads((channel / 'round.json').read_text())
        assert set(staged['per_task']) <= study_ids and 'eval' not in staged['split']
        assert 'manifest' not in staged.get('split_protocol', {})
        for folder in ('tasks', 'traces'):
            # BBEH IDs contain namespace components, so inspect serialized JSON
            # too instead of relying on basename-only comparisons.
            for path in (channel / folder).rglob('*.json*'):
                text = path.read_text()
                assert not any(tid in text for tid in eval_ids)
        prompt = json.dumps(calls[0]['request'])
        assert not any(tid in prompt for tid in eval_ids)

        exam = run('run', '--config', 'experiments/rsi-bbeh-eval.json', *common)
        assert exam.returncode == 0, exam.stdout + exam.stderr
        exam_root = runs / 'bbeh-dgm-heldout'
        exam_points = [json.loads(line) for line in (exam_root / 'curve.jsonl').read_text().splitlines()]
        assert len(exam_points) == 1
        result = exam_points[0]
        assert result['score'] == 1.0 and result['measured_by_platform'] is True
        assert result['score_kind'] == 'exam' and result['n_scored'] == 5
        assert set(result['per_task']) <= eval_ids
        assert not (set(result['per_task']) & study_ids)
        assert result['evaluated_from']['round'] == 1
        assert result['evaluated_from']['harness_sha'] == train_points[-1]['identity']['harness_sha']
        assert result['split_protocol']['kind'] == 'custom-heldout'
        assert result['split_protocol']['official_score_comparable'] is False
        assert len(calls) == 1  # Checkpoint evaluation invokes no optimizer/evaluator through DGM.
        assert not (exam_root / 'method_channel').exists()
        all_public = (train_root / 'curve.jsonl').read_text() + (exam_root / 'curve.jsonl').read_text()
        assert 'private-local-control-key' not in all_public
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
