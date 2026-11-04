"""Repeatable real Chromium checks against built dist (no server/packages needed).
Run: python3 scripts/check-content-taste.py [--url http://127.0.0.1:4325]
With no URL Playwright fulfills local build requests, including Pagefind assets.
"""
import argparse
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', help='Existing preview origin; no server is started')
parser.add_argument('--output', default='/tmp/hg-content-taste-browser')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1] / 'dist'
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
canonical = re.search(r'<link rel="canonical" href="([^"]+)"', (root / 'index.html').read_text())
assert canonical, 'Build first: dist/index.html needs a canonical URL'
base_path = urlparse(canonical[1]).path.rstrip('/') + '/'
assert base_path == '/HarnessGrad/', base_path
base = (args.url or 'https://preview.invalid').rstrip('/') + base_path
routes = ['', 'getting-started/', 'news/', 'blog/', 'news/2026-10-16-introducing-harnessgrad/', 'benchmarks/alfworld/']
errors, results = [], []

def local_route(route):
    request_path = unquote(urlparse(route.request.url).path)
    assert request_path.startswith(base_path), request_path
    path = request_path.removeprefix(base_path)
    target = root / path
    if target.is_dir():
        target /= 'index.html'
    if target.is_file():
        route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
    else:
        errors.append(f'Missing asset: {path}')
        route.fulfill(status=404, body='Not found')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    for width in [320, 390, 768, 1440]:
        for theme in ['light', 'dark']:
            context = browser.new_context(viewport={'width': width, 'height': 900}, color_scheme=theme)
            context.add_init_script(f"localStorage.setItem('starlight-theme', '{theme}')")
            if not args.url:
                context.route('**/*', local_route)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            for route in routes:
                page.goto(base + route, wait_until='networkidle')
                page.evaluate('document.fonts.ready')
                assert page.locator('h1').count() == 1, (width, theme, route, 'h1')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (width, theme, route, 'overflow')
                assert page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily.includes('Chakra Petch')"), route
                assert page.evaluate("document.fonts.check('600 24px \"Chakra Petch\"') && document.fonts.check('400 22px \"EB Garamond\"')"), route
                assert page.locator('link[rel="canonical"]').get_attribute('href') == 'https://zjunlp.github.io' + base_path + route
                if route:
                    edit_links = page.locator('a[href*="/edit/"]')
                    if route not in ('news/', 'blog/'):
                        assert edit_links.count() > 0, (route, 'missing edit link')
                    for link in edit_links.all():
                        assert link.get_attribute('href').startswith('https://github.com/zjunlp/HarnessGrad/edit/website/'), route
                    skip = page.locator('.sl-skip-link')
                    skip.focus()
                    page.keyboard.press('Enter')
                    assert page.locator('#_top').evaluate('(el) => el === document.activeElement'), 'Skip link must focus title'
                    nav = page.locator('.hg-nav')
                    assert nav.locator('a[aria-current="page"]').count() == 1
                    assert not page.locator('.hg-wordmark').evaluate('(el) => el.scrollWidth > el.clientWidth')
                    if route.startswith(('news', 'blog')):
                        assert page.locator('.hg-sidebar-toggle').count() == 0
                    if route == 'blog/':
                        assert 'No blog posts have been published yet' in page.locator('main').inner_text()
                        assert page.locator('.hg-post-list li').count() == 0
                    if route == 'news/':
                        assert page.locator('.hg-post-list li').count() == 1
                        assert page.locator('.hg-post-list time').inner_text() == 'October 16, 2026'
                    if route == 'getting-started/':
                        # Search is native, keyboard opens/closes and restores focus.
                        search = page.locator('site-search [data-open-modal]')
                        search.focus()
                        page.keyboard.press('Enter')
                        page.locator('site-search dialog').wait_for(state='visible')
                        field = page.locator('.pagefind-ui__search-input')
                        field.fill('harness')
                        page.locator('.pagefind-ui__result').first.wait_for(state='visible')
                        page.keyboard.press('Escape')
                        assert not page.locator('site-search dialog').is_visible()
                        assert search.evaluate('(el) => el === document.activeElement')
                        if width < 800:
                            menu = page.locator('.sl-menu-button')
                            menu.focus()
                            page.keyboard.press('Enter')
                            assert page.locator('#starlight__sidebar').evaluate('(el) => el.matches(":popover-open")')
                            assert page.locator('.main-frame').get_attribute('inert') is not None
                            page.keyboard.press('Escape')
                            assert not page.locator('#starlight__sidebar').evaluate('(el) => el.matches(":popover-open")')
                        else:
                            handle = page.locator('.hg-sidebar-toggle')
                            handle.click()
                            assert handle.get_attribute('aria-label') == 'Expand the sidebar'
                            assert handle.get_attribute('aria-expanded') == 'false'
                            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                            handle.click()
                            assert handle.get_attribute('aria-label') == 'Collapse the sidebar'
                    if route == 'news/2026-10-16-introducing-harnessgrad/':
                        assert page.locator('.sl-blog-metadata time').count() == 1
                        assert page.locator('a[href="#what-it-is-for"]').count() > 0
                    if route == 'blog/':
                        picker = page.locator('.hg-tools starlight-theme-select select')
                        picker.select_option('dark' if theme == 'light' else 'light')
                        assert page.locator('html').get_attribute('data-theme') != theme
                        picker.select_option(theme)
                    if route == 'getting-started/':
                        assert page.locator('.expressive-code .copy button').count() > 0
                        # Copy button retains its accessible label and native keyboard activation.
                        copy = page.locator('.expressive-code .copy button').first
                        copy.focus()
                        page.keyboard.press('Enter')
                        assert copy.evaluate('(el) => el === document.activeElement')
                    if route.startswith('benchmarks/'):
                        assert page.locator('[data-hg-filter]').count() > 0
                        assert 'Garamond' not in page.locator('table').first.evaluate('(el) => getComputedStyle(el).fontFamily')
                page.screenshot(path=str(out / f'{route.replace("/", "-") or "home"}-{width}-{theme}.png'), full_page=True)
                results.append({'page': route or 'home', 'width': width, 'theme': theme, 'result': 'PASS'})
            context.close()
    browser.close()
assert not errors, errors
report = {'views': results, 'errors': errors, 'screenshots': str(out)}
(out / 'results.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
