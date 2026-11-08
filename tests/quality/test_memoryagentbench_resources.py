"""Resource checks and bounded pinned archive restoration; no network requests."""
import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from data.datasets.memoryagentbench import prepare_resources as resources


def fixture(monkeypatch, *, extra_member=False, duplicate=False):
    payloads = {f"punkt_tab/english/{name}": f"verified {name}\n".encode() for name in (
        "abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt")}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
        if extra_member:
            archive.writestr("../../escape", b"never extract me")
            archive.writestr("punkt_tab/german/abbrev_types.txt", b"not selected")
        if duplicate:
            with pytest.warns(UserWarning):
                archive.writestr(next(iter(payloads)), b"duplicate")
    zipped = stream.getvalue()
    encoding = b"pinned encoding bytes"
    entries = [{"path": "resources/nltk/tokenizers/" + name, "archive_member": name,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "source_url": "https://pinned.example/punkt.zip"} for name, data in payloads.items()]
    entries.append({"path": "resources/tiktoken/cache", "bytes": len(encoding),
                    "sha256": hashlib.sha256(encoding).hexdigest(), "source_url": "https://pinned.example/encoding"})
    lock = {"files": entries, "nltk_archive_sha256": hashlib.sha256(zipped).hexdigest(),
            "nltk_archive_url": "https://pinned.example/punkt.zip", "nltk_data_revision": "pinned-revision"}
    monkeypatch.setattr(resources, "read_spec", lambda name: lock)
    def no_network(url):
        raise AssertionError("unexpected network: " + url)
    monkeypatch.setattr(resources, "_fetch", no_network)
    return lock, zipped, encoding


def write_encoding(root, data):
    path = root / "tiktoken/cache"; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)


def test_default_missing_check_is_read_only_and_offline(tmp_path, monkeypatch):
    fixture(monkeypatch)
    with pytest.raises(ValueError, match="missing or corrupt"):
        resources.provision(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_archive_restores_only_four_selected_members(tmp_path, monkeypatch):
    _, zipped, encoding = fixture(monkeypatch, extra_member=True)
    archive = tmp_path / "pinned.zip"; archive.write_bytes(zipped)
    root = tmp_path / "resources"; write_encoding(root, encoding)
    result = resources.provision(root, archive=archive)
    assert result["restored_files"] == 4 and result["verified_files"] == 5
    assert len(list(root.rglob("*.*"))) == 4
    assert not (tmp_path / "escape").exists()
    assert not (root / "nltk/tokenizers/punkt_tab/german").exists()
    assert resources.provision(root)["restored_files"] == 0


def test_bad_archive_pin_prevents_all_writes(tmp_path, monkeypatch):
    _, _, encoding = fixture(monkeypatch)
    archive = tmp_path / "pinned.zip"; archive.write_bytes(b"wrong")
    root = tmp_path / "resources"; write_encoding(root, encoding)
    with pytest.raises(ValueError, match="archive checksum"):
        resources.provision(root, archive=archive)
    assert not (root / "nltk").exists()


def test_missing_encoding_cannot_trigger_implicit_download(tmp_path, monkeypatch):
    _, zipped, _ = fixture(monkeypatch)
    archive = tmp_path / "pinned.zip"; archive.write_bytes(zipped)
    root = tmp_path / "resources"
    with pytest.raises(ValueError, match="encoding missing"):
        resources.provision(root, archive=archive)
    assert not root.exists()


def test_download_is_opt_in_pinned_and_atomic(tmp_path, monkeypatch):
    lock, zipped, encoding = fixture(monkeypatch)
    fetched = []
    def fake_download(url):
        fetched.append(url)
        return zipped if url == lock["nltk_archive_url"] else encoding
    monkeypatch.setattr(resources, "_fetch", fake_download)
    result = resources.provision(tmp_path, download=True)
    assert result["restored_files"] == 5 and len(fetched) == 2
    assert (tmp_path / "downloads/punkt_tab.zip").read_bytes() == zipped
    assert not list(tmp_path.rglob(".mab-resource-*"))
    assert resources.provision(tmp_path)["restored_files"] == 0


def test_download_bad_encoding_writes_nothing(tmp_path, monkeypatch):
    lock, zipped, _ = fixture(monkeypatch)
    monkeypatch.setattr(resources, "_fetch", lambda url: zipped if url == lock["nltk_archive_url"] else b"bad")
    with pytest.raises(ValueError, match="resource_hash"):
        resources.provision(tmp_path, download=True)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_existing_resource_needs_explicit_restore(tmp_path, monkeypatch):
    lock, zipped, encoding = fixture(monkeypatch)
    monkeypatch.setattr(resources, "_fetch", lambda url: zipped if url == lock["nltk_archive_url"] else encoding)
    resources.provision(tmp_path, download=True)
    target = tmp_path / "nltk/tokenizers/punkt_tab/english/abbrev_types.txt"; target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="missing or corrupt"):
        resources.provision(tmp_path)
    assert target.read_bytes() == b"bad"
    result = resources.provision(tmp_path, archive=tmp_path / "downloads/punkt_tab.zip")
    assert result["restored_files"] == 1


def test_duplicate_selected_archive_members_refused(tmp_path, monkeypatch):
    _, zipped, encoding = fixture(monkeypatch, duplicate=True)
    archive = tmp_path / "pinned.zip"; archive.write_bytes(zipped)
    write_encoding(tmp_path / "resources", encoding)
    with pytest.raises(ValueError, match="duplicated"):
        resources.provision(tmp_path / "resources", archive=archive)


def test_symlink_escape_refused(tmp_path, monkeypatch):
    fixture(monkeypatch)
    (tmp_path / "nltk").symlink_to("/tmp")
    with pytest.raises(ValueError, match="escapes"):
        resources.provision(tmp_path)


