"""Differential tests against verbatim CostBench 6424069f scorer functions.

The fixture preserves the complete env/utils/eval.py source. Its function bodies
are executed independently of the runtime package; only its atomic-tool name
provider is shared because this file tests metric semantics, not tool generation.
"""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/costbench'
FIXTURE = ROOT / 'tests/fixtures/costbench_official'
PIN = 'd40872c4bbbcbbe99cb0e64cc011dc2e19a6b1c464b2ab71143709d751303aac'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


scoring = module('costbench_scoring_test', ASSETS / 'lib/scoring.py')


def snapshot(atomic_provider=None):
    result = types.ModuleType('costbench_official_snapshot')
    exec('from typing import *\nfrom pathlib import Path\nfrom difflib import SequenceMatcher\nimport re, json, os, sys, argparse', result.__dict__)
    source = ast.parse((FIXTURE / 'eval.py').read_text())
    functions = ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef)], type_ignores=[])
    exec(compile(functions, str(FIXTURE / 'eval.py'), 'exec'), result.__dict__)
    result.BAN_TOOL_RETURN_SENTENCES = ()  # Static episodes do not contain blocked-tool replies.
    result.get_all_dynamic_atomic_tools = atomic_provider or (lambda _: {})
    return result


@pytest.fixture
def official(monkeypatch):
    dataset_root = Path(os.environ.get('HG_COSTBENCH_DATA', ROOT.parent / 'datasets/costbench'))
    source = dataset_root / 'bundle'
    if not (source / 'env/utils/eval.py').is_file():
        pytest.skip('pinned CostBench source is not prepared')
    monkeypatch.syspath_prepend(str(source))
    # The preparation runtime supplies its pinned pure-Python PyYAML module.
    runtime = Path(os.environ.get('HG_COSTBENCH_RUNTIME', dataset_root / 'runtime'))
    for site in runtime.glob('lib/python*/site-packages'):
        monkeypatch.syspath_prepend(str(site))
    monkeypatch.setenv('COSTBENCH_TRAVEL_CONFIG', str(source / 'env/config/travel_config.yaml'))
    import env.utils.eval as upstream
    assert hashlib.sha256(Path(upstream.__file__).read_bytes()).hexdigest() == PIN
    yield upstream


