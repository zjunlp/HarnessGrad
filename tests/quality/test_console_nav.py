"""控制台的分区导航：两个模式、两套条目，由 `shell.js` 一处拥有。

2026-11-02 的部署上，四张页面各写一份侧栏，于是点"对话"就少两个按钮；`index.html` 还把
`提交` 写了两遍。之后又发现"实验"旁边的数字徽标在一张页面上是"当前筛选行数"、在另一张
上是"总 run 数"（81 vs 784），第三张干脆不填。

修法不是把四份改成一致，而是**只留一份**：页面里的 `<nav class="nav">` 必须为空，
条目由 `/shell.js` 按模式注入。这个测试因此检查三件事：每张页面都装了外壳、页面里没有
残留的硬编码条目、两个模式各自的条目与顺序符合约定。

构造环境和优化 Harness 是两件独立的事（`docs/scenario-workspace.md`）：实验/轨迹/版本
只属于后者，所以它们不能出现在同一个列表里。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "console" / "static"
PAGES = ("index.html", "trace.html", "submit.html", "author.html",
         "harness.html", "launch.html")

# 导入环境只在「构造环境」里有：它是造环境的一步，放在优化 Harness 里就是同一件事的
# 第二个入口。助手页也去掉了 —— 工作区右侧已经能直接和助手对话。
HARNESS = [("实验", "/#/runs"), ("轨迹", "/#/trace"), ("版本", "/#/version"),
           ("编辑", "/harness.html"), ("启动", "/launch.html"), ("评测", "/submit.html")]
BUILD = [("场景", "/author.html#/scenarios"), ("起草", "/author.html#/draft"),
         ("导入", "/author.html#/import"), ("去优化 Harness", "/")]
HARNESS_EN = [("Runs", "/#/runs"), ("Trace", "/#/trace"), ("Version", "/#/version"),
              ("Edit", "/harness.html"), ("Launch", "/launch.html"), ("Evaluate", "/submit.html")]
BUILD_EN = [("Scenarios", "/author.html#/scenarios"), ("Draft", "/author.html#/draft"),
            ("Import", "/author.html#/import"), ("Go to Optimize Harness", "/")]


def shell() -> str:
    return (STATIC / "shell.js").read_text(encoding="utf-8")


def mode_items(mode: str, language: str = "zh") -> list[tuple[str, str]]:
    # Execute the real nav renderer and dictionaries, not a second parser for its
    # implementation. The DOM is deliberately minimal: installation is deferred.
    script = r'''
const fs = require('fs'), vm = require('vm');
const [dir, mode, language] = process.argv.slice(1);
const window = {HGStorage: {getItem() { return null; }}};
const context = {window, location: {pathname: '/', hash: '', search: ''},
  localStorage: {getItem() { return null; }}, URLSearchParams,
  document: {readyState: 'loading', body: {dataset: {}},
    documentElement: {dataset: {}, style: {setProperty() {}}},
    addEventListener() {}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(dir + '/shell-i18n.js', 'utf8'), context);
window.HGI18n = {t: key => window.HG_SHELL_I18N[language][key]};
let source = fs.readFileSync(dir + '/shell.js', 'utf8');
source = source.replace('  if (document.readyState === "loading")',
                        '  window.__navHtml = navHtml;\n  if (document.readyState === "loading")');
vm.runInContext(source, context);
process.stdout.write(JSON.stringify(window.__navHtml(mode)));
'''
    html = json.loads(subprocess.check_output(
        ["node", "-e", script, str(STATIC), mode, language], text=True))
    return [(label, href) for href, label in
            re.findall(r'<a class="nav-item" href="([^"]+)"[^>]*>([^<]+)</a>', html)]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_loads_the_shell(page):
    text = (STATIC / page).read_text(encoding="utf-8")
    assert "/shell.css" in text, page
    assert "/shell.js" in text, page


@pytest.mark.parametrize("page", PAGES)
def test_no_page_keeps_its_own_nav_entry(page):
    text = (STATIC / page).read_text(encoding="utf-8")
    nav = re.search(r'<nav class="nav">(.*?)</nav>', text, re.S)
    assert nav, f"{page} 里没有 <nav class='nav'>"
    assert "nav-item" not in nav.group(1), f"{page} 还有硬编码的侧栏条目"


def test_the_harness_mode_lists_only_the_harness_sections():
    items = mode_items("harness")
    assert items == HARNESS, items
    assert mode_items("harness", "en") == HARNESS_EN
    labels = [label for label, _ in items]
    assert "起草" not in labels and "场景" not in labels, labels


def test_the_build_mode_lists_only_the_authoring_sections():
    items = mode_items("build")
    assert items == BUILD, items
    assert mode_items("build", "en") == BUILD_EN
    labels = [label for label, _ in items]
    for section in ("实验", "轨迹", "版本"):
        assert section not in labels, labels


def test_every_mode_entry_points_at_a_real_page():
    for _label, href in mode_items("harness") + mode_items("build"):
        path = href.split("#")[0].split("?")[0] or "/"
        if path in ("/", ""):
            continue
        assert (STATIC / path.lstrip("/")).is_file(), href


def test_the_two_modes_are_switchable_and_remembered():
    text = shell()
    assert 'data-mode="harness"' in text or 'aria-pressed' in text
    assert 'window.HGStorage.save(KEY, mode)' in text
    assert 'const KEY = "hg.mode"' in text
