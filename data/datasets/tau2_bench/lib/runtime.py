"""τ² v0.1.0 half-duplex runtime: the official loop, driven by an external actor.

The official `Orchestrator.run()` owns the loop and calls the agent itself. Here the **actor is the
platform's harness**, so the loop is turned inside out: the actor submits one assistant message at a
time (`say` / `call` / `calls`), and this class performs exactly the transitions τ² performs —
assistant → user, assistant → environment — and enforces τ²'s own termination rules. Message types,
ordering and the step/error budget are the official ones, so the verifier can hand the sealed
messages to `evaluate_simulation` unchanged.

What the actor can never do: read the user's scenario, reset the conversation, set a seed, or call
the scorer. Those live behind the trusted boundary (see `relay.py` and `verify/check.py`).
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy

from tau2.data_model.message import (AssistantMessage, MultiToolMessage, ToolCall, ToolMessage,
                                     UserMessage)
from tau2.data_model.simulation import TerminationReason
from tau2.data_model.tasks import Task
from tau2.environment.environment import Environment
from tau2.orchestrator.orchestrator import DEFAULT_FIRST_AGENT_MESSAGE, Orchestrator
from tau2.registry import registry
from tau2.user.user_simulator import UserSimulator

#: The official episode budget. τ²'s defaults are 100 steps / 10 tool errors per episode, and its
#: own `run_task` passes them straight through.
MAX_STEPS = 100
MAX_ERRORS = 10
AGENT_STOP = '###STOP###'


def _tool_messages(environment: Environment, tool_calls: list[ToolCall]) -> list[ToolMessage]:
    return [environment.get_response(tc) for tc in tool_calls]


class Tau2Runtime:
    """One selected task, one conversation, no actor-visible state beyond what it is told."""

    def __init__(self, config: dict, user_model: str, user_model_args: dict):
        self.task = Task.model_validate(config['private'])
        self.domain = config['domain']
        self.task_id = config['task_id']
        self.revision = config['revision']
        self.case_sha256 = config['case_sha256']
        self.environment = registry.get_env_constructor(self.domain)()
        try:
            user_tools = self.environment.get_user_tools()
        except Exception:                                  # noqa: BLE001 - a domain may have none
            user_tools = None
        self.user = UserSimulator(tools=user_tools,
                                 instructions=str(self.task.user_scenario),
                                 llm=user_model, llm_args=dict(user_model_args))
        self.user_state = None
        self.messages: list = []
        self.steps = 0
        self.errors = 0
        self.termination_reason = None
        self.bootstrap_complete = False

    # ---------------------------------------------------------------- actor-facing operations
    def start_conversation(self) -> dict:
        """Apply the task's initial state. Idempotent; the actor gets no reset afterwards."""
        if self.bootstrap_complete:
            return self.get_runtime_status()
        initial = self.task.initial_state
        history = deepcopy(initial.message_history) if (initial and initial.message_history) else []
        self.environment.set_state(
            initialization_data=initial.initialization_data if initial else None,
            initialization_actions=initial.initialization_actions if initial else None,
            message_history=history)
        self.messages = list(history)
        self.user_state = self.user.get_init_state(list(history))
        self.bootstrap_complete = True
        return self.get_runtime_status()

    def submit_assistant_message(self, message: str) -> dict:
        text = str(message)
        self._require_open()
        assistant = AssistantMessage(role='assistant', content=text)
        self.messages.append(assistant)
        self.steps += 1
        if AGENT_STOP in text:
            self.termination_reason = TerminationReason.AGENT_STOP
            return {'agent_stop': True, 'termination_reason': self.termination_reason.value}
        reply = self._user_turn(assistant)
        return {'user': reply.get('content'), 'user_stop': reply.get('stop', False),
                'termination_reason': self._termination_value()}

    def call_assistant_tool(self, tool_name: str, arguments: dict) -> dict:
        self._require_open()
        call = ToolCall(id=f'call_{uuid.uuid4().hex[:12]}', name=tool_name, arguments=dict(arguments))
        self.messages.append(AssistantMessage(role='assistant', content=None, tool_calls=[call]))
        self.steps += 1
        tool_message = self.environment.get_response(call)
        self.messages.append(tool_message)
        self._count_error(tool_message)
        self.environment.sync_tools()
        return {'tool': tool_message.model_dump(mode='json'),
                'termination_reason': self._termination_value()}

    def submit_assistant_tool_calls(self, tool_calls_json: str) -> dict:
        """Several business calls in one assistant turn, as τ²'s `MultiToolMessage` allows."""
        self._require_open()
        batch = json.loads(tool_calls_json)
        calls = [ToolCall(id=str(item['id']), name=str(item['name']),
                          arguments=dict(item['arguments'])) for item in batch]
        self.messages.append(AssistantMessage(role='assistant', content=None, tool_calls=calls))
        self.steps += 1
        tool_messages = _tool_messages(self.environment, calls)
        if len(tool_messages) > 1:
            self.messages.append(MultiToolMessage(role='tool', tool_messages=tool_messages))
        else:
            self.messages.append(tool_messages[0])
        for tool_message in tool_messages:
            self._count_error(tool_message)
        self.environment.sync_tools()
        return {'tools': [m.model_dump(mode='json') for m in tool_messages],
                'termination_reason': self._termination_value()}

    def get_runtime_status(self) -> dict:
        return {'task_id': self.task_id, 'domain': self.domain, 'steps': self.steps,
                'errors': self.errors, 'max_steps': MAX_STEPS, 'max_errors': MAX_ERRORS,
                'messages': len(self.messages), 'started': self.bootstrap_complete,
                'termination_reason': self._termination_value()}

    # ---------------------------------------------------------------- internals
    def _user_turn(self, assistant: AssistantMessage) -> dict:
        user_message, self.user_state = self.user.generate_next_message(assistant, self.user_state)
        user_message.validate()
        self.messages.append(user_message)
        self.steps += 1
        stop = UserSimulator.is_stop(user_message)
        if stop:
            self.termination_reason = TerminationReason.USER_STOP
        result = {'content': user_message.content, 'stop': stop}
        if user_message.is_tool_call():
            # The user may call their own tools; those results are the environment's business and
            # are appended here, exactly as τ²'s loop does, so the trajectory stays official.
            tool_messages = _tool_messages(self.environment, user_message.tool_calls)
            for tool_message in tool_messages:
                self.messages.append(tool_message)
                self._count_error(tool_message)
            self.environment.sync_tools()
            result['user_tool_results'] = [m.model_dump(mode='json') for m in tool_messages]
        self._check_budget()
        return result

    def _count_error(self, message: ToolMessage) -> None:
        if getattr(message, 'error', False):
            self.errors += 1

    def _check_budget(self) -> None:
        if self.steps >= MAX_STEPS:
            self.termination_reason = TerminationReason.MAX_STEPS
        elif self.errors >= MAX_ERRORS:
            self.termination_reason = TerminationReason.TOO_MANY_ERRORS

    def _termination_value(self):
        value = self.termination_reason
        return value.value if value is not None else None

    def _require_open(self) -> None:
        if not self.bootstrap_complete:
            raise ValueError('start_conversation_first')
        if self.termination_reason is not None:
            raise ValueError(f'conversation_ended:{self._termination_value()}')

    # ---------------------------------------------------------------- sealing
    def episode(self) -> dict:
        """The sealed episode: exactly what the official scorer needs, and nothing else."""
        trajectory = []
        for index, message in enumerate(deepcopy(self.messages)):
            message.turn_idx = index
            trajectory.append(message.model_dump(mode='json'))
        return {'schema': 'hg-tau2-episode.v1', 'task_id': self.task_id, 'domain': self.domain,
                'revision': self.revision, 'case_sha256': self.case_sha256,
                'termination_reason': self._termination_value(), 'steps': self.steps,
                'errors': self.errors, 'messages': trajectory}


def agent_first_message() -> dict:
    """τ² opens with an agent greeting; it is the official default and costs no model call."""
    return DEFAULT_FIRST_AGENT_MESSAGE.model_dump(mode='json')


def orchestrator_reference() -> str:
    """Keep the reference explicit: this runtime mirrors `Orchestrator.step` (τ² v0.1.0)."""
    return Orchestrator.__module__
