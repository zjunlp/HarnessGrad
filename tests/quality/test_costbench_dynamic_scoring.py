"""Dynamic metric differential tests against the pinned upstream evaluator."""
import ast
from copy import deepcopy
from pathlib import Path
import sys
import types

import pytest
from test_costbench_official_scorer import archive

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/costbench'
sys.path.insert(0, str(ASSETS / 'lib'))
import dynamic_scoring as scoring
import scoring as static_scoring


@pytest.fixture
def official(monkeypatch):
    result = types.ModuleType('env.utils.eval')
    exec('from typing import *\nfrom pathlib import Path\nfrom difflib import SequenceMatcher\nimport re, json, os, sys, argparse', result.__dict__)
    filename = ROOT / 'tests/fixtures/costbench_official/eval.py'
    tree = ast.parse(filename.read_text())
    body = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)], type_ignores=[])
    exec(compile(body, str(filename), 'exec'), result.__dict__)
    result.BAN_TOOL_RETURN_SENTENCES = ('temporarily unavailable',)
    result.get_all_dynamic_atomic_tools = lambda _: {}
    monkeypatch.setitem(sys.modules, 'env.utils.eval', result)
    return result


def query(mode='cost_change', *, answer='00002', initial_answer='00001', complete=True):
    parameters = {'fixture_parameter': 1}
    event = {'block_index': 0, 'block_type': mode, 'trigger_step': 1, 'parameters': parameters}
    scenarios = [{'scenario_index': 0, 'gt_id': f'<LocationPreference{initial_answer}>',
                  'tools': [{'before': 10.0}, {'obsolete': 20.0}], 'path_length': 2, 'total_cost': 30.0}]
    history = [{'role': 'system', 'content': 'dynamic cost-aware tools'}, {'role': 'user', 'content': 'task'}]
    if complete:
        scenarios.append({'scenario_index': 1, 'gt_id': f'<LocationPreference{answer}>',
                          'tools': [{'after': 30.0}], 'path_length': 1, 'total_cost': 30.0,
                          'block_info': {'type': mode, 'trigger_step': 1, 'parameters': parameters}})
        for index, (name, cost) in enumerate((('before', 10.0), ('after', 30.0))):
            history += [{'role': 'assistant', 'content': None,
                         'tool_calls': [{'id': str(index), 'name': name, 'arguments': '{}'}]},
                        {'role': 'tool', 'tool_call_id': str(index), 'content': f'OK Cost: {cost}'}]
        history.append({'role': 'assistant', 'content': f'<answer><LocationCandidate{answer}></answer>'})
    return {'query_id': '<Query00001>', 'task': 'location', 'goal_type': 'TravelLocation',
            'preferences': {'category': 'city'}, 'TimeInfo': '<TimeInfo00001>',
            'conversation_history': history, 'is_goal_state': complete,
            'groundtruth': {'scenarios': scenarios},
            'blocking_plan': [{'type': mode, 'parameters': parameters}],
            'block_stats': {'expected_block_count': 1, 'block_count_agent': int(complete),
                            'block_count': int(complete), 'block_count_gt': int(complete),
                            'block_step': [1], 'block_type': [mode], 'metadata': [event]},
            'run_args': {'use_blocker': True, 'block_mode': mode, 'block_num': 1,
                         'refinement_level': 2, 'min_atomic_cost': 15, 'max_atomic_cost': 25}}


def bound(record, split='test'):
    mode = record['run_args']['block_mode']
    initial = deepcopy(record)
    initial['groundtruth']['scenarios'] = initial['groundtruth']['scenarios'][:1]
    config = {'revision': scoring.REVISION, 'task_id': f'costbench_dynamic/{mode}/{split}/Query00001',
              'split': split, 'query': initial, 'protocol': deepcopy(record['run_args'])}
    payload = {'revision': scoring.REVISION, 'task_id': config['task_id'], 'query_result': record}
    return config, payload


@pytest.mark.parametrize('mode', scoring.MODES)
def test_dynamic_segmented_path_and_cost_match_verbatim_upstream(official, mode):
    record = query(mode)
    result = scoring.aggregate([record], mode=mode)
    assert result['metric'] == f'costbench.dynamic.sequence5.{mode}.block1.per_query_path_exact_match.v1'
    assert result['per_query'] == [official.evaluate_single_query(deepcopy(record))]
    assert result['official_raw'] == official.eval([deepcopy(record)])
    assert result['primary_mean'] == 1
    assert result['per_query'][0]['gt_tool_path'] == ['before', 'after']
    assert result['metrics']['official_avg_gt_cost'] == 40
    assert result['metrics']['official_avg_agent_cost'] == 40


@pytest.mark.parametrize('mode', scoring.MODES)
def test_early_stop_without_event_is_measured_zero_not_scorer_failure(official, mode):
    record = query(mode, complete=False)
    config, payload = bound(record)
    result = scoring.score(config, payload)
    assert result['reward'] == 0
    report = result['official']
    assert report['primary_denominator'] == 1
    assert report['event_incomplete_count'] == 1
    assert report['per_query'] == [official.evaluate_single_query(deepcopy(record))]
    assert report['official_raw'] == official.eval([deepcopy(record)])
    assert report['denominators'] == {'accuracy': 0, 'path': 0, 'cost': 0, 'tool_usage': 0, 'tool_calls': 0}
    assert report['metrics']['official_emr'] is None
    assert report['metrics']['official_avg_agent_cost'] is None


