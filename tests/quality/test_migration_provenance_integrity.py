"""Verify retained migration evidence without fetching or executing it."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'tests/fixtures/hg_migration_site/PROVENANCE.json'
LEDGER_SHA256 = '43ad529599451903e8e716aaf185def97268ad353e221fc45366d9fe8b928748'


def records():
    raw = LEDGER.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEDGER_SHA256
    entries = json.loads(raw)
    assert entries
    return entries


def test_checkout_carriers_match_independently_pinned_ledger():
    history_only = 0
    for entry in records():
        carrier = entry.get('carrier')
        if carrier is None:
            assert entry.get('availability') == 'git_history_only'
            assert re.fullmatch(r'[0-9a-f]{40}', entry['source_commit'])
            assert re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
            history_only += 1
            continue
        path = ROOT / carrier
        assert path.resolve().is_relative_to(ROOT) and path.is_file() and not path.is_symlink(), carrier
        raw = path.read_bytes()
        assert len(raw) == entry['bytes'], carrier
        assert hashlib.sha256(raw).hexdigest() == entry['sha256'], carrier
    assert history_only, 'history-only entries must remain explicitly distinguished'


def test_historical_sources_match_when_commit_is_available():
    entries = [entry for entry in records()
               if entry.get('availability') == 'git_history_only' or 'source_sha256' in entry]
    assert entries
    for entry in entries:
        commit = entry['source_commit']
        assert re.fullmatch(r'[0-9a-f]{40}', commit)
        path = Path(entry['source_path'])
        assert not path.is_absolute() and '..' not in path.parts
        present = subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=ROOT,
                                 capture_output=True, timeout=10)
        if present.returncode:
            pytest.skip('historical source commit not in this checkout; fetch it explicitly for this check')
        result = subprocess.run(['git', 'show', commit + ':' + path.as_posix()], cwd=ROOT,
                                capture_output=True, timeout=10, check=True)
        assert len(result.stdout) == entry.get('source_bytes', entry['bytes'])
        assert hashlib.sha256(result.stdout).hexdigest() == entry.get('source_sha256', entry['sha256'])