def test_pinned_member_size_checked_before_decompression(tmp_path, monkeypatch):
    lock, zipped, encoding = fixture(monkeypatch)
    lock["files"][0]["bytes"] += 1
    archive = tmp_path / "pinned.zip"; archive.write_bytes(zipped)
    write_encoding(tmp_path / "resources", encoding)
    with pytest.raises(ValueError, match="member size"):
        resources.provision(tmp_path / "resources", archive=archive)


def test_lock_cannot_expand_archive_member_selection(tmp_path, monkeypatch):
    lock, _, _ = fixture(monkeypatch)
    lock["files"][0]["archive_member"] = "../../escape"
    with pytest.raises(ValueError, match="exactly the four"):
        resources.provision(tmp_path)


def test_explicit_local_import_retains_member_provenance(tmp_path, monkeypatch):
    lock, zipped, encoding = fixture(monkeypatch)
    source = tmp_path / "source"
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        for entry in lock["files"]:
            path = source / entry["path"].removeprefix("resources/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(entry["archive_member"]) if "archive_member" in entry else encoding)
    destination = tmp_path / "destination"
    result = resources.provision(destination, from_resources=source)
    assert result["restored_files"] == 5
    assert len(result["verified_local_sources"]) == 5
    assert all(item["source"].startswith(str(source)) for item in result["verified_local_sources"])
    assert not (destination / "downloads/punkt_tab.zip").exists()
    assert resources.provision(destination)["restored_files"] == 0


def test_local_import_bad_member_publishes_nothing(tmp_path, monkeypatch):
    lock, _, _ = fixture(monkeypatch)
    source = tmp_path / "source"
    target = source / lock["files"][0]["path"].removeprefix("resources/")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not the locked bytes")
    destination = tmp_path / "destination"
    with pytest.raises(ValueError, match="resource_hash_mismatch"):
        resources.provision(destination, from_resources=source)
    assert not destination.exists()


def test_offline_archive_receipt_records_exact_members(tmp_path, monkeypatch):
    lock, zipped, encoding = fixture(monkeypatch)
    archive = tmp_path / 'retained-punkt.zip'
    archive.write_bytes(zipped)
    root = tmp_path / 'resources'
    write_encoding(root, encoding)
    receipt = resources.provision(root, archive=archive)
    provenance = receipt['verified_archive_source']
    assert provenance['kind'] == 'local_archive'
    assert provenance['path'] == str(archive.resolve())
    assert provenance['declared_source_url'] == lock['nltk_archive_url']
    assert 'acquisition_url' not in provenance
    assert 'cache_destination' not in provenance
    assert provenance['sha256'] == lock['nltk_archive_sha256']
    assert provenance['bytes'] == len(zipped)
    assert len(provenance['members']) == 4
    for member in provenance['members']:
        entry = next(row for row in lock['files'] if row['path'] == member['path'])
        assert member['archive_member'] == entry['archive_member']
        assert member['sha256'] == entry['sha256']
        assert member['bytes'] == entry['bytes']
    assert resources.provision(root)['verified_archive_source'] is None


def test_differential_rejects_unpinned_upstream_before_execution(tmp_path):
    import runpy
    control = runpy.run_path(str(Path(resources.__file__).parent / 'verify/tokenizer_differential.py'))
    module = tmp_path / 'untrusted.py'
    module.write_text('raise RuntimeError("must never execute")')
    with pytest.raises(ValueError, match='checksum mismatch'):
        control['upstream_function'](module, None, None)


def test_real_locked_tokenizer_upstream_differential_when_explicitly_provided():
    import os
    resource_root = os.environ.get('MAB_DIFFERENTIAL_RESOURCES')
    upstream = os.environ.get('MAB_DIFFERENTIAL_CHUNK_MODULE')
    if not resource_root or not upstream:
        pytest.skip('explicit original-lock resources and pinned upstream chunk module required')
    import json
    import subprocess
    import sys
    file = Path(resources.__file__).parent / 'verify/tokenizer_differential.py'
    result = subprocess.run([os.environ.get('MAB_DIFFERENTIAL_PYTHON', sys.executable), '-I', '-B', str(file),
                             '--resources', resource_root, '--upstream-module', upstream],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['author_owned_text_only'] and receipt['model_calls'] == 0
    assert len(receipt['results']) == 6
    assert all(row['sentence_equal'] and row['bpe_ids_equal'] and row['upstream_chunks_equal']
               for row in receipt['results'])


@pytest.mark.parametrize('corrupt_cache', [False, True])
def test_download_archive_provenance_does_not_claim_cached_input(tmp_path, monkeypatch, corrupt_cache):
    lock, zipped, encoding = fixture(monkeypatch)
    cache = tmp_path / 'downloads/punkt_tab.zip'
    if corrupt_cache:
        cache.parent.mkdir()
        cache.write_bytes(b'corrupt previous cache')
    calls = []
    def fetch(url):
        calls.append(url)
        return zipped if url == lock['nltk_archive_url'] else encoding
    monkeypatch.setattr(resources, '_fetch', fetch)
    receipt = resources.provision(tmp_path, download=True)
    provenance = receipt['verified_archive_source']
    assert provenance['kind'] == 'download'
    assert provenance['acquisition_url'] == lock['nltk_archive_url']
    assert provenance['declared_source_url'] == lock['nltk_archive_url']
    assert provenance['cache_destination'] == str(cache)
    assert 'path' not in provenance
    assert provenance['sha256'] == lock['nltk_archive_sha256']
    assert calls.count(lock['nltk_archive_url']) == 1
    assert cache.read_bytes() == zipped
