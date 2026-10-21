"""Load the pinned CostBench source without model calls or ambient configuration."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

REVISION = '6424069f3f773ade076bcedbed302ed47cfb1837'


def bootstrap(bundle, runtime=None, *, split='test', search_space=None, config_path=None):
    if sys.version_info[:2] != (3, 12):
        raise ValueError('CostBench requires Python 3.12 (pinned runtime ABI)')
    bundle = Path(bundle).resolve()
    if split not in ('train', 'test'):
        raise ValueError('unknown CostBench split')
    pins = json.loads((Path(__file__).resolve().parents[1] / 'source-files.json').read_text())
    for relative, expected in pins.items():
        path = bundle / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('CostBench source digest mismatch: ' + relative)
    sys.dont_write_bytecode = True
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    if runtime:
        sites = list(Path(runtime).glob('lib/python*/site-packages'))
        if len(sites) != 1:
            raise ValueError('CostBench runtime must have exactly one site-packages directory')
        sys.path.insert(0, str(sites[0].resolve()))
    sys.path.insert(0, str(bundle))
    import yaml
    default = bundle / 'env/config/travel_config.yaml'
    if search_space is not None:
        if config_path is None:
            raise ValueError('search_space requires a caller-owned config_path')
        raw = yaml.safe_load(default.read_text())
        raw['paths']['search_space_path'] = str(Path(search_space).resolve())
        # Seven-digit identifiers are an explicit train-only data compatibility.
        raw['runtime']['id_length'] = 7 if split == 'train' else 5
        Path(config_path).write_text(yaml.safe_dump(raw, sort_keys=False))
        default = Path(config_path).resolve()
    os.environ['COSTBENCH_TRAVEL_CONFIG'] = str(default)
    from env.settings import load_config
    load_config(reload=True)
    # The runtime ships only test enum values. Generate train classes from the
    # author's train configuration; never add held-out values to training schemas.
    from enum import Enum
    import env.domains.travel.enums as enums
    cfg = bundle / ('env/domains/travel/train_travel_config.yaml' if split == 'train'
                    else 'env/domains/travel/travel_config.yaml')
    dimensions = yaml.safe_load(cfg.read_text())['main_subtasks']
    replacements = {}
    for task, spec in dimensions.items():
        for dimension, values in spec['dimensions'].items():
            name = task.capitalize() + ''.join(part.capitalize() for part in dimension.split('_'))
            original = getattr(enums, name)
            if [x.value for x in original] != values:
                replacement = Enum(name, {value.upper(): value for value in values})
                replacements[original] = replacement
                setattr(enums, name, replacement)
    for mapping in enums.ENUM_MAPPINGS.values():
        for key, value in mapping.items():
            if isinstance(value, list):
                mapping[key] = [replacements.get(item, item) for item in value]
    for name, cls in enums.ENUM_DIRECT_MAPPINGS.items():
        enums.ENUM_DIRECT_MAPPINGS[name] = replacements.get(cls, cls)
    for module_name in ('env.core.base_types', 'env.core.data_types'):
        module = sys.modules.get(module_name)
        if module:
            for name, cls in vars(enums).items():
                if isinstance(cls, type) and issubclass(cls, Enum):
                    setattr(module, name, cls)
    from env.core import base_types
    base_types.TRAVEL_CONFIG_PATH = str(cfg)
    if 'env.run' in sys.modules:
        sys.modules['env.run'].ID_LENGTH = 7 if split == 'train' else 5
    _install_search_index(base_types, search_space)
    _install_groundtruth_lookup(search_space)
    return {'revision': REVISION, 'files': pins, 'split': split}


def _install_search_index(base_types, search_space):
    """Choose a digest-checked shard, then run the original lookup unchanged."""
    original = getattr(base_types.get_id_from_search_space, '_costbench_original',
                       base_types.get_id_from_search_space)
    if search_space is None or Path(search_space).name != 'index.json':
        base_types.get_id_from_search_space = original
        return
    index_path = Path(search_space).resolve()
    index = json.loads(index_path.read_text())
    if index.get('schema') != 'costbench.search-index.v1':
        raise ValueError('invalid CostBench database index')
    def lookup(search_space_path, task, category, tier, style, feature_package, LocationPreferenceID=None):
        if Path(search_space_path).resolve() != index_path:
            raise RuntimeError('unexpected CostBench database path')
        entry = (index['locations'] if task == 'location' else
                 index['location_information'].get(LocationPreferenceID, index['empty']))
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('invalid CostBench shard path')
        shard = index_path.parent / relative
        if shard.is_symlink() or hashlib.sha256(shard.read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('CostBench database shard digest mismatch')
        return original(str(shard), task, category, tier, style, feature_package, LocationPreferenceID)
    lookup._costbench_original = original
    base_types.get_id_from_search_space = lookup


def _install_groundtruth_lookup(search_space):
    """Route the blocker's legacy test path to the selected trusted split.

    DynamicBlocker hardcodes a relative test database path when recomputing its
    reference ID.  Only that legacy argument is redirected; lookup and digest
    checks remain the original solver/index implementation.  Static callers
    already supply their selected database and therefore keep the same behavior.
    """
    from env.utils import solver
    original = getattr(solver.calculate_groundtruth_id, '_costbench_original',
                       solver.calculate_groundtruth_id)
    if search_space is None:
        solver.calculate_groundtruth_id = original
        return
    selected = str(Path(search_space).resolve())
    def calculate(task, preferences, search_space_path, location_preference_id=None):
        if search_space_path == 'env/data/static/search_spaces/test_search_space.json':
            search_space_path = selected
        return original(task=task, preferences=preferences, search_space_path=search_space_path,
                        location_preference_id=location_preference_id)
    calculate._costbench_original = original
    solver.calculate_groundtruth_id = calculate
