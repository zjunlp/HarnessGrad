"""`--fetch` must cross the wall, and must not leave a directory that lies.

Why this file exists
--------------------
One provisioning run measured two failures on this host (2026-11-02), and neither is a
missing feature: both are the tool doing something defensible and then reporting it wrong.

* **The wall.** `raw.githubusercontent.com` is reset-blocked (`curl: (35) Recv failure`)
  while `github.com` and `hf-mirror.com` answer. `strongreject` landed **8 of 155** files
  and `memoryagentbench`'s pinned `punkt_tab.zip` failed the same way -- and every one of
  those URLs *is* a raw URL, so on such a host those datasets cannot be provisioned at
  all. `tools/datasets.py` already defaults to `hf-mirror.com` for Hugging Face entries
  because the users are behind the same wall; `HG_GH_RAW_MIRROR` is the same answer for
  the GitHub-raw transport, opt-in so a host with normal egress asks for exactly the URL
  the loader pinned.
* **The lie.** The same run left an empty `<root>/upstream/` behind the failed kumo fetch.
  kumo's `prepare.py` skips its clone when that path exists, so the next sanctioned
  command died in 0.05 s on a missing `.gitignore`: an empty tree is a *false* "already
  prepared". Cleanup therefore has to be exactly as wide as what the call created.

So the mirror is tested for the three properties that make it safe rather than merely
present -- off by default (byte-identical URLs), applied to `raw.githubusercontent.com`
only (a rewrite of a checksummed URL on another host would look like corruption, not like
a transport choice), and named on the `--fetch` line (provenance survives the fetch) --
and the cleanup is tested for both halves: it removes what it created, and it never
removes what it did not.

No test touches the network. The transfers use a `file://` source, which both transports
(`curl` and `urllib`) support; the URL assertions replace the transfer with a recorder so
that the URL a walled host *would* ask for is what gets inspected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.datasets as datasets  # noqa: E402

#: A real `raw.githubusercontent.com` URL from `data/datasets/strongreject.py` (same path
#: shape as all 155 of its entries), so the assertion is about the host the run measured
#: and not about a synthetic host invented for the test.
RAW_URL = ("https://raw.githubusercontent.com/laude-institute/harbor-datasets/"
           "9f65cabfdadf2f2ddfff8da83b325f1efc5c3f3f/datasets/strongreject/"
           "strongreject_disinformation_and_deception_0004_poems/tests/"
           "task_metadata.json")

#: The other hosts a real `downloads()` list names, none of which may be rewritten:
#: `hf-mirror.com` (the default Hugging Face endpoint), `github.com` and a release asset,
#: and the Azure blob `memoryagentbench` pins its `o200k_base` encoding on.
UNTOUCHED_URLS = (
    "https://hf-mirror.com/datasets/ai-hyz/MemoryAgentBench/resolve/" + "a" * 40 + "/x.json",
    "https://github.com/alfworld/alfworld/releases/download/0.2.2/json_2.1.1_json.zip",
    "https://codeload.github.com/ethz-spylab/agentdojo/tar.gz/" + "a" * 40,
    "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
)

MIRROR = "https://ghproxy.example"


def _http_entry(url: str, dst: str, **extra) -> dict:
    """A minimal `http` entry; only the fields `fetch_entry` reads are spelled."""
    return {"kind": "http", "name": "synthetic raw file", "version": "a" * 40,
            "size": "small", "url": url, "dst": dst,
            "note": "synthetic entry for the fetch contract", **extra}


def _recording_download(seen: list[str]):
    """A `_download` stand-in that records the URL and writes the target.

    Replacing the transfer is what makes the URL assertion exact: the property is which
    URL a walled host would ask for, and that host is by definition unreachable here.
    """
    def fake(url: str, target: Path, *, timeout: int = 600) -> None:
        seen.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("payload", encoding="utf-8")
    return fake


def test_the_mirror_is_off_by_default(tmp_path, monkeypatch):
    """With the variable unset, the URL is the loader's URL -- byte for byte.

    The default is the whole safety argument: a checksum was taken from that URL, so a
    host with normal egress must not be given a different request because a variable it
    never set happens to exist elsewhere in the environment.
    """
    monkeypatch.delenv(datasets.GH_RAW_MIRROR_ENV, raising=False)
    seen: list[str] = []
    monkeypatch.setattr(datasets, "_download", _recording_download(seen))

    ok, message = datasets.fetch_entry(_http_entry(RAW_URL, "tasks/one.json"), tmp_path)

    assert ok, message
    # A rewrite here would silently change the request on every host that has not opted in.
    assert seen == [RAW_URL], (
        f"unset {datasets.GH_RAW_MIRROR_ENV} still rewrote the URL: {seen}")
    # Claiming a mirror that was not used would make provenance a lie in the other direction.
    assert "mirror" not in message, f"an unmirrored fetch names a mirror: {message!r}"


def test_the_mirror_rewrites_a_raw_url_and_is_visible_in_the_fetch_line(tmp_path, monkeypatch):
    """A set mirror produces the proxy shape, and the `--fetch` line says so.

    The shape is the common one (`<mirror>/<full upstream URL>`), and the message matters
    because it is the only place a later reader can recover provenance: `DOWNLOADS.md` is
    generated and must stay host-independent, and `--check` answers "present", not "from
    where". `main()` prints this exact string.
    """
    monkeypatch.setenv(datasets.GH_RAW_MIRROR_ENV, MIRROR + "/")
    seen: list[str] = []
    monkeypatch.setattr(datasets, "_download", _recording_download(seen))

    ok, message = datasets.fetch_entry(_http_entry(RAW_URL, "tasks/one.json"), tmp_path)

    assert ok, message
    expected = f"{MIRROR}/{RAW_URL}"
    # The mirror is a prefix in front of the full URL, not a replacement for the host;
    # a trailing slash on the variable must not produce a double slash either.
    assert seen == [expected], f"the mirror was not applied to the request: {seen}"
    # Provenance: the fetch line names the mirror, so these bytes can be traced.
    assert f"(via mirror {MIRROR})" in message, (
        f"the mirror is absent from the fetch line: {message!r}")


def test_the_mirror_is_refused_for_hf_entries(tmp_path, monkeypatch):
    """`hf-dataset` entries keep their endpoint, mirror or not.

    A mirror silently standing in front of the Hugging Face endpoint would break the same
    thing it exists to help: the entry's `sha256` and its revision were taken from the
    endpoint's bytes, so a rewrite there is a hash mismatch that reads like corruption.
    """
    monkeypatch.setenv(datasets.GH_RAW_MIRROR_ENV, MIRROR)
    seen: list[str] = []
    monkeypatch.setattr(datasets, "_download", _recording_download(seen))
    entry = {"kind": "hf-dataset", "name": "synthetic hf file",
             "repo": "ai-hyz/MemoryAgentBench", "version": "b" * 40,
             "dst": "raw", "files": ["fixed.json"],
             "note": "synthetic entry for the mirror-refusal contract"}

    ok, message = datasets.fetch_entry(entry, tmp_path, endpoint="https://hf-mirror.com")

    assert ok, message
    assert seen == [f"https://hf-mirror.com/datasets/ai-hyz/MemoryAgentBench/resolve/"
                    f"{'b' * 40}/fixed.json"], f"an hf URL was rewritten: {seen}"
    assert "mirror" not in message, f"an hf fetch claims the raw mirror: {message!r}"


@pytest.mark.parametrize("url", UNTOUCHED_URLS)
def test_the_mirror_refuses_every_other_host(url):
    """Only `raw.githubusercontent.com` is ever rewritten.

    `github.com`, `codeload.github.com`, a release asset, an Azure blob and the Hugging
    Face mirror are all checksummed upstreams of their own; a transport convenience that
    edited one of them would turn a working host into "corrupt data".
    """
    # Returning the URL unchanged is what keeps the mirror out of every other checksum.
    assert datasets.gh_raw_url(url, mirror=MIRROR) == url, url


def test_a_failed_fetch_leaves_no_new_directory(tmp_path):
    """The measured kumo failure: a failed transfer leaves nothing an adapter can read.

    `prepare.py` skips its clone when `<root>/upstream` exists and then reads a
    `.gitignore` that no failed download created. A transfer that wrote no bytes must
    therefore take back the directories it had to create, not just the partial file.
    """
    source = tmp_path / "no-such-source.bin"  # never created: the transfer fails
    entry = _http_entry(source.as_uri(), "kumo/upstream/.gitignore")

    ok, message = datasets.fetch_entry(entry, tmp_path)

    assert not ok, message
    # Two levels below the base were created by this call, so both must be gone.
    assert not (tmp_path / "kumo").exists(), (
        f"the failed fetch left {sorted(p.name for p in tmp_path.iterdir())}")
    # The half-written `.part` file is also not allowed to survive as evidence of a fetch.
    assert not list(tmp_path.rglob("*.part")), "the failed fetch left a partial file"


def test_a_failed_fetch_keeps_a_directory_it_did_not_create(tmp_path):
    """Cleanup is exactly as wide as the fetch: pre-existing directories survive.

    Removing a non-empty tree would delete work another entry (or the operator) did, and
    removing a pre-existing empty one is still not this call's to delete -- the guard that
    reads "does this path exist?" belongs to whoever created it.
    """
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "note.txt").write_text("operator's file", encoding="utf-8")
    empty = tmp_path / "pre-existing-empty"
    empty.mkdir()
    source = tmp_path / "no-such-source.bin"

    ok, message = datasets.fetch_entry(_http_entry(source.as_uri(), "keep/sub/one.bin"),
                                       tmp_path)
    assert not ok, message
    # A non-empty pre-existing parent is untouched, and only this call's `sub` is removed.
    assert (keep / "note.txt").is_file(), "a non-empty tree was removed by the cleanup"
    assert not (keep / "sub").exists(), "the directory this call created survived"

    ok, message = datasets.fetch_entry(
        _http_entry(source.as_uri(), "pre-existing-empty/deep/two.bin"), tmp_path)
    assert not ok, message
    # An empty pre-existing directory is also not this call's to remove.
    assert empty.is_dir(), "an empty pre-existing directory was removed by the cleanup"
    assert not (empty / "deep").exists(), "the nested directory this call created survived"


def test_a_checksum_rejected_download_leaves_no_new_directory(tmp_path):
    """The second failure path: bytes that arrived but were deleted take their tree too.

    `sha256` is verified *after* the transfer, so this failure happens outside `_download`
    and the wrapper that fixes a transport failure cannot cover it. A dataset root created
    for bytes the pin then rejected is the same false "prepared" as a failed transfer.
    """
    source = tmp_path / "source.bin"
    source.write_bytes(b"bytes the pin does not describe")
    entry = _http_entry(source.as_uri(), "checksum/nested/file.bin", sha256="0" * 64)

    ok, message = datasets.fetch_entry(entry, tmp_path)

    assert not ok, message
    # The corrupted file is removed rather than left looking provisioned.
    assert "removed the download" in message, message
    # And so is the tree that was created only to hold it.
    assert not (tmp_path / "checksum").exists(), "a rejected download left its tree"


def test_a_successful_fetch_leaves_the_file(tmp_path):
    """The other half of the cleanup contract: success keeps the tree it created.

    A cleanup that fired on success -- or a `.part` file left beside the target -- would
    pass the failure tests and still break every ordinary fetch.
    """
    source = tmp_path / "source.bin"
    source.write_bytes(b"fixed bytes")
    entry = _http_entry(source.as_uri(), "nested/dir/file.bin")

    ok, message = datasets.fetch_entry(entry, tmp_path)

    assert ok, message
    assert (tmp_path / "nested" / "dir" / "file.bin").read_bytes() == b"fixed bytes"
    assert not list(tmp_path.rglob("*.part")), "a successful fetch left a partial file"


def test_the_fetch_command_line_prints_the_mirror(capsys, tmp_path, monkeypatch):
    """The provenance reaches the operator, not just the return value.

    `main --fetch` prints `fetch_entry`'s message verbatim, so this is the end of the
    chain a person reads when a fetch is slow, fails, or has to be reproduced elsewhere.
    """
    monkeypatch.setenv(datasets.GH_RAW_MIRROR_ENV, MIRROR)
    seen: list[str] = []
    monkeypatch.setattr(datasets, "_download", _recording_download(seen))
    base = tmp_path / "data" / "synthetic"
    entry = _http_entry(RAW_URL, "one.json")
    monkeypatch.setattr(datasets, "datasets_with_downloads", lambda: [("synthetic", [entry])])
    monkeypatch.setattr(datasets, "dataset_root", lambda module: base)

    class _FakeImportlib:
        """`main` imports the adapter module only to ask it for its dataset root."""

        @staticmethod
        def import_module(_name):
            return object()

    monkeypatch.setattr(datasets, "importlib", _FakeImportlib())

    assert datasets.main(["--fetch"]) == 0
    printed = capsys.readouterr().out
    assert "ok" in printed and f"(via mirror {MIRROR})" in printed, printed
    # And the request itself went through the mirror, not around it.
    assert seen == [f"{MIRROR}/{RAW_URL}"], seen


def test_the_generated_index_documents_the_mirror():
    """`DOWNLOADS.md`'s header is where an operator looks, so the switch is documented there.

    The header is rendered, so the text has to live in `render_index` -- a hand-edited
    `DOWNLOADS.md` would be reverted by the next `--write`. The second assertion catches
    the opposite mistake, a changed renderer whose artifact was never regenerated: the
    checked-in file would then describe a tool nobody has.
    """
    rendered = datasets.render_index([])
    # A reader who never opens tools/datasets.py must still find the variable and its shape.
    assert datasets.GH_RAW_MIRROR_ENV in rendered, "the header does not name the variable"
    assert "<mirror>/https://raw.githubusercontent.com/<path>" in rendered, (
        "the header does not state the URL shape the mirror produces")
    header = rendered.split("## `", 1)[0]
    committed = datasets.INDEX.read_text(encoding="utf-8")
    assert committed.startswith(header), (
        "DOWNLOADS.md's header is stale; run `python3 tools/datasets.py --write`")


def test_an_hf_file_entry_fetches_its_one_file(tmp_path, monkeypatch):
    """`hf-file` was documented and unimplemented: `--fetch` answered "no fetch transport".

    `docs/dataset-onboarding.md` has listed `hf-file` (`repo` + `file` + `dst`) as one of
    the four kinds since the download contract was written, and `tools/datasets_doc.py`
    renders a fetch command for an adapter that declares one. The transport only knew
    `http` and `hf-dataset`, so the command the document printed for such a dataset failed
    with "[hf-file] ... no fetch transport" -- a provisioning instruction that cannot work
    is worse than no instruction, because it reads as already solved.
    """
    monkeypatch.setattr(datasets, "_download", _recording_download(seen := []))
    entry = {"kind": "hf-file", "name": "native test file", "repo": "BBEH/bbeh",
             "file": "bbeh_data.json", "version": "08e07a803851822c04399782ece3c4a07ce419f9",
             "size": "native release file", "dst": "source/bbeh_data.json",
             "note": "synthetic entry for the hf-file transport"}

    ok, message = datasets.fetch_entry(entry, tmp_path)

    assert ok, message
    # `dst` on an `hf-file` entry is the file, not a directory to put the file in.
    assert (tmp_path / "source/bbeh_data.json").read_text(encoding="utf-8") == "payload"
    assert seen == ["https://hf-mirror.com/datasets/BBEH/bbeh/resolve/"
                    "08e07a803851822c04399782ece3c4a07ce419f9/bbeh_data.json"], seen


def test_an_hf_file_entry_without_a_file_is_refused(tmp_path):
    """The one field the transport needs is the one the shape check cannot catch."""
    entry = {"kind": "hf-file", "name": "no file", "repo": "a/b", "version": "a" * 40,
             "dst": "source/x.json", "size": "s",
             "note": "synthetic entry missing the file field"}
    ok, message = datasets.fetch_entry(entry, tmp_path)
    assert ok is False and "no `file`" in message, message
