"""Pinned historical pool and code roles; no pool or library is loaded here.

The pool is not asserted to be a verified upstream download. The adapter
and QA scaffold are shared by loading and scoring, so both roles hash them.
The selected task bytes and installed library/runtime are bound separately.
"""
import hashlib
from pathlib import Path
import re

from harnessgrad.provenance import measurement_components as identities
from harnessgrad.provenance.source_declarations import literal_assignments

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/reasoning_gym.py'
PREPARE = 'data/datasets/reasoning_gym/prepare.py'
LOADER = {'adapter': ADAPTER, 'prepare': PREPARE, 'qa_base': 'data/scaffolds/qa/base.py',
          'qa_read': 'data/scaffolds/qa/read.py'}
SCORER = {'adapter': ADAPTER, 'wrapper': 'data/datasets/reasoning_gym/check.py',
          'qa_check': 'data/scaffolds/qa/verify/check.py', 'qa_base': 'data/scaffolds/qa/base.py',
          'qa_read': 'data/scaffolds/qa/read.py', 'qa_prediction': 'data/scaffolds/qa/prediction.py',
          'qa_evidence': 'data/scaffolds/qa/evidence_scorers.py', 'paths': 'harnessgrad/paths.py',
          'env_file': 'harnessgrad/env_file.py'}


def _declarations(content):
    wanted = {'PACKAGE', 'VERSION', 'POOL_SHA256', 'SEED', 'PER_FAMILY'}
    result = literal_assignments(content, wanted)
    if (set(result) != wanted or result['PACKAGE'] != 'reasoning-gym'
            or result['VERSION'] != '0.1.25'
            or not isinstance(result['POOL_SHA256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', result['POOL_SHA256'])
            or type(result['SEED']) is not int or type(result['PER_FAMILY']) is not int
            or result['PER_FAMILY'] <= 0):
        raise ValueError('invalid Reasoning Gym declaration')
    return result


def profile():
    adapter = (ROOT / ADAPTER).read_bytes()
    prepare = (ROOT / PREPARE).read_bytes()
    try:
        values = _declarations(adapter)
        if _declarations(prepare) != values:
            raise ValueError('Reasoning Gym declarations disagree')
    except (SyntaxError, ValueError, TypeError) as exc:
        raise ValueError('invalid Reasoning Gym provenance declaration') from exc
    metric = f"{values['PACKAGE']}-{values['VERSION']}-score-answer"
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    scorer = identities.capture_implementation('scorer', ROOT, SCORER, protocol=metric)
    if (loader['details']['components']['adapter'] != hashlib.sha256(adapter).hexdigest()
            or scorer['details']['components']['adapter'] != hashlib.sha256(adapter).hexdigest()
            or loader['details']['components']['prepare'] != hashlib.sha256(prepare).hexdigest()
            or loader['details']['components']['qa_base'] != scorer['details']['components']['qa_base']
            or loader['details']['components']['qa_read'] != scorer['details']['components']['qa_read']):
        raise ValueError('Reasoning Gym declaration or shared source changed during capture')
    digest = values['POOL_SHA256']
    return {'data_source': identities.data_source('reasoning_gym',
                origin='historical pinned pool; acquisition not asserted', revision='sha256:' + digest,
                declared_artifact_sha256=digest), 'loader': loader, 'scorer': scorer}
