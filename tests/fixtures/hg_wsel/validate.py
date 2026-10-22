#!/usr/bin/env python3
"""Record exact commands/output; independently hash selected-only external inputs."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
PACKET = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKET))
from packet import NAMES


def run(argv):
    command = shlex.join(argv)
    print('$ ' + command, flush=True)
    result = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    with (ROOT / 'registry/w-sel-validation.txt').open('a') as log:
        log.write('\n$ ' + command + '\n' + result.stdout + f'exit={result.returncode}\n')
    print(result.stdout, end='', flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    with (ROOT / 'registry/w-sel-validation.txt').open('a') as log:
        log.write('\n$ python3 tests/fixtures/hg_wsel/validate.py\n')
    restored = json.loads((PACKET / 'restored-observations.json').read_bytes())
    for item in restored:
        raw = (ROOT / item['restored_path']).read_bytes()
        assert raw == Path(item['existing_read_only_source']).read_bytes()
        assert len(raw) == item['bytes']
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
    custody = json.loads((PACKET / 'preserved-custody.json').read_bytes())
    raw = Path(custody['read_only_prior_path']).read_bytes()
    assert len(raw) == custody['prior_bytes']
    assert hashlib.sha256(raw).hexdigest() == custody['prior_sha256']
    old = json.loads(raw)['datasets']
    for item in custody['checks']:
        name = item['dataset']
        manifest = json.loads((PACKET / (name + '.json')).read_bytes())
        assert manifest['installed_source']['sha256'] == old[name]['source_sha256'] == item['current_source_sha256'] == item['preserved_source_sha256']
        assert manifest['installed_source']['bytes'] == old[name]['source_bytes']
        assert manifest['adapter_presented_count'] == old[name]['loaded_rows']
    message = 'PASS: 11 restored ignored text observations byte-equal; 6 preserved installed source hashes/sizes/counts match'
    print(message, flush=True)
    with (ROOT / 'registry/w-sel-validation.txt').open('a') as log:
        log.write(message + '\n')
    run(['python3', 'tools/check_dataset_blockers.py'])
    run(['python3', 'tests/fixtures/hg_wsel/packet.py'])
    run(['python3', 'tests/fixtures/hg_wsel/check_scope.py'])
    manifests = [json.loads((PACKET / (name + '.json')).read_bytes()) for name in NAMES]
    # Independent system utility hashes bytes again; none are taken from historical claims.
    inputs = [m['installed_source']['path'] for m in manifests]
    exported = [str(Path(m['export_path']) / rel) for m in manifests for rel in m['export_files']]
    run(['sha256sum', *inputs, *exported])
    # Check exact expected digests against independent sha256sum's actual output.
    expected = {m['installed_source']['path']: m['installed_source']['sha256'] for m in manifests}
    expected.update({str(Path(m['export_path']) / p): h
                     for m in manifests for p, h in m['export_files'].items()})
    got = subprocess.check_output(['sha256sum', *expected], text=True, cwd=ROOT)
    assert {line.split('  ', 1)[1]: line.split('  ', 1)[0] for line in got.splitlines()} == expected
    print('PASS: independent sha256sum agrees for 8 installed sources and 133 export files')
    with (ROOT / 'registry/w-sel-validation.txt').open('a') as log:
        log.write('PASS: independent sha256sum agrees for 8 installed sources and 133 export files\n')
    run(['python3', '-m', 'pytest', '-q', 'tests/quality/test_dataset_blockers.py',
         'tests/quality/test_dataset_journal_coverage.py',
         '--basetemp=/tmp/harnessgrad-pytest-w-sel'])
    run(['sha256sum', '-c', 'tests/fixtures/hg_wsel/SHA256SUMS'])
    run(['git', 'diff', '--check'])


if __name__ == '__main__':
    main()
