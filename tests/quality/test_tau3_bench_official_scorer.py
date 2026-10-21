"""Pinned τ³ v1.0.1 official ALL reward and pass^k differential controls.

Source: sierra-research/tau2-bench@fc0055dc, evaluator/evaluator.py:86-276,
metrics/agent_metrics.py:113-135. No NL criterion is removed for oracle runs.
"""
import importlib.util
import json

import pytest

from data.datasets import tau3_bench as dataset

ASSETS = dataset.ASSETS


def asset(name):
    spec = importlib.util.spec_from_file_location('tau3_test_' + name, ASSETS / 'lib' / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def official():
    if not (dataset.DATASET_ROOT / 'bundle/manifest.json').exists():
        pytest.skip('prepare pinned tau3 data and isolated runtime')
    asset('bootstrap').bootstrap(dataset.DATASET_ROOT / 'bundle', dataset.RUNTIME)
    from tau2.data_model.tasks import Task
    from tau2.data_model.message import ToolCall, AssistantMessage
    from tau2.domains.airline.environment import get_environment
    row = dataset.roster()['tau3_bench/airline/8']
    config = json.loads((dataset.DATASET_ROOT / row['path'] / 'private.json').read_text())
    task = Task.model_validate(config['task']); env = get_environment()
    initial = task.initial_state
    env.set_state(initialization_data=initial.initialization_data if initial else None,
                  initialization_actions=initial.initialization_actions if initial else None,
                  message_history=(initial.message_history or []) if initial else [])
    messages = list(initial.message_history or []) if initial else []
    for index, action in enumerate(task.evaluation_criteria.actions):
        call = ToolCall(id=str(index), name=action.name, arguments=action.arguments, requestor=action.requestor)
        messages.extend([AssistantMessage(role='assistant', tool_calls=[call]), env.get_response(call)])
        env.sync_tools()
    messages.append(AssistantMessage(role='assistant', content='###STOP###'))
    episode = {'domain': 'airline', 'task_id': task.id, 'termination_reason': 'agent_stop',
               'seed': dataset.TRIAL_PROTOCOL['seeds'][0], 'messages': [m.model_dump(mode='json') for m in messages]}
    return config, episode


def test_released_gold_official_all_differential(official):
    from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType
    from tau2.data_model.tasks import Task
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.utils.llm_utils import to_tau2_messages
    from tau2.utils.utils import get_now
    config, episode = official
    messages = to_tau2_messages(episode['messages'])
    for index, message in enumerate(messages): message.turn_idx = index
    direct = evaluate_simulation(SimulationRun(id='independent', task_id=episode['task_id'],
        start_time=get_now(), end_time=get_now(), duration=0, termination_reason=TerminationReason.AGENT_STOP,
        messages=messages), Task.model_validate(config['task']), EvaluationType.ALL, False, 'airline')
    assert direct.reward == asset('scoring').score(config, episode)['reward'] == 1


@pytest.mark.parametrize('reason', ['max_steps', 'too_many_errors'])
def test_incomplete_conversation_is_official_failure(official, reason):
    config, episode = official
    assert asset('scoring').score(config, {**episode, 'termination_reason': reason})['reward'] == 0


def test_transport_failure_cannot_be_scored_as_wrong_answer(official):
    config, episode = official
    with pytest.raises(RuntimeError, match='private runtime failed'):
        asset('scoring').score(config, {**episode, 'failure': 'provider_error'})


def test_no_reward_basis_removed_and_nl_uses_official_prompt(official, monkeypatch):
    from tau2.data_model.tasks import Task, RewardType
    import tau2.evaluator.evaluator_nl_assertions as module
    from tau2.data_model.message import AssistantMessage
    config, episode = official
    task = Task.model_validate(config['task'])
    task.evaluation_criteria.reward_basis = [RewardType.NL_ASSERTION]
    task.evaluation_criteria.nl_assertions = ['The assistant followed the policy.']
    requests = []
    def generate(**kwargs):
        requests.append(kwargs)
        return AssistantMessage(role='assistant', content=json.dumps({'results': [{
            'expectedOutcome': task.evaluation_criteria.nl_assertions[0], 'metExpectation': False, 'reasoning': 'fixture'}]}))
    monkeypatch.setattr(module, 'generate', generate)
    result = asset('scoring').score({**config, 'task': task.model_dump(mode='json')}, episode)
    assert result['reward'] == 0 and len(requests) == 1
    assert 'expectedOutcomes' in requests[0]['messages'][1].content


def test_pass_hat_k_formula_and_full_manifest(official):
    # Official pass_hat_k: product((s-i)/(n-i) for i in range(k)), with s<k -> 0.
    from tau2.metrics.agent_metrics import pass_hat_k
    from eval.run.repeated_trials import aggregate
    manifest = [{'task_id': 't', 'trial_id': str(i), 'seed': str(i)} for i in range(3)]
    rows = [{**m, 'status': 'measured', 'score': int(i < 2)} for i, m in enumerate(manifest)]
    report = aggregate(manifest, rows, [1, 2, 3], groups={'t': 'airline'})
    for k in (1, 2, 3):
        assert report['metrics'][str(k)] == pytest.approx(pass_hat_k(3, 2, k))
    assert not aggregate(manifest, rows[:-1], [1, 2, 3])['complete']
    assert aggregate(manifest, rows[:-1], [1, 2, 3])['metrics'] is None


def test_missing_termination_is_unmeasured(official):
    config, episode = official
    with pytest.raises(ValueError, match='incomplete_conversation'):
        asset('scoring').score(config, {**episode, 'termination_reason': None})


@pytest.mark.parametrize('reply', [{}, {'results': []}, {'results': [
    {'expectedOutcome': 'foreign assertion', 'metExpectation': True, 'reasoning': 'fixture'}]}])
def test_official_nl_empty_or_wrong_coverage_is_unmeasured(official, monkeypatch, reply):
    import tau2.evaluator.evaluator_nl_assertions as module
    from tau2.data_model.message import AssistantMessage
    config = json.loads((dataset.DATASET_ROOT / 'cases/retail/2/private.json').read_text())
    task = config['task']
    assert task['evaluation_criteria']['nl_assertions']
    monkeypatch.setattr(module, 'generate', lambda **kwargs: AssistantMessage(role='assistant', content=json.dumps(reply)))
    episode = {'task_id': task['id'], 'domain': 'retail', 'seed': 123,
               'termination_reason': 'agent_stop', 'messages': [{'role': 'assistant', 'content': '###STOP###'}]}
    with pytest.raises(ValueError, match='assertion_coverage'):
        asset('scoring').score(config, episode)


@pytest.mark.parametrize('reason', ['max_steps', 'too_many_errors'])
def test_real_nl_task_official_early_stop_remains_zero(official, reason):
    config = json.loads((dataset.DATASET_ROOT / 'cases/retail/2/private.json').read_text())
    assert config['task']['evaluation_criteria']['nl_assertions']
    episode = {'task_id': config['task']['id'], 'domain': 'retail', 'seed': 123,
               'termination_reason': reason, 'messages': []}
    assert asset('scoring').score(config, episode)['reward'] == 0
