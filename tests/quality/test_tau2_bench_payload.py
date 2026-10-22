"""The τ² v0.1.0 telecom payload: pin, split, and the public/private boundary.

Payload-level tests only — they read `manifest.json` and the case files, never import `tau2` (which
needs 3.13 and the isolated runtime) and never call a model. They skip on a machine where the
payload has not been prepared, the convention every other dataset test here follows.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_VAR = "HG_TAU2_DATA"
PAYLOAD = Path(__import__("os").environ.get(ROOT_ENV_VAR, "/mnt/20t/xubuqiang/datasets/tau2-bench"))

pytestmark = pytest.mark.skipif(
    not (PAYLOAD / "manifest.json").is_file(),
    reason=f"no prepared tau2 payload under {PAYLOAD}; run data/datasets/tau2_bench/prepare.py")


def manifest() -> dict:
    return json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))


def test_the_payload_is_the_pinned_release_and_the_telecom_domain():
    m = manifest()
    assert m["schema"] == "hg-tau2-payload.v1"
    assert m["revision"] == "v0.1.0"
    assert m["domain"] == "telecom"
    assert m["archive_sha256"] == (
        "ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377")


def test_the_adapter_pins_the_same_archive_as_the_payload():
    """The digest lives in two files; a pin that drifts between them is not a pin."""
    source = (ROOT / "data" / "datasets" / "tau2_bench.py").read_text(encoding="utf-8")
    assert manifest()["archive_sha256"] in source


def test_the_base_split_is_complete_and_every_task_is_study_material():
    """v0.1.0 has no official division, and the payload does not invent one."""
    m = manifest()
    tasks = m["tasks"]
    assert len(tasks) == 114
    assert m["counts"] == {"train": 114, "dev": 0, "eval": 0}
    assert {row["split"] for row in tasks} == {"train"}
    assert len({row["task_id"] for row in tasks}) == 114
    assert len({row["upstream_id"] for row in tasks}) == 114
    assert m["split_rule"]["kind"] == "none"


def test_every_case_carries_a_public_and_a_private_file_with_the_recorded_digest():
    m = manifest()
    for row in m["tasks"]:
        case = PAYLOAD / row["path"]
        public = (case / "public.json").read_bytes()
        private = (case / "private.json").read_bytes()
        assert hashlib.sha256(public).hexdigest() == row["public_sha256"], row["task_id"]
        assert hashlib.sha256(private).hexdigest() == row["private_sha256"], row["task_id"]
        assert json.loads(public)["task_id"] == row["task_id"]


def test_the_private_file_holds_the_scenario_and_the_public_file_does_not():
    """The boundary this payload exists for: the actor gets no user scenario and no criteria."""
    row = manifest()["tasks"][0]
    case = PAYLOAD / row["path"]
    public = json.loads((case / "public.json").read_text(encoding="utf-8"))
    private = json.loads((case / "private.json").read_text(encoding="utf-8"))
    assert "user_scenario" not in public and "evaluation_criteria" not in public
    assert private["user_scenario"] and private["evaluation_criteria"]
    for leaked in ("user_scenario", "evaluation_criteria", "ticket", "initial_state"):
        assert leaked not in public, leaked


def test_telecom_needs_no_judge_and_has_no_vacuous_task():
    """The two properties that made telecom the first domain, asserted rather than asserted-in-prose."""
    tasks = manifest()["tasks"]
    assert not any(row["needs_judge"] for row in tasks)
    assert not any(row["n_actions"] == 0 and row["n_env_assertions"] == 0
                   and row["n_communicate_info"] == 0 for row in tasks
                   if row["reward_basis"]), "a task with nothing actionable can be passed by doing nothing"


def test_the_bundle_carries_no_task_set_or_release_results():
    m = manifest()
    bundle = PAYLOAD / "bundle"
    names = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()}
    # `src/tau2/data_model/tasks.py` is the Task model and must be there; `src/.../create_tasks.py`
    # is the upstream generator. What must not be there is a *task set*: the JSON payloads under
    # `data/`, which carry the user scenarios and criteria.
    data_files = [n for n in names if n.startswith("data/")]
    assert not [n for n in data_files if Path(n).name.startswith("tasks")], \
        "a task set was copied into the bundle"
    assert not [n for n in names if "results" in Path(n).parts], "release results must not be in the bundle"
    assert m["bundle_files"] == len(names)
