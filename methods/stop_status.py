"""Structured non-retryable stops shared by isolated method code and the driver."""
SCHEMA = 'harnessgrad.terminal-stop.v1'
CODES = frozenset({'budget_exhausted', 'budget_usage_unknown',
                   'artifact_persistence_failed', 'terminal_status_invalid',
                   'task_budget_stopped'})
SCOPES = frozenset({'meta_session', 'meta_round', 'meta_run', 'task_run'})


def terminal_stop(payload):
    """Return only explicit machine status; ordinary error prose is never a signal."""
    if not isinstance(payload, dict):
        return None
    value = payload.get('terminal_stop')
    if value is None and isinstance(payload.get('method_reported'), dict):
        value = payload['method_reported'].get('terminal_stop')
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('code') not in CODES or value.get('retryable') is not False
            or value.get('scope', 'meta_session') not in SCOPES):
        return {'schema': SCHEMA, 'code': 'terminal_status_invalid',
                'retryable': False, 'scope': 'meta_session'}
    return {'schema': SCHEMA, 'code': value['code'], 'retryable': False,
            'scope': value.get('scope', 'meta_session')}


class TerminalStop(RuntimeError):
    def __init__(self, status):
        self.terminal_stop = terminal_stop({'terminal_stop': status})
        super().__init__(self.terminal_stop['code'])
