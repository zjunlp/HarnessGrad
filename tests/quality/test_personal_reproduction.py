"""C1 baseline evidence and C2 same-scenario comparison stay offline."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from harnessgrad.entrypoints.personal import main
from harnessgrad.personal.reproduction import ReproductionScenario, compare_scenario, run_scenario

FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'personal_evolution_python.json'


def scenario() -> ReproductionScenario:
    return ReproductionScenario.from_dict(json.loads(FIXTURE.read_text(encoding='utf-8')))


def test_c1_records_real_baseline_exit_logs_and_preserves_selected_sources(tmp_path):
    record = run_scenario(scenario(), tmp_path, phase='baseline')
    assert record.phase == 'baseline' and record.exit_code != 0 and not record.timed_out
    assert 'FAILED' in record.stderr and record.stdout == ''
    assert record.source_unchanged and not record.output_truncated
    saved = json.loads((tmp_path / 'evolution-runs' / record.run_ref / 'run-record.json').read_text())
    assert saved['run_ref'] == record.run_ref and saved['files_sha256'] == record.files_sha256


def test_c2_compares_the_same_command_and_only_then_marks_apply_eligible(tmp_path):
    report = compare_scenario(scenario(), tmp_path)
    assert report.comparable and report.status == 'retest_passed'
    assert report.reason_code is None and report.apply_eligible
    assert report.baseline.exit_code != 0 and report.candidate.exit_code == 0
    assert report.baseline.command == report.candidate.command
    saved = tmp_path / 'evolution-reports' / f'{report.comparison_ref}.json'
    assert json.loads(saved.read_text())['status'] == 'retest_passed'


def test_passing_baseline_is_not_reproduced_and_cannot_be_adopted(tmp_path):
    value = scenario()
    changed = replace(value, baseline_files=value.candidate_files)
    report = compare_scenario(changed, tmp_path)
    assert report.comparable and report.status == 'not_reproduced'
    assert report.reason_code == 'baseline_passed' and not report.apply_eligible


def test_timeout_and_source_mutation_are_not_comparable(tmp_path):
    timeout = replace(scenario(), command=('{python}', '-c', 'import time; time.sleep(1)'), timeout_seconds=.05)
    report = compare_scenario(timeout, tmp_path / 'timeout')
    assert not report.comparable and report.status == 'not_comparable'
    assert report.reason_code == 'execution_timeout' and not report.apply_eligible

    mutate = replace(scenario(), command=('{python}', '-c', "open('calculator.py','w').write('changed')"))
    report = compare_scenario(mutate, tmp_path / 'mutate')
    assert not report.comparable and report.reason_code == 'scenario_mismatch'

    unavailable = replace(scenario(), command=('definitely-missing-evolution-runner',))
    report = compare_scenario(unavailable, tmp_path / 'unavailable')
    assert not report.comparable and report.reason_code == 'runner_unavailable'


@pytest.mark.parametrize('change', [
    {'baseline_files': {'../escape.py': 'x'}},
    {'candidate_files': {}},
    {'command': ['sh', '-c', 'echo unsafe']},
    {'timeout_seconds': True},
])
def test_fixture_rejects_unsafe_or_ambiguous_inputs(change):
    value = json.loads(FIXTURE.read_text(encoding='utf-8'))
    value.update(change)
    with pytest.raises(ValueError):
        ReproductionScenario.from_dict(value)


@pytest.mark.parametrize('phase,key', [('baseline', 'run_ref'), ('review', 'comparison_ref')])
def test_cli_emits_a_json_receipt_and_keeps_state_outside_checkout(tmp_path, capsys, phase, key):
    state = tmp_path / 'state'
    code = main(['evolution-check', '--state-root', str(state), '--fixture', str(FIXTURE), '--phase', phase])
    assert code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt[key] and receipt['scenario_id'] == 'python-related-tests'


def test_cli_argument_errors_are_two_and_checkout_state_is_refused(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['evolution-check'])
    assert exc.value.code == 2
    checkout_state = Path(__file__).parents[2] / '.forbidden-personal-state'
    assert main(['evolution-check', '--state-root', str(checkout_state),
                 '--fixture', str(FIXTURE), '--phase', 'baseline']) == 1
    assert 'operation failed' in capsys.readouterr().err
