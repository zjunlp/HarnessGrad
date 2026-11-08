"""Legacy snapshot checkout adapter; candidate recovery uses fd-based services."""
from __future__ import annotations
import pathlib
import shutil

def check_out(snapshot_file, target, *, api) -> str | None:
    """把快照签出成候选目录；成功返回 None，失败返回一句能放进错误详情的理由。"""
    if api is None:
        return "控制台找不到 harnessgrad/snapshot.py"
    try:
        api.check_out(snapshot_file, target)
    except (api.SnapshotError, OSError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        return str(exc)
    if not (target / "harness.json").is_file():
        shutil.rmtree(target, ignore_errors=True)
        return f"{pathlib.Path(snapshot_file).name} 里没有 harness.json"
    return None
