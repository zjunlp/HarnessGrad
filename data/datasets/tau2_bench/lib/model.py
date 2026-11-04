"""Translate the official text generator to the platform-owned model capability."""
import json
import os


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_nl_judge_json_key')
        result[key] = value
    return result


def install(role, transport):
    if role not in ('environment', 'judge'):
        raise ValueError('unsupported model role')
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.utils.llm_utils import to_litellm_messages

    def generate(model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
        # v0.1.0 has no `validate_message_history` (v1.0.1 added it); the history is built by
        # the release's own message classes and the transport frame is the boundary that matters.
        response = transport(os.environ['HG_ENV_MODEL_SOCKET' if role == 'environment' else 'HG_JUDGE_SOCKET'],
                             'complete', {'messages': to_litellm_messages(messages)},
                             timeout_s=None, max_line_bytes=None)
        message = response['message']
        if role == 'judge':
            if message.get('tool_calls') or not isinstance(message.get('content'), str):
                raise ValueError('malformed_nl_judge_message')
            parsed = json.loads(message['content'], object_pairs_hook=_unique_object)
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
                                tool_calls=calls or None, usage=response['usage'])

    if role == 'environment':
        import tau2.user.user_simulator as module
    else:
        import tau2.evaluator.evaluator_nl_assertions as module
    module.generate = generate
