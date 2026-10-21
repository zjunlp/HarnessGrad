"""Released Click controls through candidate peers and the real private scorer."""
import importlib.util

import pytest

from data.datasets import cooperbench as dataset

TASK = 'cooperbench/pallets_click_task/task2956/f1-2'


@pytest.mark.parametrize('oracle', [True, False])
def test_released_pair_observed_oracle_and_noop(tmp_path, monkeypatch, oracle):
    try:
        row = dataset.roster()[TASK]
        dataset.runtime(row)
    except (OSError, KeyError):
        pytest.skip('prepare the released Click task2956 runtime first')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    spec = importlib.util.spec_from_file_location('cooperbench_observed_control', dataset.ASSETS / 'control.py')
    control = importlib.util.module_from_spec(spec); spec.loader.exec_module(control)
    result = control.run_control(TASK, oracle, True, tmp_path / ('oracle' if oracle else 'noop'))
    assert result.get('verdict', {}).get('score') == int(oracle), result
    assert result['verdict']['container_runtime']['admission']['status'] == 'passed'
    assert result['verdict']['container_runtime']['cleanup']['complete']
    assert len(result['worker_group']['events']) == 2
    for worker in result['worker_group']['workers'].values():
        receipt = worker['runtime_services']['container']
        assert receipt['admission']['status'] == 'passed'
        assert receipt['cleanup']['complete']
        assert receipt['actor_identity']['uid'] == 65534
