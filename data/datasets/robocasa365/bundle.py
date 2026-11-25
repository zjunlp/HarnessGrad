"""Pinned archives and preparation receipt for the simulator's read-only mount."""
from __future__ import annotations

from hashlib import file_digest, sha256
import json
from pathlib import Path


ASSETS = Path(__file__).resolve().parent
BUNDLE_DIR = 'prepared-v1'
RECEIPT = 'prepared.json'
RECEIPT_SHA256 = '173a4a6334ea97ecd51d589e768f0e75bd51ca72a8540057f80ded7a8fc9274f'
ARCHIVES = json.loads((ASSETS / 'archives.json').read_text())
RECIPE = {entry['dst']: entry['sha256'] for entry in ARCHIVES}

SOURCE_FILES = (
    'robocasa/robocasa/models/objects/objects.py',
    'robocasa/robocasa/utils/dataset_registry.py',
    'robocasa/robocasa/wrappers/gym_wrapper.py',
    'robosuite/robosuite/models/base.py',
    'robosuite/robosuite/utils/binding_utils.py',
)


def file_sha256(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def prepared_receipt(source_sha256):
    inputs = {'archives': RECIPE, 'source': source_sha256, 'recipe': BUNDLE_DIR}
    digest = sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return {**inputs, 'bundle': digest}


def identify(prepared):
    raw = (prepared / RECEIPT).read_bytes()
    if sha256(raw).hexdigest() != RECEIPT_SHA256:
        raise ValueError('RoboCasa365 preparation receipt checksum mismatch')
    receipt = json.loads(raw)
    source = receipt['source']
    assets = prepared / 'robocasa/robocasa/models/assets'
    required = (
        'textures', 'generative_textures', 'fixtures', 'objects/objaverse',
        'objects/aigen_objs', 'objects/lightwheel',
    )
    if (set(source) != set(SOURCE_FILES) or receipt != prepared_receipt(source)
            or any(not (assets / name).is_dir() for name in required)
            or any(file_sha256(prepared / name) != value for name, value in source.items())):
        raise ValueError('RoboCasa365 preparation receipt or required inputs changed')
    return receipt['bundle']
