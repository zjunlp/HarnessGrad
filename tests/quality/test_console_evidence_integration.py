"""Browser-to-Console evidence downloads, using only synthetic run receipts."""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest

from harnessgrad.evidence_export import check_export
from test_evidence_export import PRIVATE_TASK, SECRET, author, save_run

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def live_console(tmp_path, monkeypatch):
    runs = tmp_path / 'runs'
    save_run(runs, 'base')
    save_run(runs, 'sub', 1, tree='b')
    (runs / 'unfinished' / 'recordings').mkdir(parents=True)
    original = {p: p.read_bytes() for p in runs.rglob('*') if p.is_file()}
    spec = importlib.util.spec_from_file_location('evidence_integration_server', ROOT / 'console/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, 'RUNS', runs)
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'settings.json')
    monkeypatch.setattr(server, 'api_health', lambda: {'runs_count': 3, 'running': [], 'disk_free_gb': None})
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError('An evidence export must not launch work or call a model')

    for name in ('api_launch', 'api_explain', 'api_explain_range', 'probe_endpoint'):
        monkeypatch.setattr(server, name, forbidden)
    httpd = server.ConsoleServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}'
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not forbidden_calls
        assert original == {p: p.read_bytes() for p in runs.rglob('*') if p.is_file()}
        assert not (tmp_path / 'settings.json').exists()


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_browser_download_is_a_verifiable_bundle(live_console, tmp_path, language):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 980}, accept_downloads=True)
            context.add_init_script(f"localStorage.setItem('hg.language', '{language}')")
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(live_console + '/evidence.html?run=sub&round=1')
            page.wait_for_function("document.querySelector('#submission-round').value === '1'")
            assert 'unfinished' in page.locator('#catalog').inner_text()
            assert page.locator('h1').inner_text() == ('导出证据' if language == 'zh' else 'Export evidence')
            page.locator('#rule').fill('Synthetic protocol: report the specified round.')
            page.locator('#budget-rounds').fill('1')
            page.locator('#budget-trials').fill('3')
            page.locator('#budget-source').fill('Synthetic test protocol')
            first = page.locator('.concern').first
            first.locator('.id').fill('R1')
            first.locator('.concern-text').fill('Synthetic baseline comparison')
            first.locator('.baseline-run').select_option('base')
            first.locator('.baseline-round').select_option('0')
            page.locator('#add').click()
            second = page.locator('.concern').nth(1)
            second.locator('.id').fill('R2')
            second.locator('.concern-text').fill('Synthetic unmeasured condition')
            second.locator('.status').select_option('unmeasured')
            second.locator('.reason').fill('Not run')
            with page.expect_download() as download:
                page.locator('#export').click()
            target = tmp_path / f'evidence-{language}.json'
            download.value.save_as(target)
            assert download.value.suggested_filename == 'evidence-export.json'
            raw = target.read_bytes()
            bundle = json.loads(raw)
            assert not check_export(bundle)
            assert bundle['manifest']['submission'] == {'run_id': 'sub', 'round': 1}
            assert bundle['manifest']['requests'][1]['status'] == 'unmeasured'
            assert len(bundle['manifest']['attempts']) == 3
            assert bundle['comparisons'][0]['request_id'] == 'R1'
            assert SECRET.encode() not in raw and PRIVATE_TASK.encode() not in raw
            page.evaluate('window.scrollTo(0, 0)')
            page.screenshot(path=str(tmp_path / f'evidence-{language}-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(tmp_path / f'evidence-{language}-mobile.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

            # Advanced JSON must reach the strict parser without losing duplicates.
            page.locator('summary').click()
            draft = json.dumps(author())[:-1] + ', "budget": ' + json.dumps(author()['budget']) + '}'
            page.locator('#json').fill(draft)
            page.locator('#use-json').check()
            with page.expect_response(lambda response: response.url.endswith('/api/evidence/export')) as response:
                page.locator('#export').click()
            assert response.value.status == 400
            assert response.value.json() == {'error': 'invalid-manifest'}
            assert not errors
        finally:
            browser.close()
