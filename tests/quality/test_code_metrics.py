"""候选 harness"作为代码"是什么 —— 诊断,不是分数。

**为什么值得学这一条。** HarnessDev 在他们的 creator 写出来、进化出来的 harness 上量过:
"169 个新增函数或类里,113 个从入口可达,31 个只被死代码可达,25 个没有任何调用者"(§4.3)。
没有这个数字,本平台会把"追加了 25 个没人调用的函数"和"什么都没改"打成一模一样的分数 ——
而永不执行的死代码在 diff 里、在 token 计数里、在"这一轮做了 14 处编辑"里**看起来都像工作**。

**这个文件同时钉住它的三种错法**(静态、仅 Python、名字匹配),因为一个"可达性数字"
天然比它应得的更让人信任:
  * 动态派发看不见 —— `getattr`/`importlib`/`exec`/字符串表/子进程调用兄弟脚本,只走这些
    路径的函数会被读成"没有调用者";
  * 非 Python 的 harness 返回 `language: "other"`,**不给可达性数字** —— 零会读成"测过了,
    结果是零",而"没法看"不是"看了没发现";
  * "可达"是"被入口传递 import 的文件里出现过",不是"这一轮真的执行过"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import code_metrics  # noqa: E402


def _harness(tmp_path: Path, files: dict[str, str], *, entry: str = "agent.py") -> Path:
    root = tmp_path / "cand"
    root.mkdir(parents=True)
    (root / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": entry}))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _names(measured: dict) -> set[str]:
    return {u["name"] for u in measured["unreferenced"]}


def test_a_module_nobody_imports_is_unreachable(tmp_path):
    """HarnessDev 的"只被死代码可达",在文件粒度上。"""
    root = _harness(tmp_path, {
        "agent.py": ("from helpers import used\n\n\ndef main():\n    used()\n\n\n"
                     "if __name__ == \"__main__\":\n    main()\n"),
        "helpers.py": "def used():\n    return 1\n",
        "orphan.py": "def nobody_imports_me():\n    return 2\n",
    })
    got = code_metrics.measure(root)
    assert got["language"] == "python"
    assert got["python_files"] == 3
    assert got["entrypoint"] == "agent.py"
    assert got["unreachable_modules"] == ["orphan.py"]


def test_a_definition_no_one_names_is_unreferenced(tmp_path):
    """HarnessDev 的"没有任何调用者"。`dead_fn` 的**模块**可达,函数本身没有调用者 —— 两者分开记。"""
    root = _harness(tmp_path, {
        "agent.py": ("import dead\n\n\nclass Runner:\n"
                     "    def __init__(self):\n        self.x = 1\n\n\n"
                     "def main():\n    Runner()\n    dead.dead_fn()\n\n\n"
                     "def never_called():\n    return 3\n\n\n"
                     "if __name__ == \"__main__\":\n    main()\n"),
        "dead.py": "def dead_fn():\n    return 1\n\n\ndef also_never_called():\n    return 2\n",
    })
    got = code_metrics.measure(root)
    assert _names(got) == {"never_called", "also_never_called"}, got["unreferenced"]
    by_name = {u["name"]: u for u in got["unreferenced"]}
    assert by_name["never_called"]["file"] == "agent.py"
    assert by_name["never_called"]["kind"] == "function"
    # 模块可达不等于函数被调用:死函数在可达的模块里也要被抓出来
    assert by_name["also_never_called"]["reachable"] is True
    assert got["functions"] == 4, got


def test_dunders_are_exempt_from_having_no_caller(tmp_path):
    """`__init__` 由运行时调用,没有文本调用者说明不了任何事。"""
    root = _harness(tmp_path, {
        "agent.py": ("class R:\n    def __init__(self):\n        pass\n\n\n"
                     "def main():\n    R()\n\n\n"
                     "if __name__ == \"__main__\":\n    main()\n"),
    })
    assert code_metrics.measure(root)["unreferenced"] == []


def test_a_method_reached_only_dynamically_is_reported_as_unreferenced(tmp_path):
    """**已知的错法,写成测试。** `getattr(module, name)` 派发看不见 —— 这条既说明限制,
    也说明我们选择让字符串计数参与(所以这里的字符串确实把它救了回来)。"""
    root = _harness(tmp_path, {
        "agent.py": ("import handlers\n\n\ndef main():\n"
                     "    getattr(handlers, 'chosen')()\n\n\n"
                     "if __name__ == \"__main__\":\n    main()\n"),
        "handlers.py": "def chosen():\n    return 1\n",
    })
    # 字符串 'chosen' 被计为引用 —— 这是刻意降低假阳性,不是精确
    assert code_metrics.measure(root)["unreferenced"] == []


def test_relative_imports_resolve(tmp_path):
    """`from . import x` / `from .x import y` 要能解析,否则包里的一切都会被读成不可达。"""
    root = _harness(tmp_path, {
        "agent.py": "from pkg import part\n\n\ndef main():\n    part.go()\n",
        "pkg/__init__.py": "",
        "pkg/part.py": "from . import helper\n\n\ndef go():\n    helper.h()\n",
        "pkg/helper.py": "def h():\n    return 1\n",
    })
    got = code_metrics.measure(root)
    assert got["unreachable_modules"] == [], got["unreachable_modules"]
    assert got["entrypoint"] == "agent.py"


def test_dependency_trees_are_not_the_harnesss_code(tmp_path):
    """平台会为一个声明了依赖的 harness 绑定 venv;把 site-packages 当"harness 的代码"
    会报出几百个属于别人的无调用者函数。"""
    root = _harness(tmp_path, {
        "agent.py": ("def main():\n    return 0\n\n\n"
                     "if __name__ == \"__main__\":\n    main()\n"),
        ".venv/lib/site-packages/lib.py": "def library_function():\n    return 1\n",
        "__pycache__/stale.py": "def stale():\n    return 2\n",
    })
    got = code_metrics.measure(root)
    assert got["python_files"] == 1
    assert got["unreferenced"] == []


def test_a_harness_that_is_not_python_gets_no_reachability_numbers(tmp_path):
    """零会读成"测过了,结果是零"。"没法看"必须长得像"没法看"。"""
    root = tmp_path / "sh"
    root.mkdir()
    (root / "harness.json").write_text(json.dumps({"name": "sh", "version": "1",
                                                   "entrypoint": "agent.sh"}))
    (root / "agent.sh").write_text("#!/bin/sh\necho hi\n")
    got = code_metrics.measure(root)
    assert got["language"] == "other"
    assert got["python_files"] == 0
    assert got["unreachable_modules"] == [] and got["unreferenced"] == []


def test_a_candidate_that_does_not_parse_is_reported_not_skipped(tmp_path):
    """语法错的文件要说出来 —— 静默只统计能解析的部分,等于把一个坏候选报成好候选。"""
    root = _harness(tmp_path, {
        "agent.py": "def main(:\n",
        "ok.py": "def fine():\n    return 1\n",
    })
    got = code_metrics.measure(root)
    assert len(got["parse_errors"]) == 1
    assert got["parse_errors"][0]["file"] == "agent.py"
    assert "SyntaxError" in got["parse_errors"][0]["error"]
    assert got["python_files"] == 2, "坏文件也要算进文件数,否则规模被悄悄缩小"


def test_an_empty_or_missing_directory_is_not_an_error(tmp_path):
    """被拒绝的候选可能连目录都没有。诊断不能因此抛异常 —— 这恰恰是最想问"方法到底做了什么"的时候。"""
    assert code_metrics.measure(tmp_path / "nope")["language"] == "other"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert code_metrics.measure(empty)["python_files"] == 0


def test_the_real_reference_harness_is_clean(tmp_path):
    """我们自己的参考 harness 是单文件、没有死代码 —— 这条是基线,不是目标。"""
    got = code_metrics.measure(ROOT / "base_harness" / "loop")
    assert got["language"] == "python"
    assert got["unreachable_modules"] == []
    assert got["unreferenced"] == []
    assert got["parse_errors"] == []


def test_the_diagnostic_reaches_the_curve_point_end_to_end(tmp_path):
    """**接线测试。** 诊断必须真的落到曲线点上,否则它只是又一个没人读的计算。

    一条 mode A 的运行,方法往候选里塞两样东西:一个没有调用者的函数、一个没人 import
    的模块。两样都要在 round 1 的点上看得见 —— 而且 H0 的点上**不该有**这个字段:
    H0 不是方法交出来的候选,给基线配一个"候选代码"读数就是在描述一件没发生的事。
    """
    import os
    import subprocess

    method = tmp_path / "fake_method.py"
    method.write_text(f'''
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "methods")!r})
from protocol import emit, read_request

req = read_request()
base, dest = Path(req["base_harness"]), Path(req["workspace"]) / "next"
if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(base, dest)
agent = dest / "agent.py"
agent.write_text(agent.read_text() + "\\n\\ndef added_but_never_called():\\n    return 41\\n")
(dest / "orphan_module.py").write_text("def nobody_imports_this():\\n    return 42\\n")
Path(req["trajectory_out"]).write_text(json.dumps({{
    "steps": [{{"harness_dir": str(dest), "label": "add dead code",
               "edit_kind": "harness_source", "claimed_cost": {{"generation_tokens": 7}},
               "method_reported": {{"score": None}}}}],
    "trajectory_shape": "sequence", "nominated": 0,
    "provenance": {{"acceptance_rule": {{"text": "always accept", "source": "test",
                                       "calibrated": False}}}}}}))
emit({{"steps": 1, "changed": True, "generation_tokens": 7}})
''')

    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock"}
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "dead-code", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {method}"],
        cwd=ROOT, capture_output=True, text=True, timeout=900, env=env)
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "dead-code" / "curve.jsonl").read_text().splitlines() if l.strip()]
    assert len(points) == 2, points
    assert "candidate_code" not in points[0], "H0 不是候选,不该有候选代码读数"

    code = points[1]["candidate_code"]
    assert code["language"] == "python"
    added = {u["name"] for u in code["unreferenced"]}
    assert "added_but_never_called" in added, code
    assert code["unreachable_modules"] == ["orphan_module.py"], code
    assert points[1]["score"] == 0.0, "诊断不是分数:它不该改变这一轮的分数"
