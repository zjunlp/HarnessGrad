"""Official v0.1.0 ALL scorer: the release's own evaluator, over the sealed conversation.

v0.1.0's `evaluate_simulation` takes `(simulation, task, evaluation_type, solo_mode, domain)` --
there is no communication-mode argument and no extra environment kwargs. `EvaluationType.ALL`
runs **every** evaluator and then multiplies only the parts the task's reward basis names; the
default basis is `[DB, COMMUNICATE]` and telecom declares `[ENV_ASSERTION]` or
`[ACTION, ENV_ASSERTION]`.

Two consequences this module has to live with rather than assume away:

* The NL evaluator is called for **every task with `nl_assertions`**, 58 of 278 here, even when
  the basis then multiplies its verdict out. The judge channel is therefore declared per task in
  the adapter (`needs_judge`) and patched by the verifier before scoring; a task whose basis
  *does* name `NL_ASSERTION` gets its coverage checked below instead of being trusted.
* Each evaluator returns **1.0 when its own criterion list is empty** (upstream's `all([])`), and
  the basis then multiplies that in: 164 airline/retail tasks declare no `communicate_info`, so
  their COMMUNICATE half is a free 1.0 and the database half is what decides. That is the
  release's rule, not a defect to patch here -- what it means for this platform is that a *task*
  whose every named half is empty would be a number that cannot fail, so `control.py` selects
  tasks with a state-changing gold action and `tests/quality/test_tau2_bench_official_scorer.py`
  measures an empty episode per selected task and reports 0, not 1.
"""
from copy import deepcopy
from collections import Counter


def score(config, episode):
    from tau2.data_model.tasks import Task
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
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
                        messages=messages, seed=episode['seed'])
    reward = evaluate_simulation(run, task, EvaluationType.ALL, solo_mode=False,
                                 domain=config['domain'])
    from tau2.data_model.tasks import RewardType
    criteria = task.evaluation_criteria
    basis = getattr(criteria, 'reward_basis', None) or []
    if termination in ('agent_stop', 'user_stop') and criteria and RewardType.NL_ASSERTION in basis:
        expected = criteria.nl_assertions or []
        observed = [item.nl_assertion for item in (reward.nl_assertions or [])]
        if Counter(observed) != Counter(expected):
            raise ValueError('nl_judge_assertion_coverage_mismatch')
    return reward.model_dump(mode='json')
