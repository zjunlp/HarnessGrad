"""The console's sidebar is one list of sections, and it does not change per page.

Three defects were visible on the deployed console on 2026-11-02 and are pinned here,
because each one is invisible to every other test:

* `index.html` carried **two** identical `提交` links (a second one was added next to the
  existing one), so the nav showed the same destination twice;
* each page declared its own `<nav>`: `index` had 对话/提交/实验/轨迹/版本, `chat` had
  实验/轨迹/版本/设置, `trace` had three items and `submit` had four. Clicking 对话 therefore
  *replaced* the sidebar with a shorter one, which reads as missing navigation;
* the run badge meant "rows currently loaded" on one page and "total runs" on another
  (81 vs 784 for the same console), and two pages never filled it -- so it was removed
  rather than left saying two different things in the same slot.

Pure file parsing: no server, no browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "console" / "static"
PAGES = ("index.html", "chat.html", "trace.html", "submit.html")

#: One order, for every page. A section that only exists on one page is not a section.
SECTIONS = [("对话", "/chat.html"), ("提交", "/submit.html"), ("实验", "/#/runs"),
            ("轨迹", "/#/trace"), ("版本", "/#/version")]


def nav_items(page: str) -> list[tuple[str, str, str]]:
    text = (STATIC / page).read_text(encoding="utf-8")
    nav = re.search(r'<nav class="nav">(.*?)</nav>', text, re.S)
    assert nav, f"{page} has no <nav>"
    rows = re.findall(r'<a class="nav-item" href="([^"]+)"([^>]*)>\s*([^<]+?)\s*(?:<span|</a>)',
                      nav.group(1), re.S)
    return [(label.strip(), href, attrs) for href, attrs, label in rows]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_lists_the_same_sections_in_the_same_order(page):
    got = [(label, href) for label, href, _ in nav_items(page)]
    assert got == SECTIONS, f"{page}: {got}"


@pytest.mark.parametrize("page", PAGES)
def test_no_section_appears_twice(page):
    labels = [label for label, _, _ in nav_items(page)]
    assert len(labels) == len(set(labels)), labels
    hrefs = [href for _, href, _ in nav_items(page)]
    assert len(hrefs) == len(set(hrefs)), hrefs


@pytest.mark.parametrize("page", PAGES)
def test_the_current_page_is_marked_exactly_once(page):
    """`aria-current` is what a screen reader uses to answer "where am I"."""
    marked = [label for label, _, attrs in nav_items(page) if "aria-current" in attrs]
    assert len(marked) == 1, (page, marked)


@pytest.mark.parametrize("page", PAGES)
def test_the_nav_carries_no_count(page):
    """A section list must not show a number whose meaning depends on the page."""
    text = (STATIC / page).read_text(encoding="utf-8")
    nav = re.search(r'<nav class="nav">(.*?)</nav>', text, re.S).group(1)
    assert "nav-badge" not in nav, page
