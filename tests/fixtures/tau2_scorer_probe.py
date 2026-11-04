#!/usr/bin/env python3
"""Run the pinned v0.1.0 ALL scorer on one τ² task and print the reward it returns.

A probe, not a scorer: `tests/quality/test_tau2_bench_official_scorer.py` runs it under the
dataset's own Python 3.13 runtime, which the platform's 3.12 test interpreter cannot import. It
evaluates two episodes of the same task -- the released gold actions replayed, and an empty
conversation -- so a task whose gold is unreachable and a task whose check cannot fail are
distinguishable. The judge is answered in-process with `{"results": []}`, which the release reads
as "nothing to grade"; every selected task's reward basis excludes `NL_ASSERTION`, so the answer
is multiplied out rather than counted.

    python3 tau2_scorer_probe.py <bundle> <lib-dir> <private.json> <domain> {gold,empty}
"""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle')
    parser.add_argument('lib')
    parser.add_argument('case')
    parser.add_argument('domain')
    parser.add_argument('arm', choices=['gold', 'empty'])
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.lib).resolve()))
    from bootstrap import bootstrap
    bootstrap(Path(args.bundle))
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.data_model.simulation import SimulationRun, TerminationReason
    from tau2.data_model.tasks import Task
    from tau2.evaluator import evaluator_nl_assertions
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.registry import registry
    from tau2.utils.utils import get_now

    def judge(model, messages, **kwargs):
        # `evaluate_nl_assertions` reads `results` from the completion; an empty list is
        # "nothing to grade", never a fabricated verdict.
        return AssistantMessage(role='assistant', content=json.dumps({'results': []}))

    evaluator_nl_assertions.generate = judge

    config = json.loads(Path(args.case).read_text())
    task = Task.model_validate(config['task'])
    messages = list(task.initial_state.message_history or []) if task.initial_state else []
    if args.arm == 'gold':
        environment = registry.get_env_constructor(args.domain)(solo_mode=False)
        initial = task.initial_state
        environment.set_state(
            initialization_data=initial.initialization_data if initial else None,
            initialization_actions=initial.initialization_actions if initial else None,
            message_history=messages)
        for index, action in enumerate(task.evaluation_criteria.actions or []):
            call = ToolCall(id=str(index), name=action.name, arguments=action.arguments,
                            requestor=action.requestor)
            messages.append(AssistantMessage(role='assistant', tool_calls=[call], content=None))
            messages.append(environment.get_response(call))
        messages.append(AssistantMessage(role='assistant', content='###STOP###'))
    now = get_now()
    run = SimulationRun(id='probe', task_id=task.id, start_time=now, end_time=now, duration=0.0,
                        termination_reason=TerminationReason.AGENT_STOP, messages=messages,
                        seed=1)
    reward = evaluate_simulation(run, task, EvaluationType.ALL, solo_mode=False,
                                 domain=args.domain)
    print(json.dumps({'task_id': config['task_id'], 'release': config['release'],
                      'arm': args.arm, 'reward': reward.reward,
                      'basis': reward.reward_basis,
                      'breakdown': reward.reward_breakdown,
                      'db_match': None if reward.db_check is None else reward.db_check.db_match,
                      'env_assertions': None if reward.env_assertions is None else
                      [item.met for item in reward.env_assertions],
                      'actions': None if reward.action_checks is None else
                      [item.action_match for item in reward.action_checks]}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
