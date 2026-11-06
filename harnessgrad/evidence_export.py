"""Portable, privacy-filtered evidence packs built from an explicit run directory.

Author annotations describe concerns and selection. Receipt projections describe
observations. Neither hashes nor a successful export establish authenticity,
statistical independence, or completeness outside the inventoried directory.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import tempfile

from harnessgrad import evidence, evidence_stats
from harnessgrad.receipt_verification import (
    DEFAULT_MAX_BYTES, ReceiptStore, canonical, claim_states, digest,
    measurement_problems, safe_id, verify_in_store,
)

SCHEMA = 'harnessgrad.evidence-export.v1'
_SECRET = re.compile(r'\b(?:sk[-_]|hf_|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{12,}')
_URL = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>]+')
_ASSIGNMENT = re.compile(r'(?i)\b(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+')
_COST_FIELDS = ('evaluation_trials', 'harness_tokens', 'harness_model_calls',
                'method_generation_tokens', 'method_generation_input', 'method_generation_output',
                'method_model_calls', 'method_cache_read_tokens', 'env_generation_tokens', 'wall_clock_s')
_IDENTITY_FIELDS = ('harness_name', 'harness_version', 'harness_sha', 'agent_model', 'agent_backend',
                    'method', 'method_model', 'framework_version', 'run_mode', 'jobs', 'trials',
                    'gpu_budget', 'max_steps', 'max_tokens', 'context_window', 'skill_sha')
_CONFIG_FIELDS = {
    'run': ('harness', 'dataset', 'side', 'sampling', 'rounds', 'trials', 'jobs', 'sandbox', 'mode'),
    'agent': ('model', 'backend', 'max_steps', 'max_tokens', 'context_window', 'temperature', 'top_p', 'timeout_s'),
    'method': ('model', 'max_tokens', 'timeout_s', 'max_attempts'),
    'sandbox': ('memory_mb', 'cpus', 'pids'),
}
_NUMERIC_FIELDS = frozenset(('jobs', 'trials', 'rounds', 'max_steps', 'max_tokens',
    'context_window', 'gpu_budget', 'temperature', 'top_p', 'timeout_s',
    'max_attempts', 'memory_mb', 'cpus', 'pids', 'seed', 'requested_rounds',
    'completed_rounds'))
_BOOLEAN_FIELDS = frozenset(('coverage_complete', 'sandbox'))


def _object(value):
    return value if isinstance(value, dict) else {}


def _annotation(value):
    if not isinstance(value, str):
        raise evidence.EvidenceError('annotation_must_be_text')
    if len(value) > 16000:
        raise evidence.EvidenceError('annotation_too_long')
    return _ASSIGNMENT.sub('[REDACTED]', _URL.sub('[URL WITHHELD]', _SECRET.sub('[REDACTED]', value)))


def _scalar(value):
    if value is None or type(value) is bool or evidence.finite_number(value):
        return value
    if isinstance(value, str) and len(value) <= 160:
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+/-]*', value) and '..' not in value and not _SECRET.search(value):
            return value
    return None


def _fields(obj, names):
    obj = _object(obj)
    def field(name):
        value = obj[name]
        if name in _NUMERIC_FIELDS:
            return value if evidence.finite_number(value) else None
        if name in _BOOLEAN_FIELDS:
            return value if type(value) is bool else None
        if name in ('image_digest', 'harness_sha', 'content_sha', 'skill_sha') and isinstance(value, str):
            if re.fullmatch(r'sha256:[0-9a-fA-F]{64}', value):
                return value
        return _scalar(value) if isinstance(value, str) else None
    return {name: field(name) for name in names if name in obj}


def _config(meta):
    config = _object(meta.get('config'))
    return {area: _fields(config[area], names) for area, names in _CONFIG_FIELDS.items()
            if area in config}


def _cost(point):
    return {key: value if evidence.finite_number(value) and value >= 0 else None
            for key, value in ((key, _object(point.get('cost')).get(key)) for key in _COST_FIELDS)}


def _count(point, name):
    direct = point.get('n_timeout' if name == 'timeouts' else 'n_' + name)
    if type(direct) is int and direct >= 0:
        return direct
    values = point.get(name)
    return len(values) if isinstance(values, (dict, list)) else None


def _point(point):
    tasks = _object(point.get('per_task'))
    numeric = all(evidence.finite_number(v) for v in tasks.values())
    result = {
        'run_id': point['run_id'], 'round': point['round'],
        'score': point.get('score') if evidence.finite_number(point.get('score')) else None,
        'identity': _fields(point.get('identity'), _IDENTITY_FIELDS),
        'side': _scalar(point.get('side')), 'score_kind': _scalar(point.get('score_kind')),
        'n_scored': len(tasks) if numeric else None,
        'recorded_n_scored': _scalar(point.get('n_scored')),
        'task_set_sha256': digest(canonical(sorted(tasks))),
        'task_scores': [tasks[k] for k in sorted(tasks)] if numeric else None,
        'sampling': _fields(point.get('sampling'), ('dataset', 'policy', 'seed')),
        'scoring_identity_sha256': _scalar(_object(point.get('scoring_identity')).get('sha256')),
        'environment': _fields(point.get('env'), ('kind', 'image_digest', 'placement')),
        'measured_by_platform': point.get('measured_by_platform') is True,
        'measurement_problems': measurement_problems(point),
        'coverage': {key: _count(point, key) for key in ('invalid', 'unmeasured', 'harness_failed', 'timeouts')},
        'complete_task_coverage': evidence_stats.complete(point),
        'reused_or_cached': evidence_stats.reused(point),
        'cost': _cost(point), 'trial_variation': evidence_stats.trial_variation(point),
    }
    rows = point.get('per_task_trials')
    if isinstance(rows, dict) and set(rows) == set(tasks) and all(
        isinstance(v, list) and all(evidence.finite_number(x) for x in v) for v in rows.values()
    ):
        result['task_trials'] = [rows[k] for k in sorted(rows)]
    return result


def _metadata(meta):
    meta = meta or {}
    return {**_fields(meta, ('run_id', 'dataset', 'harness', 'agent_model', 'agent_backend')),
            'platform': _fields(meta.get('platform'), ('content_sha', 'git_sha', 'git_commit')),
            'config': _config(meta),
            'completion': _fields(meta.get('completion'), ('status', 'requested_rounds', 'completed_rounds', 'coverage_complete'))}


def _attempt(store, run, authored):
    rounds = store.measured(run)
    completion = _object((run['meta'] or {}).get('completion'))
    status = 'measured' if rounds else 'invalid' if run['problems'] or run['points'] else 'unmeasured'
    reason = None if rounds else 'no_usable_measurement_receipt'
    source = 'receipts'
    if completion.get('status') in ('failed', 'stopped'):
        status, reason = completion['status'], 'recorded_completion_' + completion['status']
    elif completion.get('status') == 'incomplete' and completion.get('failed_rounds'):
        status, reason = 'failed', 'recorded_failed_rounds'
    if authored.get('status') in ('failed', 'stopped', 'invalid', 'unmeasured'):
        status, reason, source = authored['status'], _annotation(authored.get('reason', '')), 'author'
        if not reason:
            raise evidence.EvidenceError('missing_attempt_reason')
    elif authored.get('status') == 'measured' and status != 'measured':
        raise evidence.EvidenceError('declared_measurement_missing')
    claimed_rounds = authored.get('rounds', [])
    if any(r not in rounds for r in claimed_rounds):
        raise evidence.EvidenceError('declared_round_not_measured')
    result = {'run_id': run['run_id'], 'purpose': _annotation(authored.get('purpose', 'not supplied by author')),
              'status': status, 'status_source': source, 'rounds': rounds,
              'completion_status': _scalar(completion.get('status')),
              'unusable_rounds': sorted(set(run['points']) - set(rounds))}
    if reason:
        result['reason'] = reason
    return result


def _budget(run):
    points = list(run['points'].values())
    totals = {}
    finished = _object((run['meta'] or {}).get('completion')).get('status') in ('complete', 'early_stop')
    for key in _COST_FIELDS:
        values = [_cost(p)[key] for p in points]
        recorded = [v for v in values if v is not None]
        ambiguous = (key.startswith('method_') and any(
            p.get('steps_in_submission', 1 if p['round'] == 0 else None) != 1 for p in points))
        reused = any(evidence_stats.reused(p) and _cost(p)[key] not in (None, 0) for p in points)
        total = sum(recorded) if recorded and not ambiguous and not reused else None
        if total is not None and not evidence.finite_number(total):
            total = None
        totals[key] = {'recorded_sum': total, 'recorded_rounds': len(recorded),
                       'curve_rounds': len(points),
                       'coverage': 'all_curve_points' if values and len(recorded) == len(values) else 'partial_or_missing',
                       'reason': 'ambiguous_submission_cost' if ambiguous else 'reused_cost' if reused else None}
    return {'source': 'curve.cost; config.run for planned limits',
            'declared_limits': _fields(_object((run['meta'] or {}).get('config')).get('run'),
                                      ('rounds', 'trials', 'jobs')),
            'totals': totals, 'run_finished': finished,
            'unrecorded_spend_possible': True,
            'scope': 'recorded_curve_points_only; excludes work without a curve receipt'}


def _patch(store, run_id, number):
    name = f'diffs/round-{number}.patch'
    try:
        data = store.read(run_id, name)
        if data is None:
            return {'path': name, 'status': 'missing', 'sha256': None}
        return {'path': name, 'status': 'withheld_for_privacy', 'sha256': digest(data), 'bytes': len(data)}
    except FileNotFoundError:
        return {'path': name, 'status': 'missing', 'sha256': None}
    except (OSError, ValueError):
        return {'path': name, 'status': 'unreadable', 'sha256': None}


def _diff(left, right):
    changes = []
    for area, names in _CONFIG_FIELDS.items():
        a, b = _object(left.get(area)), _object(right.get(area))
        for name in names:
            if (name in a, a.get(name)) != (name in b, b.get(name)):
                changes.append({'field': f'{area}.{name}', 'before': a.get(name), 'after': b.get(name),
                                'before_recorded': name in a, 'after_recorded': name in b})
    return changes


def _comparison(base_run, base, run, point):
    changes = _diff(_config(base_run['meta'] or {}), _config(run['meta'] or {}))
    comparison = evidence_stats.compare_conditions(base_run, base, run, point)
    # Private configuration differences are flagged but their names/values are not copied.
    raw_changed = _object((base_run['meta'] or {}).get('config')) != _object((run['meta'] or {}).get('config'))
    delta = point['score'] - base['score']
    return {**comparison, 'score_delta': delta if evidence.finite_number(delta) else None,
            'interpretation': 'descriptive_difference; no causal_or_significance_claim',
            'diff': {'changed': bool(changes) or raw_changed or base['identity']['harness_sha'] != point['identity']['harness_sha'],
                     'config': changes, 'full_config_changed': raw_changed,
                     'harness_sha': {'before': _scalar(base['identity']['harness_sha']), 'after': _scalar(point['identity']['harness_sha'])},
                     'code_diff': 'see patch hash descriptors; patch text withheld'}}


def build_export(document, run_root, *, max_bytes=DEFAULT_MAX_BYTES, max_runs=None, max_total_bytes=None):
    """Discover every immediate entry; fill observations and verify every claim."""
    if not isinstance(document, dict) or evidence._shape_problems(document):
        raise evidence.EvidenceError('invalid_manifest_shape')
    for key in ('submission', 'requests', 'selection', 'budget'):
        if key not in document:
            raise evidence.EvidenceError('missing_manifest_field')
    if document.get('schema') != evidence.SCHEMA:
        raise evidence.EvidenceError('invalid_manifest_schema')
    document = copy.deepcopy(document)
    authored = {}
    for attempt in document.get('attempts', []):
        run_id = attempt.get('run_id')
        if not safe_id(run_id) or _SECRET.search(run_id) or run_id in authored:
            raise evidence.EvidenceError('invalid_or_duplicate_run_id')
        if 'status' in attempt and attempt['status'] not in evidence.STATUSES:
            raise evidence.EvidenceError('invalid_attempt_status')
        authored[run_id] = attempt
    store = ReceiptStore(run_root, max_bytes, max_runs=max_runs, max_total_bytes=max_total_bytes)
    try:
        if any(not safe_id(name) or _SECRET.search(name) for name in store.names):
            raise evidence.EvidenceError('unsafe_run_entry_name')
        if set(authored) - set(store.names):
            raise evidence.EvidenceError('declared_attempt_directory_missing')
        # Missing result directories are errors; they must not disappear from discovery.
        for _, claim in claim_states(document):
            if not safe_id(claim.get('run_id')) or claim['run_id'] not in store.names:
                raise evidence.EvidenceError('referenced_run_missing')
            if type(claim.get('round')) is not int or claim['round'] < 0:
                raise evidence.EvidenceError('explicit_round_required')
        runs = {name: store.load_run(name) for name in store.names}
        attempts = [_attempt(store, runs[name], authored.get(name, {})) for name in store.names]
        manifest = {'schema': evidence.SCHEMA, 'created_utc': datetime.now(timezone.utc).isoformat(),
                    'submission': {k: document['submission'].get(k) for k in ('run_id', 'round')},
                    'attempts': attempts, 'requests': [], 'selection': {}, 'budget': {}}
        comparisons = []
        for request in document['requests']:
            item = {k: _annotation(request[k]) for k in ('id', 'concern') if k in request}
            item.update({'status': request.get('status', 'measured'), 'runs': request.get('runs', [])})
            if request.get('reason') is not None:
                item['reason'] = _annotation(request['reason'])
            result = request.get('result')
            if result:
                run_id, number = result.get('run_id'), result.get('round')
                point = runs[run_id]['points'].get(number)
                if number not in store.measured(runs[run_id]):
                    raise evidence.EvidenceError('missing_or_invalid_result')
                if result.get('metric', 'score') != 'score':
                    raise evidence.EvidenceError('unsupported_metric')
                if 'value' in result and (not evidence.finite_number(result['value']) or result['value'] != point['score']):
                    raise evidence.EvidenceError('claimed_score_mismatch')
                item['result'] = {'run_id': run_id, 'round': number, 'metric': 'score', 'value': point['score']}
            baseline = request.get('vs_baseline')
            if baseline:
                if not result:
                    raise evidence.EvidenceError('baseline_requires_result')
                base_id, base_round = baseline['baseline_run'], baseline['baseline_round']
                if base_round not in store.measured(runs[base_id]):
                    raise evidence.EvidenceError('missing_or_invalid_baseline')
                comparison = _comparison(runs[base_id], runs[base_id]['points'][base_round], runs[run_id], point)
                item['vs_baseline'] = {'baseline_run': base_id, 'baseline_round': base_round, 'diff': comparison['diff']}
                comparisons.append({'request_id': item['id'], **comparison})
            manifest['requests'].append(item)
        selection = document['selection']
        reported = _object(selection.get('reported'))
        manifest['selection'] = {'reported': {k: reported.get(k) for k in ('run_id', 'round')},
                                 'rule': _annotation(selection.get('rule', '')),
                                 'rejected': [{'run_id': x.get('run_id'), 'why': _annotation(x.get('why', ''))}
                                              for x in selection.get('rejected', [])]}
        budget = document['budget']
        manifest['budget'] = {'rounds': budget.get('rounds'), 'source': _annotation(budget.get('source', '')),
                              'provenance': 'author_declared_limit; observed costs are in runs[*].budget'}
        if 'trials' in budget:
            if type(budget['trials']) is not int or budget['trials'] < 1:
                raise evidence.EvidenceError('invalid_declared_trials')
            manifest['budget']['trials'] = budget['trials']
        problems, notes = evidence.check_manifest(manifest)
        if problems:
            raise evidence.EvidenceError('manifest_inconsistent')
        findings = verify_in_store(manifest, store)
        if findings:
            raise evidence.EvidenceError(findings[0]['code'])
        units = []
        for name, run in runs.items():
            projected = {'run_meta': _metadata(run['meta']),
                         'curve': [_point(p) for _, p in sorted(run['points'].items())],
                         'patches': [_patch(store, name, number) for number in sorted(run['points'])]}
            units.append({'run_id': name, 'source_sha256': run['hashes'],
                          'missing': run['missing'], 'problems': run['problems'],
                          'projection': projected, 'projection_sha256': digest(canonical(projected)),
                          'budget': _budget(run)})
        groups = document.get('variation_groups', [])
        if not isinstance(groups, list):
            raise evidence.EvidenceError('invalid_variation_groups')
        variation, seen = [], set()
        attempt_status = {a['run_id']: a['status'] for a in attempts}
        for group in groups:
            if not isinstance(group, dict) or not safe_id(group.get('id')) or group['id'] in seen:
                raise evidence.EvidenceError('invalid_variation_group')
            seen.add(group['id'])
            states = group.get('states')
            if not isinstance(states, list):
                raise evidence.EvidenceError('invalid_variation_states')
            pairs = []
            refs = []
            for state in states:
                if not isinstance(state, dict) or state.get('run_id') not in runs or type(state.get('round')) is not int:
                    raise evidence.EvidenceError('invalid_variation_state')
                run = runs[state['run_id']]
                if attempt_status[state['run_id']] != 'measured' or state['round'] not in store.measured(run):
                    raise evidence.EvidenceError('invalid_variation_measurement')
                refs.append({k: state[k] for k in ('run_id', 'round')})
                pairs.append((run, run['points'][state['round']]))
            variation.append({'id': group['id'], 'states': refs, **evidence_stats.run_variation(pairs)})
        mentioned = {manifest['submission']['run_id']} | {x['run_id'] for x in manifest['selection']['rejected']}
        for request in manifest['requests']:
            mentioned.update(request['runs'])
        bundle = {'schema': SCHEMA, 'manifest': manifest, 'runs': units, 'comparisons': comparisons,
                  'variation_groups': variation,
                  'discovery': {'scope': 'immediate non-regular entries of the supplied root',
                                'run_ids': store.names, 'global_completeness': 'not_established',
                                'unexplained_attempts': sorted(set(runs) - mentioned)},
                  'privacy': {'raw_tasks_traces_answers_logs_and_patches': 'excluded',
                              'receipt_fields': 'allowlist', 'author_text': 'author_review_required'},
                  'verification': {'score_claims': 'matched_to_source_receipts',
                                   'aggregate_score_validation': 'receipt_value_only; historical_aggregation_not_recomputed',
                                   'authenticity': 'not_established', 'scorers_rerun': False,
                                   'notes': ['partial_task_coverage_is_reported_not_imputed']}}
        store.recheck()
        bundle['content_sha256'] = digest(canonical(bundle))
        return bundle
    finally:
        store.close()


def check_export(bundle):
    """Verify exported bytes and manifest consistency, not source authenticity."""
    if not isinstance(bundle, dict) or bundle.get('schema') != SCHEMA:
        return ['invalid_export_schema']
    payload = {k: v for k, v in bundle.items() if k != 'content_sha256'}
    try:
        if digest(canonical(payload)) != bundle.get('content_sha256'):
            return ['export_digest_mismatch']
        if evidence.check_manifest(bundle.get('manifest'))[0]:
            return ['invalid_manifest']
        points, run_ids, units = {}, set(), {}
        for run in bundle['runs']:
            name = run['run_id']
            if not safe_id(name) or name in run_ids:
                return ['invalid_export_inventory']
            run_ids.add(name)
            units[name] = run
            if digest(canonical(run['projection'])) != run.get('projection_sha256'):
                return ['projection_digest_mismatch']
            for point in run['projection']['curve']:
                number = point['round']
                if (type(number) is not int or number < 0 or (name, number) in points
                        or point.get('run_id') != name):
                    return ['invalid_export_round']
                points[name, number] = point
        if (run_ids != {a['run_id'] for a in bundle['manifest']['attempts']}
                or sorted(run_ids) != bundle['discovery']['run_ids']):
            return ['export_inventory_mismatch']
        for attempt in bundle['manifest']['attempts']:
            for number in attempt.get('rounds', []):
                point = points.get((attempt['run_id'], number))
                if point is None or point.get('measurement_problems') != [] or point.get('measured_by_platform') is not True:
                    return ['export_attempt_round_mismatch']
        for _, claim in claim_states(bundle['manifest']):
            point = points.get((claim.get('run_id'), claim.get('round')))
            if (point is None or point.get('measured_by_platform') is not True
                    or point.get('measurement_problems') != []
                    or not evidence.finite_number(point.get('score'))):
                return ['missing_export_measurement']
            if 'metric' in claim or 'value' in claim:
                if claim.get('metric') != 'score' or claim.get('value') != point['score']:
                    return ['export_score_mismatch']
        comparisons = {c['request_id']: c for c in bundle['comparisons']}
        requests = {r['id']: r for r in bundle['manifest']['requests'] if r.get('vs_baseline')}
        if set(comparisons) != set(requests) or len(comparisons) != len(bundle['comparisons']):
            return ['export_comparison_inventory_mismatch']
        for name, request in requests.items():
            base, result = request['vs_baseline'], request['result']
            a = points[base['baseline_run'], base['baseline_round']]
            b = points[result['run_id'], result['round']]
            difference = b['score'] - a['score']
            expected = difference if evidence.finite_number(difference) else None
            comparison = comparisons[name]
            if comparison.get('score_delta') != expected:
                return ['export_delta_mismatch']
            expected_config = _diff(units[base['baseline_run']]['projection']['run_meta']['config'],
                                    units[result['run_id']]['projection']['run_meta']['config'])
            if (comparison.get('diff') != base.get('diff')
                    or comparison['diff'].get('config') != expected_config
                    or comparison['diff'].get('harness_sha') != {
                        'before': a['identity'].get('harness_sha'), 'after': b['identity'].get('harness_sha')}):
                return ['export_diff_mismatch']
        for run in units.values():
            for point in run['projection']['curve']:
                summary = point['trial_variation']
                if summary.get('status') != 'available':
                    continue
                rows = point.get('task_trials')
                if (not rows or point['reused_or_cached'] or not point['complete_task_coverage']
                        or any(not isinstance(row, list) or len(row) != len(rows[0]) for row in rows)):
                    return ['export_trial_variation_mismatch']
                expected = evidence_stats.describe([statistics.mean(row[i] for row in rows)
                                                    for i in range(len(rows[0]))])
                if not _same_statistics(summary, expected):
                    return ['export_trial_variation_mismatch']
                if summary.get('per_task_sample_variances') != [evidence_stats.describe(row)['sample_variance'] for row in rows]:
                    return ['export_trial_variation_mismatch']
        for group in bundle['variation_groups']:
            if group.get('status') != 'available':
                continue
            selected = [points[(s['run_id'], s['round'])] for s in group['states']]
            attempt_status = {a['run_id']: a['status'] for a in bundle['manifest']['attempts']}
            if (len({s['run_id'] for s in group['states']}) != len(selected)
                    or any(attempt_status[s['run_id']] != 'measured' for s in group['states'])
                    or len({p['identity'].get('harness_sha') for p in selected}) != 1
                    or len({p['task_set_sha256'] for p in selected}) != 1
                    or any(p['reused_or_cached'] or not p['complete_task_coverage'] for p in selected)
                    or not _same_statistics(group, evidence_stats.describe([p['score'] for p in selected]))):
                return ['export_run_variation_mismatch']
    except (KeyError, ValueError, TypeError, RecursionError):
        return ['invalid_export_shape']
    return []


def _same_statistics(observed, expected):
    """JSON round-trips preserve these derived values exactly."""
    return expected.get('status') == 'available' and all(observed.get(key) == expected.get(key)
        for key in ('n', 'mean', 'sample_variance', 'sample_std', 'ddof'))


def write_export(bundle, output):
    """Atomically publish a new owner-readable file. Never overwrite an export."""
    output = Path(output)
    data = canonical(bundle) + b'\n'
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.evidence-', delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink()
    return output
