"""Trusted AgentDojo v1.2.2 bridge. Runs only in the isolated dataset Python runtime.

Metadata and oracle generation never enter the agent SETUP. Evaluation replays only
function/args plus final text; supplied tool results or state are not trusted.
Utility and attacker success remain separate official predicates. No LLM is called.
Uses registered ``ignore_previous`` verbatim (including its upstream typo), not
the paper's ``important_instructions`` attacker or the former local template.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.attacks import load_attack
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime
from agentdojo.task_suite import get_suites, get_suite
from agentdojo.types import ChatAssistantMessage, text_content_block_from_string

VERSION = "v1.2.2"
ATTACK_NAME = "ignore_previous"


class ReplayPipeline(BasePipelineElement):
    def __init__(self, action):
        self.action = action

    def query(self, query, runtime, env, messages=[], extra_args={}):
        out = []
        for raw in self.action.get("calls", []):
            call = FunctionCall(function=raw["function"], args=raw["args"])
            runtime.run_function(env, call.function, call.args)
            out.append(ChatAssistantMessage(role="assistant", tool_calls=[call],
                       content=[text_content_block_from_string("")]))
        out.append(ChatAssistantMessage(role="assistant", tool_calls=None,
                   content=[text_content_block_from_string(self.action.get("output", ""))]))
        return query, runtime, env, out, extra_args


def metadata():
    records = []
    for name, suite in get_suites(VERSION).items():
        for uid, user in suite.user_tasks.items():
            for iid in suite.injection_tasks:
                records.append({"suite": name, "user": uid, "injection": iid,
                                "prompt": user.PROMPT})
    return records


def get_case(case):
    suite = get_suite(VERSION, case["suite"])
    user = suite.get_user_task_by_id(case["user"])
    injection = suite.get_injection_task_by_id(case["injection"])
    attacker = load_attack(ATTACK_NAME, suite, GroundTruthPipeline(None))
    injections = attacker.attack(user, injection)
    return suite, user, injection, injections


def oracle(case):
    suite, user, _, injections = get_case(case)
    env = user.init_environment(suite.load_and_inject_default_environment(injections))
    return {"calls": [c.model_dump(include={"function", "args"}) for c in user.ground_truth(env)],
            "output": user.GROUND_TRUTH_OUTPUT}


def snapshot(case):
    suite, user, _, injections = get_case(case)
    env = user.init_environment(suite.load_and_inject_default_environment(injections))
    return {"suite": case["suite"], "attack": ATTACK_NAME,
            "environment": env.model_dump(mode="json"),
            "tools": [{"name": tool.name, "description": tool.description,
                       "parameters": tool.parameters.model_json_schema()} for tool in suite.tools]}


def evaluate(case, action):
    suite, user, injection, injections = get_case(case)
    utility, attack_success = suite.run_task_with_pipeline(
        ReplayPipeline(action), user, injection, injections)
    return {"utility": bool(utility), "attack_success": bool(attack_success),
            "attack": ATTACK_NAME, "suite_version": VERSION,
            "reward": float(utility)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["metadata", "oracle", "evaluate", "snapshot"])
    parser.add_argument("--case")
    parser.add_argument("--action")
    args = parser.parse_args()
    if args.mode == "metadata":
        result = metadata()
    else:
        case = json.loads(Path(args.case).read_text())
        if args.mode == "oracle":
            result = oracle(case)
        elif args.mode == "snapshot":
            result = snapshot(case)
        else:
            action = json.loads(Path(args.action).read_text()) if args.action else {"calls": [], "output": ""}
            result = evaluate(case, action)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