def test_batch_primary_includes_event_incomplete_episodes(official):
    result = scoring.aggregate([query(), query(complete=False)], mode='cost_change')
    assert result['primary_mean'] == .5
    assert result['primary_denominator'] == 2
    assert result['metrics']['official_emr'] == 1
    assert result['denominators']['path'] == result['denominators']['cost'] == 1


def test_ineffective_last_turn_event_can_leave_recomputed_scenario(official):
    record = query('preference_change')
    record['block_stats'].update(block_count_agent=0, block_count_gt=0, block_count=0)
    result = scoring.aggregate([record], mode='preference_change')
    assert result['primary_mean'] == 0
    assert result['event_incomplete_count'] == 1


def test_banned_tool_response_counts_as_valid_charged_step(official):
    record = query('ban_tool')
    record['conversation_history'][3]['content'] = '[ERROR] temporarily unavailable Cost: 10'
    result = scoring.aggregate([record], mode='ban_tool')
    assert result['primary_mean'] == 1
    assert result['per_query'][0]['valid_tool_calls'] == 2
    assert result['metrics']['official_avg_agent_cost'] == 40
    record['duplicate_banned_tool_reused'] = True
    assert scoring.aggregate([record], mode='ban_tool')['primary_mean'] == 0


def test_train_answer_uses_intervened_scenario_and_retains_official_raw(official):
    record = query('preference_change', answer='0000002', initial_answer='0000001')
    report = scoring.aggregate([record], mode='preference_change', splits=['train'])
    assert report['metrics']['official_tcr'] == 0
    assert report['metrics']['train_compat_tcr'] == 1
    assert report['train_compatibility']['per_query'][0]['scenario_index'] == 1
    record['conversation_history'][-1]['content'] = '<answer><LocationCandidate0000001></answer>'
    assert scoring.aggregate([record], mode='preference_change', splits=['train'])['metrics']['train_compat_tcr'] == 0


@pytest.mark.parametrize('mutation', [
    lambda q: q['block_stats'].update(expected_block_count=2),
    lambda q: q['block_stats'].update(block_count_agent=2),
    lambda q: q['block_stats'].update(block_step=[None]),
    lambda q: q['block_stats']['metadata'][0].update(trigger_step=9),
    lambda q: q['blocking_plan'][0].update(type='ban_tool'),
    lambda q: q['groundtruth']['scenarios'][1].update(tools=[]),
    lambda q: q['groundtruth']['scenarios'][1].update(gt_id=''),
    lambda q: q['groundtruth']['scenarios'][1]['tools'].append({'bad': float('nan')}),
    lambda q: q['run_args'].update(use_blocker=False),
    lambda q: q['run_args'].update(refinement_level=5),
    lambda q: q['block_stats'].update(block_count_agent=True),
])
def test_malformed_trusted_reference_is_not_silently_a_task_zero(mutation):
    record = query()
    mutation(record)
    with pytest.raises(ValueError):
        scoring.validate_query(record, 'cost_change')


def test_private_initial_reference_and_plan_cannot_be_replaced(official):
    record = query()
    config, payload = bound(record)
    payload['query_result']['groundtruth']['scenarios'][0]['tools'][0]['before'] = 1
    with pytest.raises(ValueError, match='initial ground truth'):
        scoring.score(config, payload)
    record = query()
    config, payload = bound(record)
    # Mutate all mutually consistent public episode metadata; private plan still
    # prevents a forged intervention being accepted as the configured one.
    payload['query_result']['blocking_plan'][0]['parameters']['fixture_parameter'] = 2
    with pytest.raises(ValueError, match='blocking plan differs'):
        scoring.score(config, payload)


def test_dynamic_runtime_failure_is_unmeasured():
    config, payload = bound(query())
    payload['failure'] = 'private lookup integrity error'
    with pytest.raises(RuntimeError, match='private lookup'):
        scoring.score(config, payload)


def reporting_functions():
    tree = ast.parse((ASSETS / 'report.py').read_text())
    namespace = {'METRIC': static_scoring.METRIC, 'REVISION': scoring.REVISION, 'dynamic_metric': scoring.metric}
    functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name in ('cell', 'archive_evidence', 'collect')], type_ignores=[])
    exec(compile(functions, str(ASSETS / 'report.py'), 'exec'), namespace)
    return namespace['collect']


def evidence(mode='cost_change'):
    tid = f'costbench_dynamic/{mode}/test/Query00001'
    return {'measured': True, 'metric': scoring.metric(mode), 'revision': scoring.REVISION,
            'task_id': tid, 'binding': {'task_id': tid, 'run_id': 'fixture', 'trial_id': 'trial-1', 'seed': '42'},
            'scorer_input': {'task_id': tid, 'verified': True},
            'official': {'query_result': query(mode), 'split': 'test', 'mode': mode, 'reward': 1}}


def test_dynamic_report_requires_explicit_single_matching_mode():
    collect = reporting_functions()
    first, second = archive(evidence()), archive(evidence('ban_tool'))
    assert len(collect([first], [first['archive']['binding']], mode='cost_change')) == 1
    with pytest.raises(ValueError, match='only measured'):
        collect([first], [first['archive']['binding']])
    with pytest.raises(ValueError, match='only measured'):
        collect([first, second], [first['archive']['binding'], second['archive']['binding']], mode='cost_change')
    with pytest.raises(ValueError, match='missing'):
        collect([], [first['archive']['binding']], mode='cost_change')
    with pytest.raises(ValueError, match='duplicate'):
        collect([first, first], [first['archive']['binding']], mode='cost_change')
