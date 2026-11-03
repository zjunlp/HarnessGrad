"""Independent native process: translate I/O without rewriting the MiniAgent loop.

The SDK transport observer covers primary, side, reader and forced-answer calls.
MockTransport exercises the actual native loop and OpenAI SDK without any socket.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import signal
import sys


#: The contract's `observation.kind`, the same four words every other adapter writes
#: (`docs/adapter-spec.md` section 4). The native tool name goes beside it as `tool`.
_OBSERVATION_KINDS = {
    "shell": "shell", "bash": "shell", "terminal": "shell",
    "read": "filesystem", "read_file": "filesystem", "read_skill": "filesystem",
    "write": "filesystem", "edit": "filesystem", "glob": "filesystem", "grep": "filesystem",
    "web_fetch": "api", "web_search": "api", "read_url": "api",
}


def observation_kind(tool: str) -> str:
    return _OBSERVATION_KINDS.get((tool or "").lower(), "other")


def mock_response(request):
    import httpx
    body = json.loads(request.content)
    responses = request.url.path.endswith('/responses')
    history = body.get('input') if responses else body.get('messages')
    history = history if isinstance(history, list) else []
    observed = any(m.get('type') == 'function_call_output' or m.get('role') == 'tool'
                   for m in history)
    command = "printf 'MINIAGENT_MOCK\\n' > answer.txt"
    if responses:
        output = ([{'type': 'message', 'id': 'mock_msg', 'role': 'assistant',
                    'status': 'completed', 'content': [{'type': 'output_text',
                    'text': 'MINIAGENT_MOCK', 'annotations': []}]}] if observed else
                  [{'type': 'function_call', 'id': 'mock_tool', 'call_id': 'mock_tool',
                    'name': 'shell', 'arguments': json.dumps({'command': command})}])
        payload = {'id': 'resp_mock', 'object': 'response', 'created_at': 0,
                   'status': 'completed', 'model': body.get('model'), 'output': output,
                   'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
    else:
        message = ({'role': 'assistant', 'content': 'MINIAGENT_MOCK'} if observed else
                   {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'mock_tool', 'type': 'function', 'function': {'name': 'shell',
                    'arguments': json.dumps({'command': command})}}]})
        payload = {'id': 'chat_mock', 'object': 'chat.completion', 'created': 0,
                   'model': body.get('model'), 'choices': [{'index': 0, 'message': message,
                    'finish_reason': 'stop' if observed else 'tool_calls'}],
                   'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
    return httpx.Response(200, json=payload, request=request)


def _children(pid: int) -> list[int]:
    try:
        direct = [int(value) for value in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
    except (OSError, ValueError):
        return []
    return direct + [child for item in direct for child in _children(item)]


async def main():
    request = json.load(sys.stdin)
    root = Path(request['root'])
    sys.path.insert(0, str(root))
    from runtime import llm, trace as native_trace
    import httpx
    from openai import AsyncOpenAI

    # Adopt detached native shell descendants so successful commands cannot leak
    # services when this task finishes. This does not provide a sandbox; HG does.
    if sys.platform.startswith('linux'):
        ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if os.name == 'posix':
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    secrets = request.get('secrets') or []
    trace_path = Path(request['trace'])

    def record(value):
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        for secret in secrets:
            encoded = encoded.replace(secret, '<redacted>')
        with trace_path.open('a', encoding='utf-8') as stream:
            stream.write(encoded + '\n')

    calls = 0
    input_tokens = output_tokens = 0
    input_known = output_known = True
    responses_seen = 0

    async def on_request(message):
        nonlocal calls
        body = json.loads(message.content)
        expected_model = request['model'].removeprefix('openai/')
        if body.get('model') != expected_model:
            raise ValueError('MiniAgent attempted a model outside the platform contract')
        for name in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
            if body.get(name, request['max_tokens']) > request['max_tokens']:
                raise ValueError('native call exceeds HG_AGENT_MAX_OUTPUT_TOKENS')
        # UTF-8 bytes provide a conservative prompt bound without introducing a
        # model-specific tokenizer. It may refuse a prompt the model could fit.
        if len(message.content) > request['context_window'] - request['max_tokens']:
            raise ValueError('native request exceeds conservative context byte bound')
        calls += 1
        record({'model_request': {'call': calls, 'model': body.get('model'),
                                  'path': message.url.path}})

    async def on_response(response):
        nonlocal input_tokens, output_tokens, input_known, output_known, responses_seen
        await response.aread()
        responses_seen += 1
        try:
            data = response.json()
        except ValueError:
            data = {}
        usage = data.get('usage') or {}
        in_value = usage.get('prompt_tokens', usage.get('input_tokens'))
        out_value = usage.get('completion_tokens', usage.get('output_tokens'))
        if isinstance(in_value, int): input_tokens += in_value
        else: input_known = False
        if isinstance(out_value, int): output_tokens += out_value
        else: output_known = False
        choices = data.get('choices') or []
        if choices:
            reply = (choices[0].get('message') or {}).get('content') or ''
        else:
            reply = ''.join(content.get('text', '') for item in data.get('output') or []
                            if item.get('type') == 'message' for content in item.get('content') or [])
        record({'reply': reply, 'model_response': {'status': response.status_code,
                'usage': usage, 'id': data.get('id')}})

    base = request['base_url'] or 'http://miniagent.mock/v1'
    transport = httpx.MockTransport(mock_response) if request['backend'] == 'mock' else None
    http = httpx.AsyncClient(transport=transport, trust_env=False,
                            timeout=request['request_timeout'],
                            event_hooks={'request': [on_request], 'response': [on_response]})
    client = AsyncOpenAI(api_key=request['api_key'], base_url=base,
                         max_retries=0, http_client=http)

    def get_client(base_url):
        if base_url.rstrip('/') != base.rstrip('/'):
            raise ValueError('side model endpoint differs from the platform endpoint')
        return client
    llm._get_client = get_client

    original_writer = native_trace.TraceWriter
    class ObservedWriter(original_writer):
        def event(self, kind, **payload):
            super().event(kind, **payload)
            if kind == 'tool_call':
                args = payload.get('args') or {}
                name = payload.get('tool') or ''
                result = payload.get('result') or ''
                observation = {'kind': observation_kind(name), 'returncode': None,
                               'output_chars': len(result), 'truncated': False,
                               'output': result}
                if name == 'shell':
                    try:
                        value = json.loads(result)
                        observation.update(returncode=value.get('exit_code'),
                                           output_chars=len(value.get('output') or ''),
                                           truncated=bool(value.get('truncated')),
                                           output=value.get('output') or '',
                                           timed_out=bool(value.get('timed_out')))
                    except (ValueError, AttributeError):
                        pass
                record({'step': payload.get('turn'),
                        'command': args.get('command', name) if isinstance(args, dict) else name,
                        'tool': name, 'args': args, 'observation': observation})
            # Retain the native dialect too: methods can inspect hooks, retries,
            # skills and runtime events that have no platform summary field.
            record({'miniagent_event': {'kind': kind, **payload}})
    native_trace.TraceWriter = ObservedWriter

    try:
        from runtime.agent import run
        result = await run(question=request['prompt'], runs_root=request['runs_root'],
                           max_turns=request['max_turns'], workspace_dir=root,
                           working_dir=Path(request['workdir']),
                           llm_config={'api_key': request['api_key'], 'base_url': base,
                                       'model_name': request['model'], 'side_model': request['model'],
                                       'side_base_url': base, 'max_tokens': request['max_tokens'],
                                       'llm_max_retries': 1}, web_config={})
        Path(request['result']).write_text(json.dumps({'answer': result.get('answer'),
             'stop_reason': result.get('stop_reason')}, ensure_ascii=False), encoding='utf-8')
    finally:
        usage = {'calls': calls, 'tokens_complete': responses_seen == calls and input_known and output_known}
        if responses_seen == calls and input_known: usage['input_tokens'] = input_tokens
        if responses_seen == calls and output_known: usage['output_tokens'] = output_tokens
        record({'usage': usage})
        await client.close()
        for child in reversed(_children(os.getpid())):
            try: os.kill(child, signal.SIGKILL)
            except ProcessLookupError: pass
        reap_deadline = loop.time() + 1
        while loop.time() < reap_deadline:
            try:
                waited, _ = os.waitpid(-1, os.WNOHANG)
                if waited == 0:
                    await asyncio.sleep(0.01)
            except ChildProcessError:
                break


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        raise SystemExit(1)
