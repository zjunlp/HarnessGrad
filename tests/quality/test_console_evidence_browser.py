"""Offline browser checks against synthetic catalog and export responses."""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
STATIC = Path(__file__).resolve().parents[2] / 'console' / 'static'


class Handler(SimpleHTTPRequestHandler):
    draft = None
    failure = None
    raw = None

    def log_message(self, *args):
        pass

    def send_json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/evidence/catalog':
            return self.send_json({'runs': [{'run_id': '<img src=x onerror=alert(1)>', 'status': 'failed', 'rounds': [], 'budget': {}}, {'run_id': 'run-a', 'status': 'measured', 'rounds': [{'round': 2, 'score': .5, 'score_kind': 'score', 'harness_sha': 'abc'}], 'budget': {}}], 'scope': {'kind': 'configured_runs_root', 'run_count': 2}, 'limits': {}})
        if self.path == '/api/health':
            return self.send_json({})
        return super().do_GET()

    def do_POST(self):
        Handler.raw = self.rfile.read(int(self.headers['Content-Length']))
        Handler.draft = json.loads(Handler.raw)
        if Handler.failure:
            return self.send_json({'error': Handler.failure}, 413)
        return self.send_json({'schema': 'harnessgrad.evidence-export.v1'})


@pytest.fixture
def server():
    Handler.failure = None
    Handler.draft = None
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(STATIC)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


def test_evidence_export_and_error_offline(server, tmp_path):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        page = browser.new_page(locale='en-US', viewport={'width': 390, 'height': 844}, accept_downloads=True)
        page.goto(server + '/evidence.html?run=run-a&round=2')
        page.wait_for_function("document.querySelector('#scope').textContent.includes('2')")
        assert page.locator('#submission-run').input_value() == 'run-a'
        assert page.locator('#submission-round').input_value() == '2'
        assert page.locator('#catalog img').count() == 0
        assert '<img' in page.locator('#catalog').inner_text()
        page.locator('#rule').fill('Final round declared in advance')
        page.locator('#budget-rounds').fill('2')
        page.locator('#budget-trials').fill('1')
        page.locator('#budget-source').fill('protocol')
        page.locator('.id').fill('R1')
        page.locator('.concern-text').fill('Compare')
        with page.expect_download() as download:
            page.locator('#export').click()
        assert download.value.suggested_filename == 'evidence-export.json'
        assert Handler.draft['manifest']['submission'] == {'run_id': 'run-a', 'round': 2}
        assert Handler.draft['manifest']['selection']['rule'] == 'Final round declared in advance'
        Handler.failure = 'evidence-limit-exceeded'
        page.locator('#export').click()
        page.wait_for_function("document.querySelector('#message').textContent.includes('scope was not reduced')")
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path='/tmp/hg-evidence-mobile-viewport.png')
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.screenshot(path='/tmp/hg-evidence-desktop-viewport.png')
        Handler.failure = None
        page.locator('summary').click()
        page.locator('#json').fill('{"schema":"a","schema":"b"}')
        page.locator('#use-json').check()
        with page.expect_download():
            page.locator('#export').click()
        assert b'"schema":"a","schema":"b"' in Handler.raw
        page.locator('#load').set_input_files({'name': 'large.json', 'mimeType': 'application/json', 'buffer': b' ' * 1048577})
        assert '1 MiB' in page.locator('#message').inner_text()
        page.goto(server + '/evidence.html?run=run-a&round=99')
        page.wait_for_function("document.querySelector('#submission-run').value === 'run-a'")
        assert page.locator('#submission-round').input_value() == ''
        page.evaluate("localStorage.setItem('hg.language', 'zh')")
        page.reload()
        assert page.locator('h1').inner_text() == '导出证据'
        browser.close()


@pytest.mark.parametrize('snapshot,language', [(False, 'en'), (True, 'zh')])
def test_restore_requires_state_or_snapshot(snapshot, language):
    from urllib.parse import urlparse

    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        page = browser.new_page()
        page.add_init_script(f"localStorage.setItem('hg.language', '{language}')")
        posted = []
        row = {'id': 'sample', 'has_curve': True, 'rounds': 1,
               'rounds_stored': 0, 'diffs_stored': 1,
               'snapshot_rounds': [1] if snapshot else []}

        def serve(route):
            path = urlparse(route.request.url).path
            if path == '/api/harness/from-round':
                posted.append(route.request.post_data_json)
                route.fulfill(status=400, content_type='application/json', body='{"error":"fixture"}')
            elif path == '/api/runs':
                route.fulfill(json={'rows': [row]})
            elif path == '/api/runs/sample':
                route.fulfill(json={'row': row, 'rounds': [{'round': 1, 'score': .5}]})
            elif path.startswith('/api/'):
                route.fulfill(json={})
            else:
                file = STATIC / ('index.html' if path == '/' else path.lstrip('/'))
                if not file.is_file() or file.parent != STATIC:
                    return route.fulfill(status=404)
                kind = 'text/html' if file.suffix == '.html' else 'text/css' if file.suffix == '.css' else 'text/javascript'
                route.fulfill(content_type=kind, body=file.read_bytes())

        page.route('**/*', serve)
        page.goto('http://console.test/#/harness/sample/1')
        page.wait_for_function("document.querySelector('#h-current').textContent.includes('sample')")
        page.locator('#h-edit-btn').click()
        if snapshot:
            page.wait_for_function("document.querySelector('#h-start-hint').textContent.includes('fixture')")
            assert posted == [{'run': 'sample', 'round': 1}]
        else:
            assert 'no recoverable state or snapshot' in page.locator('#h-start-hint').inner_text()
            assert not posted
        browser.close()
