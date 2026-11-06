"""Bounded, read-only console access to evidence receipts."""
import json
import os
import re
import threading

from harnessgrad import evidence, evidence_export
from harnessgrad.receipt_verification import ReceiptStore, ReceiptLimitExceeded, safe_id


def _setting(name, default):
    value = os.environ.get('HG_CONSOLE_EVIDENCE_' + name, str(default))
    if not re.fullmatch(r'[1-9][0-9]{0,8}', value):
        raise ValueError('invalid evidence limit')
    return int(value)


try:
    LIMITS = {'max_runs': _setting('MAX_RUNS', 500),
              'max_input_bytes': _setting('MAX_INPUT_BYTES', 64 * 1024 * 1024),
              'max_output_bytes': _setting('MAX_OUTPUT_BYTES', 32 * 1024 * 1024),
              'max_concurrent': _setting('MAX_CONCURRENT', 2)}
except ValueError:
    # Keep other Console features usable, but never substitute more permissive
    # limits for an invalid deployment configuration.
    LIMITS = None
_GATE = threading.BoundedSemaphore(LIMITS['max_concurrent']) if LIMITS else None


def _encode(value):
    chunks, size = [], 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':')).iterencode(value):
        data = chunk.encode('utf-8')
        size += len(data)
        if size > LIMITS['max_output_bytes']:
            raise ReceiptLimitExceeded()
        chunks.append(data)
    return b''.join(chunks)


def _number(value):
    return value if evidence.finite_number(value) and abs(value) <= 1e15 else None


def catalog(root):
    store = ReceiptStore(root, max_runs=LIMITS['max_runs'], max_total_bytes=LIMITS['max_input_bytes'])
    try:
        if any(not safe_id(n) or evidence_export._SECRET.search(n) for n in store.names):
            raise ValueError('unsafe inventory')
        rows = []
        for name in store.names:
            run = store.load_run(name)
            attempt = evidence_export._attempt(store, run, {})
            meta = run['meta'] or {}
            config = meta.get('config') if isinstance(meta.get('config'), dict) else {}
            planned = config.get('run') if isinstance(config.get('run'), dict) else {}
            rounds = []
            for number in store.measured(run):
                if number > 9007199254740991:
                    raise ReceiptLimitExceeded()
                point = run['points'][number]
                identity = point.get('identity') or {}
                sha = identity.get('harness_sha')
                sha = sha if isinstance(sha, str) and re.fullmatch(r'(?:sha256:)?[0-9a-fA-F]{40,64}', sha) else None
                kind = point.get('score_kind')
                kind = kind if kind in ('accuracy', 'score', 'reward', 'pass_rate', 'exam', 'training') else None
                rounds.append({'round': number, 'score': _number(point.get('score')), 'score_kind': kind, 'harness_sha': sha})
            rows.append({'run_id': name, 'status': attempt['status'], 'rounds': rounds,
                         'budget': {k: _number(planned.get(k)) for k in ('rounds', 'trials')}})
        store.recheck()
        return _encode({'runs': rows, 'scope': {'kind': 'configured_runs_root', 'run_count': len(rows)}, 'limits': LIMITS})
    finally:
        store.close()


def export(root, manifest):
    return _encode(evidence_export.build_export(manifest, root, max_runs=LIMITS['max_runs'],
                                                max_total_bytes=LIMITS['max_input_bytes']))


def execute(root, manifest=None, *, is_export=False):
    if _GATE is None:
        return 503, {'error': 'evidence-unavailable'}
    if not _GATE.acquire(blocking=False):
        return 429, {'error': 'evidence-busy'}
    try:
        try:
            return 200, export(root, manifest) if is_export else catalog(root)
        except ReceiptLimitExceeded:
            return 413, {'error': 'evidence-limit-exceeded'}
        except evidence.EvidenceError:
            return 400 if is_export else 503, {'error': 'invalid-manifest' if is_export else 'evidence-unavailable'}
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            return (400, {'error': 'invalid-manifest'}) if is_export else (503, {'error': 'evidence-unavailable'})
        except OSError:
            return 503, {'error': 'evidence-unavailable'}
    finally:
        _GATE.release()
