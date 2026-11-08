"""Strict display-only shared-H0 projection boundary; no model or driver launch."""
from copy import deepcopy
import json

import pytest

from tools.run_summary import _shared_h0_current_cost, cost_totals, duration_seconds


def point():
    return {'round': 0, 'shared_from': {'run_id': 'authored-donor', 'path': '/authored/donor',
        'h0_sha': 'a' * 40, 'measured_with': {'jobs': 1, 'gpu_budget': None}},
        'cost': {'harness_model_calls': 5, 'harness_tokens': 100},
        'cumulative': {'evaluation_trials': 1, 'wall_clock_s': 99},
        'current_run_cost': {'version': 1, 'source': 'shared_h0_no_execution',
            'evaluation_trials': 0, 'harness_tokens': 0, 'harness_model_calls': 0,
            'env_generation_tokens': 0, 'wall_clock_s': 1.25,
            'harness_preparation_wall_clock_s': .25}}


def test_valid_h0_projection_preserves_wall_prep_and_reference():
    value = point()
    before = deepcopy(value)
    assert _shared_h0_current_cost(value) == value['current_run_cost']
    totals = cost_totals([value], 'train', [])
    assert totals['calls'] == totals['tokens'] == 0
    assert totals['cost_provenance']['historical_references'][0]['cost'] == before['cost']
    assert duration_seconds([value], {}, []) == (1.25, 'current_run_cost.wall_clock_s')
    assert value == before


@pytest.mark.parametrize('scenario', [
    'round-one', 'missing-round', 'boolean-round', 'missing-reference',
    'missing-donor', 'missing-path', 'missing-h0-source', 'empty-donor',
    'missing-measured-with', 'bad-measured-with', 'missing-measured-jobs', 'boolean-measured-jobs',
    'missing-projection', 'bad-projection', 'boolean-version', 'wrong-version', 'wrong-source',
    'extra-method-cost', 'extra-unknown-field',
])
def test_invalid_context_or_projection_is_unknown_without_mutating_history(scenario):
    value = point()
    if scenario == 'round-one': value['round'] = 1
    elif scenario == 'missing-round': value.pop('round')
    elif scenario == 'boolean-round': value['round'] = False
    elif scenario == 'missing-reference': value.pop('shared_from')
    elif scenario == 'missing-donor': value['shared_from'].pop('run_id')
    elif scenario == 'missing-path': value['shared_from'].pop('path')
    elif scenario == 'missing-h0-source': value['shared_from'].pop('h0_sha')
    elif scenario == 'empty-donor': value['shared_from']['run_id'] = ' '
    elif scenario == 'missing-measured-with': value['shared_from'].pop('measured_with')
    elif scenario == 'bad-measured-with': value['shared_from']['measured_with'] = []
    elif scenario == 'missing-measured-jobs': value['shared_from']['measured_with'].pop('jobs')
    elif scenario == 'boolean-measured-jobs': value['shared_from']['measured_with']['jobs'] = False
    elif scenario == 'missing-projection': value.pop('current_run_cost')
    elif scenario == 'bad-projection': value['current_run_cost'] = []
    elif scenario == 'boolean-version': value['current_run_cost']['version'] = True
    elif scenario == 'wrong-version': value['current_run_cost']['version'] = 2
    elif scenario == 'wrong-source': value['current_run_cost']['source'] = 'authored-unknown-source'
    elif scenario == 'extra-method-cost':
        value['current_run_cost'].update(method_model_calls=9, method_generation_tokens=90)
    elif scenario == 'extra-unknown-field': value['current_run_cost']['undeclared_cost'] = 0
    assert_unknown(value)


@pytest.mark.parametrize('reference', ['malformed', '', [], {}, None, False, 0])
def test_bad_reference_shape_never_crashes_or_falls_back_to_history(reference):
    value = point()
    value['shared_from'] = reference
    assert_unknown(value)


@pytest.mark.parametrize('field', ['evaluation_trials', 'harness_tokens', 'harness_model_calls', 'env_generation_tokens'])
@pytest.mark.parametrize('bad_zero', [False, 0.0])
def test_boolean_and_float_are_not_declared_integer_zero(field, bad_zero):
    value = point()
    value['current_run_cost'][field] = bad_zero
    assert_unknown(value)


@pytest.mark.parametrize('field', ['wall_clock_s', 'harness_preparation_wall_clock_s'])
@pytest.mark.parametrize('bad_time', [False, -1, None, '1', float('nan'), float('inf')])
def test_wall_and_prep_require_finite_nonnegative_numbers(field, bad_time):
    value = point()
    value['current_run_cost'][field] = bad_time
    assert_unknown(value)


def test_missing_required_wall_is_unknown():
    value = point()
    value['current_run_cost'].pop('wall_clock_s')
    assert_unknown(value)


def assert_unknown(value):
    before = json.dumps(value, sort_keys=True)
    assert _shared_h0_current_cost(value) is None
    totals = cost_totals([value], 'train', [])
    assert totals['calls'] is None and totals['tokens'] is None
    assert totals['cost_provenance']['historical_references'][0]['cost'] == value['cost']
    assert duration_seconds([value], {}, []) == (None, None)
    assert json.dumps(value, sort_keys=True) == before
