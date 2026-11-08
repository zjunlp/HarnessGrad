"""Author-owned CostBench controls; real service/scorer tests are explicit opt-in."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / 'tools/check_costbench_lifecycle.py'


def test_control_help_needs_only_standard_library(tmp_path):
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(CONTROL), '--help'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--runtime-root' in result.stdout and '--bundle' in result.stdout


def test_missing_execution_artifact_fails_read_only(tmp_path):
    control = runpy.run_path(str(CONTROL))
    from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable
    with pytest.raises(DatasetRuntimeUnavailable):
        control['run'](tmp_path / 'absent-runtime', tmp_path / 'absent-bundle', tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
    assert not (tmp_path / 'absent-runtime').exists()


def test_real_author_owned_static_dynamic_lifecycle(tmp_path):
    store = os.environ.get('HG_COSTBENCH_LIFECYCLE_RUNTIME_ROOT')
    bundle = os.environ.get('HG_COSTBENCH_LIFECYCLE_BUNDLE')
    if not store or not bundle:
        pytest.skip('explicit prepared execution artifact and original-pin source bundle required')
    output = tmp_path / 'control'
    result = subprocess.run([sys.executable, '-I', '-B', str(CONTROL),
                             '--runtime-root', store, '--bundle', bundle, '--output', str(output)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr + result.stdout
    receipt = json.loads((output / 'lifecycle-receipt.json').read_text())
    assert receipt['author_owned_only'] and not receipt['released_cases_read']
    assert receipt['model_calls'] == 0 and receipt['dynamic_modes_tested'] == ['cost_change']
    service, verifier = receipt['service_binding'], receipt['verifier_binding']
    assert service['runtime_id'] == verifier['runtime_id']
    assert service['role'] == 'service' and verifier['role'] == 'verifier'
    assert len(receipt['results']) == 6
    for row in receipt['results']:
        expected = 1.0 if row['arm'] == 'oracle' else 0.0
        assert row['verdict']['score'] == expected
        assert row['service_process_exited'] and row['relay_directory_removed']
        assert row['service_receipt']['stopped_cleanly']
        service_observed = row['service_receipt']['python_runtime']
        assert service_observed['python'] == service['python']
        assert service_observed['isolated'] and service_observed['readonly']
        assert all(origin and origin.startswith(service['path'] + '/')
                   for origin in service_observed['package_origins'].values())
        evidence = row['verdict']['evidence']
        observed = evidence['python_runtime']
        assert observed['python'] == verifier['python']
        assert observed['isolated'] and observed['readonly']
        assert all(origin and origin.startswith(verifier['path'] + '/')
                   for origin in observed['package_origins'].values())
        assert evidence['measured']
        if row['mode'] == 'dynamic' and row['arm'] == 'oracle':
            assert row['block_count'] == 1
            assert len(json.loads((output / 'dynamic-oracle/sealed-episode.json').read_text())[
                'payload']['query_result']['groundtruth']['scenarios']) == 2
