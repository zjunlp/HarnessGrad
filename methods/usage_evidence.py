"""Allowlisted usage observations; canonical counters cannot reconstruct wire presence."""
import math
import re

SOURCES = frozenset({'pi-ai', 'deepseek-wire', 'openai-completions-wire'})
FIELDS = frozenset({'inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens',
                    'totalTokens', 'reasoningTokens'})
RAW = frozenset({'input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens',
                 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
                 'cache_read_input_tokens', 'cache_creation_input_tokens', 'reasoning_tokens',
                 'input', 'output', 'cacheRead', 'cacheWrite', 'total', *FIELDS})


def usage_evidence(payload):
    value = payload.get('usage_evidence') if isinstance(payload, dict) else None
    if (not isinstance(value, dict) or not isinstance(value.get('source'), str)
            or value['source'] not in SOURCES):
        return None
    raw = value.get('raw')
    if not isinstance(raw, dict):
        return None
    def number(v):
        if type(v) is int or v is None:
            return v
        return v if type(v) is float and math.isfinite(v) else None
    observed = {k: number(v) for k, v in raw.items() if k in RAW}
    for detail in ('prompt_tokens_details', 'completion_tokens_details'):
        if isinstance(raw.get(detail), dict):
            observed[detail] = {k: number(v) for k, v in raw[detail].items()
                                if k in {'cached_tokens', 'reasoning_tokens'}}
    supplied = value.get('fields') or {}
    fields = {k: supplied.get(k) if isinstance(supplied.get(k), str) and supplied[k] in {'observed', 'derived', 'unknown'}
              else 'unknown' for k in FIELDS} if isinstance(supplied, dict) else {k: 'unknown' for k in FIELDS}
    return {'source': value['source'], 'raw': observed, 'fields': fields}


def transport_identity(payload, *, credentials=()):
    payload = payload if isinstance(payload, dict) else {}
    blocked = set(credentials)
    def identity(value):
        return value if (isinstance(value, str) and value not in blocked
            and not value.startswith('sk-')
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', value)) else None
    return {k: identity(payload.get(k)) for k in
            ('session_id', 'attempt_id', 'request_id', 'retry_of')}
