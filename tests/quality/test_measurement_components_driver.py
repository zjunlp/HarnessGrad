"""End-to-end wiring with an authored profile and the mock demo actor."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_mock_driver_records_reuses_and_refuses_changed_components(tmp_path):
    bootstrap = tmp_path / 'bootstrap.py'
    bootstrap.write_text('''import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from harnessgrad.provenance import measurement_components as identities
def resolve(dataset):
    if dataset != 'demo':
        return None
    return {'data_source': identities.data_source('demo', origin='synthetic', revision='v1'),
            'loader': identities.implementation('loader', {'provider': os.environ['AUTHORED_LOADER'] * 64}),
            'scorer': identities.implementation('scorer', {'verify': 'b' * 64})}
identities.resolve = resolve
from harnessgrad.provenance import scoring_identity
scoring_identity.resolve = lambda dataset: scoring_identity.create({
    'metric': 'synthetic', 'scorer_sha256': resolve(dataset)['scorer']['sha256']}) if dataset == 'demo' else None
import driver
raise SystemExit(driver.main())
''')
    def run(name, *extra, loader='a'):
        return subprocess.run([sys.executable, str(bootstrap), '--harness', 'loop_plain',
            '--dataset', 'demo', '--sampling', 'all', '--tasks', 't01,t02', '--jobs', '1',
            '--no-sandbox', '--rounds', '0', '--improver', 'none', '--no-summary', '--run-id', name,
            '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / ('work-' + name)),
            '--method-entrypoint', f'{sys.executable} {ROOT / "methods/noop/run.py"}', *extra],
            cwd=ROOT, env={**os.environ, 'HG_AGENT_BACKEND': 'mock', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
                           'AUTHORED_LOADER': loader}, capture_output=True, text=True, timeout=120)

    donor = run('donor')
    assert donor.returncode == 0, donor.stdout[-2000:] + donor.stderr[-2000:]
    directory = tmp_path / 'runs/donor'
    meta = json.loads((directory / 'run_meta.json').read_text())
    point = json.loads((directory / 'curve.jsonl').read_text().splitlines()[0])
    payload = json.loads((directory / 'h0.json').read_text())
    bundle = meta['measurement_components']
    assert point['measurement_components'] == payload['measurement_components'] == bundle
    assert payload['base']['measurement_components'] == bundle
    shared = run('shared', '--h0-from', 'donor')
    assert shared.returncode == 0, shared.stdout[-2000:] + shared.stderr[-2000:]
    shared_point = json.loads((tmp_path / 'runs/shared/curve.jsonl').read_text().splitlines()[0])
    assert shared_point['measurement_components'] == bundle
    rejected = run('changed', '--h0-from', 'donor', loader='c')
    assert rejected.returncode == 2
    assert 'loader identity changed' in rejected.stdout + rejected.stderr
    resumed = run('donor', '--resume', '--rounds', '1', loader='c')
    assert resumed.returncode == 2
    assert 'loader identity changed' in resumed.stdout + resumed.stderr
