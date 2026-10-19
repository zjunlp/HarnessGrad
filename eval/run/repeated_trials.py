"""Complete-manifest pass^k reporting, explicitly selected by VERIFY.trial_protocol.

This report is additional to legacy run_reward. Missing/invalid trials never become
zero or a smaller denominator. A complete report requires every planned binary result.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile



def protocol_for(tasks, verifiers, trials):
    specs = [(verifiers.get(t['task_id']) or {}).get('trial_protocol') for t in tasks]
    if not any(s is not None for s in specs):
        return None
    if not specs or any(s != specs[0] for s in specs):
        raise ValueError('a repeated-trial evaluation requires one explicit protocol for every task')
    from data.scaffolds.runtime_extensions import validate_trials
    spec=specs[0];validate_trials(spec)
    if trials != spec['trials']:
        raise ValueError('requested trials differs from trial_protocol.trials')
    ids=[t['task_id'] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate task IDs in repeated-trial manifest')
    return spec


def aggregate(manifest, rows, ks, *, groups=None):
    expected={(r['task_id'],r['trial_id']):r for r in manifest}
    if not manifest or len(expected)!=len(manifest):
        raise ValueError('empty or duplicate trial manifest')
    seen={}
    for row in rows:
        key=(row['task_id'],row['trial_id'])
        if key not in expected or key in seen:
            raise ValueError('unexpected or duplicate trial result')
        if row.get('seed') != expected[key].get('seed'):
            raise ValueError('trial result seed differs from manifest')
        seen[key]=row
    missing=[list(k) for k in expected if k not in seen]
    invalid=[list(k) for k,r in seen.items() if r.get('status')!='measured'
             or type(r.get('score')) not in (int,float) or r['score'] not in (0,1)]
    tasks=sorted({k[0] for k in expected})
    sizes={t:sum(1 for k in expected if k[0]==t) for t in tasks}
    if not ks or any(type(k) is not int or k<1 or any(k>n for n in sizes.values()) for k in ks):
        raise ValueError('pass^k requires positive k <= planned trials for every task')
    out={'schema':'harnessgrad.repeated-trials.v1','metric':'pass^k',
         'manifest':manifest,'manifest_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),
         'results':[seen[k] for k in expected if k in seen], 'missing':missing,'invalid':invalid,
         'complete':not missing and not invalid,'metrics':None,'per_task':None}
    if not out['complete']:
        return out
    per_task={}
    for task in tasks:
        n=sizes[task];successes=sum(r['score']==1 for k,r in seen.items() if k[0]==task)
        per_task[task]={str(k):math.comb(successes,k)/math.comb(n,k) if successes>=k else 0.0 for k in ks}
    out.update(per_task=per_task,metrics={str(k):sum(per_task[t][str(k)] for t in tasks)/len(tasks) for k in ks})
    if groups is not None:
        if set(groups)!=set(tasks):raise ValueError('groups must cover exactly the manifest tasks')
        out['groups']={group:{str(k):sum(per_task[t][str(k)] for t in tasks if groups[t]==group)
                                     /sum(groups[t]==group for t in tasks) for k in ks}
                       for group in sorted(set(groups.values()))}
    return out


def result_row(result, plan, provider_failure=None):
    verdict=result.get('verdict') or {}
    score=verdict.get('score',1.0 if verdict.get('passed') else 0.0)
    invalid=result.get('invalid') or verdict.get('unmeasured') or provider_failure
    # Explicit signal/timeout records remain distinct from measured benchmark failure.
    from eval.observe.tracing import process_outcome
    outcome=process_outcome(result.get('exit_code'),result.get('stderr'),wrapper=bool(result.get('sandbox')))
    if outcome in ('stopped_by_signal','relayed_signal','timeout'):
        invalid=invalid or outcome
    return {**plan,'status':'unmeasured' if invalid else 'measured',
            'score':None if invalid else score,'reason':invalid,
            'runtime_services':result.get('runtime_services'), 'judge':verdict.get('judge')}


def persist(root, report):
    if root is None:return
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    immutable_json(root/'repeated-trials.json', report)


def immutable_json(path, value):
    """Publish complete JSON atomically; a resume may reuse, never replace, evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.trial-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if json.loads(path.read_text()) != value:
                raise ValueError(f'repeated-trial evidence already exists with different content: {path}')
    finally:
        os.unlink(temporary)


def report_root(progress_dir, round_no, harness_sha):
    return Path(progress_dir) / 'protocols' / f'round-{round_no}' / hashlib.sha256(harness_sha.encode()).hexdigest()[:16]


def plan(tasks, protocol, *, run_id, round_no, harness_sha):
    return {'configuration': protocol, 'run_id': run_id, 'round': round_no,
            'harness_sha': harness_sha,
            'manifest': [{'task_id': task['task_id'],
                          'trial_id': f'round-{round_no}.harness-{harness_sha}.trial-{i + 1}',
                          'seed': str(seed)}
                         for task in tasks for i, seed in enumerate(protocol['seeds'])]}


def save_task(root, task_plan, rows):
    report = aggregate(task_plan['manifest'], rows, task_plan['configuration']['ks'])
    report.update({k: v for k, v in task_plan.items() if k != 'manifest'})
    task_id = task_plan['manifest'][0]['task_id']
    immutable_json(Path(root) / 'tasks' / (hashlib.sha256(task_id.encode()).hexdigest() + '.json'), report)


def resume_rows(root, full_plan, prior):
    """Require actual complete per-trial evidence for every skipped progress task."""
    rows = []
    expected_tasks = {p['task_id'] for p in full_plan['manifest']}
    if set(prior) - expected_tasks:
        raise ValueError('resume progress contains tasks outside the selected trial manifest')
    directory = Path(root) / 'tasks'
    stored = {}
    for path in directory.glob('*.json'):
        report = json.loads(path.read_text())
        ids = {p['task_id'] for p in report['manifest']}
        if len(ids) != 1:
            raise ValueError('resume task evidence must name exactly one task')
        task_id = next(iter(ids))
        if task_id in stored or task_id not in expected_tasks:
            raise ValueError('unexpected or duplicate resume task evidence')
        stored[task_id] = report
    if set(stored) != set(prior):
        raise ValueError('resume requires matching completed progress and per-trial evidence for every skipped task')
    for task_id, progress in prior.items():
        report = stored[task_id]
        manifest = [p for p in full_plan['manifest'] if p['task_id'] == task_id]
        if (report['manifest'] != manifest or any(report.get(k) != full_plan[k]
                for k in ('configuration', 'run_id', 'round', 'harness_sha'))):
            raise ValueError('resume trial manifest or execution identity differs')
        checked = aggregate(manifest, report['results'], full_plan['configuration']['ks'])
        if not checked['complete']:
            raise ValueError('resume task has incomplete or unmeasured trial evidence')
        scores = [row['score'] for row in checked['results']]
        if (progress.get('process_outcome') != 'completed'
                or progress.get('n_trials') != len(scores)
                or progress.get('trial_scores') != scores
                or progress.get('score') != sum(scores) / len(scores)):
            raise ValueError('resume progress differs from its measured trial evidence')
        rows.extend(checked['results'])
    return rows
