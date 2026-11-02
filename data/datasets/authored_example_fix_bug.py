"""由 `tools/scenario_builder.py --publish` 生成；改内容请改 `data/authored/example-fix-bug/bundle.json`，不要改这里。"""
from data.scaffolds.authored import load as _load_authored

KIND = "dataset"
ORACLE_SOURCE = "authored"
ORACLE_VALIDATED_BY = "tools/scenario_builder.py --gates (oracle / empty / deterministic)"
ORACLE_COVERAGE = "all tasks"
OFFICIAL_EVAL = None
SPLIT_SOURCE = "authored bundle: every task in the bundle is scored"
TASKS, SETUP, VERIFY = _load_authored("example-fix-bug")
def load():
    """The registry protocol: (tasks, scorable). Baked-in answers would be a
    second source of truth; an authored bundle grades by its verifier.
    """
    return TASKS, {}
DEFAULT_ENV = {"kind": "files"}
DEFAULT_SPLIT = {"by": "sha256(task_id)", "eval_fraction": 0.2}
