"""Pinned v0.1.0 ALL scoring over a privately bound, sealed conversation.

Never repair criteria, fabricate a termination reason, or replace the release's
reward. ALL executes NL assertions even when their reward basis is ignored.
"""
from copy import deepcopy
from collections import Counter


def validate_identity(config, episode):
    """Compare episode identity with trusted *private* inputs, never itself."""
    if config['release'] != 'v0.1.0':
        raise ValueError('unsupported private release')
    expected = {'task_id': config['task']['id'], 'domain': config['domain'],
                'revision': config['release'], 'case_task_id': config['task_id'],
                'case_sha256': config['case_sha256']}
    if any(episode.get(key) != value for key, value in expected.items()):
        raise ValueError('episode task identity mismatch')
    # Missing health evidence and unfinished runs are not official zero scores.
    # This release has no AGENT_ERROR enum and no incomplete-episode scoring rule.
    if 'failure' not in episode:
        raise ValueError('episode missing failure status')
    if episode['failure'] is not None:
        raise RuntimeError('private runtime failed: ' + str(episode['failure']))
    if episode['termination_reason'] is None:
        raise ValueError('unfinished_episode_without_official_termination')
    if not isinstance(episode['messages'], list):
        raise ValueError('episode messages must be a list')
    if type(episode['seed']) is not int:
        raise ValueError('episode seed must be an integer')


def score(config, episode):
    validate_identity(config, episode)
    from tau2.data_model.tasks import Task, RewardType
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.utils.llm_utils import to_tau2_messages
    from tau2.utils.utils import get_now
    task = Task.model_validate(config['task'])
    termination = TerminationReason(episode['termination_reason'])
    messages = to_tau2_messages(deepcopy(episode['messages']))
    for index, message in enumerate(messages):
        message.turn_idx = index
    now = get_now()
    run = SimulationRun(id='trusted-episode', task_id=task.id, start_time=now, end_time=now,
                        duration=0.0, termination_reason=termination,
                        messages=messages, seed=episode['seed'])
    reward = evaluate_simulation(run, task, EvaluationType.ALL, solo_mode=False,
                                 domain=config['domain'])
    criteria = task.evaluation_criteria
    basis = getattr(criteria, 'reward_basis', None) or []
    expected = (criteria.nl_assertions or []) if criteria else []
    early = termination in (TerminationReason.MAX_STEPS, TerminationReason.TOO_MANY_ERRORS)
    performed = bool(expected) and not early
    observed = [item.nl_assertion for item in (reward.nl_assertions or [])]
    # This is coverage of work performed, not just coverage of multiplied-in
    # scores. Empty/partial/duplicate/unexpected results fail even on DB-only rows.
    if not early and Counter(observed) != Counter(expected):
        raise ValueError('nl_judge_assertion_coverage_mismatch')
    result = reward.model_dump(mode='json')
    result['judge_coverage'] = {
        'status': ('performed' if performed else
                   'skipped_official_early_termination' if early else 'not_required'),
        'expected_count': len(expected), 'observed_count': len(observed),
        'used_in_reward': RewardType.NL_ASSERTION in basis,
        'complete': not early,
    }
    return result
