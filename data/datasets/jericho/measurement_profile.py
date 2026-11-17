"""Jericho ROM archive declaration and conservative loader/scorer identities.

The pinned archive is a declaration, not proof of the ROM selected for a run.
The actual ROM commitment and the admitted interpreter are recorded separately.
"""
import hashlib
from pathlib import Path
import re
import sys

from harnessgrad.provenance import measurement_components as identities
from harnessgrad.provenance.source_declarations import literal_assignments

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = 'data/datasets/jericho.py'
ENVIRONMENT = 'data/datasets/jericho/lib/jer_env.py'
LOADER = {'adapter': ADAPTER, 'environment': ENVIRONMENT,
          'declarations': 'data/scaffolds/declarations.py',
          'scorer_input': 'data/scaffolds/scorer_input.py',
          'native_binding': 'data/scaffolds/public_native.py', 'dataset_paths': 'harnessgrad/paths.py'}
SCORER = {'adapter': ADAPTER, 'environment': ENVIRONMENT,
          'check': 'data/datasets/jericho/verify/check.py',
          'scorer_input': 'data/scaffolds/scorer_input.py', 'native_binding': 'data/scaffolds/public_native.py'}
METRIC = 'Jericho clamped game score / positive game maximum; zero if maximum nonpositive'


def _source(raw):
    wanted = {'ARCHIVE_VERSION', 'ARCHIVE_SHA256', 'ARCHIVE_URL'}
    try:
        values = literal_assignments(raw, wanted, allow_chained=True)
        digest, url = values['ARCHIVE_SHA256'], values['ARCHIVE_URL']
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or values['ARCHIVE_VERSION'] != digest or not isinstance(url, str)
                or not url.startswith('https://codeload.github.com/BYU-PCCL/z-machine-games/')):
            raise ValueError('invalid archive declaration')
        return url, digest
    except (SyntaxError, TypeError, ValueError) as exc:
        raise ValueError('invalid Jericho source declaration') from exc


def profile():
    adapter = (ROOT / ADAPTER).read_bytes()
    environment = (ROOT / ENVIRONMENT).read_bytes()
    url, digest = _source(adapter)
    loaded = sys.modules.get('data.datasets.jericho')
    if loaded is not None and (getattr(loaded, 'ARCHIVE_SHA256', None) != digest
                               or getattr(loaded, 'ARCHIVE_URL', None) != url):
        raise ValueError('loaded Jericho archive declaration differs from source')
    loader = identities.capture_implementation('loader', ROOT, LOADER)
    scorer = identities.capture_implementation('scorer', ROOT, SCORER, protocol=METRIC)
    for key, raw in (('adapter', adapter), ('environment', environment)):
        digest_now = hashlib.sha256(raw).hexdigest()
        if (loader['details']['components'][key] != digest_now
                or scorer['details']['components'][key] != digest_now):
            raise ValueError('Jericho shared source changed during identity capture')
    return {'data_source': identities.data_source('jericho', origin=url,
                revision='sha256:' + digest, declared_artifact_sha256=digest),
            'loader': loader, 'scorer': scorer}
