"""Copy public profile sources into a temporary tree for identity controls."""
import importlib.util
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def copy_profile_sources(root, dataset, *, extra_attributes=()):
    profile = ROOT / 'data/datasets' / dataset / 'measurement_profile.py'
    spec = importlib.util.spec_from_file_location('_test_' + dataset + '_profile', profile)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = set(module.LOADER.values()) | set(module.SCORER.values()) | {
        f'data/datasets/{dataset}/measurement_profile.py',
        f'data/datasets/{dataset}/scoring_profile.py',
        *(getattr(module, name) for name in extra_attributes),
    }
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
