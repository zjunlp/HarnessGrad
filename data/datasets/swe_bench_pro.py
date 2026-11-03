"""swe_bench_pro: pinned native task world, artifact export and fresh Harbor verification.

Loading is conditional on prepared local immutable images, source-bound runtime
manifest, fixed Harbor interpreter and independent local judge where required.
"""
from pathlib import Path

from data.scaffolds import roots
import os
from data.scaffolds import flashrsi_harbor as native

KIND = 'dataset'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = 'none'
DEFAULT_ENV = {'kind': 'files', 'network': 'none'}
DATASET_ROOT = roots.dataset_root(
    'swe_bench_pro',
    legacy=os.environ.get('HG_SWE_BENCH_PRO_DATA', '/mnt/20t/xubuqiang/datasets/swe_bench_pro'))
REVISION = '66f92766bba642462d4bbe5479e83f91f9211862'
SOURCE = 'https://github.com/scaleapi/SWE-bench_Pro-os'
METRIC = 'resolved'


def inventory():
    return native.inventory(DATASET_ROOT, 'swe_bench_pro')


def load():
    rows = inventory()
    return [native.public_task(r) for r in rows], {r.task_id: METRIC for r in rows}


def provision_status():
    return native.status(DATASET_ROOT, 'swe_bench_pro')


def downloads():
    return [{'kind': 'http', 'name': 'swe_bench_pro' + ' fixed native source', 'version': REVISION,
             'url': 'https://codeload.github.com/scaleapi/SWE-bench_Pro-os/tar.gz/66f92766bba642462d4bbe5479e83f91f9211862',
             'dst': '_src/native-source.tar.gz' if 'swe_bench_pro' != 'jobbench' else '_src/dataset-card.md',
             'size': 'source-dependent',
             'note': 'Pinned native source only. Preparation must preserve its images, private verifier and collection semantics; a downloaded archive is not execution readiness.'}]


from collections.abc import Mapping


class _Selected(Mapping):
    def __iter__(self):
        return iter(r.task_id for r in inventory())
    def __len__(self):
        return len(inventory())
    def __init__(self, build):
        self.build = build
    def __getitem__(self, task_id):
        row = next((r for r in inventory() if r.task_id == task_id), None)
        if row is None:
            raise KeyError(task_id)
        return self.build(row)


def __getattr__(name):
    if name == 'SETUP':
        return _Selected(native.setup)
    if name == 'VERIFY':
        return _Selected(native.verifier)
    if name == 'ENV':
        return _Selected(lambda r: dict(DEFAULT_ENV))
    raise AttributeError(name)
