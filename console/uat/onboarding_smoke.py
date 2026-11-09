#!/usr/bin/env python3
"""Exercise a private local Console using only the demo fixture and mock backend.

Creates uniquely named candidate/method/run records; it does not delete evidence.
The server must use a private external state root, runs root, and budget directory.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


MANIFEST = json.dumps({'name': 'uat', 'version': '0.1', 'path': '.',
                       'entrypoint': 'agent.py', 'backend': 'cli', 'install': None})
AGENT = '''#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser(); p.add_argument('--task'); p.add_argument('--workdir'); a = p.parse_args()
Path(a.workdir, 'answer.txt').write_text('alpha')
Path(a.workdir, 'trace.jsonl').write_text(json.dumps({'usage': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0}}) + '\\n')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='temporary local Console URL, e.g. http://127.0.0.1:46991')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path:
        parser.error('use an explicit http://127.0.0.1:PORT URL')
    suffix = secrets.token_hex(4)
    initial, candidate, method = 'uat-manifest-' + suffix, 'uat-agent-' + suffix, 'uat_method_' + suffix
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1450, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/harness.html', wait_until='networkidle')
        page.wait_for_selector('#files [data-path="harness.json"]')
        page.click('#files [data-path="harness.json"]')
        page.wait_for_function("document.querySelector('#editing').textContent.trim()==='harness.json'")
        page.fill('#editor', MANIFEST); page.fill('#name', initial); page.click('#save')
        page.wait_for_selector('#candidate-launch-link', timeout=30000)
        page.click('#files [data-path="agent.py"]')
        page.wait_for_function("document.querySelector('#editing').textContent.trim()==='agent.py'")
        page.fill('#editor', AGENT); page.fill('#name', candidate); page.click('#save')
        page.wait_for_function("name => document.querySelector('#candidate-launch-link')?.getAttribute('href')?.includes(name)", arg=candidate, timeout=30000)
        page.click('#candidate-launch-link'); page.wait_for_selector('#f-harness')
        assert page.input_value('#f-harness') == 'candidate:' + candidate
        page.select_option('#f-dataset', 'demo'); page.select_option('#f-task-backend', 'mock')
        page.fill('#f-jobs', '1')
        page.locator('details summary').click(); page.click('#load-method-template')
        page.wait_for_function("document.querySelector('#custom-method-source').value.includes('def propose')")
        page.fill('#custom-method-name', method); page.click('#create-method')
        page.wait_for_function("name => document.querySelector('#f-method').value === 'custom:' + name", arg=method, timeout=30000)
        page.fill('#f-rounds', '1')
        page.wait_for_function("!document.querySelector('#go').disabled", timeout=45000)
        assert page.input_value('#f-task-backend') == 'mock' and page.input_value('#f-dataset') == 'demo'
        assert page.input_value('#f-improver') == 'none'
        page.click('#go'); page.wait_for_function("document.querySelector('#result').textContent.includes('run:')", timeout=15000)
        run_id = re.search(r'run:\s*(\S+)', page.inner_text('#result')).group(1)
        for _ in range(120):
            response = page.request.get(base + '/api/runs/' + run_id)
            data = response.json() if response.ok else {}
            row = data.get('row', {})
            if row.get('launch_exit_code') is not None:
                break
            time.sleep(1)
        else:
            raise AssertionError('driver exit receipt not produced within 120 seconds')
        if row['launch_exit_code'] != 0:
            log = page.request.get(base + '/api/runs/' + run_id + '/launch-log').json()
            raise AssertionError('mock run failed: ' + log.get('text', '')[-3000:])
        assert row['status'] == 'done' and len(data['rounds']) >= 1
        assert all(point.get('n_harness_failed') == 0 for point in data['rounds'])
        page.goto(base + '/#/runs/' + run_id, wait_until='networkidle')
        page.wait_for_selector('#show-launch-log'); page.click('#show-launch-log')
        page.wait_for_function("document.querySelector('#launch-log').textContent.includes('日志') || document.querySelector('#launch-log').textContent.includes('Logs')")
        assert not errors, errors
        print(json.dumps({'run_id': run_id, 'candidate': candidate, 'method': method,
                          'rounds': len(data['rounds']), 'exit_code': row['launch_exit_code']}))
        browser.close()


if __name__ == '__main__':
    main()
