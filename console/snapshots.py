"""Read-only loader for the configured platform's portable snapshot API."""
import importlib.util
import pathlib

def load(platform):
    """`harnessgrad/snapshot.py`，从控制台指向的那棵平台树里按文件加载。

    控制台可以独立启动（`python3 console/server.py`），所以不把平台根塞进 `sys.path`；
    直接按路径加载。平台树里没有这个模块时为 None —— 快照这一路当作不可用，另外两路照旧。
    """
    source = pathlib.Path(platform) / "harnessgrad" / "snapshot.py"
    if not source.is_file():
        return None
    spec = importlib.util.spec_from_file_location("hg_console_snapshot", source)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError):
        return None
    return module


def _round_snapshot_path(run_path, round_no: int) -> pathlib.Path:
    return run_path / "snapshots" / f"round-{round_no}.json"
