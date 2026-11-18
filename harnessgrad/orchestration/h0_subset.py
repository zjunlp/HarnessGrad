"""Explicit, evidence-backed views of an original H0; never new measurements."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from eval.run.aggregation import Scores
from harnessgrad import dataset_runtime_identity, scoring_identity, verification_context
from harnessgrad.records import _curve_point, level_fields, test_rate_fields
from .historical_evidence import require_verification_context, reused_measurement
from . import h0


def _snapshot(directory):
    # Commit to the original evidence bytes, including namespaced trial receipts.
    files = [directory / name for name in ('run_meta.json', 'h0.json', 'curve.jsonl')]
    for name in ('round0_traces', 'verdicts/round-0'):
        files.extend(sorted((directory / name).rglob('*')))
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()}


def materialize_subset(donor, work, source_sha, manifest, attribution, options, identities,
                       task_ids, *, setups, envs):
    """Admit originals, then derive only a caller-selected, fully evidenced cohort."""
    try:
        if options.sampling != 'all' or options.side != 'train':
            raise ValueError('H0 subset view requires sampling all on the train side')
        snapshot = _snapshot(donor.directory)
        selected = list(task_ids)
        donor_ids = list((donor.point.get('sampling') or {}).get('task_ids') or [])
        if (not selected or len(selected) != len(set(selected)) or
                len(donor_ids) != len(set(donor_ids)) or not set(selected) < set(donor_ids)):
            raise ValueError('H0 subset view requires a nonempty proper subset of donor task IDs')
        if donor.payload is None:
            raise ValueError('H0 subset view requires original h0.json evidence')
        if donor.point.get('round') != 0 or donor.point.get('reused_from') is not None:
            raise ValueError('H0 subset view requires the original round-zero measurement')
        if json.loads((donor.directory / 'h0.json').read_text()) != donor.payload:
            raise ValueError('donor payload changed after loading')
        first = next(line for line in (donor.directory / 'curve.jsonl').read_text().splitlines() if line.strip())
        if json.loads(first) != donor.point:
            raise ValueError('donor curve changed after loading')
        metadata = json.loads((donor.directory / 'run_meta.json').read_text())
        raw = donor.payload.get('base') or {}
        contexts = [metadata.get('verification_context'), donor.payload.get('verification_context'),
                    raw.get('verification_context')]
        for context in contexts:
            require_verification_context(context, contexts[0])
        require_verification_context(donor.point.get('verification_context'), contexts[0], donor_ids)
        # Validate whole original runtime envelopes and their agreement before projection.
        runtime = metadata.get('dataset_runtime_identity')
        for value in (donor.payload.get('dataset_runtime_identity'), raw.get('dataset_runtime_identity')):
            dataset_runtime_identity.require_same(value, runtime)
        dataset_runtime_identity.require_same(donor.point.get('dataset_runtime_identity'),
            dataset_runtime_identity.project(runtime, donor_ids))
        scoring_identity.require_same(metadata.get('scoring_identity'), identities.scoring)
        if (donor.point.get('identity') or {}).get('harness_sha') != source_sha:
            raise ValueError('donor curve base source differs')
        if set(donor.point.get('per_task') or {}) != set(donor_ids):
            raise ValueError('donor curve does not measure its recorded cohort')
        for tid in donor_ids:
            if tid not in raw.get('per_task', {}) or tid not in donor.point.get('per_task', {}):
                raise ValueError('H0 subset view missing selected measured task')
            if raw['per_task'][tid] != donor.point['per_task'][tid]:
                raise ValueError('donor raw and curve scores disagree')
        h0.admit_h0_donor_records(donor, work, identities, options, setups=setups,
                                 envs=envs, selected_ids=selected)
        evidence = reused_measurement(donor.directory, donor.point, [donor.point])
        for key in ('traces', 'verdicts'):
            if any(tid not in evidence[key] for tid in selected):
                raise ValueError('H0 subset view missing selected ' + key)
        if raw.get('repeated_trials'):
            raise ValueError('H0 subset view does not support a repeated-trial protocol report')
        # Retain only measurement fields understood by this view. In particular,
        # donor-wide diagnostics/reports must not become recipient task channels.
        allowed = ('per_task', 'invalid', 'unmeasured', 'timeouts', 'harness_failed',
                   'per_task_std', 'per_task_trials', 'per_task_scorer_isolation',
                   'per_task_test_rate', 'per_task_test_rate_trials', 'evaluation_protocols',
                   'harness_usage_by_task', 'environment_usage_by_task', 'not_run',
                   'harness_runtime', 'model_gateway', 'identity_declared', 'trials',
                   'harness_runtime_identity', 'tampered', 'cached_tasks')
        base = {key: deepcopy(raw[key]) for key in allowed if key in raw}
        # All task-keyed channels are restricted before persistence or method access.
        task_maps = ('per_task', 'invalid', 'unmeasured', 'timeouts', 'harness_failed',
                     'per_task_std', 'per_task_trials', 'per_task_scorer_isolation',
                     'per_task_test_rate', 'per_task_test_rate_trials', 'evaluation_protocols',
                     'harness_usage_by_task', 'environment_usage_by_task')
        for key in task_maps:
            if isinstance(base.get(key), dict):
                base[key] = {tid: base[key][tid] for tid in selected if tid in base[key]}
        for key in ('traces', 'trial_traces', 'verdicts', 'trial_verdicts', 'harness_output'):
            base[key] = {tid: evidence[key][tid] for tid in selected if tid in evidence[key]}
        if any(tid in (base.get('invalid') or {}) or tid in (base.get('unmeasured') or {})
               for tid in selected):
            raise ValueError('H0 subset view selected an invalid or unmeasured task')
        trials = int(raw.get('trials') or donor.point.get('n_trials') or 1)
        if trials < 1 or options.trials != trials:
            raise ValueError('requested trials differ from the original H0 measurement')
        scores = Scores(trials)
        for tid in selected:
            verdict = base['verdicts'][tid]
            if verdict.get('score') != base['per_task'][tid]:
                raise ValueError('donor verdict and measurement score disagree')
            cells = base['trial_verdicts'].get(tid) or [verdict]
            if len(cells) != trials:
                raise ValueError('H0 subset view missing selected trial verdicts')
            if trials > 1:
                if (len(base['trial_traces'].get(tid, [])) != trials or
                        len((base.get('per_task_trials') or {}).get(tid, [])) != trials):
                    raise ValueError('H0 subset view missing selected trial evidence')
                if [cell.get('score') for cell in cells] != base['per_task_trials'][tid]:
                    raise ValueError('donor trial verdict and measurement scores disagree')
            scores.measured(tid, [{'verdict': cell} for cell in cells])
            if scores.per_task[tid] != base['per_task'][tid]:
                raise ValueError('donor trial mean and measurement score disagree')
        base.update(verification_context.fields(verification_context.project(identities.verification, selected)))
        base.update(dataset_runtime_identity.fields(dataset_runtime_identity.project(identities.dataset, selected)))
        base.update(per_task_std=scores.per_task_std, per_task_trials=scores.per_task_trials,
                    per_task_scorer_isolation=scores.per_task_isolation, score_std=scores.run_spread(),
                    per_task_test_rate=scores.tests.per_task,
                    per_task_test_rate_trials=scores.tests.per_task_trials,
                    tests_passed=scores.tests.passed, tests_total=scores.tests.total,
                    test_rate_n_tasks=len(scores.tests.per_task),
                    test_rate=(sum(scores.tests.per_task.values()) / len(scores.tests.per_task)
                               if scores.tests.per_task else None))
        base['not_run'] = [tid for tid in raw.get('not_run', []) if tid in selected]
        base['cached_tasks'] = [tid for tid in raw.get('cached_tasks', []) if tid in selected]
        # Raw full-run token/service totals cannot be relabelled as subset totals.
        for key in ('tokens', 'env_usage', 'score'):
            base.pop(key, None)
        alias = h0.build_shared_point(donor, source_sha, manifest, attribution, options)
        sampling = {'dataset': options.dataset, 'policy': 'all', 'task_ids': selected,
                    'selection_basis_round': 0, 'selected_within': options.side}
        point = _curve_point(run_id=attribution.run_id, round_index=0,
            identity=alias['identity'], sampling=sampling, scores=[base['per_task'][tid] for tid in selected],
            task_ids=selected, train_scores=[base['per_task'][tid] for tid in selected], train_ids=selected,
            scoring_identity=identities.scoring, dataset_runtime_identity=base.get('dataset_runtime_identity'),
            harness_runtime_identity=identities.initial_actor, verification_context=base['verification_context'],
            env_record=h0._env_record(envs, selected), model_gateway=raw.get('model_gateway'),
            harness_runtime=raw.get('harness_runtime'), evaluation_protocols=base.get('evaluation_protocols'),
            invalid=base.get('invalid'), harness_failed=base.get('harness_failed'), timeouts=base.get('timeouts'),
            n_trials=trials, score_std=base['score_std'], per_task_std=base['per_task_std'],
            per_task_trials=base['per_task_trials'], cached_tasks=base['cached_tasks'],
            cost=deepcopy(donor.point.get('cost') or {}),
            cumulative=deepcopy(donor.point.get('cumulative') or {}), label='H0 (verified subset view)',
            **test_rate_fields(base), **level_fields(base, selected))
        if snapshot != _snapshot(donor.directory):
            raise ValueError('donor evidence changed while deriving H0 subset view')
        point['shared_from'] = alias['shared_from']
        point['h0_subset_view'] = {'version': 1, 'source_task_ids': donor_ids,
            'selected_task_ids': selected, 'source_files_sha256': snapshot,
            'cost_scope': 'original_donor_measurement_not_prorated', 'new_evaluation_trials': 0}
        return h0.SharedH0(base, point)
    except (OSError, TypeError, ValueError, KeyError, StopIteration) as exc:
        raise h0.H0Refused(f'refusing --h0-subset-view {donor.run_id!r}: {exc}') from exc
