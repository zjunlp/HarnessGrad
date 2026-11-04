"""Identity-bound AppWorld H0 attempt history and derived task coverage.

No process is launched here. Progress and curves are indexes, never substitutes
for a missing official verdict. Process failures and raw verdicts are separate.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA = 'harnessgrad.appworld-campaign.v2'
SELECTION_SCHEMA = 'harnessgrad.appworld-selection.v2'
IDENTITY_SCHEMA = 'harnessgrad.appworld-measurement.v2'
POLICY = 'official-verdict-and-driver-timeout-separated-v1'
POOLS = ('train', 'dev', 'test_normal', 'test_challenge')


class RecoveryError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RecoveryError(f'unreadable receipt {path}: {type(exc).__name__}') from exc
    if not isinstance(value, dict):
        raise RecoveryError(f'expected object: {path}')
    return value


def valid_score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate_identity(identity):
    if not isinstance(identity, dict) or identity.get('schema') != IDENTITY_SCHEMA:
        raise RecoveryError('missing v2 measurement identity; legacy identity needs review')
    required = {'schema', 'dataset', 'harness', 'pool', 'side', 'source_sha256',
                'pool_ids_sha256', 'scorer_sha256', 'platform_commit', 'harness_sha256',
                'manifest_sha256', 'runtime_sha256', 'model', 'budget', 'policy'}
    if set(identity) != required or identity['dataset'] != 'appworld' or identity['pool'] not in POOLS:
        raise RecoveryError('invalid measurement identity fields')
    for key in required:
        if key.endswith('_sha256'):
            value = identity[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise RecoveryError(f'unknown or invalid identity: {key}')
    if not isinstance(identity['harness'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', identity['harness']):
        raise RecoveryError('invalid harness name')
    if not isinstance(identity['platform_commit'], str) or not re.fullmatch(r'[a-f0-9]{40}', identity['platform_commit']):
        raise RecoveryError('platform commit identity missing')
    if identity['side'] != ('train' if identity['pool'] == 'train' else 'eval'):
        raise RecoveryError('pool/side identity mismatch')
    if identity['policy'] != POLICY:
        raise RecoveryError('unknown measurement policy')
    model = identity['model']
    if not isinstance(model, dict) or set(model) != {'backend', 'name', 'endpoint', 'weights_sha256', 'tokenizer_sha256', 'deployment_sha256'}:
        raise RecoveryError('model identity must bind weights, tokenizer and deployment, not alias alone')
    if model['backend'] != 'openai' or model['endpoint'] not in ('http://127.0.0.1:8001/v1', 'http://127.0.0.1:8002/v1') or not model['name']:
        raise RecoveryError('invalid local model binding')
    for key in ('weights_sha256', 'tokenizer_sha256', 'deployment_sha256'):
        value = model[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise RecoveryError(f'model identity unknown: {key}')
    budget = identity['budget']
    if not isinstance(budget, dict) or set(budget) != {'deadline_s', 'request_timeout_s', 'output_tokens', 'trials', 'seed', 'jobs', 'native_settings', 'context_policy'}:
        raise RecoveryError('incomplete execution budget identity')
    for key in ('deadline_s', 'request_timeout_s', 'output_tokens', 'jobs'):
        if type(budget[key]) is not int or budget[key] <= 0:
            raise RecoveryError(f'invalid budget: {key}')
    if type(budget['trials']) is not int or budget['trials'] != 1 or type(budget['seed']) is not int:
        raise RecoveryError('H0 recovery requires one trial and an explicit integer seed')
    if budget['context_policy'] != 'native-defaults' or not isinstance(budget['native_settings'], dict):
        raise RecoveryError('native context policy must be explicit')
    allowed = {'HG_AGENT_MODEL_CONTEXT_CAPACITY', 'HG_AGENT_MODEL_CONTEXT_CAPACITY_SOURCE',
               'HG_AGENT_STOP_MARGIN_S', 'HG_AGENT_OPENCLAW_TIMEOUT_S',
               'HG_AGENT_DSH_TIMEOUT_S', 'HG_MINISWE_STEP_LIMIT', 'HG_MINISWE_TIMEOUT_S',
               'HG_AGENT_MAX_STEPS', 'HG_AGENT_MAX_TURNS', 'HG_AGENT_MAX_REQUEST_BYTES'}
    if set(budget['native_settings']) - allowed or any(not isinstance(v, str) or not v for v in budget['native_settings'].values()):
        raise RecoveryError('unsupported native settings; do not inherit unbound budgets')
    if 'HG_AGENT_MODEL_CONTEXT_CAPACITY' in budget['native_settings'] and 'HG_AGENT_MODEL_CONTEXT_CAPACITY_SOURCE' not in budget['native_settings']:
        raise RecoveryError('explicit model capacity requires its source')
    return identity


def selection_identity(selection, harness, pool):
    try:
        expected = selection['identities'][f'{harness}:{pool}']
    except KeyError as exc:
        raise RecoveryError(f'missing reviewed identity: {harness}:{pool}') from exc
    validate_identity(expected)
    if expected['harness'] != harness or expected['pool'] != pool:
        raise RecoveryError('review identity belongs to another harness/pool')
    return expected


def _run_dir(receipt):
    candidates = sorted(p for p in (receipt / 'runs').glob('*') if p.is_dir())
    if len(candidates) > 1:
        raise RecoveryError(f'ambiguous run directories: {receipt}')
    return candidates[0] if candidates else None


def discover_attempts(status):
    """Use attempts, or read legacy pointers; discovery never requires exit.json."""
    rows = list(status.get('attempts') or [])
    if status.get('schema') != SCHEMA:
        rows += list(status.get('completed') or []) + list(status.get('inherited_completed') or [])
    current = status.get('current') or {}
    if current.get('receipt'):
        rows.append(current)
    elif current.get('command'):
        cmd = current['command']
        try:
            root = Path(cmd[cmd.index('--receipts-root') + 1])
        except (ValueError, IndexError, TypeError) as exc:
            raise RecoveryError('current attempt has no receipt root') from exc
        candidates = [p for p in sorted(root.iterdir()) if p.is_dir() and
                      ((p / 'launch.json').is_file() or (p / 'runs').is_dir() or (p / 'exit.json').is_file())] if root.exists() else []
        if len(candidates) > 1:
            raise RecoveryError('ambiguous interrupted action root')
        if candidates:
            rows.append({**current, 'receipt': str(candidates[0])})
    unique = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get('receipt'):
            raise RecoveryError('attempt entry has no receipt path')
        if not row.get('harness') or row.get('pool') not in POOLS:
            raise RecoveryError('attempt entry has invalid harness/pool')
        path = str(Path(row['receipt']).resolve())
        pointer = {k: row[k] for k in ('harness', 'pool')}
        pointer['receipt'] = path
        if path in unique and unique[path] != pointer:
            raise RecoveryError('one receipt is assigned to different harness/pool pairs')
        unique[path] = pointer
    return list(unique.values())


def _task_objects(directory):
    rows = {}
    if directory.exists():
        for path in sorted(directory.glob('*.json')):
            row = read(path)
            tid = row.get('task_id')
            if not isinstance(tid, str) or not tid or tid in rows:
                raise RecoveryError(f'missing/duplicate task identity in {directory}')
            rows[tid] = (row, path)
    return rows


def read_attempt(pointer, expected, cohort, selection_sha256):
    receipt = Path(pointer['receipt'])
    result = {**pointer, 'identity_status': 'unverified', 'issues': [], 'tasks': {}, 'evidence': {}}
    if not receipt.is_dir():
        result['issues'].append('receipt_missing')
        return result
    # A live launcher holds this file. Never race an active process or mistake a
    # transient observation timeout for a stopped run.
    lock = receipt / '.attempt.lock'
    if lock.exists():
        import fcntl
        with lock.open('rb') as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecoveryError(f'attempt is still live: {receipt}') from exc
    launch_path = receipt / 'launch.json'
    launch = read(launch_path) if launch_path.exists() else {}
    if launch.get('schema') != 'harnessgrad.appworld-launch.v2' or launch.get('selection_sha256') != selection_sha256:
        result['issues'].append('launch_selection_identity_mismatch')
    identity = launch.get('measurement_identity')
    try:
        validate_identity(identity)
    except RecoveryError:
        result['issues'].append('measurement_identity_missing_or_invalid')
    else:
        if identity != expected or launch.get('measurement_identity_sha256') != digest(identity):
            result['issues'].append('measurement_identity_mismatch')
        else:
            result['identity_status'] = 'matched'
    selected = launch.get('selected')
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)) or set(selected) - set(cohort):
        result['issues'].append('selected_tasks_missing_or_outside_cohort')
        selected = []
    result['selected'] = selected
    run = _run_dir(receipt)
    if run is None:
        result['issues'].append('run_directory_missing')
        return result
    if run.is_symlink() or run.name != launch.get('run_id'):
        result['issues'].append('run_directory_identity_mismatch')
    meta_path = run / 'run_meta.json'
    if meta_path.exists():
        meta = read(meta_path)
        cfg = meta.get('config') or {}
        actual = {'model': {k: (cfg.get('agent') or {}).get(k) for k in ('backend', 'model', 'base_url')},
                  'seed': str((cfg.get('run') or {}).get('seed')), 'trials': meta.get('trials'),
                  'jobs': meta.get('jobs'), 'rounds': (cfg.get('run') or {}).get('rounds'),
                  'side': (cfg.get('run') or {}).get('side'),
                  'harness': (meta.get('harness_source') or {}).get('submission_sha256')}
        wanted = {'model': {'backend': expected['model']['backend'], 'model': expected['model']['name'], 'base_url': expected['model']['endpoint']},
                  'seed': str(expected['budget']['seed']), 'trials': 1, 'jobs': expected['budget']['jobs'],
                  'rounds': 0, 'side': expected['side'], 'harness': expected['harness_sha256']}
        if actual != wanted or meta.get('run_id') != launch.get('run_id') or meta.get('dataset') != 'appworld':
            result['issues'].append('runtime_identity_mismatch')
    else:
        result['issues'].append('runtime_identity_missing')
    progress = _task_objects(run / 'progress/round-0')
    verdicts = _task_objects(run / 'verdicts/round-0')
    journals = _task_objects(run / 'task-verdicts/round-0')
    starts = _task_objects(run / 'task-starts/round-0')
    for tid, (marker, path) in starts.items():
        if (path.resolve() != path.absolute() or marker.get('schema') != 'harnessgrad.task-start.v1'
                or marker.get('run_id') != run.name or marker.get('round') != 0):
            result['issues'].append(f'task_start_binding_invalid:{tid}')
        result['evidence'][str(path)] = file_hash(path)
    curve_scores = {}
    curve = run / 'curve.jsonl'
    if curve.exists():
        for line in curve.read_text().splitlines():
            if not line.strip():
                continue
            try:
                point = json.loads(line)
            except ValueError as exc:
                raise RecoveryError(f'malformed curve: {curve}') from exc
            if point.get('round') != 0:
                raise RecoveryError('only H0 curves can enter H0 recovery')
            for tid, score in (point.get('per_task') or {}).items():
                if tid in curve_scores and curve_scores[tid] != score:
                    raise RecoveryError('conflicting H0 curve scores')
                curve_scores[tid] = score
    started = set(starts)
    events = run / 'events.jsonl'
    if events.exists():
        for line in events.read_text().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise RecoveryError(f'incomplete event log needs review: {events}') from exc
            if event.get('round') == 0 and event.get('kind') == 'task' and event.get('task'):
                started.add(event['task'])
    all_ids = set(progress) | set(verdicts) | set(journals) | set(curve_scores) | started
    if all_ids - set(selected):
        result['issues'].append('task_evidence_outside_selected_subset')
    for tid in sorted(all_ids & set(cohort)):
        p, pp = progress.get(tid, ({}, None))
        v, vp = verdicts.get(tid, ({}, None))
        journal_path = None
        if tid in journals or 'verdict_receipt' in p:
            from eval.observe import task_verdicts
            try:
                journal, journal_path = task_verdicts.load(run, 0, tid, p.get('verdict_receipt'))
                if len(journal['trials']) != 1:
                    raise ValueError('H0 recovery requires exactly one journaled trial')
                trial = journal['trials'][0]
                native = trial.get('verdict')
                if not isinstance(native, dict):
                    raise ValueError('journal has no actual verifier result')
                if v and any(v.get(key) != native.get(key) for key in ('score', 'kind', 'evidence', 'invalid', 'unmeasured')):
                    result['issues'].append(f'journal_final_verdict_conflict:{tid}')
                if not v:
                    v = {**native, 'task_id': tid, 'round': 0}
                from eval.observe.tracing import process_outcome
                process_fields = {'exit_code': trial.get('exit_code'),
                                  'process_outcome': process_outcome(trial.get('exit_code'), trial.get('stderr'),
                                                                     wrapper=bool(trial.get('sandbox')))}
                if any(key in p and p[key] != value for key, value in process_fields.items()):
                    result['issues'].append(f'journal_progress_process_conflict:{tid}')
                p = {'task_id': tid, 'round': 0, **p, **process_fields}
            except (OSError, ValueError, TypeError) as exc:
                result['issues'].append(f'journal_verdict_invalid:{tid}:{exc}')
        evidence = v.get('evidence') or {}
        if not isinstance(evidence, dict):
            result['issues'].append(f'malformed_verdict_evidence:{tid}')
            evidence = {}
        if p and (p.get('round') != 0 or p.get('task_id') not in (None, tid)):
            result['issues'].append(f'progress_task_round_mismatch:{tid}')
        raw = v.get('score')
        official = (not v.get('invalid') and not v.get('unmeasured') and
                    v.get('kind') == 'command' and v.get('round') == 0 and
                    evidence.get('task_id') == tid and evidence.get('measured') is True and
                    evidence.get('source_split') == pointer['pool'] and
                    evidence.get('metric') == 'appworld.task_goal_completion' and
                    type(evidence.get('num_tests')) is int and evidence['num_tests'] > 0 and valid_score(raw))
        reason = p.get('invalid') or v.get('invalid') or v.get('unmeasured')
        process = p.get('process_outcome')
        timeout = (reason == 'timeout' or isinstance(reason, dict) and reason.get('outcome') == 'timeout'
                   or process == 'platform_timeout')
        # Official artifact verdicts remain independent of a nonzero harness exit.
        # A driver-assigned timeout zero is kept in its own class and own mean.
        status = 'invalid' if reason and not timeout else 'timeout' if timeout else 'measured' if official else 'unmeasured'
        score = raw if status == 'measured' else None
        item = {'status': status, 'score': score, 'raw_official_score': raw if official else None,
                'official_verdict_present': official, 'process_outcome': process,
                'exit_code': p.get('exit_code'), 'harness_failed': type(p.get('exit_code')) is int and p['exit_code'] != 0,
                'timeout': timeout or process == 'platform_timeout', 'progress_score': p.get('score'),
                'driver_timeout_score': 0.0 if timeout and curve_scores.get(tid) == 0 else None,
                'reason': reason if reason else None if official else 'official_verdict_missing',
                'receipt': str(receipt)}
        if official and tid in curve_scores and curve_scores[tid] != raw and not timeout:
            result['issues'].append(f'curve_verdict_conflict:{tid}')
        if official and valid_score(p.get('score')) and p['score'] != raw and not timeout:
            result['issues'].append(f'progress_verdict_conflict:{tid}')
        result['tasks'][tid] = item
        for path in (pp, vp, journal_path):
            if path: result['evidence'][str(path)] = file_hash(path)
    for path in (launch_path, meta_path, curve, events):
        if path.exists(): result['evidence'][str(path)] = file_hash(path)
    if result['issues']:
        result['identity_status'] = 'unverified'
    return result


def recover(selection, old=None):
    old = old or {}
    issues = []
    if selection.get('schema') != SELECTION_SCHEMA:
        raise RecoveryError('selection requires reviewed v2 identities')
    if old and old.get('schema') != SCHEMA:
        issues.append('legacy_status_requires_review')
    if old and old.get('selection_sha256') != digest(selection):
        issues.append('selection_identity_mismatch')
    pointers = discover_attempts(old)
    attempts = []
    coverage = {}
    for key, expected in selection.get('identities', {}).items():
        harness, pool = key.split(':', 1)
        expected = selection_identity(selection, harness, pool)
        info = selection['pools'][pool]
        cohort = info['selected']
        if not cohort or len(cohort) != len(set(cohort)) or type(info.get('eligible')) is not int or info['eligible'] < len(cohort):
            raise RecoveryError('invalid frozen cohort')
        pair_attempts = [read_attempt(p, expected, cohort, digest(selection)) for p in pointers if p['harness'] == harness and p['pool'] == pool]
        for attempt in pair_attempts:
            previous = next((a for a in old.get('attempt_evidence', []) if a.get('receipt') == attempt['receipt']), None)
            if previous:
                for path, old_hash in previous.get('evidence', {}).items():
                    if attempt['evidence'].get(path) != old_hash:
                        attempt['issues'].append('previously_recorded_evidence_changed')
                        attempt['identity_status'] = 'unverified'
        attempts.extend(pair_attempts)
        tasks = {}
        for tid in cohort:
            observed = [a['tasks'][tid] for a in pair_attempts if tid in a['tasks']]
            matched = [a['tasks'][tid] for a in pair_attempts if tid in a['tasks'] and a['identity_status'] == 'matched']
            if len(observed) > 1:
                tasks[tid] = {'status': 'unmeasured', 'reason': 'multiple_attempts_require_review', 'attempts': observed}
                issues.append(f'duplicate_task_attempt:{key}:{tid}')
            elif len(matched) == 1:
                tasks[tid] = matched[0]
            elif observed:
                tasks[tid] = {'status': 'unmeasured', 'reason': 'identity_unverified', 'attempts': observed}
            else:
                tasks[tid] = {'status': 'not_recorded', 'reason': 'no_task_receipt'}
        scores = {tid: t['score'] for tid, t in tasks.items() if t['status'] == 'measured'}
        counts = {status: sum(t['status'] == status for t in tasks.values()) for status in ('measured', 'invalid', 'timeout', 'unmeasured', 'not_recorded')}
        driver_scores = list(scores.values()) + [t['driver_timeout_score'] for t in tasks.values() if t['status'] == 'timeout' and t.get('driver_timeout_score') is not None]
        coverage[key] = {'harness': harness, 'pool': pool, 'eligible': info['eligible'], 'selected': len(cohort),
                         **counts, 'tasks': tasks, 'per_task': scores,
                         'harness_nonzero_exit': sum(bool(t.get('harness_failed')) for t in tasks.values()),
                         'raw_official_verdicts': sum(bool(t.get('official_verdict_present')) for t in tasks.values()),
                         'official_verdict_mean': sum(scores.values()) / len(scores) if scores else None,
                         'official_verdict_denominator': len(scores),
                         'mean_population': 'matched official verdicts without platform invalid; process exit does not exclude; timeouts reported separately',
                         'driver_timeout_inclusive_mean': sum(driver_scores) / len(driver_scores) if driver_scores else None,
                         'driver_timeout_inclusive_denominator': len(driver_scores),
                         'attempt_coverage_complete': counts['not_recorded'] == 0,
                         'measurement_complete': counts['measured'] == len(cohort),
                         'remaining_task_ids': [tid for tid, t in tasks.items() if t['status'] == 'not_recorded']}
        for a in pair_attempts:
            issues.extend(f'{a["receipt"]}:{i}' for i in a['issues'])
    known = set(selection.get('identities', {}))
    if any(f'{p["harness"]}:{p["pool"]}' not in known for p in pointers):
        issues.append('attempt_outside_reviewed_pairs')
    # Invalid/timeout/unfinished attempts need inspection, not an automatic retry.
    blocked = [key for key, c in coverage.items() if c['invalid'] or c['timeout'] or c['unmeasured']]
    return {'schema': SCHEMA, 'selection_sha256': digest(selection), 'policy': POLICY,
            'attempts': pointers, 'attempt_evidence': attempts, 'coverage': coverage,
            'completed_pools': [key for key, c in coverage.items() if c['measurement_complete']],
            'review_required_pools': blocked, 'issues': sorted(set(issues)),
            'execution_allowed': not issues and not blocked, 'current': None}


def legacy_audit(selection, status):
    """Inventory old pointers without promoting old progress to comparable scores."""
    attempts = discover_attempts(status)
    return {'schema': 'harnessgrad.appworld-legacy-audit.v1', 'execution_allowed': False,
            'issues': ['legacy_selection_and_receipts_require_identity_review'],
            'attempts': [{**a, 'exists': Path(a['receipt']).is_dir(),
                          'run_directories': [str(p) for p in sorted((Path(a['receipt'])/'runs').glob('*')) if p.is_dir()]}
                         for a in attempts],
            'completed_pools': [], 'selection_sha256': digest(selection),
            'score': None, 'note': 'attempt references only; no comparable coverage inferred'}
