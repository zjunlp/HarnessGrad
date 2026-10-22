"""Test-only code acquisition gate; no evaluator implementation or invocation."""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/bfcl_ast_source"
REPOSITORY = "https://github.com/ShishirPatil/gorilla"
ARCHIVE_SHA256 = "57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe"
MAX_METADATA = 1 << 20
MAX_SOURCE = 16 << 20
REFUSAL = dict(status="blocked", reason="bfcl_ast_source_contract_refused",
               measured=False, production_authorized=False)


class SourceContractError(ValueError):
    pass


def require(condition):
    if not condition:
        raise SourceContractError(REFUSAL["reason"])


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def text_value(value):
    return isinstance(value, str) and bool(value.strip())


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def no_constant(_):
    raise SourceContractError(REFUSAL["reason"])


def relative_path(value):
    require(text_value(value) and "\\" not in value)
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and ".." not in path.parts)
    require(not any(part.startswith(".") for part in path.parts))
    require(not set(path.parts) & {"data", "possible_answer", "results", "predictions", "heldout"})
    require(not value.lower().endswith((".json", ".jsonl", ".csv", ".parquet", ".zip",
                                        ".gz", ".tar", ".whl", ".pyc")))
    return path


def regular_file(root, path):
    """Reject links before file content access, including links in parent directories."""
    current = root
    require(stat.S_ISDIR(current.lstat().st_mode))
    for part in path.parts[:-1]:
        current = current / part
        require(stat.S_ISDIR(current.lstat().st_mode))
    candidate = root / path
    require(stat.S_ISREG(candidate.lstat().st_mode))
    return candidate


def check_source(directory, approved_receipt_sha256):
    """Validate exact approved metadata before reading any projected source bytes.

    The trusted caller owns/quiesces the directory and independently supplies the
    pin. This test helper is not production admission or a hostile-tree sandbox.
    """
    try:
        require(digest(approved_receipt_sha256))
        receipt = regular_file(directory, PurePosixPath("provenance.json"))
        require(receipt.stat().st_size <= MAX_METADATA)
        raw = receipt.read_bytes()
        require(len(raw) <= MAX_METADATA)
        require(hashlib.sha256(raw).hexdigest() == approved_receipt_sha256)
        doc = json.loads(raw, object_pairs_hook=unique_object, parse_constant=no_constant)
        require(isinstance(doc, dict) and set(doc) == {
            "schema", "repository", "repo_tag", "archive_sha256", "commit",
            "review", "entrypoints", "files"})
        require(doc["schema"] == "harnessgrad.bfcl-ast-source.v1")
        require(doc["repository"] == REPOSITORY and doc["repo_tag"] == "v1.3")
        require(doc["archive_sha256"] == ARCHIVE_SHA256)
        require(isinstance(doc["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", doc["commit"])
                and doc["commit"] != "0" * 40)
        review = doc["review"]
        require(isinstance(review, dict) and set(review) == {
            "reviewed_by", "tag_commit_binding", "archive_member_binding",
            "code_only_closure", "entrypoint_contract"})
        require(all(text_value(value) for value in review.values()))
        require(isinstance(doc["files"], list) and 1 <= len(doc["files"]) <= 256)
        files = {}
        for row in doc["files"]:
            require(isinstance(row, dict) and set(row) == {
                "path", "role", "bytes", "sha256", "archive_member", "requires"})
            path = relative_path(row["path"])
            require(row["path"] not in files)
            require(row["role"] in {"source", "license", "dependency-spec"})
            require(row["role"] != "source" or path.suffix == ".py")
            require(type(row["bytes"]) is int and 0 < row["bytes"] <= MAX_SOURCE)
            require(digest(row["sha256"]))
            require(row["archive_member"] == "gorilla-1.3/" + row["path"])
            require(isinstance(row["requires"], list)
                    and all(text_value(value) for value in row["requires"]))
            require(len(set(row["requires"])) == len(row["requires"]))
            files[row["path"]] = row
        require(sum(row["bytes"] for row in files.values()) <= MAX_SOURCE)
        require(any(row["role"] == "license" for row in files.values()))
        require(all(set(row["requires"]) <= files.keys() for row in files.values()))
        entrypoints = doc["entrypoints"]
        require(isinstance(entrypoints, dict) and set(entrypoints) == {"parser", "checker", "dispatch"})
        for entry in entrypoints.values():
            require(isinstance(entry, dict) and set(entry) == {"path", "symbol"})
            require(text_value(entry["path"]) and entry["path"] in files)
            require(files[entry["path"]]["role"] == "source" and text_value(entry["symbol"]))
        projection = directory / "files"
        require(stat.S_ISDIR(projection.lstat().st_mode))
        observed = set()
        # Inventory filenames only; do not follow links or inspect undeclared contents.
        pending = [projection]
        while pending:
            for candidate in pending.pop().iterdir():
                mode = candidate.lstat().st_mode
                if stat.S_ISDIR(mode):
                    pending.append(candidate)
                else:
                    require(stat.S_ISREG(mode))
                    observed.add(candidate.relative_to(projection).as_posix())
        require(observed == files.keys())
        candidates = {}
        for name, row in files.items():
            candidate = regular_file(projection, PurePosixPath(name))
            require(candidate.stat().st_size == row["bytes"])
            candidates[name] = candidate
        for name, candidate in candidates.items():
            content = candidate.read_bytes()
            require(len(content) == files[name]["bytes"])
            require(hashlib.sha256(content).hexdigest() == files[name]["sha256"])
        return dict(status="passed", scope="approved_code_projection_integrity",
                    receipt_sha256=approved_receipt_sha256, files=len(files),
                    measured=False, production_authorized=False)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise SourceContractError(REFUSAL["reason"]) from exc


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("source contract attempted network or execution")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)


