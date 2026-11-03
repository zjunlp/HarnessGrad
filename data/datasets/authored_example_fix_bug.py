"""由 `tools/scenario_builder.py --publish` 生成；改内容请改 `data/authored/example-fix-bug/bundle.json`，不要改这里。"""
from data.scaffolds.authored import load as _load_authored

KIND = "dataset"
ORACLE_SOURCE = "authored"
ORACLE_VALIDATED_BY = "tools/scenario_builder.py --gates (oracle / empty / deterministic)"
ORACLE_COVERAGE = "all tasks"
OFFICIAL_EVAL = None
SPLIT_SOURCE = "authored bundle: every task in the bundle is scored"
_BUNDLE_NAME = "example-fix-bug"
TASKS, SETUP, VERIFY = _load_authored("example-fix-bug")
def load():
    """The registry protocol: (tasks, scorable). Baked-in answers would be a
    second source of truth; an authored bundle grades by its verifier.
    """
    return TASKS, {}
def downloads():
    """一个 authored bundle 的数据就是它自己（`data/authored/<slug>`，随 git 走）。

    不写 `downloads()` 是沉默，不是"没有数据"：这里声明来源、内容摘要与字节数，
    于是 run 的记录能说清它量的是哪一份 bundle。
    """
    import hashlib
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "authored" / "example-fix-bug"
    digest = hashlib.sha256((root / "bundle.json").read_bytes()).hexdigest()
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return [{"kind": "authored", "name": _BUNDLE_NAME, "version": digest[:16],
             "size": f"{size} bytes",
             "note": "authored scenario bundle: bundle.json + materials + oracle, "
                     "versioned with this repository",
             "path": "data/authored/example-fix-bug", "sha256": digest}]
DEFAULT_ENV = {"kind": "files"}
DEFAULT_SPLIT = {"by": "sha256(task_id)", "eval_fraction": 0.2}
