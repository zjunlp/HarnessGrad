"""准备环境:别人拿到平台之后,「配置一次」到底是一条命令还是一个按钮。

一次运行要两样东西,它们属于**不同的主人**,这也是为什么它们分开报、分开做:

  * **任务的镜像** —— 环境本身。Terminal-Bench 是 89 个镜像、本地实测约 104 GB,从
    registry 拉。§2.5.5 禁止运行中途拉取(拉取会以 daemon 的权限执行第三方内容,而那发生
    在任何沙箱存在之前),所以它必须是一个**人刻意的动作**。一个按钮正好是这个动作。
  * **harness 自己的依赖** —— 与任务无关。一份 overlay = `(install 内容, Python 小版本)`,
    约 12 秒 / 42 MB,而份数是**数据集**的属性,不是 harness 的属性。

在这之前两样都只有 CLI:拉镜像是 `import_env.py --image`(一次一个,89 次要手工循环),
建 overlay 是 `configure_harness.py`。界面只**报告**结果(3 题标成不可跑),不让你修。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import import_env                                               # noqa: E402
import serve_ui                                                 # noqa: E402


# ---------------------------------------------- 枚举:一条命令看整个数据集 ---

def test_a_dataset_can_be_enumerated_in_one_call():
    """`--dataset` 之前不存在,所以「配置这个数据集」要手工从 task.toml 里扒 89 个 tag。"""
    images = import_env.dataset_images("terminal_bench")
    assert len(images) == 89, len(images)
    assert all(isinstance(t, str) and isinstance(i, str) for t, i in images)
    ids = [t for t, _ in images]
    assert len(set(ids)) == len(ids), "同一个 task 出现了两次"


def test_a_files_dataset_has_no_images_to_import():
    """没有容器任务的数据集不是"缺 0 个",是"不需要" —— 面板要能分清这两件事。"""
    assert import_env.dataset_images("probe_set") == []


def test_import_env_can_be_asked_not_to_pull():
    """`--no-pull` 让这一步在离线主机上也能问一句「缺什么」而不动手。"""
    import subprocess
    proc = subprocess.run(
        [sys.executable, "tools/import_env.py", "--dataset", "probe_set", "--no-pull",
         "--json"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-400:]
    assert "declares no `exec` tasks" in proc.stdout


# ------------------------------------------------------ 只读报告:缺什么 ---

def test_the_report_says_what_is_missing_without_touching_anything():
    """报告必须便宜且只读:它绝不能自己去探测解释器,那是配置任务该干的活。"""
    report = serve_ui.prepare_report("terminal_bench", "loop")
    assert report["images"]["total"] == 89
    assert report["images"]["local"] + len(report["images"]["missing"]) == 89
    assert report["dependencies"]["declared"] == "requirements.txt"
    assert isinstance(report["dependencies"]["configured"], list)
    assert {a["id"] for a in report["actions"]} == {"overlay", "images"}


def test_a_harness_that_declares_nothing_is_not_reported_as_broken():
    """`cli_agent` 不声明 install:正确的读法是"不需要配置",不是一个待修的缺项。"""
    report = serve_ui.prepare_report("terminal_bench", "cli_agent")
    assert report["dependencies"]["declared"] is None
    overlay = next(a for a in report["actions"] if a["id"] == "overlay")
    assert overlay["runnable"] is False and overlay["reason"], \
        "说它不能做,却不说为什么,读的人只会以为按钮坏了"


def test_a_bad_dataset_is_refused_not_guessed():
    assert "error" in serve_ui.prepare_report("../etc", "loop")
    assert "error" in serve_ui.prepare_report("no_such_dataset_xyz", "loop")


def test_a_dataset_with_no_containers_reports_no_images():
    report = serve_ui.prepare_report("probe_set", "loop")
    assert report["images"]["total"] == 0
    images = next(a for a in report["actions"] if a["id"] == "images")
    assert images["runnable"] is False


# ------------------------------------------- 执行:后台任务 + 流式日志 ---

def test_an_unknown_step_is_refused():
    assert "error" in serve_ui.start_prepare(
        {"what": "rm-rf", "dataset": "probe_set", "harness": "loop"})


def test_a_prepare_job_writes_its_own_log_and_starts_from_the_tool():
    """任务跑的是**已有的工具**,不是把逻辑重新实现一遍。

    理由和 driver 用子进程而不是 import 一样:一次配置可能跑几十分钟,而且它执行的是
    第三方内容;隔一个进程边界,失败就只是一份日志,不是一个半途被污染的界面进程。
    """
    started = serve_ui.start_prepare(
        {"what": "overlay", "dataset": "probe_set", "harness": "loop"})
    assert "started" in started, started
    job = serve_ui.prepare_status(started["started"])
    assert job["prepare"] == "overlay"
    assert job["log"], "没有日志的准备工作就是一个转圈图标"
    log = job["log"]
    # 命令本身留在日志第一行 —— 它是最直接的复现方式
    assert "configure_harness.py" in log, log[:300]


def test_a_prepare_job_lives_outside_the_run_directory():
    """它不是一次实验:一个在 `runs/` 里找曲线的人不该撞见一个从未发生过的 run。"""
    assert serve_ui.PREPARE_ROOT.parent == serve_ui.RUNS
    assert serve_ui.PREPARE_ROOT.name == "_prepare"


def test_prepare_status_tolerates_an_unknown_job():
    assert serve_ui.prepare_status("no-such-job")["log"] == ""
    assert "error" in serve_ui.prepare_status("../../etc")


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
