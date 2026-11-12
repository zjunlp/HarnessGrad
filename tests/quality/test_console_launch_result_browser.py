"""Offline browser coverage for the launch-to-diagnostics handoff."""
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

playwright = pytest.importorskip("playwright.sync_api")
STATIC = Path(__file__).resolve().parents[2] / "console" / "static"


@pytest.mark.parametrize("language,labels", [
    ("zh", ("查看运行", "查看启动日志")),
    ("en", ("View run", "View launch log")),
])
def test_successful_launch_links_run_and_bounded_log_without_server_path(language, labels):
    run_id = 'console-run-<&"'
    fixture = {
        "datasets": [{"name": "fixture", "kind": "fixture"}],
        "harnesses": ["loop"], "candidates": [], "improvers": [], "methods": [],
        "task_models": ["mock"], "meta_models": [],
        "defaults": {"dataset": "fixture", "harness": "loop", "method": "none",
                     "task_backend": "mock", "task_model": "mock", "rounds": 0},
    }
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        context = browser.new_context(viewport={"width": 397, "height": 727})
        context.add_init_script(f"localStorage.setItem('hg.language', '{language}')")

        def serve(route):
            request = route.request
            path = unquote(urlparse(request.url).path)
            if path == "/api/launch/form":
                return route.fulfill(json=fixture)
            if path == "/api/launch/preview":
                return route.fulfill(json={"argv": ["python3", "driver.py"],
                                           "preflight": {"ok": True}})
            if path == "/api/runs" and request.method == "POST":
                return route.fulfill(status=202, json={
                    "run_id": run_id,
                    "log": "/private/server/console-secret.log",
                    "argv": ["python3", "driver.py", "--run-id", run_id],
                })
            if path == f"/api/runs/{run_id}/launch-log":
                return route.fulfill(json={"text": "synthetic launch failed\n", "truncated": False})
            if path.startswith("/api/"):
                return route.fulfill(status=404, json={"error": "not-found"})
            file = STATIC / path.lstrip("/")
            if not file.is_file() or file.parent != STATIC:
                return route.fulfill(status=404, body="not found")
            mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}.get(file.suffix, "text/plain")
            return route.fulfill(status=200, content_type=mime, body=file.read_bytes())

        context.route("**/*", serve)
        page = context.new_page()
        page.goto("http://console.test/launch.html")
        page.locator("#go").wait_for(state="visible")
        page.wait_for_function("() => !document.querySelector('#go').disabled")
        page.locator("#go").click()

        run_link = page.get_by_role("link", name=labels[0])
        run_link.wait_for()
        assert run_link.get_attribute("href") == f"/index.html#/runs/console-run-%3C%26%22"
        assert "/private/server" not in page.locator("#result").inner_text()
        assert run_id in page.locator("#result").inner_text()
        assert run_link.is_visible()
        assert page.locator("#result").evaluate(
            "el => el.parentElement.parentElement.scrollLeft") == 0
        assert page.locator("#result").evaluate(
            "el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }")

        page.get_by_role("button", name=labels[1]).click()
        page.locator("#launched-log").wait_for(state="visible")
        assert "synthetic launch failed" in page.locator("#launched-log").inner_text()
        context.close()
        browser.close()
