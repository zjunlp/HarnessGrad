"""Pin upstream engine.py and compare every candidate to its published predicate.

Source: linhaowei1/kumo@576748764f7d72634654d0784944a485eca9047c,
 env/engine.py lines 20-34. Apache-2.0; license accompanies the verbatim engine.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from data.datasets import kumo


def asset(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


engine = asset('kumo_official_engine', kumo.ASSETS / 'vendor/engine.py')
from unittest.mock import patch
with patch.dict(sys.modules, {'engine': engine}):
    scoring = asset('kumo_scoring_test', kumo.ASSETS / 'lib/scoring.py')


# Verbatim upstream function, lines 20-34 (method indentation removed).
def evaluate_answer(self, answer):
    # evaluate if answer is ruled out by the observations. Return True if ruled out.

    if answer not in self.truth_space:
        # print(f"Answer {answer} not in valid truth space")
        return False

    for action, observation in self.observations.items():
        action_info = self.ta_mapping[action]
        for state, impossible_diseases in action_info["states"].items():
            if (action_info['type'] == 'str' and observation == state) or (action_info['type'] == 'float' and observation >= state[0] and observation <= state[1]):
                if answer in impossible_diseases:
                    return False

    return True


def test_official_engine_bytes_and_transcribed_function_do_not_drift():
    raw = (kumo.ASSETS / 'vendor/engine.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '6cd2bf798fbe53fc3122a4c2420e08e9534050aecd147d757aa98b4a556b0f05'
    import ast
    import inspect
    import textwrap
    left = ast.parse(textwrap.dedent(inspect.getsource(engine.Environment.evaluate_answer)))
    right = ast.parse(inspect.getsource(evaluate_answer))
    assert ast.dump(left) == ast.dump(right)


@pytest.mark.parametrize('observation', [0, 1, 2])
def test_numeric_boundaries_and_ambiguous_truths(observation):
    env = engine.Environment(['a', 'b', 'c'], ['probe'],
                             {'probe': {'type': 'float', 'states': {(0, 1): ['a'], (1, 2): ['b']}}},
                             {'probe': observation})
    for answer in ['', 'a', 'b', 'c', 'invalid']:
        assert env.evaluate_answer(answer) == evaluate_answer(env, answer)
    assert env.evaluate_answer('c')
    if observation == 1:
        assert not env.evaluate_answer('a') and not env.evaluate_answer('b')


def test_prepared_pool_all_candidates_and_audited_exclusions():
    if not kumo.provision_status()['present']:
        pytest.skip('KUMO source not provisioned')
    multi = 0
    for tid, row in kumo.roster().items():
        raw = (kumo.PREPARED / row['path'] / 'private.json').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['private_sha256']
        case = json.loads(raw)
        env = scoring.environment(case)
        assert env.evaluate_answer(case['valid_truth'])
        assert not env.evaluate_answer('')
        accepted = []
        for truth in case['truths']:
            assert env.evaluate_answer(truth) == evaluate_answer(env, truth), tid
            if env.evaluate_answer(truth):
                accepted.append(truth)
        multi += len(accepted) > 1
    assert len(kumo.roster()) == 5290
    assert multi == 35
    audit = json.loads((kumo.DATASET_ROOT / 'official-audit.json').read_text())
    rejected = {r['task_id'] for r in audit['rows'] if not r['accepted']}
    assert rejected == {r['task_id'] for r in kumo.manifest()['exclusions']}
    assert len(rejected) == 10 and all(r['repeat_matches'] for r in audit['rows'])
