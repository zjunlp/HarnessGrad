"""Real official τ³ scoring and archive identity through task_sensitivity."""
import copy
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from data.datasets import tau3_bench as dataset
from test_tau3_bench_dataset import control_module
from tools import task_sensitivity as ts


def test_sensitivity_runs_all_tau3_trials_for_identical_harnesses(tmp_path, monkeypatch):
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    source = tmp_path/'source'
    shutil.copytree(dataset.ASSETS/'oracle_harness', source)
    manifest = json.loads((source/'harness.json').read_text())
    manifest['runtime_paths'] = list(dict.fromkeys([
        sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
    (source/'harness.json').write_text(json.dumps(manifest))
    tid = 'tau3_bench/airline/8'
    task = dataset.control_task(tid)
    row = dataset.roster()[tid]
    private = json.loads((dataset.DATASET_ROOT/row['path']/'private.json').read_text())
    run = tmp_path/'run'
    run.mkdir()
    args = SimpleNamespace(sandbox=True, seed='control', trials=3, jobs=1, cache=None)
    with control_module().Endpoint() as endpoint:
        setup = copy.deepcopy(dataset.SETUP[tid])
        setup['files'].append({'path': '.oracle/actions.json',
                              'content': json.dumps({'actions': private['task']['evaluation_criteria']['actions']})})
        verifier = dataset.VERIFY[tid]
        scores, invalid = ts._measure([('control', source), ('same-content', source)],
            [task], {}, {tid: setup}, {tid: verifier}, {tid: dataset.ENV[tid]},
            args, work, run, {})
    assert scores == {tid: {'control': 1, 'same-content': 1}} and not invalid
    assert endpoint.requests
    records = [json.loads(p.read_text()) for p in (run/'trial-evidence').rglob('*.json')]
    assert len(records) == 6
    assert len({r['binding']['harness_sha'] for r in records}) == 1
    assert len({r['binding']['trial_id'] for r in records}) == 6
    for record in records:
        assert record['outcome']['status'] == 'measured' and record['outcome']['score'] == 1
        result = record['result']
        assert result['verdict']['score'] == 1
        assert result['trial_evidence_output']['trace_status'] == 'captured'
        saved = Path(result['runtime_services']['tool_relay']['recording']['archive'])
        assert saved.is_file()
        episode = json.loads(saved.read_text())
        assert episode['binding']['trial_id'] == record['binding']['trial_id']
        assert episode['binding']['seed'] == record['binding']['seed']
    for round_no in (0, 1):
        assert {r['binding']['seed'] for r in records if r['binding']['round'] == round_no} == {
            str(seed) for seed in dataset.TRIAL_PROTOCOL['seeds']}
    reports = [json.loads(p.read_text()) for p in (run/'protocols').rglob('repeated-trials.json')]
    assert len(reports) == 2 and all(r['complete'] for r in reports)
    assert all(all(score == 1 for score in r['metrics'].values()) for r in reports)

