"""LoCoMo data declaration, input loader and deterministic scorer provenance.

No conversation data, NLTK package or external service is read here. Upstream
scorer metadata is a declaration, not an attestation of downloaded source bytes.
"""
import hashlib
import json
from pathlib import Path
import re

from harnessgrad.provenance import measurement_components as identities
from harnessgrad.provenance.source_declarations import literal_assignments

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/locomo.py'
SOURCE = 'data/datasets/locomo/specs/source.json'
LOADER = {'adapter': ADAPTER, 'inputs': 'data/datasets/locomo/lib/inputs.py'}
SCORER = {'check': 'data/datasets/locomo/verify/check.py',
          'scoring': 'data/datasets/locomo/lib/scoring.py',
          'aggregation': 'data/datasets/locomo/report.py'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate LoCoMo source declaration key')
        result[key] = value
    return result


def _declarations(adapter_bytes, source_bytes):
    try:
        values = literal_assignments(adapter_bytes, {'SOURCE_COMMIT', 'DATA_SHA256', 'METRIC'})
        spec = json.loads(source_bytes, object_pairs_hook=_unique)
        data, harbor = spec['data'], spec['harbor']
        revision, digest, metric = values['SOURCE_COMMIT'], values['DATA_SHA256'], values['METRIC']
        expected_url = f'https://raw.githubusercontent.com/snap-research/locomo/{revision}/data/locomo10.json'
        if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)
                or data['commit'] != revision or data['sha256'] != digest
                or data['url'] != expected_url or data['path'] != 'data/locomo10.json'
                or spec['metric'] != metric):
            raise ValueError('LoCoMo source or metric declarations differ')
        if (harbor['repository'] != 'https://github.com/harbor-framework/harbor'
                or not isinstance(harbor['commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', harbor['commit'])):
            raise ValueError('invalid Harbor scorer provenance')
        for name, upstream_path in (
                ('metric.py', 'adapters/locomo/src/locomo/metric.py'),
                ('task-template/tests/verifier.py', 'adapters/locomo/src/locomo/task-template/tests/verifier.py')):
            entry = harbor['files'][name]
            if (entry['upstream_path'] != upstream_path or not isinstance(entry['sha256'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                    or not isinstance(entry['git_blob'], str) or not re.fullmatch(r'[0-9a-f]{40}', entry['git_blob'])):
                raise ValueError('invalid Harbor scorer source declaration')
        if (not isinstance(spec['dependency']['nltk'], str) or not spec['dependency']['nltk']
                or type(spec['dependency']['nltk_corpora_required']) is not bool):
            raise ValueError('invalid Harbor scorer dependency declaration')
        declared_scorer = {'repository': harbor['repository'], 'commit': harbor['commit'],
                           'metric': harbor['files']['metric.py'],
                           'verifier': harbor['files']['task-template/tests/verifier.py'],
                           'dependency': spec['dependency']}
        encoded = json.dumps(declared_scorer, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()
        return values, data['url'], hashlib.sha256(encoded).hexdigest()
    except (SyntaxError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid LoCoMo provenance declaration') from exc


def profile():
    adapter_bytes = (ROOT / ADAPTER).read_bytes()
    source_bytes = (ROOT / SOURCE).read_bytes()
    values, url, scorer_declaration = _declarations(adapter_bytes, source_bytes)
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    source_stamp = identities.capture_implementation('loader', ROOT, {'source_manifest': SOURCE})
    if (loader['details']['components']['adapter'] != hashlib.sha256(adapter_bytes).hexdigest()
            or source_stamp['details']['components']['source_manifest'] != hashlib.sha256(source_bytes).hexdigest()):
        raise ValueError('LoCoMo declaration changed during capture')
    captured = identities.capture_implementation('scorer', ROOT, SCORER, protocol=values['METRIC'])
    scorer = identities.implementation('scorer', {**captured['details']['components'],
        'declared_harbor_scorer': scorer_declaration}, protocol=values['METRIC'])
    return {'data_source': identities.data_source('locomo', origin=url, revision=values['SOURCE_COMMIT'],
                                                  declared_artifact_sha256=values['DATA_SHA256']),
            'loader': loader, 'scorer': scorer}
