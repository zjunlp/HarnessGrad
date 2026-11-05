#!/usr/bin/env python3
"""Focused smoke checks against a running Astro preview (requires Python Playwright)."""
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:4398/HarnessGrad/'
with sync_playwright() as p:
    browser = p.chromium.launch()
    def page(languages, **kwargs):
        ctx = browser.new_context(**kwargs)
        ctx.add_init_script('Object.defineProperty(navigator,"languages",{get:()=>%s})' % repr(languages))
        return ctx, ctx.new_page()
    for langs, expected in [(['en-US', 'zh-CN'], '/HarnessGrad/'), (['zh-CN','en'], '/HarnessGrad/zh/'), (['fr','zh'], '/HarnessGrad/zh/'), (['fr','de'], '/HarnessGrad/')]:
        c, tab = page(langs)
        tab.goto(BASE, wait_until='networkidle')
        assert tab.url.endswith(expected), (langs, tab.url)
        c.close()
    c, tab = page(['zh'])
    tab.goto(BASE, wait_until='networkidle')
    tab.locator('[data-locale=en]').click()
    tab.reload(wait_until='networkidle')
    assert '/zh/' not in tab.url
    tab.locator('[data-locale=zh]').click()
    tab.reload(wait_until='networkidle')
    assert '/zh/' in tab.url
    tab.goto(BASE+'getting-started/?x=1#install', wait_until='networkidle')
    assert '/zh/' not in tab.url
    tab.locator('[data-locale=zh]').click()
    assert '/zh/getting-started/?x=1#install' in tab.url
    tab.goto(BASE+'zh/benchmarks/appworld/', wait_until='networkidle')
    assert tab.get_by_text('此内容尚不支持你的语言。').count() == 1
    assert tab.locator('h1').count() == 1
    c.close()
    c, tab = page(['zh'], viewport={'width': 390, 'height': 844}, color_scheme='dark')
    c.add_init_script('Object.defineProperty(window,"localStorage",{get(){throw new Error("disabled")}})')
    tab.goto(BASE+'zh/', wait_until='networkidle')
    tab.locator('[data-locale=en]').click()
    assert '/zh/' not in tab.url
    tab.reload(wait_until='networkidle')
    assert '/zh/' not in tab.url
    assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
    c.close()
    c, tab = page(['zh'])
    tab.goto(BASE+'zh/getting-started/', wait_until='networkidle')
    tab.locator('button[data-open-modal]').click()
    search = tab.locator('.pagefind-ui__search-input')
    search.fill('HarnessGrad')
    tab.locator('.pagefind-ui__result').first.wait_for(timeout=15000)
    assert '找到' in tab.locator('.pagefind-ui__message').inner_text()
    assert tab.locator('.pagefind-ui__search-clear').inner_text() == '清除'
    search.fill('unlikelyuniquesearchzzzz')
    tab.get_by_text('没有找到与', exact=False).wait_for(timeout=15000)
    c.close()
    for width in (320, 1440):
        c, tab = page(['en'], viewport={'width': width, 'height': 900})
        tab.goto(BASE+'getting-started/', wait_until='networkidle')
        links = tab.locator('.hg-languages a')
        for link in links.all():
            box = link.bounding_box()
            assert box['width'] >= 44 and box['height'] >= 44, box
            assert tab.evaluate('(el) => { const r=el.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return el === hit || el.contains(hit); }', link.element_handle())
        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
        c.close()
    browser.close()
print('browser i18n smoke checks passed')
