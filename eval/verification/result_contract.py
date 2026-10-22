"""Optional strict result interpretation. Legacy verdicts are unchanged."""
from __future__ import annotations

import math

from data.scaffolds.runtime_extensions import validate_result as validate


def apply(verdict, spec):
    if spec is None:
        return verdict
    validate(spec)
    evidence = verdict.get('evidence') or {}
    reason = verdict.get('unmeasured')
    if not isinstance(evidence, dict):
        reason = 'scorer evidence is not an object'
    elif evidence.get('measured') is False:
        reason = evidence.get('error') or 'scorer explicitly reported measured=false'
    score = verdict.get('score')
    if (type(score) not in (int, float) or not math.isfinite(score)
            or not spec['score_range'][0] <= score <= spec['score_range'][1]):
        reason = reason or 'scorer produced no finite score within declared range'
    if reason:
        return {**verdict, 'score': None, 'passed': False,
                'outcome': 'unmeasured', 'unmeasured': str(reason)}
    return verdict
