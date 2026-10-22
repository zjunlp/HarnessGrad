"""NESTFUL v2: measured refusal of the official execution Win Rate.

Canonical IBM repository includes 1861 gold sequences and 4349 Python function files.
Official scorer selects basic_functions.py before func_file_map, resolves references,
executes positional arguments, and compares against JSON-decoded gold_answer. Our first
complete official sweep returned 1857 successes/4 failures; a repeat returned 1854/7.
These are observations on this host, not portable constants.

Concrete causes: sqrt in basic_functions yields an integer rather than the recorded 3.87;
max_number in basic_functions takes one argument but a gold sequence passes two;
object_id returns a live process address whereas gold records a different address;
gid2name reads the host Unix group database. Additional functions consume RNG/global state.
The audit runs unchanged upstream function bodies in an isolated process and records ids.

This is not a claim that all NESTFUL metrics are unavailable: full sequence match and
function/parameter metrics are distinct official metrics. Under this goal, silently dropping
Win Rate is not permitted. A separately named, explicitly audited deterministic subset or
upstream repair could be considered later; neither is published as full NESTFUL here.
"""
from __future__ import annotations

import json
from pathlib import Path

KIND = "dataset"
REVISION = "fc2c4123e73500a56185a5fb354f05d1c8b4890c"
DATASET_ROOT = Path("/mnt/20t/xubuqiang/datasets/nestful")
ROOT = DATASET_ROOT / "_src" / f"NESTFUL-{REVISION}"
DATA = ROOT / "data_v2" / "nestful_data.jsonl"
FUNCS = ROOT / "data_v2" / "executable_functions"
DATA_SHA256 = "e7c9999e2eaaa2bea87776bc55e2b0913a0cf089f4b6817c7644554547df22c1"
SOURCE_SHA256 = "ea8ad67cbd3680b4b85e3b60d219898fda9643240636ef8ac3b020c46fb95d1b"
REFUSED = (
    "nestful: official execution Win Rate is unreproducible as released. The pinned official "
    "calculate_win_score applied to all 1861 gold sequences returned 1857 true/4 false on "
    "this host; a repeat returned 1854 true/7 false. Gold execution includes recorded object "
    "addresses, host group lookups, random/stateful functions and basic_functions overrides "
    "with incompatible signatures/results. Gold therefore cannot reliably reproduce the "
    "recorded result. Function/slot F1 or full sequence match are distinct metrics and will "
    "not silently replace Win Rate. A separately named measured subset or repaired upstream "
    "release is a possible future path, not an onboarded full benchmark."
)
REFUSAL = REFUSED


def _raw():
    with DATA.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load():
    raise ValueError(REFUSED)


def downloads():
    return [{
        "kind": "http", "name": "NESTFUL v2 official data, functions and scorer",
        "version": REVISION,
        "url": f"https://codeload.github.com/IBM/NESTFUL/tar.gz/{REVISION}",
        "size": "~1.3 MB compressed; ~18 MB extracted executable function tree",
        "sha256": SOURCE_SHA256, "dst": "_src/nestful.tar.gz", "extract_to": "_src",
        "note": "Canonical commit verified via GitHub API and codeload. HF rows alone omit "
                "the 4349 Python tool files needed for Win Rate. Gold execution itself "
                "fails due to host/process-dependent state and basic_functions overrides; "
                "see data/datasets/nestful/audit.py. No gold/function tree is offered as agent SETUP.",
    }]


def provision_status():
    return {"root": str(DATASET_ROOT), "present": DATA.is_file() and FUNCS.is_dir(),
            "split_dir": str(ROOT), "tasks": 1861}
