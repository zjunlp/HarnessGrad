"""A real DGM/driver/native-scorer recipe cycle with a scripted local CLI Meta-Harness.

All rows are author-written BBEH-shaped instrument tasks. The solver is mock;
the CLI Meta-Harness is scripted; no learned model, paid API or BBEH capability
result is claimed. The actual DGM policy proposes the source edit, and only the
platform executes tasks and computes study/held-out scores.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

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
    editor_dir = tmp_path / 'local-editor'
    editor_dir.mkdir()
    launcher = editor_dir / 'editor'
    (editor_dir / 'proposal.py').write_text(proposal)
    launcher.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('release-editor 1.0.0')
else:
    assert len(sys.argv) == 2
    Path('agent.py').write_bytes(Path(__file__).with_name('proposal.py').read_bytes())
    print('release-cli-call ' + json.dumps({'model': os.environ['HG_IMPROVER_MODEL'], 'prompt': sys.argv[1]}))
    print('Instrument control: follow the public marker in each task.')
''')
    launcher.chmod(0o755)
    registry = tmp_path / 'improvers.json'
    registry.write_text(json.dumps({'default': 'release-editor', 'improvers': {'release-editor': {
        'name': 'release-editor', 'kind': 'cli', 'argv_style': 'dsh', 'launcher': str(launcher),
        'model': 'local-scripted-editor', 'provider': 'authored', 'profile': 'headless',
        'api_key_env': 'RELEASE_EDITOR_KEY',
    }}}))
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(('HG_METHOD_', 'HG_AGENT_', 'HG_IMPROVER', 'HARNESSGRAD_'))}
    environment.update(PYTHONDONTWRITEBYTECODE='1', HG_DATA_BBEH=str(data),
        HG_AGENT_BACKEND='mock', HG_AGENT_MODEL='mock', HG_AGENT_MAX_STEPS='1',
        HG_IMPROVER_CONFIG=str(registry), HG_REVIEWED_ENVIRONMENT_ONLY='1',
        CODEX_HOME=str(tmp_path / 'unused-codex-home'), DSH_HOME=str(tmp_path / 'unused-dsh-home'),
        RELEASE_EDITOR_KEY='private-local-control-key',
        HG_SLOT_GUARD_TIMEOUT='10', HG_SLOT_MIN_FREE_GB='1')
    split = tmp_path / 'bbeh-split.json'
    runs, work = tmp_path / 'runs', tmp_path / 'work'

    def run(*args):
        return subprocess.run([sys.executable, '-m', 'harnessgrad.cli', *args], cwd=ROOT,
            env=environment, capture_output=True, text=True, errors='replace', timeout=180)

    frozen = run('split', 'create', '--dataset', 'bbeh', '--source', 'official-eval',
                 '--train-ratio', '0.2', '--seed', '42', '--output', str(split))
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    common = ['--set', f'split={split}', '--set', f'runs_root={runs}', '--set',
              f'work_root={work}', '--improver', 'release-editor']
    study = run('run', '--config', 'experiments/rsi-bbeh.json', *common)
    assert study.returncode == 0, study.stdout + study.stderr
    train_root = runs / 'bbeh-dgm-study'
    meta = json.loads((train_root / 'run_meta.json').read_text())
    train_points = [json.loads(line) for line in (train_root / 'curve.jsonl').read_text().splitlines()]
    assert [point['score'] for point in train_points] == [0.0, 1.0], study.stdout + study.stderr
    assert meta['completion']['status'] == 'complete'
    assert meta['completion']['completed_rounds'] == meta['completion']['requested_rounds'] == 1
    assert 'methods/dgm/run.py' in meta['method_entrypoint']
    def editor_calls():
        calls = []
        for archive in runs.glob('*/method_logs/round-*/*-full.log'):
            for line in archive.read_text().splitlines():
                if line.startswith('release-cli-call '):
                    calls.append(json.loads(line.removeprefix('release-cli-call ')))
        return calls

    calls = editor_calls()
    assert len(calls) == 1 and calls[0]['model'] == 'local-scripted-editor'
    assert train_points[1]['identity']['improver']['name'] == 'release-editor'
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
    prompt = calls[0]['prompt']
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
    assert editor_calls() == calls  # The frozen checkpoint evaluation performs no edit.
    assert not (exam_root / 'method_channel').exists()
    all_public = (train_root / 'curve.jsonl').read_text() + (exam_root / 'curve.jsonl').read_text()
    assert 'private-local-control-key' not in all_public
