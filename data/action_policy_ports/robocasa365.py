"""Expose RoboCasa365's public episode relay as a PandaOmron policy port."""
from pathlib import Path

from data.scaffolds.asset_module import load


def environment():
    root = Path(__file__).resolve().parents[1]
    protocol = load(root / 'datasets/robocasa365/protocol.py')
    return {
        'control': 'panda-omron-delta-20hz-v1',
        'cameras': list(protocol.CAMERAS),
        'states': list(protocol.STATE_KEYS),
        'actions': dict(protocol.ACTION_SIZES),
        'max_chunk': protocol.MAX_CHUNK,
        'action_bounds': [-1, 1],
    }