def query(path=('cheap_a', 'cheap_b'), costs=(18.0, 19.0), *, goal=True, answer='00001'):
    history = [{'role': 'system', 'content': 'cost-aware tools'}, {'role': 'user', 'content': 'task'}]
    for number, (name, cost) in enumerate(zip(path, costs)):
        history += [{'role': 'assistant', 'content': None,
                     'tool_calls': [{'id': str(number), 'name': name, 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': str(number), 'content': f'OK. Cost: {cost}'}]
    if answer is not None:
        history.append({'role': 'assistant', 'content': f'<answer><LocationCandidate{answer}></answer>'})
    return {'query_id': 'fixture-1', 'conversation_history': history, 'is_goal_state': goal,
            'groundtruth': {'scenarios': [{'scenario_index': 0, 'gt_id': '<LocationPreference00001>',
                                          'tools': [{'cheap_a': 18.0}, {'cheap_b': 19.0}]}]},
            'block_stats': {'expected_block_count': 0, 'block_count': 0, 'block_step': []},
            'run_args': {'min_atomic_cost': 15.0, 'max_atomic_cost': 25.0}}


def test_verbatim_official_scorer_pin():
    assert hashlib.sha256((FIXTURE / 'eval.py').read_bytes()).hexdigest() == PIN
    provenance = json.loads((FIXTURE / 'PROVENANCE.json').read_text())
    assert provenance['sha256'] == PIN and provenance['revision'] == scoring.REVISION


@pytest.mark.parametrize('record', [
    query(), query(('expensive_a', 'expensive_b'), (22.0, 24.0)),
    query(('cheap_a', 'cheap_a', 'cheap_b'), (18.0, 18.0, 19.0)),
    query(('cheap_a',), (18.0,), goal=False, answer=None),
    query((), (), goal=False, answer=None), query(answer='99999'),
])
def test_adapter_differential_against_verbatim_scorer(official, record):
    independent = snapshot(official.get_all_dynamic_atomic_tools)
    expected_row = independent.evaluate_single_query(deepcopy(record))
    result = scoring.aggregate([record])
    assert result['per_query'] == [expected_row]
    assert result['official_raw'] == independent.eval([deepcopy(record)])
    assert result['primary_mean'] == float(expected_row['tool_path_exact_match'])


def test_cost_optimization_changes_reward_without_changing_answer(official):
    cheap, expensive = query(), query(('expensive_a', 'expensive_b'), (22.0, 24.0))
    result = scoring.aggregate([cheap, expensive])
    assert result['primary_mean'] == .5
    assert result['metrics']['official_tcr'] == 1
    assert result['metrics']['cost_gap_raw_units'] == 4.5
    assert result['metrics']['official_avg_delta_atomic'] == .225
    assert result['denominators']['cost'] == 2


def test_noop_is_primary_failure_but_not_zero_cost_optimum(official):
    result = scoring.aggregate([query((), (), goal=False, answer=None)])
    assert result['primary_mean'] == 0
    assert result['primary_denominator'] == 1
    assert result['denominators']['path'] == result['denominators']['cost'] == 0
    assert result['metrics']['official_emr'] is None
    assert result['metrics']['cost_gap_raw_units'] is None
    assert result['metrics']['invalid_tool_use_ratio'] is None
    # Retain the upstream empty-denominator value only in explicitly raw evidence.
    assert result['official_raw']['cost_metrics']['avg_agent_cost'] == 0


def test_batch_aggregation_preserves_different_denominators(official):
    result = scoring.aggregate([query(), query((), (), goal=False, answer=None)])
    assert result['primary_mean'] == .5
    assert result['metrics']['official_emr'] == 1
    assert result['metrics']['official_avg_agent_cost'] == 37
    assert result['denominators'] == {'accuracy': 1, 'path': 1, 'cost': 1, 'tool_usage': 2, 'tool_calls': 2}


def test_invalid_calls_are_reported_separately_from_path_cost(official):
    record = query(('invalid', 'cheap_a', 'cheap_b'), (123, 18, 19))
    record['conversation_history'][3]['content'] = '[ERROR] unknown tool Cost: 123'
    result = scoring.aggregate([record])
    assert result['primary_mean'] == 1
    assert result['metrics']['official_avg_agent_cost'] == 37
    assert result['metrics']['invalid_tool_use_ratio'] == pytest.approx(1 / 3)


def test_path_match_does_not_invent_answer_correctness_requirement(official):
    result = scoring.aggregate([query(answer='99999')])
    assert result['primary_mean'] == 1
    assert result['metrics']['official_tcr'] == 0


@pytest.mark.parametrize('mutation', [
    lambda q: q.pop('is_goal_state'),
    lambda q: q['block_stats'].update(expected_block_count=1),
    lambda q: q['groundtruth'].update(scenarios=[]),
    lambda q: q['groundtruth']['scenarios'][0]['tools'].append({'bad': float('nan')}),
])
def test_incomplete_or_dynamic_records_are_not_measured_as_zero(mutation):
    record = query()
    mutation(record)
    with pytest.raises(ValueError):
        scoring.validate_query(record)


def test_upstream_five_digit_answer_parser_does_not_support_released_seven_digit_ids():
    independent = snapshot()
    assert independent.extract_candidate_id('<answer><LocationCandidate1234567></answer>') == '12345'
    assert independent.extract_gt_id_number('<LocationPreference1234567>') == '1234567'


def test_runtime_failure_remains_unmeasured():
    config = {'revision': scoring.REVISION, 'task_id': 'costbench/test/1', 'query': query()}
    payload = {'revision': scoring.REVISION, 'task_id': config['task_id'], 'failure': 'transport_failure'}
    with pytest.raises(RuntimeError, match='transport_failure'):
        scoring.score(config, payload)


def reporting_functions():
    # The manifest checks are pure: isolate them from runtime provisioning imports.
    source = ast.parse((ASSETS / 'report.py').read_text())
    namespace = {'METRIC': scoring.METRIC, 'REVISION': scoring.REVISION}
    functions = ast.Module(body=[node for node in source.body
                                if isinstance(node, ast.FunctionDef) and node.name in ('cell', 'collect')],
                           type_ignores=[])
    exec(compile(functions, str(ASSETS / 'report.py'), 'exec'), namespace)
    return namespace['collect']


def evidence(task='costbench/test/1', trial='trial-1'):
    return {'measured': True, 'metric': scoring.METRIC, 'revision': scoring.REVISION,
            'task_id': task, 'binding': {'task_id': task, 'run_id': 'fixture', 'trial_id': trial},
            'scorer_input': {'task_id': task, 'verified': True},
            'official': {'query_result': query(), 'split': 'test'}}


def test_report_enforces_complete_unique_declared_cells():
    collect = reporting_functions()
    first = evidence()
    second = evidence(trial='trial-2')
    expected = [first['binding'], second['binding']]
    assert len(collect([second, first], expected)) == 2
    with pytest.raises(ValueError, match='missing'):
        collect([first], expected)
    with pytest.raises(ValueError, match='duplicate'):
        collect([first, first], expected)
    with pytest.raises(ValueError, match='unexpected'):
        collect([first, second], [first['binding']])
    with pytest.raises(ValueError, match='duplicates'):
        collect([first], [first['binding'], first['binding']])


def test_report_rejects_unmeasured_or_unverified_records():
    collect = reporting_functions()
    failed = evidence()
    expected = [failed['binding']]
    failed['measured'] = False
    with pytest.raises(ValueError, match='only measured'):
        collect([failed], expected)
    failed['measured'] = True
    failed['scorer_input']['verified'] = False
    with pytest.raises(ValueError, match='only measured'):
        collect([failed], expected)


@pytest.mark.parametrize('content', [
    '<answer><LocationCandidate1234567></answer>',
    '<answer><DiningCandidate{1234567}></answer>',
    '<answer><AccommodationCandidate{Candidate_ID:1234567}></answer>',
    '<answer>TransportationCandidate 1234567</answer>',
    '<think>ignore 8888888</think><answer><ShoppingCandidate1234567></answer>',
])
def test_train_compat_parser_handles_released_seven_digit_identifiers(content):
    assert scoring.extract_train_candidate_id(content) == '1234567'


@pytest.mark.parametrize('content', [
    '<answer><LocationCandidate12345></answer>',
    '<answer><LocationCandidate12345678></answer>',
    '<LocationCandidate1234567>',
])
def test_train_compat_parser_does_not_truncate_or_accept_missing_answer_tags(content):
    assert scoring.extract_train_candidate_id(content) is None


def test_train_answer_repair_is_named_and_does_not_change_official_metrics(official):
    record = query(answer='1234567')
    record['groundtruth']['scenarios'][0]['gt_id'] = '<LocationPreference1234567>'
    train = scoring.aggregate([record], splits=['train'])
    test = scoring.aggregate([record], splits=['test'])
    assert train['primary_mean'] == test['primary_mean'] == 1
    assert train['official_raw'] == test['official_raw']
    assert train['metrics']['official_tcr'] == test['metrics']['official_tcr'] == 0
    assert train['metrics']['train_compat_tcr'] == 1
    assert test['metrics']['train_compat_tcr'] is None
    assert train['train_compatibility']['metric'] == scoring.TRAIN_COMPAT_METRIC
    assert train['train_compatibility']['denominator'] == 1


def test_train_compat_tcr_keeps_official_accuracy_eligibility(official):
    record = query(answer='1234567')
    record['groundtruth']['scenarios'][0]['gt_id'] = '<LocationPreference1234567>'
    result = scoring.aggregate([record, query((), (), goal=False, answer=None)], splits=['train', 'train'])
    assert result['metrics']['train_compat_tcr'] == 1
    assert result['train_compatibility']['denominator'] == 1


def test_episode_ground_truth_cannot_replace_private_reference(official):
    record = query()
    config = {'revision': scoring.REVISION, 'task_id': 'costbench/test/1', 'split': 'test', 'query': deepcopy(record)}
    record['groundtruth']['scenarios'][0]['tools'] = [{'cheating': 1.0}]
    payload = {'revision': scoring.REVISION, 'task_id': config['task_id'], 'failure': None,
               'query_result': record}
    with pytest.raises(ValueError, match='ground truth'):
        scoring.score(config, payload)
