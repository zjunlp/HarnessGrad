"""Browser-only shell geometry control; no server, writes, or model calls."""
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
STATIC = Path(__file__).resolve().parents[2] / "console" / "static"
PAGES = ("/", "/#/trace", "/#/version", "/trace.html", "/harness.html",
         "/launch.html", "/submit.html", "/author.html", "/evidence.html")


def test_shell_fits_mobile_in_both_languages():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            for width in (320, 390):
                for language in ("en", "zh"):
                    context = browser.new_context(viewport={"width": width, "height": 800})
                    context.add_init_script(f"localStorage.setItem('hg.language', '{language}')")

                    def serve(route):
                        from urllib.parse import urlparse
                        path = urlparse(route.request.url).path
                        if path.startswith('/api/'):
                            route.fulfill(status=200, content_type='application/json', body='{}')
                            return
                        name = 'index.html' if path == '/' else path.lstrip('/')
                        file = STATIC / name
                        if not file.is_file() or file.parent != STATIC:
                            route.fulfill(status=404, body='not found')
                            return
                        content_type = ('text/html' if file.suffix == '.html' else
                                        'text/css' if file.suffix == '.css' else 'text/javascript')
                        route.fulfill(status=200, content_type=content_type, body=file.read_bytes())

                    context.route('**/*', serve)
                    page = context.new_page()
                    for path in PAGES:
                        page.goto('http://console.test' + path, wait_until='domcontentloaded')
                        page.locator('.sh-bar .sh-mode').first.wait_for()
                        result = page.evaluate('''() => {
                          const bar = document.querySelector('.sh-bar');
                          const logo = bar.querySelector('.sh-logo');
                          const modes = [...bar.querySelectorAll('.sh-mode')];
                          const fits = el => { const r = el.getBoundingClientRect();
                            return r.width > 0 && r.left >= -0.5 && r.right <= innerWidth + 0.5; };
                          const nav = document.querySelector('.side .nav-item');
                          const settings = document.querySelector('#shell-open-settings');
                          return {bar: fits(bar), logo: fits(logo), modes: modes.map(fits),
                            nav: !!nav && fits(nav), settings: !!settings && fits(settings),
                            scrollWidth: document.documentElement.scrollWidth};
                        }''')
                        assert result['bar'] and result['logo'] and all(result['modes']) and result['nav'] and result['settings'], (width, language, path, result)
                        assert result['scrollWidth'] <= width, (width, language, path, result)
                    context.close()
        finally:
            browser.close()
