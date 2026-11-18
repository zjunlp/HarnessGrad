"""Persistent method-attempt accounting and optional between-session admission.

This is not a provider gateway. Limits gate new method sessions; already admitted
requests may overshoot and must be recorded. Unreported costs stay unknown.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from methods.stop_status import SCHEMA, TerminalStop, terminal_stop


def _limit(value):
    if value is None or value == '':
        return None
    if not str(value).isascii() or not str(value).isdigit():
        raise ValueError('meta token limit must be a non-negative integer')
    return int(value)


def _stop(code, scope):
    return {'schema': SCHEMA, 'code': code, 'scope': scope, 'retryable': False}


class MethodAttempts:
    def __init__(self, run_dir, run_id, *, round_limit=None, run_limit=None):
        self.root = Path(run_dir)
        self.run_id = run_id
        self.limits = {'round': _limit(round_limit), 'run': _limit(run_limit)}
        self.path = self.root / 'method_attempts.json'

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / '.method-attempts.lock',
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.exists() or self.path.is_symlink():
                src = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(src) as stream:
                    state = json.load(stream)
                if (state.get('schema') != 'harnessgrad.method-attempts.v1'
                        or state.get('run_id') != self.run_id
                        or state.get('limits') != self.limits
                        or not isinstance(state.get('attempts'), list)):
                    raise ValueError('method attempt ledger identity or limits changed')
            else:
                state = {'schema': 'harnessgrad.method-attempts.v1',
                         'run_id': self.run_id, 'limits': self.limits, 'attempts': [],
                         'scope': 'method-session admission; not provider hard enforcement'}
            yield state
            out, name = tempfile.mkstemp(prefix='.method-attempts-', dir=self.root)
            try:
                with os.fdopen(out, 'w') as stream:
                    json.dump(state, stream, indent=2, allow_nan=False)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        finally:
            os.close(fd)

    def begin(self, number):
        if type(number) is not int or number < 1:
            raise ValueError('method attempt requires a positive round')
        try:
            with self._locked() as state:
                for row in state['attempts']:
                    if row.get('terminal_stop'):
                        raise TerminalStop(row['terminal_stop'])
                remaining = []
                for scope, limit in self.limits.items():
                    if limit is None:
                        continue
                    relevant = [r for r in state['attempts']
                                if scope == 'run' or r['round'] == number]
                    if any(type(r.get('observed_total_tokens')) is not int
                           or r['observed_total_tokens'] < 0 for r in relevant):
                        raise TerminalStop(_stop('budget_usage_unknown', 'meta_' + scope))
                    used = sum(r['observed_total_tokens'] for r in relevant)
                    remaining.append(limit - used)
                    if used >= limit:
                        raise TerminalStop(_stop('budget_exhausted', 'meta_' + scope))
                attempt = uuid.uuid4().hex
                state['attempts'].append({'id': attempt, 'round': number,
                    'state': 'admitted', 'observed_total_tokens': None,
                    'usage_coverage': 'unknown', 'terminal_stop': None,
                    'admitted_token_allowance': min(remaining) if remaining else None})
                return attempt
        except TerminalStop:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # No dispatch after an unreadable configured accounting boundary.
            raise TerminalStop(_stop('budget_usage_unknown', 'meta_run')) from exc

    def allowance(self, attempt):
        """Read the immutable allowance assigned at admission, without new admission."""
        with self._locked() as state:
            row = next(x for x in state['attempts'] if x['id'] == attempt)
            allowance = row.get('admitted_token_allowance')
            if allowance is not None and (type(allowance) is not int or allowance < 0):
                raise ValueError('invalid admitted token allowance')
            return allowance

    def round_usage(self, number):
        """Return the retained lower bound and coverage for previous attempts."""
        if type(number) is not int or number < 1:
            raise ValueError('method attempt requires a positive round')
        with self._locked() as state:
            rows = [row for row in state['attempts'] if row.get('round') == number]
            lower = 0
            complete = True
            for row in rows:
                total = row.get('observed_total_tokens')
                observed_lower = row.get('observed_total_tokens_lower_bound')
                if type(total) is int and total >= 0:
                    lower += total
                else:
                    complete = False
                    if type(observed_lower) is int and observed_lower >= 0:
                        lower += observed_lower
            return {'generation_tokens_lower_bound': lower, 'complete': complete,
                    'attempt_count': len(rows)}

    def finish(self, attempt, reply):
        usage = reply.get('method_usage') if isinstance(reply, dict) else None
        usage = usage if isinstance(usage, dict) else None
        total = usage.get('total_tokens') if isinstance(usage, dict) else None
        reported = reply.get('method_reported') or {}
        transport = reported.get('method_unknown_transport_attempts')
        if (type(total) is not int or total < 0
                or (isinstance(usage, dict) and usage.get('complete') is False)
                or (type(transport) is int and transport > 0)):
            total = None
        with self._locked() as state:
            row = next(x for x in state['attempts'] if x['id'] == attempt)
            if row['state'] != 'admitted':
                raise ValueError('method attempt already reconciled')
            lower = (usage or {}).get('observed_total_tokens_lower_bound')
            lower = lower if type(lower) is int and lower >= 0 else total
            row.update(state='returned', observed_total_tokens=total,
                       observed_total_tokens_lower_bound=lower,
                       usage_coverage='observed' if total is not None else 'unknown',
                       terminal_stop=terminal_stop(reply))
            # Reported fields are preserved individually, never guessed from total.
            row['reported_usage'] = {k: v for k, v in (usage or {}).items()
                                     if k in {'input', 'output', 'calls', 'total_tokens'}
                                     and (v is None or type(v) is int and v >= 0)}
            for scope, limit in self.limits.items():
                if limit is None:
                    continue
                selected = [r for r in state['attempts']
                            if scope == 'run' or r['round'] == row['round']]
                known = sum(r['observed_total_tokens'] for r in selected
                            if type(r.get('observed_total_tokens')) is int)
                complete = all(type(r.get('observed_total_tokens')) is int for r in selected)
                row[scope + '_observed_overshoot_tokens'] = max(0, known - limit) if complete else None
                lower_sum = sum(r.get('observed_total_tokens_lower_bound') or
                                r.get('observed_total_tokens') or 0 for r in selected)
                row[scope + '_observed_overshoot_tokens_lower_bound'] = max(0, lower_sum - limit)

    def stop(self, attempt, status):
        """Persist post-return archive failure without reconciling cost twice."""
        status = terminal_stop({'terminal_stop': status})
        if status is None:
            raise ValueError('terminal status is required')
        with self._locked() as state:
            row = next(x for x in state['attempts'] if x['id'] == attempt)
            row['terminal_stop'] = status
