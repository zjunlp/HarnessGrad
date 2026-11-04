"""Local browser smoke test. Requires existing Python Playwright + Chromium; installs nothing."""
from pathlib import Path
from urllib.parse import unquote, urlparse
import json
import mimetypes
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / 'dist'
OUT = Path('/tmp/hg-hub-polish-v3-browser')
OUT.mkdir(exist_ok=True)
BASE = 'https://preview.invalid/HarnessGrad-Hub/'
errors = []
results = []


def route(request):
    path = unquote(urlparse(request.request.url).path).removeprefix('/HarnessGrad-Hub/')
    target = ROOT / path
    if target.is_dir():
        target /= 'index.html'
    if target.is_file():
        request.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
    else:
        errors.append(f'Missing local asset: {path}')
        request.fulfill(status=404, body='Not found')


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    for width, mode in [(320, 'normal'), (390, 'normal'), (768, 'normal'), (1440, 'normal'), (1440, 'reduced'), (390, 'nojs'), (1440, 'nojs'), (390, 'dark')]:
        page = browser.new_page(viewport={'width': width, 'height': 900}, java_script_enabled=mode != 'nojs', reduced_motion='reduce' if mode == 'reduced' else 'no-preference', color_scheme='dark' if mode == 'dark' else 'light')
        page.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until='networkidle')
        assert page.title() == 'HarnessGrad: Harness as A Continual Learner'
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (width, mode, 'overflow')
        assert page.locator('[data-cycle-step]').count() == 4
        for step in page.locator('[data-cycle-step]').all():
            assert step.is_visible()
        figure = page.locator('.cycle-illustration')
        if mode != 'nojs':
            buttons = page.locator('[data-cycle-button]')
            for index in range(4):
                button = buttons.nth(index)
                button.focus()
                page.keyboard.press('Enter' if index % 2 == 0 else 'Space')
                page.wait_for_timeout(250)
                assert figure.get_attribute('data-state') == str(index)
                assert button.get_attribute('aria-pressed') == 'true'
                assert page.locator('[data-cycle-button][aria-pressed="true"]').count() == 1
                assert button.evaluate('(el) => el === document.activeElement')
                assert button.bounding_box()['height'] >= 44
            buttons.nth(0).click()
            page.evaluate('document.activeElement.blur()')
            for index in [1, 2, 3, 2, 1, 0]:
                page.locator('[data-cycle-step]').nth(index).evaluate('(el) => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - innerHeight * .3)')
                page.wait_for_timeout(230)
                if width >= 900 and mode != 'reduced':
                    assert figure.get_attribute('data-state') == str(index), (width, mode, index, figure.get_attribute('data-state'))
                else:
                    assert figure.get_attribute('data-state') == '0'
            assert page.locator('[aria-live]').count() == 0
        else:
            assert not page.locator('.cycle-controls').is_visible()
            assert figure.get_attribute('data-state') == '3'
        if width < 900 or mode == 'reduced':
            assert figure.evaluate('(el) => getComputedStyle(el).position') != 'sticky'
        page.locator('#how-it-learns').scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f'cycle-{width}-{mode}.png'))
        page.screenshot(path=str(OUT / f'full-{width}-{mode}.png'), full_page=True)
        results.append({'width': width, 'mode': mode, 'result': 'PASS'})
        page.close()
    browser.close()
assert not errors, errors
print(json.dumps({'views': results, 'errors': errors, 'screenshots': str(OUT)}, indent=2))
