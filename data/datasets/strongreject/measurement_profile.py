"""StrongREJECT declared input inventory, shared loader and scorer identities.

No task files, templates or judge services are read here. Manifest hashes are
upstream declarations, not proof of data actually used. The adapter is shared
by loader and scorer, so its entire source is conservatively in both roles.
"""
import hashlib
import json
from pathlib import Path
import re

from data.datasets import strongreject
from harnessgrad.provenance import measurement_components as identities

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = 'data/datasets/strongreject/specs/sources.json'
ADAPTER = 'data/datasets/strongreject.py'
LOADER = {'adapter': ADAPTER, 'prepare': 'data/datasets/strongreject/prepare.py'}
SCORER = {'adapter': ADAPTER, 'check': 'data/datasets/strongreject/verify/check.py',
          'scoring': 'data/datasets/strongreject/lib/scoring.py',
          'judge': 'eval/service/judge.py', 'judge_request': 'eval/service/judge_request.py',
          'transport': 'eval/service/tool_transport.py'}


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate StrongREJECT manifest key')
        obj[key] = value
    return obj


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _declarations(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
        if type(value['schema']) is not int or value['schema'] != 1:
            raise ValueError('unsupported manifest schema')
        task_revision, evaluator_revision = value['task_revision'], value['evaluator_revision']
        if any(not isinstance(x, str) or not re.fullmatch(r'[0-9a-f]{40}', x)
               for x in (task_revision, evaluator_revision)):
            raise ValueError('invalid revision')
        entries = value['files']
        if not isinstance(entries, list):
            raise ValueError('invalid source list')
        indexed = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'path', 'url', 'sha256'}:
                raise ValueError('invalid source entry')
            path, url, digest = entry['path'], entry['url'], entry['sha256']
            if (not isinstance(path, str) or not path or path in indexed
                    or not isinstance(url, str) or not url.startswith('https://raw.githubusercontent.com/')
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('invalid source entry')
            indexed[path] = entry
        tasks = [item for path, item in indexed.items() if path.startswith('tasks/')]
        if len(tasks) != 150 or any('/' + task_revision + '/' not in e['url'] for e in tasks):
            raise ValueError('invalid task inventory')
        csv = indexed['upstream-dataset.csv']
        rubric, evaluator = indexed['upstream-judge_templates.json'], indexed['upstream-evaluate.py']
        if any('/' + evaluator_revision + '/' not in e['url'] for e in (rubric, evaluator)):
            raise ValueError('invalid evaluator provenance')
        data = {'task_revision': task_revision, 'tasks': sorted(tasks, key=lambda e: e['path']),
                'upstream_dataset': csv}
        scorer = {'evaluator_revision': evaluator_revision, 'templates': rubric, 'evaluator': evaluator}
        return value, task_revision + ':' + _digest(data), _digest(scorer)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid StrongREJECT provenance declaration') from exc


def profile():
    raw = (ROOT / MANIFEST).read_bytes()
    manifest, data_revision, declared_scorer = _declarations(raw)
    # The adapter holds a module-level copy used by the actual verification path.
    # Do not record declarations different from that selected copy.
    if strongreject.SOURCES != manifest:
        raise ValueError('StrongREJECT loaded source declaration differs from current manifest')
    metric = strongreject.judge_profile()['metric']
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    manifest_stamp = identities.capture_implementation('loader', ROOT, {'manifest': MANIFEST})
    captured = identities.capture_implementation('scorer', ROOT, SCORER, protocol=metric)
    if (manifest_stamp['details']['components']['manifest'] != hashlib.sha256(raw).hexdigest()
            or loader['details']['components']['adapter'] != captured['details']['components']['adapter']):
        raise ValueError('StrongREJECT source changed during capture')
    scorer = identities.implementation('scorer', {**captured['details']['components'],
                                                   'declared_upstream': declared_scorer}, protocol=metric)
    return {'data_source': identities.data_source('strongreject',
             origin='Harbor StrongREJECT task metadata and StrongREJECT upstream CSV',
             revision=data_revision), 'loader': loader, 'scorer': scorer}
