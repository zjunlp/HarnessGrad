#!/usr/bin/env python3
"""Offline integrity of a historical loader candidate; never certify the Hub export.

Only retained code, license and Git/config metadata are read. The loader is not
imported, and no scorer, dataset, network, model or endpoint is invoked.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/triviaqa_hub_codex225"
COMMIT = "413205c1a7247a2ff81cf709117ce93624fa6fd3"
RECEIPT_SHA256 = "36996acbab13dca74686c9ffc152df4dd0f418c875b3dfaabeeb29493dc2aef3"
PINS = {
    "trivia_qa.py": ("653d4b0179d708c23d20aebf40253bc64c2b30d70627247d2a4e6d63c63213d2",
                     "5e1c9aab6b9e71f7d1251089ae34590794c7ae58"),
    "LICENSE": ("cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
                "d645695673349e3947e8e5ae42332d0ac3164cd7"),
    "dataset_infos.json": ("220e5edddaa870a2840d363bd0d69a08997a4c6e107795ac727e5fa935533194",
                           "02fa50221855cf0619e5f6a6dfcdcf977f484be0"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git_hash(kind, content):
    return hashlib.sha1(kind.encode() + b" " + str(len(content)).encode()
                        + b"\0" + content).hexdigest()


def read_json(relative):
    return json.loads((FIXTURE / relative).read_text())


def validate():
    receipt_bytes = (FIXTURE / "acquisition.json").read_bytes()
    require(hashlib.sha256(receipt_bytes).hexdigest() == RECEIPT_SHA256,
            "acquisition receipt changed")
    receipt = json.loads(receipt_bytes)
    files = set()
    failures = []
    for request in receipt["requests"]:
        if "path" not in request:
            failures.append(request)
            continue
        path = FIXTURE / request["path"]
        require(path.resolve().is_relative_to(FIXTURE.resolve()), "path escapes fixture")
        content = path.read_bytes()
        require(len(content) == request["size_bytes"], f"size: {path.name}")
        require(hashlib.sha256(content).hexdigest() == request["sha256"],
                f"hash: {path.name}")
        if "decoded_file" in request:
            decoded = request["decoded_file"]
            filename = Path(decoded["path"]).name
            require(filename in PINS, "unexpected acquired file")
            local = (FIXTURE / "historical_loader" / filename).read_bytes()
            api = json.loads(content)
            sha256, blob = PINS[filename]
            require(base64.b64decode(api["content"]) == local, "API decoding mismatch")
            require(hashlib.sha256(local).hexdigest() == sha256 == decoded["sha256"],
                    f"source hash: {filename}")
            require(git_hash("blob", local) == blob == api["sha"]
                    == decoded["git_blob_sha1"], f"blob identity: {filename}")
            files.add(filename)
    require(files == set(PINS), "missing acquired file")
    require(len(failures) == 4 and all(
        request["url"].startswith("https://huggingface.co/")
        and request["error_type"] == "URLError"
        and "Network is unreachable" in request["error"]
        and request["response_retained"] is False for request in failures),
        "failed exact-revision Hub requests must stay failures")

    commit = read_json("evidence/git_commit_response.json")
    verification = commit["verification"]
    header, message = verification["payload"].split("\n\n", 1)
    signed = (header + "\ngpgsig " + verification["signature"].replace("\n", "\n ")
              + "\n\n" + message).encode()
    require(git_hash("commit", signed) == COMMIT == commit["sha"], "commit identity")
    trees = {}
    for name in ("root", "datasets", "trivia_qa"):
        tree = read_json(f"evidence/{name}_tree_response.json")
        require(tree["truncated"] is False, "truncated Git tree")
        content = b"".join(entry["mode"].lstrip("0").encode() + b" "
                           + entry["path"].encode() + b"\0" + bytes.fromhex(entry["sha"])
                           for entry in tree["tree"])
        require(git_hash("tree", content) == tree["sha"], f"tree identity: {name}")
        trees[name] = (tree["sha"], {e["path"]: e for e in tree["tree"]})
    require(commit["tree"]["sha"] == trees["root"][0], "root tree linkage")
    require(trees["root"][1]["datasets"]["sha"] == trees["datasets"][0],
            "datasets tree linkage")
    require(trees["datasets"][1]["trivia_qa"]["sha"] == trees["trivia_qa"][0],
            "loader tree linkage")
    for filename, (_, blob) in PINS.items():
        tree = trees["root" if filename == "LICENSE" else "trivia_qa"][1]
        require(tree[filename]["sha"] == blob, f"commit file membership: {filename}")
    require("NOTICE" not in trees["root"][1], "review additional notice")
    source = (FIXTURE / "historical_loader/trivia_qa.py").read_text()
    require("Licensed under the Apache License, Version 2.0" in source, "source license")
    require("Version 2.0, January 2004" in
            (FIXTURE / "historical_loader/LICENSE").read_text(), "original license")
    manifest = read_json("historical_loader/dataset_infos.json")["rc.nocontext"]
    require("human_answers" not in manifest["features"]["answer"], "schema changed")
    history = read_json("evidence/history.json")
    require([entry["sha"] for entry in history[:2]] == [
        "8803c182e6d03e3cb43f62da2572548f55e5b14d", COMMIT], "path history changed")
    return {
        "historical_loader_commit": COMMIT,
        "historical_files_verified": sorted(files),
        "license": "Apache-2.0 (historical loader)",
        "hub_revision": "0f7faf33a3908546c6fd5b73a660e0f8ff173c2f",
        "hub_export_linkage": "unverified; four retained network failures",
        "scorer_compatibility": "not certified",
        "production_authorized": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
