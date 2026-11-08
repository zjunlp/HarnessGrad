"""Read-only console health projection from explicit observations and roots."""
import shutil
if __package__:
    from .run_query import kind_counts
else:
    from run_query import kind_counts

def describe(live, rows, *, runs, explain_missing, lookup, read_json) -> dict:
    sha = ""
    for row in rows[:16]:
        meta = read_json(runs / row["id"] / "run_meta.json")
        platform = meta.get("platform")
        sha = (platform.get("content_sha") if isinstance(platform, dict) else "") or ""
        if sha:
            break
    try:
        usage = shutil.disk_usage("/")
        disk_free = round(usage.free / 2**30, 1)
    except OSError:
        disk_free = None
    # 只报告落在本 console 的 runs 根目录下的 driver；别的 work-root 不属于这一屏。
    running = [{"run_id": k, **v} for k, v in sorted(live.items()) if lookup(k)]
    return {
        "platform_content_sha": sha or None,
        "running": running,
        "disk_free_gb": disk_free,
        "runs_root": str(runs),
        "runs_count": len(rows),
        "kinds": kind_counts(rows),
        # 只报告“是否配置”，不报告 base_url / model / key。前端据此在未配置时
        # 直接显示 setup note，而不是先发一个注定 503 的请求（浏览器会把它记成
        # console error，而只读台的控制台必须是干净的）。
        "explain_configured": not explain_missing,
        "explain_missing_env": explain_missing,
    }
