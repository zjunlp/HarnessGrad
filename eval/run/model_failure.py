"""Opt-in HTTP interference and terminal provider-failure classification."""
import os
import re

from data.scaffolds.runtime_extensions import validate_model_failure_policy


def enabled(verifier):
    if 'model_failure_policy' not in (verifier or {}):
        return False
    validate_model_failure_policy(verifier['model_failure_policy'])
    return True


def admit(*, sandbox, setup, env):
    if (not sandbox or (env or {}).get('kind', 'files') != 'files'
            or (setup or {}).get('workers') is not None
            or (os.environ.get('HG_AGENT_BACKEND', 'mock') != 'mock'
                and (env or {}).get('network') != 'none')):
        raise ValueError('model_failure_policy v1 requires sandboxed files without workers and an isolated live model route')


def failure(result, policy=None):
    if result.get('invalid'):
        return None
    report = (result.get('runtime_services') or {}).get('agent_model_http') or {}
    statuses = (policy or {}).get('invalidate_on_http_status', [])
    if report.get('version') == 1:
        for index, call in enumerate(report.get('calls', [])):
            if (call.get('operation') in ('chat.completions', 'completions', 'responses')
                    and call.get('status') in statuses):
                # The observer has matched final response headers to an inference
                # request. A partial body or another broken connection cannot
                # erase that status. This policy excludes interference; it does
                # not claim that the error caused the Harness to terminate.
                status = call['status']
                return {'stage': 'harness model call', 'service': 'agent model',
                        'detail': f'Platform observed inference HTTP {status}; configured trial validity policy excludes this trial regardless of recovery or task score',
                        'http_status': status, 'operation': call['operation'], 'call_index': index,
                        'reason': 'configured_http_status', 'policy': 'model_failure_policy.v1'}

    # Keep the existing terminal-failure rule when no configured status matched.
    if (not result.get('exit_code')
            or (result.get('verdict') or {}).get('passed')):
        return None
    if not report.get('complete'):
        return None
    lines = (result.get('stderr') or '').strip().splitlines()
    terminal = lines[-1] if lines else ''
    # Independent requests can complete out of order, and a failed future can
    # raise after another request succeeds. Response order does not prove recovery.
    for call in reversed(report.get('calls', [])):
        if call['operation'] == 'other' or not call.get('complete'):
            continue
        status = call['status']
        if status != 429 and not 500 <= status <= 599:
            continue
        specific = 'RateLimitError' if status == 429 else 'InternalServerError'
        recognized = re.search(r'\b' + specific + r'\b', terminal)
        if not recognized and re.search(r'\b' + str(status) + r'\b', terminal):
            recognized = re.search(r'\b(APIStatusError|HTTPStatusError|HTTPError)\b', terminal)
        if recognized:
            return {'stage': 'harness model call', 'service': 'agent model',
                    'detail': f'Unrecovered model HTTP {status} followed by terminal {recognized.group(0)} (exit {result["exit_code"]})',
                    'http_status': status, 'policy': 'model_failure_policy.v1'}
    return None
