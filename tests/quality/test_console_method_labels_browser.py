"""Offline real-browser launch dropdown: all traffic is intercepted."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

playwright = pytest.importorskip('playwright.sync_api')
STATIC = Path(__file__).resolve().parents[2] / 'console' / 'static'


@pytest.mark.parametrize('language', ['en', 'zh'])
@pytest.mark.parametrize('default', ['a', 'gepa', 'future_<"&'])
def test_launch_method_options(language, default):
    previews = []
    requests = []
    methods = [{'name': n, 'title': 'DO NOT DISPLAY description'} for n in
               ['a', 'gepa', 'b', 'echo_base', 'noop', 'A', 'future_<"&']]
    fixture = {'datasets': [{'name': 'fixture'}], 'harnesses': ['loop'],
               'improvers': [{'name': 'fixture'}], 'methods': methods,
               'defaults': {'method': default}}
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        context = browser.new_context()
        context.add_init_script(f"localStorage.setItem('hg.language', '{language}')")

        def serve(route):
            request = route.request
            requests.append((request.method, request.url))
            assert request.method == 'GET'
            path = urlparse(request.url).path
            if path == '/api/launch/form':
                return route.fulfill(json=fixture)
            if path == '/api/launch/preview':
                previews.append(parse_qs(urlparse(request.url).query))
                return route.fulfill(json={'argv': [], 'preflight': {'ok': True}})
            if path.startswith('/api/'):
                return route.fulfill(json={})
            file = STATIC / path.lstrip('/')
            if not file.is_file() or file.parent != STATIC:
                return route.fulfill(status=404, body='not found')
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript'}.get(file.suffix, 'text/plain')
            route.fulfill(status=200, content_type=mime, body=file.read_bytes())

        context.route('**/*', serve)
        page = context.new_page()
        page.goto('http://console.test/launch.html')
        page.locator('#f-method').wait_for()
        options = page.locator('#f-method option').evaluate_all('(els) => els.map(e => [e.value, e.textContent])')
        assert options[1:] == [['gepa', 'GEPA'], ['noop', 'No-op'], ['A', 'A'], ['future_<"&', 'future_<"&']]
        assert options[0][0] == 'none'
        assert options[0][1] == ('No method (single-point measurement)' if language == 'en' else '不使用方法（只测一个点）')
        assert page.locator('#f-method').input_value() == 'none'
        page.locator('#f-method').select_option('future_<"&')
        page.wait_for_function("() => document.querySelector('#f-method').value === 'future_<\"&'")
        page.wait_for_timeout(100)
        assert previews[-1]['method'] == ['future_<"&']
        assert all(method == 'GET' for method, _ in requests)
        context.close()
        browser.close()
