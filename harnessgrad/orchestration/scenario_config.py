"""Reviewed scenario admission and adapter selector policy.

Return option/environment deltas; the entrypoint owns process and parser mutations.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from eval.run.metrics import select_task_set


@dataclass(frozen=True)
class ScenarioOptions:
    dataset: str
    split: str | None
    side: str
    sampling: str
    rounds: int | None
    trials: int
    pool: str | None
    jobs: int


def scenario_option_updates(current: ScenarioOptions, spec: dict, explicit: set[str],
                            *, platform_root: Path, select_sampling=select_task_set):
    """Bind reviewed values before policy selection; never silently override an explicit flag."""
    values = {key: spec[key] for key in ('dataset', 'split', 'side', 'sampling', 'rounds', 'trials')}
    values.update(pool=spec.get('pool'), jobs=spec['budget']['jobs'])
    split = values['split']
    if split not in ('declared', 'default', 'official'):
        path = (platform_root / split).resolve(strict=True)
        if not path.is_relative_to(platform_root.resolve()) or not path.is_file():
            raise ValueError('scenario split must be a readable file inside the platform')
        values['split'] = str(path)
    try:
        select_sampling({}, values['sampling'])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"scenario sampling is not supported: {exc}") from exc
    if values['side'] == 'eval' and values['sampling'] != 'all':
        raise ValueError('scenario eval sampling must be all; other policies need separate admission')
    for field, value in values.items():
        actual = getattr(current, field)
        if field == 'split' and field in explicit and actual not in ('declared', 'default', 'official'):
            actual = str(Path(actual).resolve(strict=True))
        if field in explicit and actual != value:
            raise ValueError(f'scenario {field} conflicts with explicit --{field}')
        yield field, value


def pool_environment(dataset: str, pool: str | None) -> dict[str, str]:
    """Reuse existing adapter pool selectors, before an adapter can be imported."""
    if pool is None:
        return {}
    key = {'appworld': 'HG_APPWORLD_POOL', 'alfworld': 'HG_ALFWORLD_POOL'}.get(dataset)
    if key:
        return {key: pool}
    if pool in ('train', 'eval'):
        return {}  # This is already the side selected by the driver.
    raise ValueError(f'pool {pool!r} has no existing selector for dataset {dataset!r}')


def check_scenario_dataset(spec: dict, *, platform_root: Path, datasets) -> None:
    """Published datasets, plus the senior-approved local mock/train smoke fixture."""
    name = spec['dataset']
    if (name == 'demo' and spec['name'] == 'demo-smoke' and spec['side'] == 'train'
            and spec['model']['backend'] == 'mock'):
        return
    if name in datasets.published():
        return
    if name.isidentifier() and (platform_root / 'data/datasets' / f'{name}.py').is_file():
        try:
            datasets.kind_of(name)
        except (ImportError, OSError, ValueError) as exc:
            raise ValueError(f'dataset {name!r} is not provisioned: {exc}') from exc
        raise ValueError(f'dataset {name!r} is not currently published')
    raise ValueError(f'unknown dataset {name!r} in scenario (not a published dataset)')


def check_scenario_resume(enabled: bool, run_id: str | None, runs_root: str, receipt: dict | None) -> None:
    """Refuse changed scenario identity before the existing resume path writes metadata."""
    if not enabled:
        return
    if not run_id:
        if receipt is not None:
            raise ValueError('scenario resume requires an explicit --run-id')
        return
    path = Path(runs_root) / run_id / 'run_meta.json'
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        if receipt is not None:
            raise ValueError(f'scenario resume needs readable original metadata: {exc}') from exc
        return  # Preserve the legacy resume path's own refusals.
    prior = saved.get('scenario') if isinstance(saved, dict) else None
    if receipt is None and prior is None:
        return
    if (not isinstance(prior, dict) or receipt is None or not prior.get('sha256')
            or any(prior.get(k) != receipt.get(k) for k in ('name', 'sha256'))):
        raise ValueError('scenario identity is missing or changed; use the original scenario or a fresh run ID')
