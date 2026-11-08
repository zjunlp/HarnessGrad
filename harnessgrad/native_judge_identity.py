"""JobBench's selected custom-local judge semantics, never credential identity.

Only model and base URL are read. API keys are transport secrets and must never
be inspected, hashed, persisted or used to select a measurement identity here.
"""
from __future__ import annotations
import hashlib
import ipaddress
import os
from urllib.parse import urlsplit, urlunsplit

PROFILE = 'jobbench-custom-local-http-v1'
REMEDY = 'prepare the JobBench native runtime again and use a new measurement/run'


def selection(environment=None):
    environment = os.environ if environment is None else environment
    return _selection(environment.get('HG_JUDGE_HARBOR_BASE_URL'),
                      environment.get('HG_JUDGE_HARBOR_MODEL'))


def normalize_endpoint(base):
    try:
        if (not isinstance(base, str) or not base or not base.isascii()
                or any(ord(c) <= 32 for c in base)):
            raise ValueError
        parsed = urlsplit(base)
        host = ipaddress.ip_address(parsed.hostname or '')
        if (parsed.scheme != 'http' or not host.is_loopback or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or '?' in base or '#' in base):
            raise ValueError
        port = parsed.port
        if port == 0:raise ValueError
        host_text = '[' + host.compressed + ']' if host.version == 6 else host.compressed
        authority = host_text + ((':' + str(port)) if port not in (None, 80) else '')
        return urlunsplit(('http', authority, parsed.path.rstrip('/'), '', ''))
    except (ValueError, TypeError):
        raise ValueError('JobBench judge requires a non-secret loopback HTTP URL without userinfo/query/fragment') from None


def _selection(base, model):
    canonical = normalize_endpoint(base)
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model):
        raise ValueError('JobBench judge requires an explicit model')
    return {'judge_base_url': canonical, 'judge_identity': {
        'version': 1, 'profile': PROFILE, 'model': model.strip(),
        'endpoint_sha256': hashlib.sha256(canonical.encode('ascii')).hexdigest()}}


def validate_identity(value):
    if (not isinstance(value, dict) or set(value) != {'version','profile','model','endpoint_sha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['profile'] != PROFILE
            or not isinstance(value['model'], str) or not value['model'] or value['model'] != value['model'].strip()
            or any(ord(c) < 32 for c in value['model']) or not isinstance(value['endpoint_sha256'], str)
            or len(value['endpoint_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['endpoint_sha256'])):
        raise ValueError('JobBench judge identity is missing or invalid; ' + REMEDY)
    return dict(value)


def admit_identity(value, environment=None):
    value = validate_identity(value)
    if value != selection(environment)['judge_identity']:
        raise ValueError('JobBench judge model or endpoint changed; ' + REMEDY)
    return value


def validate_selection(entry):
    value = validate_identity(entry.get('judge_identity'))
    expected = _selection(entry.get('judge_base_url'), value['model'])
    if entry.get('judge_base_url') != expected['judge_base_url'] or value != expected['judge_identity']:
        raise ValueError('JobBench prepared judge selection is invalid; ' + REMEDY)
    return expected


def admit_selection(entry, environment=None):
    expected = validate_selection(entry)
    admit_identity(expected['judge_identity'], environment)
    return expected


def public_summary(value):
    """Method-visible semantic profile; no endpoint, digest or transport secret."""
    selected = validate_identity(value)
    return {key:selected[key] for key in ('version','profile','model')}
