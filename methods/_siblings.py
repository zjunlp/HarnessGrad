"""按文件路径加载 `methods/` 里的兄弟模块，绕开顶层名字碰撞。

为什么不直接 `import protocol`：`sys.modules` 是**先于** `sys.path` 命中的。同一个进程里
（例如全套 quality 测试）只要有人先把顶层名字 `protocol` 注册上 —— CooperBench 的
`data/datasets/cooperbench/lib/protocol.py` 就是 —— `import protocol` 拿到的就是**它**，
于是 `from protocol import emit` 直接 ImportError，或者更糟：拿到一个同名但接口不同的模块，
在运行到某个属性时才炸（实测：`methods/dgm` 的批次控制死在
`module 'protocol' has no attribute 'studied_score'`）。

一个 method 在自己的进程里通常不会被抢名字，但"通常"不是保证：任何依赖带一个
`protocol.py`/`editor.py` 就够了。所以这里按**路径**加载，并以 `hg_method_<name>` 注册，
名字由本模块决定，不会与别人的顶层模块撞。

用法（每个 entrypoint 一行）::

    import _siblings
    editor = _siblings.load("editor")
    protocol = _siblings.load("protocol")

`methods/` 已经在该 method 的 sandbox 里（`PLATFORM_ROOT/methods` 被绑定），所以这个文件
和被加载的兄弟模块一样可见。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

__all__ = ["load", "HERE"]

HERE = Path(__file__).resolve().parent


def load(name: str, *, private: str | None = None):
    """`methods/<name>.py` 作为模块对象返回；同名多次调用返回同一个对象。"""
    key = private or f"hg_method_{name}"
    existing = sys.modules.get(key)
    if existing is not None:
        return existing
    path = HERE / f"{name}.py"
    # **别和同一份文件的另一个对象并存。** 测试常常先 `import editor`（`methods/` 在
    # `sys.path` 上），随后方法再按路径加载 `editor`：如果这里另起一个模块对象，同一个
    # 进程里就有两个 `editor`，测试给 `editor.ask` 打的桩就到不了方法手里（实测
    # `test_rrsi.py` 三条红在"没有凭据"）。同一个文件就复用已有对象。
    plain_existing = sys.modules.get(name)
    if plain_existing is not None:
        same_file = getattr(plain_existing, "__file__", None)
        try:
            same_file = same_file is not None and Path(same_file).resolve() == path.resolve()
        except OSError:                                   # pragma: no cover - defensive
            same_file = False
        if same_file:
            sys.modules[key] = plain_existing
            return plain_existing
    if not path.is_file():
        raise ImportError(f"{path} does not exist; a method may only load its own siblings")
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:            # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    # **先把普通名字也指向它，再执行模块体**（`setdefault`，绝不覆盖已有的）。
    # 顺序很重要：`editor` 在执行时会加载 `apply`，而 `apply` 里 `import editor`。
    # 如果等 exec 完了再登记，那次嵌套 import 会从 `sys.path` 再加载一份 `editor`，
    # 于是同一个进程里出现两个 editor 对象 —— 测试给 `editor.ask` 打的桩就到不了
    # 方法手里（实测 `test_rrsi.py` 因此红 3 条）。先登记就没有这个窗口。
    # 已有同名模块时不覆盖：别的模块（CooperBench 的 `protocol`）先注册就让它留着，
    # 我们只用私有名，这正是这个文件存在的理由。
    sys.modules.setdefault(name, module)
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(key, None)
        if sys.modules.get(name) is module:
            sys.modules.pop(name, None)
        raise
    return module
