"""Translate τ²'s official model calls onto the platform-owned model capability.

τ² v0.1.0 reaches a model through `tau2.utils.llm_utils.generate`, which calls
`litellm.completion(model=...)`. The platform never lets a dataset pick a provider or a base URL,
so this module replaces the *module-level* `generate` that the user simulator (and, when a task
needs one, the NL judge) imported, and serves it from the platform's model socket instead.

**Why the patch is on the module, not an argument.** `tau2/user/user_simulator.py` does
`from tau2.utils.llm_utils import generate` at import time and calls it as a bare name; the NL
assertion evaluator does the same. There is no injection point, so the binding is replaced after
import — the same reason the judge model has to be re-bound before evaluation
(`DEFAULT_LLM_NL_ASSERTIONS` is a constant, not a parameter).
"""
from __future__ import annotations

import json
import os


def install(role: str, transport) -> None:
    """Serve τ²'s `generate` from `HG_ENV_MODEL_SOCKET` (simulator) or `HG_JUDGE_SOCKET` (judge)."""
    if role not in ('environment', 'judge'):
        raise ValueError('unsupported model role')
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.utils.llm_utils import to_litellm_messages
    # v0.1.0 has no `validate_message_history` in `llm_utils` (τ³ added it there); the runtime
    # validates each message as it is appended, so the shim does not re-validate.

    socket_env = 'HG_ENV_MODEL_SOCKET' if role == 'environment' else 'HG_JUDGE_SOCKET'

    def generate(model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
        response = transport(os.environ[socket_env], 'complete',
                             {'messages': to_litellm_messages(messages)},
                             timeout_s=None, max_line_bytes=None)
        message = response['message']
        if role == 'judge':
            # A judge reply that is not one strict boolean per requested assertion is
            # *unmeasured*, never a pass: upstream's `all([])` would otherwise call it a success.
            parsed = json.loads(message.get('content') or 'null')
            results = parsed.get('results') if isinstance(parsed, dict) else None
            if not isinstance(results, list) or any(
                not isinstance(item, dict) or type(item.get('metExpectation')) is not bool
                or not isinstance(item.get('expectedOutcome'), str)
                or not isinstance(item.get('reasoning'), str) for item in results
            ):
                raise ValueError('malformed_nl_judge_result')
        calls = [ToolCall(id=c['id'], name=c['function']['name'],
                          arguments=json.loads(c['function']['arguments']))
                 for c in message.get('tool_calls') or []]
        return AssistantMessage(role='assistant', content=message.get('content'),
                                tool_calls=calls or None, usage=response.get('usage'))

    if role == 'environment':
        import tau2.user.user_simulator as module
    else:
        import tau2.evaluator.evaluator_nl_assertions as module
    module.generate = generate
