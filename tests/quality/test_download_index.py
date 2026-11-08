"""Download documentation stays readable and complete on an unprovisioned checkout."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import dataset_lint, datasets  # noqa: E402


def entry(**overrides):
    return {"kind": "http", "name": "Released tasks", "version": "v1.0",
            "size": "1 MB", "url": "https://example.org/tasks.zip", "dst": "raw",
            "note": "Pinned source; prepare the downloaded data before evaluation.",
            **overrides}


def render(download):
    return datasets.render_index([{"dataset": "example", "entries": [download]}])


@pytest.mark.parametrize("download,expected", [
    (entry(sha256="a" * 64), ["**URL:** https://example.org/tasks.zip", "`" + "a" * 64 + "`"]),
    (entry(kind="hf-file", repo="owner/model", file="weights.bin"),
     ["**Repo:** `owner/model`", "**File:** `weights.bin`"]),
    (entry(kind="hf-dataset", repo="owner/tasks", files=["test.jsonl", "train.jsonl"],
           sha256={"train.jsonl": "a" * 64, "test.jsonl": "b" * 64}),
     ["**Files:** `test.jsonl`, `train.jsonl`", "| `test.jsonl` | `" + "b" * 64 + "` |",
      "| `train.jsonl` | `" + "a" * 64 + "` |"]),
    (entry(kind="pip", package="example-package"), ["**Package:** `example-package==v1.0`"]),
])
def test_download_types_retain_their_source_and_integrity_details(download, expected):
    if download["kind"] != "http":
        download.pop("url")
    if download["kind"] == "pip":
        download.pop("dst")
    document = render(download)
    assert "### Released tasks\n" in document
    assert f"**Type:** `{download['kind']}`" in document
    assert "**Version (pinned):** `v1.0`" in document
    assert download["note"] in document
    assert "{'" not in document
    for detail in expected:
        assert detail in document


@pytest.mark.parametrize("manifest", [None, "data/datasets/example/source files.json"])
def test_large_inventory_is_bounded_and_full_details_remain_accessible(manifest):
    files = {f"books/seed-{i:05}.txt": f"{i:064x}" for i in range(10_000)}
    download = entry(files=list(reversed(files)), sha256=files)
    if manifest:
        download["manifest"] = manifest
    original = deepcopy(download)
    document = render(download)
    assert "10,000 files" in document
    assert "10,000 per-file checksums" in document
    # Bound the entry independently of shared provisioning help in the preamble.
    assert len(document.split("## `example`\n", 1)[1]) < 2_000
    assert "| File | SHA256 |" not in document
    assert "books/seed-00000.txt" in document
    assert "books/seed-09999.txt" not in document
    if manifest:
        assert "(data/datasets/example/source%20files.json)" in document
    else:
        assert "--list --json --dataset example" in document
    assert download == original, "rendering must preserve the downloader's complete inventory"
    assert render(entry(**{**download, "files": list(files),
                           "sha256": dict(reversed(files.items()))})) == document


def test_long_filenames_also_get_bounded_examples():
    path = "books/" + "a" * 2_000 + "/seed-1.txt"
    document = render(entry(files=[path], sha256={path: "a" * 64}))
    assert "**Files:** 1 file (" in document
    assert len(document.split("## `example`\n", 1)[1]) < 2_000
    assert path not in document
    assert "--list --json --dataset example" in document


def test_checksum_table_escapes_filename_pipes():
    document = render(entry(sha256={"a|b.txt": "a" * 64}))
    assert "| `a\\|b.txt` |" in document


@pytest.mark.parametrize("kind", ["http", "hf-dataset"])
def test_summarized_inventory_still_fetches_and_verifies_every_file(tmp_path, monkeypatch, kind):
    payloads = {f"seed-{i}.txt": f"rules for {i}".encode() for i in range(12)}
    download = entry(kind=kind, repo="owner/tasks", files=list(payloads),
                     sha256={name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
                     manifest="source.json")

    def local_download(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[target.name])

    monkeypatch.setattr(datasets, "_download", local_download)
    assert "12 files" in render(download)
    ok, message = datasets.fetch_entry(download, tmp_path)
    assert ok, message
    assert {p.name for p in (tmp_path / "raw").iterdir()} == set(payloads)
    assert datasets.check_entry(download, tmp_path)["present"]
    (tmp_path / "raw" / "seed-11.txt").write_bytes(b"changed")
    assert not datasets.check_entry(download, tmp_path)["present"]


@pytest.mark.parametrize("manifest,valid", [
    ("source.json", True), ("missing.json", False), ("../outside.json", False),
    (".", False), ("/tmp/source.json", False), ([], False), ("", False),
])
def test_manifest_links_must_resolve_to_repository_files(tmp_path, monkeypatch, manifest, valid):
    (tmp_path / "source.json").write_text("{}")
    monkeypatch.setattr(dataset_lint, "ROOT", tmp_path)
    result = dataset_lint.Result("example")
    dataset_lint.lint_downloads([entry(manifest=manifest)], dataset_lint.load_schema(), result)
    assert result.ok == valid, result.errors
    if not valid:
        assert "manifest" in result.errors[0]


def test_catalog_reads_declarations_without_unrelated_imports_or_pool_initialization(tmp_path, monkeypatch):
    adapters = tmp_path / "data" / "datasets"
    adapters.mkdir(parents=True)
    (adapters / "example.py").write_text('''\
import unavailable_runtime_dependency
from pathlib import Path
KIND = "dataset"
REVISION = "v1.0"
BASE = "https://example.org/"
PINS = {"tasks.jsonl": "a" * 64}
POOL = Path("missing-task-pool.json").read_text()
def source_url(name):
    return BASE + REVISION + "/" + name
def downloads():
    return [{"kind": "http", "name": name, "version": REVISION,
             "url": source_url(name), "sha256": digest}
            for name, digest in PINS.items()]
''')
    monkeypatch.setattr(datasets, "ROOT", tmp_path)

    def unavailable_module(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(datasets.importlib, "import_module", unavailable_module)
    rows = datasets.datasets_with_downloads(source_fallback=True)
    assert rows == [("example", [{"kind": "http", "name": "tasks.jsonl", "version": "v1.0",
                                  "url": "https://example.org/v1.0/tasks.jsonl", "sha256": "a" * 64}])]
    assert not (tmp_path / "missing-task-pool.json").exists()


def test_unreadable_download_declaration_fails_instead_of_omitting_a_dataset(tmp_path, monkeypatch):
    adapters = tmp_path / "data" / "datasets"
    adapters.mkdir(parents=True)
    (adapters / "example.py").write_text('''\
KIND = "dataset"
raise FileNotFoundError("missing task pool")
PINS = missing_source_metadata()
def downloads():
    return PINS
''')
    monkeypatch.setattr(datasets, "ROOT", tmp_path)

    def unavailable_module(name):
        raise FileNotFoundError("missing task pool")

    monkeypatch.setattr(datasets.importlib, "import_module", unavailable_module)
    with pytest.raises(RuntimeError, match="Cannot read example downloads"):
        datasets.datasets_with_downloads(source_fallback=True)


def test_dataset_with_no_downloads_has_an_explicit_empty_section():
    document = datasets.render_index([{"dataset": "bundled", "entries": []}])
    assert "## `bundled`\n\n_Declares no downloads._" in document


@pytest.mark.parametrize("name,directory,override,destination", [
    ("kumo", "kumo", "HG_DATA_KUMO", "upstream"),
    ("gsm8k", "gsm8k", "HG_DATA_GSM8K", "."),
    ("costbench_dynamic", "costbench", "HG_DATA_COSTBENCH", "dynamic-prepared-v1"),
    ("strongreject", "strongreject/native-v1", "HG_DATA_STRONGREJECT_NATIVE_V1", "tasks/example.json"),
    ("terminal_bench", "terminal-bench", "HG_DATA_TERMINAL_BENCH", "."),
    ("bbeh", "bbeh", "HG_DATA_BBEH", "source/bbeh_data.json"),
    ("browsecomp", "browsecomp", "HG_DATA_BROWSECOMP", "source"),
    ("swe_bench_pro", "swe_bench_pro", "HG_DATA_SWE_BENCH_PRO", "_src/native-source.tar.gz"),
])
def test_logical_destinations_follow_adapter_layout_and_each_download(name, directory, override, destination):
    download = entry(dst=destination)
    original = deepcopy(download)
    document = datasets.render_index([{"dataset": name, "entries": [download]}])
    root = "<HG_DATA_ROOT>/" + directory
    expected = root if destination == "." else root + "/" + destination
    assert f"**Data root:** `{root}`" in document
    assert f"**Root override:** `{override}`" in document
    assert f"**Destination:** `{expected}`" in document
    assert download == original, "logical documentation must not change fetching paths"


def test_each_download_and_extraction_uses_its_own_relative_path():
    document = datasets.render_index([{"dataset": "kumo", "entries": [
        entry(name="archive", dst="_src/source.zip", extract_to="unpacked"),
        entry(name="books", dst="books"),
    ]}])
    assert "**Destination:** `<HG_DATA_ROOT>/kumo/_src/source.zip`" in document
    assert "**Extract to:** `<HG_DATA_ROOT>/kumo/unpacked`" in document
    assert "**Destination:** `<HG_DATA_ROOT>/kumo/books`" in document
    assert "/upstream" not in document


def test_logical_index_is_independent_of_local_configuration(tmp_path, monkeypatch):
    from harnessgrad import paths
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(paths, "CHECKOUT", root)
    rows = [{"dataset": "kumo", "entries": [entry(dst="upstream")]}]
    (root / ".env").write_text("HG_DATA_ROOT=../first-server\nHG_DATA_KUMO=../custom\n")
    first = datasets.render_index(rows)
    monkeypatch.setenv("HG_DATA_ROOT", "/another/server/data")
    monkeypatch.setenv("HG_DATA_KUMO", "/another/server/special-kumo")
    second = datasets.render_index(rows)
    assert first == second
    for physical_path in [str(root), "first-server", "/another/server"]:
        assert physical_path not in second


def test_explicit_absolute_destination_is_accessible_without_leaking_server_path():
    document = render(entry(dst="/some/server/private-destination"))
    assert "**Destination:** `<ABSOLUTE_DESTINATION>`" in document
    assert "/some/server" not in document
    assert "**Full declaration:** `python3 tools/datasets.py --list --json --dataset example`" in document


def test_check_output_keeps_real_paths_while_index_uses_logical_paths(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    physical_root = tmp_path / "server-disk" / "kumo"
    download = entry(dst="upstream")
    module = SimpleNamespace(DATASET_ROOT=physical_root)
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda **kwargs: [("kumo", [download])])
    monkeypatch.setattr(datasets, "_download_adapter", lambda _: module)
    monkeypatch.setattr(sys, "argv", ["datasets.py", "--check", "--dataset", "kumo"])
    assert datasets.main() == 1
    output = capsys.readouterr().out
    assert f"root: {physical_root}" in output
    assert str(physical_root / "upstream") in output
    assert "<HG_DATA_ROOT>" not in output
    document = datasets.render_index([{"dataset": "kumo", "entries": [download]}])
    assert "**Destination:** `<HG_DATA_ROOT>/kumo/upstream`" in document
    assert str(physical_root) not in document


def test_authored_check_and_fetch_are_read_only_and_report_missing_invalid(tmp_path, monkeypatch):
    import shutil
    from data.scaffolds import authored
    fixture = tmp_path / 'data/authored/example-fix-bug'
    shutil.copytree(ROOT / 'data/authored/example-fix-bug', fixture)
    monkeypatch.setattr(datasets, 'ROOT', tmp_path)
    entry = authored.download_entry('example-fix-bug', authored_root=fixture.parent)
    monkeypatch.setattr(datasets, '_download', lambda *a, **kw: pytest.fail('authored must not download'))
    snapshot = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert datasets.check_entry(entry, tmp_path)['present']
    assert datasets.fetch_entry(entry, tmp_path, force=True)[0]
    assert snapshot == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    (fixture / 'bundle.json').write_text('{invalid')
    result = datasets.check_entry(entry, tmp_path)
    assert result['status'] == 'invalid' and not result['present']
    assert not datasets.fetch_entry(entry, tmp_path)[0]
    (fixture / 'bundle.json').unlink()
    result = datasets.check_entry(entry, tmp_path)
    assert result['status'] == 'missing' and not result['present']


def test_missing_authored_declaration_does_not_abort_other_catalog_rows(tmp_path, monkeypatch):
    from data.scaffolds.authored import module_source
    adapters = tmp_path / 'data/datasets'
    adapters.mkdir(parents=True)
    (adapters / 'authored_example_fix_bug.py').write_text(module_source('example-fix-bug'))
    (adapters / 'neighbor.py').write_text('KIND="dataset"\ndef downloads():\n return [{"kind":"http","name":"neighbor","dst":"missing"}]\n')
    monkeypatch.setattr(datasets, 'ROOT', tmp_path)
    rows = dict(datasets.datasets_with_downloads(source_fallback=True))
    assert set(rows) == {'authored_example_fix_bug', 'neighbor'}
    authored_row = datasets.check_entry(rows['authored_example_fix_bug'][0], tmp_path)
    assert authored_row['status'] == 'missing'
    assert not authored_row['present']
    assert not datasets.check_entry(rows['neighbor'][0], tmp_path)['present']
