"""Official τ² v0.1.0 `ALL` scorer, with the validity gate v0.1.0 lacks.

Two upstream behaviours are corrected here, and both were measured on this host rather than assumed:

1. **`all([])` is not a success.** `evaluate_simulation` returns 1.0 when the reward basis selects
   nothing to check — an airline task with no actions and only a non-basis `nl_assertion` scored 1.0
   for a do-nothing episode. A task whose basis has no non-empty criterion is `unmeasured` here.
2. **v0.1.0 has no `AGENT_ERROR` termination.** Its enum is user_stop / agent_stop / max_steps /
   too_many_errors. An episode that ends without any stop signal is scored through the official
   early-termination rule (`max_steps`), and the receipt records that the termination was
   *substituted*, so nobody can read it as the episode having hit the budget.
"""
from __future__ import annotations

from copy import deepcopy


def actionable_basis(task) -> set[str]:
    """Which declared reward-basis entries actually have something to check."""
    from tau2.data_model.tasks import RewardType

    criteria = task.evaluation_criteria
    if criteria is None:
        return set()
    # `RewardType` is a `str` enum: `str(RewardType.DB)` is "RewardType.DB", not "DB", so the
    # value has to be taken explicitly or every basis looks empty.
    basis = {str(getattr(entry, 'value', entry)) for entry in (criteria.reward_basis or [])}
    present = {
        'DB': True,
        'ACTION': bool(criteria.actions),
        'ENV_ASSERTION': bool(criteria.env_assertions),
        'COMMUNICATE': bool(criteria.communicate_info),
        'NL_ASSERTION': bool(criteria.nl_assertions),
    }
    return {name for name in present if name in basis and present[name]}


def score(config: dict, episode: dict) -> dict:
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.data_model.tasks import Task, RewardType
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.utils.llm_utils import to_tau2_messages
    from tau2.utils.utils import get_now

    task = Task.model_validate(config['private'])
    if episode.get('task_id') != config['task_id'] or episode.get('domain') != config['domain']:
        raise ValueError('episode task identity mismatch')
    if episode.get('case_sha256') != config['case_sha256']:
        raise ValueError('episode case digest mismatch')
    if episode.get('failure'):
        raise RuntimeError('private runtime failed: ' + str(episode['failure']))
    if not actionable_basis(task):
        # The upstream metric would be 1.0 here. Refusing is the point: the benchmark's number
        # cannot be reached from a basis that has nothing in it.
        raise ValueError('no_actionable_reward_basis')
    termination = episode.get('termination_reason')
    substituted = None
    if termination is None:
        termination = TerminationReason.MAX_STEPS.value
        substituted = {'reason': 'incomplete_conversation_without_stop_signal',
                       'recorded_termination_reason': None,
                       'scorer_termination_reason': termination}
    messages = to_tau2_messages(deepcopy(episode['messages']))
    for index, message in enumerate(messages):
        message.turn_idx = index
    now = get_now()
    run = SimulationRun(
        id='trusted-episode', task_id=task.id, start_time=now, end_time=now, duration=0.0,
        termination_reason=TerminationReason(termination), messages=messages, seed=episode.get('seed'))
    reward = evaluate_simulation(run, task, EvaluationType.ALL, solo_mode=False, domain=config['domain'])
    result = reward.model_dump(mode='json')
    result['actionable_basis'] = sorted(actionable_basis(task))
    if substituted:
        result['termination_substitution'] = substituted
    # A judge-dependent task must be judged by the pinned judge; telecom has none, and a domain that
    # gains one later must not silently skip it.
    basis = {str(getattr(entry, 'value', entry))
              for entry in (task.evaluation_criteria.reward_basis or [])}
    if RewardType.NL_ASSERTION.value in basis and not config.get('judge_model'):
        raise ValueError('nl_assertion_basis_without_pinned_judge')
    return result
