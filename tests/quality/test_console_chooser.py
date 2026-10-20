"""控制台给出的数据集清单必须等于平台的发布清单。

为什么会漏：控制台原来自己扫 `data/datasets/*.py`，只读每个文件的前 4 KB 找
`KIND = "dataset"`。`alfworld`、`agentdojo`、`gaia`、`terminal_bench` 等 9 个的 KIND 写在
更后面，于是被静默漏掉 —— 包括这个项目自己的研究对象 alfworld。一个"选择器少了九个选项"
的 bug，界面上只表现为"没得选"，不会有任何报错。

`data/scaffolds/registry.py::published()` 在文档里就是"哪些数据集存在"的唯一答案，所以
这里断言两者逐项相等；控制台不再保留自己的一份判断。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds.registry import published  # noqa: E402


def _console():
    spec = importlib.util.spec_from_file_location("console_server", ROOT / "console" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_console_dataset_chooser_matches_the_registry():
    module = _console()
    offered = sorted(item["name"] for item in module.adapters())
    expected = sorted(published())
    assert offered == expected, (
        f"控制台少给 / 多给了数据集：缺 {sorted(set(expected) - set(offered))}，"
        f"多 {sorted(set(offered) - set(expected))}")


def test_every_offered_name_is_a_module_on_disk():
    module = _console()
    for item in module.adapters():
        assert (ROOT / "data" / "datasets" / f"{item['name']}.py").is_file(), item
