"""The τ² v0.1.0 case manifest is pinned, counted, and says which tasks need a second party.

What can be checked without importing `tau2` (which needs Python 3.13 and the isolated runtime) is
the data layer: every task has a public half and a private half with digests, the counts match the
pinned release, the tasks whose gold actions the *user* performs are marked, and the tasks whose
scoring makes a judge call are marked. All three flags are measured from the release's own task
rows rather than remembered -- the adapter carried a remembered number for two of them and both
were wrong.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.datasets import tau2_bench as dataset  # noqa: E402


def test_the_manifest_matches_the_pin():
    manifest = dataset.inventory()
    assert manifest["release"] == dataset.REPO_TAG == "v0.1.0"
    assert manifest["archive_sha256"] == dataset.TARBALL_SHA256
    assert manifest["counts"] == dataset.N_TASKS


def test_the_checksummed_manifest_is_the_one_in_the_tree():
    """A digest that matches the file it is checked against proves nothing on its own."""
    assert hashlib.sha256(dataset.MANIFEST.read_bytes()).hexdigest() == dataset.MANIFEST_SHA256


def test_every_task_has_two_halves_and_a_digest():
    rows = dataset.inventory()["tasks"]
    assert len(rows) == sum(dataset.N_TASKS.values()) == 278
    for row in rows:
        assert row["public_sha256"] and row["private_sha256"], row
        assert row["path"].startswith(f"cases/{row['domain']}/"), row
        assert row["task_id"].startswith("tau2_bench/"), row
    for domain, count in dataset.N_TASKS.items():
        assert len([r for r in rows if r["domain"] == domain]) == count


def test_the_halves_on_disk_match_the_manifest():
    """The digests are the only reason a prepared asset can be trusted."""
    # `DATASET_ROOT` is lazy: the configuration error is raised by the first real filesystem
    # access, not by the attribute, so both are inside the guard.
    try:
        root = dataset.DATASET_ROOT
        provisioned = (root / "manifest.json").is_file()
    except Exception:  # noqa: BLE001 - an unconfigured root is simply "not provisioned"
        pytest.skip("dataset storage is not configured on this machine")
    if not provisioned:
        pytest.skip(f"τ² assets are not provisioned at {root}")
    for row in dataset.roster().values():
        for kind, key in (("public", "public_sha256"), ("private", "private_sha256")):
            path = root / row["path"] / f"{kind}.json"
            assert path.is_file(), path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row[key], path


def test_user_performed_gold_actions_are_marked_per_task():
    manifest = dataset.inventory()
    measured = {domain: 0 for domain in dataset.DOMAINS}
    for task in manifest["tasks"]:
        if task["user_actions"]:
            measured[task["domain"]] += 1
    assert measured == manifest["user_action_tasks"]
    # Measured: only telecom carries user-performed gold actions in this release.
    assert measured["airline"] == 0 and measured["retail"] == 0
    assert measured["telecom"] > 0


def test_a_judge_is_declared_where_the_all_evaluator_calls_one():
    """`EvaluationType.ALL` runs `NLAssertionsEvaluator` for every task with `nl_assertions`,
    then multiplies its verdict out when the basis is the default `[DB, COMMUNICATE]`. So the
    flag is "does the scorer make a model call", not "does the reward use the answer" -- and
    measured, it is all 50 airline tasks plus 8 retail ones.
    """
    manifest = dataset.inventory()
    judged = [task["domain"] for task in manifest["tasks"] if task["needs_judge"]]
    assert len(judged) == 58
    assert {domain: judged.count(domain) for domain in dataset.DOMAINS} == {
        "airline": 50, "retail": 8, "telecom": 0}


def test_a_task_with_nl_assertions_but_the_default_basis_is_still_flagged():
    """The defect this replaced: the flag used to ask whether `NL_ASSERTION` was *in the basis*,
    which answered 0. One task with the default basis and a non-empty `nl_assertions` list is
    enough to keep that reading out.
    """
    rows = {row["task_id"]: row for row in dataset.inventory()["tasks"]}
    flagged = [tid for tid, row in rows.items() if row["needs_judge"]]
    assert "tau2_bench/airline/8" in flagged
    assert "tau2_bench/retail/0" not in flagged


def test_a_tampered_manifest_is_refused(tmp_path, monkeypatch):
    manifest = json.loads(dataset.MANIFEST.read_text(encoding="utf-8"))
    manifest["counts"] = {"airline": 1, "retail": 1, "telecom": 1}
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    dataset.inventory.cache_clear()
    monkeypatch.setattr(dataset, "MANIFEST", broken)
    with pytest.raises(ValueError):
        dataset.inventory()
    dataset.inventory.cache_clear()
