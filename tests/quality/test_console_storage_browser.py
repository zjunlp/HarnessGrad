"""Offline browser coverage of the shared console storage boundary."""
from pathlib import Path
from urllib.parse import urlparse

import pytest

playwright = pytest.importorskip('playwright.sync_api')
STATIC = Path(__file__).resolve().parents[2] / 'console' / 'static'
PAGES = ['/', '/trace.html', '/submit.html', '/author.html', '/harness.html', '/launch.html']


@pytest.mark.parametrize('blocked', [True, False])
def test_storage_boundary(blocked):
    requests, errors, dialogs = [], [], []
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        context = browser.new_context(locale='en-US')
        if blocked:
            context.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('Blocked', 'SecurityError'); }});")
        def serve(route):
            request = route.request
            requests.append((request.method, request.url))
            if request.method != 'GET':
                return route.fulfill(status=405, body='blocked')
            path = urlparse(request.url).path
            if path.startswith('/api/'):
                return route.fulfill(json={})
            file = STATIC / ('index.html' if path == '/' else path.lstrip('/'))
            if not file.is_file() or file.parent != STATIC:
                return route.fulfill(status=404, body='not found')
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript'}.get(file.suffix, 'text/plain')
            route.fulfill(status=200, content_type=mime, body=file.read_bytes())
        context.route('**/*', serve)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
        for path in PAGES:
            page.goto('http://console.test' + path)
            page.locator('.sh-mode').first.wait_for()
            assert page.locator('.side .nav .nav-item').count() > 0
            if page.locator('#sidebar-toggle').count():
                page.locator('#sidebar-toggle').click()
                if page.locator('body').get_attribute('data-sidebar') == 'collapsed':
                    page.locator('#sidebar-toggle').click()
            page.locator('#shell-open-settings').click()
            assert page.locator('#sh-language').is_visible()
            page.locator('#sh-theme [data-theme="dark"]').click()
            assert page.locator('html').get_attribute('data-theme') == 'dark'
            if blocked:
                assert page.locator('#hg-storage-warning').is_visible()
                page.locator('#sh-language').select_option('zh')
                assert dialogs and 'Cannot save language' in dialogs[-1]
        if not blocked:
            page.reload()
            assert page.locator('html').get_attribute('data-theme') == 'dark'
            page.locator('#shell-open-settings').click()
            page.locator('#sh-language').select_option('zh')
            page.wait_for_function("document.documentElement.lang === 'zh-CN'")
            page.reload()
            assert page.locator('html').get_attribute('lang') == 'zh-CN'
        assert not errors, errors
        assert all(method == 'GET' for method, _ in requests)
        context.close()
        browser.close()
