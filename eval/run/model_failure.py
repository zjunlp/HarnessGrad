"""Opt-in terminal HTTP provider-failure classification, independent of archives."""
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


def failure(result):
    # Successful output or recovery is positive evidence, even if earlier requests
    # failed. Never classify arbitrary nonzero exits or all HTTP 4xx as providers.
    if (result.get('invalid') or not result.get('exit_code')
            or (result.get('verdict') or {}).get('passed')):
        return None
    report = (result.get('runtime_services') or {}).get('agent_model_http') or {}
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
