#!/usr/bin/env python3
"""侧栏高亮必须跟着页面内的跳转走（实验 / 轨迹 / 版本 是同一张页面的不同 hash）。

为什么值得一个浏览器测试：这三个入口的 `pathname` 完全相同，只有 `hash` 变。侧栏若只在
加载时算一次高亮，就会"画面换了、左边还亮着进来时那一项"——实测 2026-11-02 点"版本"，
左侧仍显示"实验"。静态检查看不出来，因为出错的是运行时状态。

    python3 console/uat/shell_nav_smoke.py [--url http://127.0.0.1:4460]
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

CASES = [("/#/version", "版本"), ("/#/trace", "轨迹"), ("/#/runs", "实验")]


def current(page) -> list[str]:
    return page.eval_on_selector_all(
        ".side .nav-item[aria-current='page']", "els => els.map(e => e.textContent.trim())")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:4460")
    args = ap.parse_args()
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{args.url}/?mode=harness", wait_until="networkidle")
        page.wait_for_timeout(800)
        for href, expect in CASES:
            page.click(f".side .nav-item[href='{href}']")
            page.wait_for_timeout(600)
            got = current(page)
            if got != [expect]:
                failures.append(f"{href}: 期望高亮 {expect}，实际 {got}")
        if errors:
            failures.append(f"页面报错：{errors[:3]}")
        browser.close()
    for line in failures:
        print(f"FAIL {line}")
    print("shell nav: " + ("PASS" if not failures else "FAIL"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
