"""Keep an interrupted measurement's evaluation limits immutable on resume."""
from __future__ import annotations

import json
from pathlib import Path

from harnessgrad.config import current
from harnessgrad.config.limits import parse


def _policy(value):
    if value is None:
        return {}
    checked = parse(json.dumps(value))

    def compact(settings):
        # Empty area/dataset objects do nothing. A null leaf is an explicit
        # override, however, and must not disappear during comparison.
        return {key: compact(item) if isinstance(item, dict) else item
                for key, item in settings.items()
                if not isinstance(item, dict) or compact(item)}

    return compact(checked)


def resume_policy_mismatch(run_dir: Path) -> str | None:
    """Check before writing metadata or continuing either train or eval work.

    Legacy runs without this field used the empty operator policy. Other resume
    guards still decide whether their task/state evidence is sufficient.
    """
    path = run_dir / 'run_meta.json'
    try:
        meta = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(meta, dict):
            raise ValueError('run metadata must be an object')
        config = meta.get('config')
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValueError('recorded config must be an object')
        runtime = config.get('runtime')
        if runtime is None:
            runtime = {}
        if not isinstance(runtime, dict):
            raise ValueError('recorded runtime config must be an object')
        before = _policy(runtime.get('evaluation_limits'))
        after = _policy(current().runtime.evaluation_limits)
    except (OSError, TypeError, ValueError) as exc:
        return f'cannot verify HG_EVAL_LIMITS against run_meta.json: {type(exc).__name__}'
    if before != after:
        return 'HG_EVAL_LIMITS differs from the interrupted run; use its original policy or a new run ID'
    return None
