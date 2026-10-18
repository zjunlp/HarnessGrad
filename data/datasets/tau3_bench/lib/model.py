"""Translate the official text generator to the platform-owned model capability."""
import json
import os


def install(role, transport):
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.utils.llm_utils import to_litellm_messages, validate_message_history

    def generate(model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
        validate_message_history(messages)
        response = transport(os.environ['HG_ENV_MODEL_SOCKET' if role == 'environment' else 'HG_JUDGE_SOCKET'],
                             'complete', {'messages': to_litellm_messages(messages)})
        message = response['message']
        if role == 'judge':
            parsed = json.loads(message.get('content'))
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
