"""Official v1.0.1 ALL scorer, including every declared natural-language assertion."""
from copy import deepcopy
from collections import Counter


def score(config, episode):
    from tau2.data_model.tasks import Task
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.orchestrator.modes import CommunicationMode
    from tau2.utils.llm_utils import to_tau2_messages
    from tau2.utils.utils import get_now
    task = Task.model_validate(config['task'])
    if episode['task_id'] != task.id or episode['domain'] != config['domain']:
        raise ValueError('episode task identity mismatch')
    if episode.get('failure'):
        raise RuntimeError('private runtime failed: ' + episode['failure'])
    termination = episode['termination_reason']
    if termination is None:
        # The relay is sealed after the candidate exits. A healthy unfinished
        # conversation is a candidate failure, evaluated by the official early-
        # termination rule. Missing health/termination fields are damaged evidence,
        # not proof of an unfinished conversation. Never alter the recorded messages
        # or invent a successful STOP on the candidate's behalf.
        if episode['failure'] is not None:
            raise ValueError('unfinished episode must explicitly report failure: null')
        termination = TerminationReason.AGENT_ERROR.value
    if not isinstance(episode['messages'], list):
        raise ValueError('episode messages must be a list')
    messages = to_tau2_messages(deepcopy(episode['messages']))
    for index, message in enumerate(messages):
        message.turn_idx = index
    now = get_now()
    run = SimulationRun(id='trusted-episode', task_id=task.id, start_time=now, end_time=now,
                        duration=0.0, termination_reason=TerminationReason(termination),
                        messages=messages, seed=episode['seed'], mode=CommunicationMode.HALF_DUPLEX.value)
    kwargs = {'retrieval_variant': 'bm25', 'task': task} if config['domain'] == 'banking_knowledge' else {}
    reward = evaluate_simulation(run, task, EvaluationType.ALL, solo_mode=False,
                                 domain=config['domain'], mode=CommunicationMode.HALF_DUPLEX,
                                 env_kwargs=kwargs)
    from tau2.data_model.tasks import RewardType
    criteria = task.evaluation_criteria
    if termination in ('agent_stop', 'user_stop') and criteria and RewardType.NL_ASSERTION in criteria.reward_basis:
        expected = criteria.nl_assertions or []
        observed = [item.nl_assertion for item in (reward.nl_assertions or [])]
        if Counter(observed) != Counter(expected):
            raise ValueError('nl_judge_assertion_coverage_mismatch')
    return reward.model_dump(mode='json')
