#!/usr/bin/env python3
"""Offline first-use acceptance; this is not a benchmark capability measurement.

Uses the already installed interpreter and Loop dependencies. All generated files stay
in a newly created directory beneath --output-root; no installation is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TIMEOUT = 180
DISCLAIMER = 'Offline mock smoke only; this does not establish benchmark capability.'


class AcceptanceError(Exception):
    pass


def run_command(argv, *, env, log, timeout=TIMEOUT):
    """Run only fixed argv and retain output even on failure or timeout."""
    with log.open('wb') as output:
        proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise AcceptanceError(f'command timed out; see {log}') from exc


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        encoded = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=encoded,
                     headers={'Content-Type': 'application/json'} if encoded is not None else {})
        res = conn.getresponse()
        raw = res.read()
        if res.status != 200:
            raise AcceptanceError(f'Console {method} {path} returned {res.status}: {raw[:500]!r}')
        return json.loads(raw)
    finally:
        conn.close()


def check_console(control, runs):
    from harnessgrad import evidence
    from harnessgrad.evidence_export import check_export
    spec = importlib.util.spec_from_file_location('first_use_console', ROOT / 'console/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.RUNS = runs
    server.live_drivers = lambda: {}
    # Expose only the endpoints this check needs, even to other loopback clients.
    allowed_get = {'/api/runs', '/api/runs/smoke', '/api/runs/smoke/rounds/0',
                   '/api/evidence/catalog'}
    class ReadOnlyHandler(server.Handler):
        def log_message(self, fmt, *args):
            with (control / 'console.log').open('a') as stream:
                stream.write((fmt % args) + '\n')
        def do_GET(self):
            if self.path not in allowed_get:
                return self._error('not found', 404)
            return super().do_GET()
        def do_POST(self):
            if self.path != '/api/evidence/export':
                return self._error('not found', 404)
            return super().do_POST()
        def do_DELETE(self):
            return self._error('method-not-allowed', 405)
    httpd = server.ConsoleServer(('127.0.0.1', 0), ReadOnlyHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    before = snapshot(runs)
    try:
        port = httpd.server_port
        listing = request(port, 'GET', '/api/runs')
        if {r.get('id') for r in listing.get('rows', [])} != {'smoke', 'prepared'}:
            raise AcceptanceError('Console Runs does not contain exactly the two smoke runs')
        detail = request(port, 'GET', '/api/runs/smoke')
        round_data = request(port, 'GET', '/api/runs/smoke/rounds/0')
        if not detail or not round_data:
            raise AcceptanceError('Console run or round response is empty')
        catalog = request(port, 'GET', '/api/evidence/catalog')
        ids = {r.get('run_id') for r in catalog.get('runs', [])}
        if ids != {'smoke', 'prepared'}:
            raise AcceptanceError('Console catalog does not contain exactly the two smoke runs')
        manifest = {'schema': evidence.SCHEMA,
            'submission': {'run_id': 'smoke', 'round': 0},
            'requests': [{'id': 'R1', 'concern': 'Record the local smoke result.',
                          'runs': ['smoke'], 'result': {'run_id': 'smoke', 'round': 0, 'metric': 'score'}}],
            'attempts': [{'run_id': 'smoke', 'purpose': 'offline smoke'}],
            'selection': {'reported': {'run_id': 'smoke', 'round': 0}, 'rule': 'fixed round zero'},
            'budget': {'rounds': 0, 'trials': 1, 'source': 'demo-smoke scenario'}}
        bundle = request(port, 'POST', '/api/evidence/export', {'manifest': manifest})
        (control / 'evidence-export.json').write_text(json.dumps(bundle, indent=2) + '\n')
        problems = check_export(bundle)
        if problems:
            raise AcceptanceError(f'export verification failed: {problems}')
        if bundle.get('manifest', {}).get('submission') != {'run_id': 'smoke', 'round': 0}:
            raise AcceptanceError('export identifies the wrong submission')
        if snapshot(runs) != before:
            raise AcceptanceError('Console changed run records')
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AcceptanceError('Console did not stop')


def execute(control, runtime_root):
    # Read-only admission: refuse a missing store before the driver can prepare it.
    from harnessgrad import python_exec_runtime
    python_exec_runtime.prepare(ROOT / "base_harness/loop", root=runtime_root, build=False)
    runs, work = control / 'runs', control / 'work'
    home, temporary = control / 'home', control / 'tmp'
    home.mkdir(); temporary.mkdir()
    env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home),
           'TMPDIR': str(temporary), 'LANG': 'C.UTF-8', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
           'HG_AGENT_BACKEND': 'mock', 'HG_HARNESS_RUNTIME_ROOT': str(runtime_root)}
    common = [sys.executable, str(ROOT / 'driver.py'), '--scenario', 'demo-smoke',
              '--harness', 'loop', '--improver', 'none',
              '--runs-root', str(runs), '--work-root', str(work)]
    def invoke(name, run_id, extra=()):
        return run_command(common + ['--run-id', run_id, *extra], env=env, log=control / f'{name}.log')
    if invoke('smoke', 'smoke') != 0:
        raise AcceptanceError('mock smoke failed; see smoke.log')
    def verify_run(run_id):
        run = runs / run_id
        meta = json.loads((run / 'run_meta.json').read_text())
        points = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines() if line.strip()]
        receipt = json.loads((run / 'harness-preparation/round-0/attempt-0001.json').read_text())['receipt']
        if (meta.get('agent_backend') != 'mock' or meta.get('completion', {}).get('status') != 'complete'
                or receipt.get('installed') is not False or receipt.get('cache_hit') is not True
                or len(points) != 1 or points[0].get('round') != 0
                or points[0].get('measured_by_platform') is not True
                or type(points[0].get('score')) not in (int, float)
                or not math.isfinite(points[0]['score'])
                or points[0].get('n_invalid') != 0 or points[0].get('n_harness_failed') != 0
                or set(points[0].get('per_task', {})) != {'t01', 't02', 't03', 't04'}
                or not (run / 'state_after_round0').is_dir()):
            raise AcceptanceError(f'{run_id} is missing valid mock smoke artifacts')
    verify_run('smoke')
    original = (snapshot(runs / 'smoke'), snapshot(work / 'smoke'))
    rc = invoke('duplicate', 'smoke')
    if rc != 2 or 'fresh --run-id' not in (control / 'duplicate.log').read_text():
        raise AcceptanceError('duplicate ID was not explicitly refused')
    if (snapshot(runs / 'smoke'), snapshot(work / 'smoke')) != original:
        raise AcceptanceError('duplicate ID changed the existing record or workspace')
    if invoke('prepare', 'prepared', ['--prepare-only']) != 0:
        raise AcceptanceError('preparation failed; see prepare.log')
    if invoke('consume', 'prepared') != 0:
        raise AcceptanceError('prepared run was not consumed; see consume.log')
    verify_run('prepared')
    check_console(control, runs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--runtime-root', type=Path, default=Path(os.environ.get(
        'HG_HARNESS_RUNTIME_ROOT') or
        (Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'harnessgrad/harness-runtimes')),
        help='already prepared Loop runtime store; never install dependencies')
    args = parser.parse_args(argv)
    output = args.output_root.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('--output-root must be outside the platform checkout')
    output.mkdir(parents=True, exist_ok=True)
    control = Path(tempfile.mkdtemp(prefix='first-use-', dir=output))
    report = {'status': 'failed', 'scope': DISCLAIMER, 'control_directory': str(control)}
    try:
        execute(control, args.runtime_root.resolve())
        report['status'] = 'passed'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    (control / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
