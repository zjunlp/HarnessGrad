"""Read reviewed scenario files without importing adapters or running a harness."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath


def _validate(spec: dict, expected_name: str) -> None:
    """Validate the frozen scenario shape; runtime availability belongs to the caller."""
    if not isinstance(spec, dict):
        raise ValueError('scenario must contain a JSON object')
    for field in ('name', 'dataset', 'split', 'side', 'sampling'):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            raise ValueError(f'scenario {field} must be a non-empty string')
    if spec['name'] != expected_name:
        raise ValueError('scenario name must match its filename')
    if spec['side'] not in ('train', 'eval'):
        raise ValueError('scenario side must be train or eval')
    split = spec['split']
    if (Path(split).is_absolute() or PureWindowsPath(split).anchor
            or '..' in PureWindowsPath(split).parts or '..' in Path(split).parts):
        raise ValueError('scenario split must be a symbol or a relative path inside the platform')
    if spec['side'] == 'eval' or 'pool' in spec:
        if not isinstance(spec.get('pool'), str) or not spec['pool'].strip():
            raise ValueError('scenario pool must be a non-empty string for eval')
    for field, minimum in (('rounds', 0), ('trials', 1)):
        if type(spec.get(field)) is not int or spec[field] < minimum:
            raise ValueError(f'scenario {field} must be an integer >= {minimum}')
    kinds = spec.get('env_kinds')
    if (not isinstance(kinds, list) or not kinds
            or any(not isinstance(k, str) or not k.strip() for k in kinds)):
        raise ValueError('scenario env_kinds must be a non-empty list of strings')
    budget = spec.get('budget')
    if not isinstance(budget, dict) or type(budget.get('jobs')) is not int or budget['jobs'] != 1:
        raise ValueError('scenario budget.jobs must be 1')
    model = spec.get('model')
    if not isinstance(model, dict) or model.get('backend') not in ('mock', 'live'):
        raise ValueError('scenario model.backend must be mock or live')
    if 'required' in model and type(model['required']) is not bool:
        raise ValueError('scenario model.required must be a boolean')
    if 'limits' in spec:
        limits = spec['limits']
        if not isinstance(limits, dict):
            raise ValueError('scenario limits must be an object')
        for field, value in limits.items():
            if (type(value) not in (int, float) or value <= 0
                    or (type(value) is float and not math.isfinite(value))):
                raise ValueError(f'scenario limits.{field} must be a finite positive number')
    if 'note' in spec and not isinstance(spec['note'], str):
        raise ValueError('scenario note must be a string')


def load(name: str, root: Path | None = None) -> tuple[dict, dict]:
    """Return a scenario and {name, path, sha256} of its original file bytes.

    NAME is one filename stem, not a path. The default root is this package;
    tests may supply their own directory. SHA-256 covers the bytes actually
    parsed, including whitespace, rather than a re-serialized JSON object.
    """
    if (not isinstance(name, str) or not name.strip() or name in ('.', '..')
            or any(c in name for c in '/\\:\0')):
        raise ValueError('scenario name must be a single non-empty filename stem')
    root = (Path(root) if root is not None else Path(__file__).parent).resolve(strict=True)
    try:
        path = (root / f'{name}.json').resolve(strict=True)
    except RuntimeError as exc:  # pathlib before 3.13 reports symlink loops this way.
        raise ValueError(f'scenario name could not be resolved: {exc}') from exc
    if not path.is_relative_to(root):
        raise ValueError('scenario file resolves outside the scenario directory')
    if not path.is_file():
        raise ValueError('scenario must be a regular file')
    raw = path.read_bytes()
    try:
        spec = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f'scenario is not valid UTF-8 JSON: {exc}') from exc
    _validate(spec, name)
    return spec, {'name': name, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
