"""运行日志的两种读者必须看到同一件事。

driver 同时写出给人看的文本和给面板读的 `events.jsonl`。这不是为了好看 —— 是因为
一旦两处各自格式化,面板就会开始显示一次"重建出来的"运行,而和真实运行不一致的日志
比丑陋的日志危险得多:它会成为某件没发生过的事情的证据。

这里钉住三件容易退化的事:
  * 事件流真的写了,而且带着面板排版需要的字段
  * 表里不出现永远填不上的列(`sel` 曾经每一行都是 `?`)
  * 没有测量出来的分数,不会被印成一个数字
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.console import Console                                   # noqa: E402


def _capture() -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    return Console(stream=stream), stream


# ------------------------------------------------------- 表格不印空列 ---

def _point(**over):
    point = {
        "round": 0, "score": 0.167, "score_ci95": [0.0, 0.4],
        "per_task": {"t01": 0.0, "t02": 0.5}, "train_score": 0.25,
        "train_per_task": {"t03": 1.0, "t04": 0.0},
        "split": {"train": ["t03", "t04"], "eval": ["t01", "t02"]},
        "selection_effect": {"selected_on_reported_set": None,
                             "selection_pool_size": None},
        "identity": {"harness_sha": "abcdef0123456789"},
        "measured_by_platform": True,
    }
    point.update(over)
    return point


def test_the_table_does_not_print_a_column_it_never_fills():
    """`sel` 在 mode A 的每一条曲线上都是 `?`。一列恒定的噪声会教人跳过整张表。"""
    console, stream = _capture()
    console.results([_point()], split={"train": ["t03", "t04"],
                                       "eval": ["t01", "t02"]})
    out = stream.getvalue()
    assert "?" not in out, f"表里还留着填不上的列:\n{out}"
    assert "sel" not in out, f"空的 sel 列不该被印出来:\n{out}"


def test_the_selection_column_comes_back_when_it_has_something_to_say():
    """mode B 会真的设置它。有内容时必须显示,否则这次省略就变成了隐藏信息。"""
    console, stream = _capture()
    console.results([_point(selection_effect={
        "selected_on_reported_set": True, "selection_pool_size": 5})])
    out = stream.getvalue()
    assert "sel" in out and "yes" in out, out


def test_an_unmeasured_point_is_never_printed_as_a_score():
    """平台没产出的数字,不能和平台产出的数字长得一样。"""
    console, stream = _capture()
    console.results([_point(measured_by_platform=False,
                            method_reported={"score": 0.9})])
    out = stream.getvalue()
    assert "not measured" in out
    assert "method" in out
    assert "0.900" in out, "方法自称的分数仍然要看得见,只是必须标明是它的"


def test_the_train_column_appears_only_when_a_point_has_one():
    """**这条以前是 `bool(split)`,在新契约下恒真且恒错。**

    一次运行只有一侧,所以那个"train"列会永远是第一列的副本 —— 而重复的列会让人
    以为测了两个集合。它真正要回答的是"这次测量里有没有第二个任务集",所以它现在
    看的就是那个。两个方向都测:没有就不印,有就印。
    """
    both = {"train": ["t03", "t04"], "eval": ["t01", "t02"]}

    console, stream = _capture()
    console.results([_point(train_score=None, train_per_task={})], split=both)
    assert "train" not in stream.getvalue(), (
        "没有 train 分数却印了 train 列 —— 那是 per_task 的副本")

    console, stream = _capture()
    console.results([_point()], split=both)
    assert "train" in stream.getvalue(), (
        "点上有 train 分数(旧记录的形态)却不印那一列,读的人就不知道测了两组")


# --------------------------------------------------------- 事件流本身 ---

def _run(tmp_path: Path, run_id: str = "console-check"):
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", run_id, "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"},
    )
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    return runs / run_id, proc


def test_the_run_writes_the_event_stream_the_panel_lays_out(tmp_path):
    """面板不解析终端文本。它需要的字段必须真的在事件里。"""
    run_dir, _ = _run(tmp_path)
    events = [json.loads(l) for l in
              (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    kinds = [e["kind"] for e in events]
    assert "header" in kinds, kinds

    header = next(e for e in events if e["kind"] == "header")
    for field in ("run_id", "harness", "mode", "dataset", "n_tasks", "model",
                  "backend", "sandbox", "split"):
        assert field in header, f"表头缺 {field}:{sorted(header)}"
    assert header["split"]["train"] and header["split"]["eval"], \
        "面板要靠这个显示「训练 4 / 评测 4」"

    rounds = [e for e in events if e["kind"] == "round"]
    assert rounds, "至少要有一轮"
    for e in rounds:
        assert "eval" in e and "train" in e and "measured" in e, e

    # 有序号:面板重渲染时靠它判断有没有新事件,顺序错了会把旧事件画成新的
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs), seqs


def test_the_run_records_where_its_artifacts_are(tmp_path):
    run_dir, _ = _run(tmp_path, "console-artifacts")
    events = [json.loads(l) for l in
              (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    labels = {e["label"] for e in events if e["kind"] == "artifact"}
    assert {"curve", "workspace"} <= labels, labels


def test_no_ansi_escapes_reach_the_drivers_output(tmp_path):
    """driver 的输出会被写进文件、再被读出来展示。

    颜色码只在真终端里有意义,在文件里是一串垃圾 —— 而且它会让下游所有"读日志"的
    代码都得先剥一层转义序列。所以这里不写颜色,靠版式而不是靠色块来表达结构。
    """
    _, proc = _run(tmp_path, "console-ansi")
    assert "\x1b[" not in proc.stdout, "driver 的输出里有 ANSI 转义序列"
    assert "\x1b[" not in proc.stderr


def test_a_reused_run_id_is_refused_and_previous_events_are_preserved(tmp_path):
    """A fresh run cannot silently replace or append to an existing receipt."""
    run_dir, first = _run(tmp_path, "reused-id")
    assert first.returncode == 0
    before = (run_dir / 'events.jsonl').read_bytes()
    second = subprocess.run(
        [sys.executable, 'driver.py', '--harness', 'loop_plain', '--mode', 'A',
         '--rounds', '1', '--run-id', 'reused-id', '--dataset', 'demo', '--sampling', 'all',
         '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / 'work'),
         '--method-entrypoint', f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, 'HG_AGENT_BACKEND': 'mock'})
    assert second.returncode != 0 and 'already exists' in second.stderr
    assert (run_dir / 'events.jsonl').read_bytes() == before

    events = [json.loads(l) for l in
              (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    headers = [e for e in events if e["kind"] == "header"]
    assert len(headers) == 1, \
        f"一个 events.jsonl 里有 {len(headers)} 次运行的表头,面板会把它们叠起来显示"
    seqs = [e["seq"] for e in events]
    assert seqs[0] == 1 and seqs == sorted(seqs), \
        f"seq 必须从 1 起且单调:{seqs[:6]}…"


# ------------------------------------------- 记录必须在产生时就可读,不能等结束 ---

#: 一个会挂住一会儿的方法,用来在"运行还没结束"时观察磁盘。
SLOW_STUB = '''\
import json, sys, time
from pathlib import Path
req = json.loads(sys.stdin.read())
base = Path(req["base_harness"])
time.sleep(8)
Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": [{"harness_dir": str(base), "label": "slow", "edit_kind": "none",
               "claimed_cost": {}, "method_reported": {}}],
    "trajectory_shape": "sequence", "nominated": 0, "provenance": {"method": "slow"}}))
sys.stdout.write(json.dumps({"changed": False}))
'''


def test_the_curve_is_on_disk_before_the_run_ends(tmp_path):
    """`curve.jsonl` 必须**在产生时就落盘**,不能等运行结束才写。

    它以前只在 main 结尾写一次。后果不是"少一个文件",而是:一次被中止的运行会把
    **上一次**的曲线留在盘上,于是控制台显示的曲线属于一个和 `run_meta.json` 里
    不同的方法 —— 读起来就是"我选的是 RRSI,为什么在跑 DGM"。
    """
    import time

    stub = tmp_path / "slow_method.py"
    stub.write_text(SLOW_STUB)
    runs = tmp_path / "runs"
    proc = subprocess.Popen(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "durable", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {stub}"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})

    curve = runs / "durable" / "curve.jsonl"
    # The method sleeps, so the run *cannot* finish before the curve is written; the deadline only has
    # to outlast a slow start. Measured 2026-10-15: a 6 s deadline flaked once this box was carrying a
    # 40-job dataset pass, while the durability property itself still held. The loop now also stops if
    # the process exits, so a real failure still reports through the assertion below rather than a
    # timeout, and the property asserted is unchanged: the curve must be on disk **while the run is
    # still going**.
    deadline = time.time() + 120
    while time.time() < deadline and not curve.exists() and proc.poll() is None:
        time.sleep(0.1)

    assert proc.poll() is None, "方法还在睡,运行不该已经结束"
    assert curve.exists(), "运行还没结束,但 curve.jsonl 还不在盘上 —— 中止就会留下上一次的"
    points = [json.loads(l) for l in curve.read_text().splitlines() if l.strip()]
    assert len(points) >= 1 and points[0]["round"] == 0, points

    proc.kill()
    proc.wait(timeout=30)


def test_a_reused_run_id_does_not_inherit_the_previous_runs_run_meta(tmp_path):
    """`run_meta.json` 是合并写的(为了中途补全),所以第一次写必须显式清空。

    实测:一个 `method_entrypoint` 写着 `rrsi` 的 run 目录里,躺着上一次 DGM 运行的
    `rounds_measured: 7` 和 `final_score: 1.0`,两次运行读起来像一次。
    """
    from harnessgrad.records import _write_run_meta

    meta = tmp_path / "run_meta.json"
    _write_run_meta(meta.parent, run_id="first", method_entrypoint="dgm",
                           rounds_measured=7, final_score=1.0)
    assert json.loads(meta.read_text())["rounds_measured"] == 7

    # 新的一次运行,同名 —— 必须看不到上一次的任何字段
    _write_run_meta(meta.parent, fresh=True, run_id="second",
                           method_entrypoint="rrsi")
    after = json.loads(meta.read_text())
    assert after["method_entrypoint"] == "rrsi"
    assert "rounds_measured" not in after, f"继承了上一次运行的字段:{after}"
    assert "final_score" not in after, after

    # 而同一次运行内的后续补全仍然是合并的
    _write_run_meta(meta.parent, rounds_measured=3, final_score=0.5)
    merged = json.loads(meta.read_text())
    assert merged["method_entrypoint"] == "rrsi" and merged["rounds_measured"] == 3
