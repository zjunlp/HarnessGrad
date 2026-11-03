"""deepswe: pinned native task world, artifact export and fresh Harbor verification.

Loading is conditional on prepared local immutable images, source-bound runtime
manifest, fixed Harbor interpreter and independent local judge where required.
"""
from pathlib import Path

from data.scaffolds import roots
import os
from data.scaffolds import native_harbor as native

KIND = 'dataset'
ORACLE_SOURCE = 'released'
OFFICIAL_EVAL = None
SPLIT = None
SPLIT_SOURCE = 'none'
DEFAULT_ENV = {'kind': 'files', 'network': 'none'}
DATASET_ROOT = roots.dataset_root('deepswe',
                              legacy=os.environ.get('HG_DEEPSWE_DATA', '/mnt/20t/xubuqiang/datasets/deepswe'))
REVISION = '0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea'
SOURCE = 'https://github.com/datacurve-ai/deep-swe'
METRIC = 'reward'


def inventory():
    return native.inventory(DATASET_ROOT, 'deepswe')


def load():
    rows = inventory()
    return [native.public_task(r) for r in rows], {r.task_id: METRIC for r in rows}


def provision_status():
    return native.status(DATASET_ROOT, 'deepswe')


def downloads():
    return [{'kind': 'http', 'name': 'deepswe' + ' fixed native source', 'version': REVISION,
             'url': 'https://codeload.github.com/datacurve-ai/deep-swe/tar.gz/0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea',
             'dst': '_src/native-source.tar.gz' if 'deepswe' != 'jobbench' else '_src/dataset-card.md',
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
