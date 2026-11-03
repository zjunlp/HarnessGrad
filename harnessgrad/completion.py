"""Experiment completion is separate from the last valid score.

An H0 measurement survives method failure. That does not mean the requested
experiment finished, and graceful method stopping is different from a failure.
"""
from __future__ import annotations


def describe(curve, *, requested_rounds, failed_steps=(), method_stopped=False):
    requested = max(0, int(requested_rounds))
    valid = [p for p in curve if p.get('measured_by_platform') is True
             and p.get('score') is not None and not p.get('unmeasured')
             and p.get('n_scored', 1) != 0]
    completed_ids = {p['round'] for p in valid if isinstance(p.get('round'), int) and p['round'] > 0}
    unresolved = sorted({int(row['round']) for row in failed_steps
                         if isinstance(row.get('round'), int) and row['round'] not in completed_ids})
    coverage = bool(valid) and all(not any(p.get(k) for k in
        ('invalid', 'unmeasured', 'harness_failed', 'timeouts')) and p.get('n_scored', 1) != 0
        for p in curve)
    completed = len(completed_ids)
    if unresolved or not coverage:
        status, reason = 'incomplete', 'method or measurement failed; see failed_steps and per-task evidence'
    elif completed >= requested:
        status, reason = 'complete', 'requested improvement measurements completed'
    elif method_stopped:
        status, reason = 'early_stop', 'method explicitly stopped before its maximum round budget'
    else:
        status, reason = 'incomplete', 'requested improvement rounds did not complete'
    return {'status': status, 'requested_rounds': requested, 'completed_rounds': completed,
            'reason': reason, 'failed_rounds': unresolved, 'coverage_complete': coverage}