def write_receipt(directory, doc):
    raw = json.dumps(doc, sort_keys=True).encode()
    (directory / "provenance.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def inert_projection(tmp_path):
    # No official symbols, evaluator functions, license or acquisition are invented.
    payloads = {"synthetic/parser.py": b"# SYNTHETIC inert parser-role bytes\n",
                "synthetic/checker.py": b"# SYNTHETIC inert checker-role bytes\n",
                "synthetic/dispatch.py": b"# SYNTHETIC inert dispatch-role bytes\n",
                "LICENSE": b"SYNTHETIC integrity test; not an upstream license\n"}
    files = []
    for name, content in payloads.items():
        candidate = tmp_path / "files" / name
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(content)
        files.append(dict(path=name, role="license" if name == "LICENSE" else "source",
                          bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                          archive_member="gorilla-1.3/" + name, requires=[]))
    doc = dict(schema="harnessgrad.bfcl-ast-source.v1", repository=REPOSITORY,
               repo_tag="v1.3", archive_sha256=ARCHIVE_SHA256, commit="1" * 40,
               review={key: "SYNTHETIC simulated approval; no upstream evidence" for key in
                       ("reviewed_by", "tag_commit_binding", "archive_member_binding",
                        "code_only_closure", "entrypoint_contract")},
               entrypoints={key: dict(path=f"synthetic/{key}.py", symbol="SYNTHETIC_UNVERIFIED")
                            for key in ("parser", "checker", "dispatch")}, files=files)
    return tmp_path, doc, write_receipt(tmp_path, doc)


def forbid_source_reads(monkeypatch):
    original = Path.read_bytes
    def guarded(path):
        assert path.name == "provenance.json", "refused metadata read projected source"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", guarded)


def test_missing_independent_pin_refuses_before_any_read(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("missing pin read bytes"))
    with pytest.raises(SourceContractError):
        check_source(tmp_path, None)


def test_absent_acquisition_is_a_refusal_not_a_skip(tmp_path):
    with pytest.raises(SourceContractError):
        check_source(tmp_path, "a" * 64)


def test_retained_candidate_is_blocked_even_with_its_integrity_digest(monkeypatch):
    directory = FIXTURE / "candidate-codex195"
    raw = (directory / "provenance.json").read_bytes()
    doc = json.loads(raw)
    assert doc["schema"] == "harnessgrad.bfcl-ast-source-candidate.v1"
    assert doc["status"] == "blocked"
    assert all(doc[key] is False for key in
               ("custodian_approved", "measured", "production_authorized"))
    assert all(doc["archive"][key] is False for key in
               ("downloaded", "digest_verified", "member_binding_verified"))
    assert all(doc["closure"][key] is False for key in
               ("checker_body_reviewed", "complete_in_repository_import_closure",
                "complete_official_metric_semantics_verified", "third_party_runtime_locked"))
    forbid_source_reads(monkeypatch)
    monkeypatch.setattr(Path, "iterdir", lambda *_: pytest.fail("candidate inventoried source"))
    # Computed only for this refusal regression; this is not an approval pin.
    with pytest.raises(SourceContractError):
        check_source(directory, hashlib.sha256(raw).hexdigest())


def test_contract_pin_matches_adapter_declarations_without_importing_or_loading_data():
    tree = ast.parse((ROOT / "data/datasets/bfcl_v3.py").read_text())
    declarations = {node.targets[0].id: ast.literal_eval(node.value)
                    for node in tree.body if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {"REPO_TAG", "TARBALL_SHA256"}}
    assert declarations == {"REPO_TAG": "v1.3", "TARBALL_SHA256": ARCHIVE_SHA256}


def test_inert_integrity_receipt_never_claims_semantics_or_authorization(inert_projection):
    directory, _, pin = inert_projection
    assert check_source(directory, pin) == dict(status="passed",
        scope="approved_code_projection_integrity", receipt_sha256=pin, files=4,
        measured=False, production_authorized=False)


def test_edited_receipt_cannot_supply_its_own_approval(inert_projection, monkeypatch):
    directory, doc, pin = inert_projection
    doc["review"]["reviewed_by"] = "changed simulated approval"
    write_receipt(directory, doc)
    forbid_source_reads(monkeypatch)
    with pytest.raises(SourceContractError):
        check_source(directory, pin)


@pytest.mark.parametrize("mutation", [
    "repository", "tag", "archive", "commit", "review", "unknown-field", "missing-license",
    "duplicate-path", "member", "dependency", "entrypoint", "payload", "traversal", "noncanonical",
    "backslash", "size", "bool-size", "oversize", "digest",
])
def test_unapproved_contract_shape_refuses_before_source_reads(inert_projection, monkeypatch, mutation):
    directory, original, _ = inert_projection
    doc = copy.deepcopy(original)
    row = doc["files"][0]
    if mutation == "repository": doc["repository"] = "unrelated"
    elif mutation == "tag": doc["repo_tag"] = "main"
    elif mutation == "archive": doc["archive_sha256"] = "0" * 64
    elif mutation == "commit": doc["commit"] = "0" * 40
    elif mutation == "review": doc["review"]["archive_member_binding"] = None
    elif mutation == "unknown-field": doc["production_authorized"] = True
    elif mutation == "missing-license": doc["files"].pop()
    elif mutation == "duplicate-path": doc["files"].append(copy.deepcopy(row))
    elif mutation == "member": row["archive_member"] = "another-release/" + row["path"]
    elif mutation == "dependency": row["requires"] = ["missing_helper.py"]
    elif mutation == "entrypoint": doc["entrypoints"]["parser"]["path"] = "LICENSE"
    elif mutation == "payload": row["path"] = "data/BFCL_v3_simple.json"
    elif mutation == "traversal": row["path"] = "../outside.py"
    elif mutation == "noncanonical": row["path"] = "synthetic//parser.py"
    elif mutation == "backslash": row["path"] = "synthetic\\parser.py"
    elif mutation == "size": row["bytes"] = 0
    elif mutation == "bool-size": row["bytes"] = True
    elif mutation == "oversize": row["bytes"] = MAX_SOURCE + 1
    elif mutation == "digest": row["sha256"] = None
    pin = write_receipt(directory, doc)
    forbid_source_reads(monkeypatch)
    with pytest.raises(SourceContractError):
        check_source(directory, pin)


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b'{"schema":NaN}', b'\xff'])
def test_invalid_json_is_refused(inert_projection, monkeypatch, raw):
    directory, _, _ = inert_projection
    (directory / "provenance.json").write_bytes(raw)
    forbid_source_reads(monkeypatch)
    with pytest.raises(SourceContractError):
        check_source(directory, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("failure", ["missing", "extra", "file-link", "directory-link", "size"])
def test_entire_inventory_preflight_precedes_source_reads(inert_projection, monkeypatch, failure):
    directory, _, pin = inert_projection
    target = directory / "files/synthetic/dispatch.py"
    if failure == "missing": target.unlink()
    elif failure == "extra": (directory / "files/unselected.json").write_bytes(b"unread synthetic bytes")
    elif failure == "file-link":
        target.unlink()
        target.symlink_to("checker.py")
    elif failure == "directory-link": (directory / "files/linked").symlink_to("synthetic", target_is_directory=True)
    elif failure == "size": target.write_bytes(b"changed size")
    forbid_source_reads(monkeypatch)
    with pytest.raises(SourceContractError):
        check_source(directory, pin)


@pytest.mark.parametrize("name", ["synthetic/checker.py", "LICENSE"])
def test_same_size_source_or_license_drift_is_refused(inert_projection, name):
    directory, _, pin = inert_projection
    target = directory / "files" / name
    content = target.read_bytes()
    target.write_bytes(b"!" + content[1:])
    with pytest.raises(SourceContractError):
        check_source(directory, pin)


@pytest.mark.parametrize("failure", ["link", "oversize"])
def test_receipt_preflight_refuses_before_reading_any_content(inert_projection, monkeypatch, failure):
    directory, _, pin = inert_projection
    receipt = directory / "provenance.json"
    if failure == "link":
        receipt.rename(directory / "original-receipt")
        receipt.symlink_to("original-receipt")
    else:
        receipt.write_bytes(b" " * (MAX_METADATA + 1))
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("invalid receipt read bytes"))
    with pytest.raises(SourceContractError):
        check_source(directory, pin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-sha256", help="independent custodian-approved exact receipt pin")
    args = parser.parse_args(argv)
    try:
        result = check_source(FIXTURE, args.receipt_sha256)
    except SourceContractError:
        print(json.dumps(REFUSAL, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def test_explicit_gate_has_fixed_refusal_and_nonzero_exit(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys.modules[__name__], "FIXTURE", tmp_path)
    assert main(["--receipt-sha256", "a" * 64]) == 2
    assert json.loads(capsys.readouterr().out) == REFUSAL


if __name__ == "__main__":
    raise SystemExit(main())
