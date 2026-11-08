"""Historical classification never licenses deleting or repinning the original bytes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_SHA256 = 'e4c717d04c7ccaaca861f63e6df2f2c9e766d4fecdc370d45f5dbdfd55c47784'


def test_inactive_index_retains_every_original_and_separate_licenses():
    raw = (ROOT / 'registry/inactive-evidence-v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == INDEX_SHA256
    index = json.loads(raw)
    assert len(index['files']) == 28
    for record in index['files']:
        path = ROOT / record['path']
        assert path.is_file(), 'inactive historical evidence missing: ' + record['path']
        data = path.read_bytes()
        assert len(data) == record['bytes']
        assert hashlib.sha256(data).hexdigest() == record['sha256']
    assert (ROOT / 'LICENSE').is_file()
    assert (ROOT / 'tests/fixtures/ujson_pypi_codex226/LICENSE.txt').is_file()
