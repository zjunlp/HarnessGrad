"""The pinned τ² v0.1.0 ALL scorer, run for real under the dataset's own Python 3.13 runtime.

The platform's test interpreter is 3.12, and v0.1.0 declares `requires-python = ">=3.13"`, so this
cannot import `tau2` in-process. It runs `tests/fixtures/tau2_scorer_probe.py` through
`<runtime>/bin/python` instead: the probe bootstraps the pinned bundle, replays the released gold
actions into the domain environment, and evaluates the result with `EvaluationType.ALL`.

Two arms of one task are the point. The gold arm proves the check is reachable (reward 1); the
empty arm proves it can fail (reward 0). A scorer that returns 1 for both is a scorer that cannot
distinguish an answer from nothing, and a suite that only runs the first arm cannot see that.

The judge channel is the release's own requirement, not a convenience: `EvaluationType.ALL` calls
`NLAssertionsEvaluator` for any task with `nl_assertions`, then multiplies the verdict out when the
basis is the default `[DB, COMMUNICATE]`. The probe answers it in-process with `{"results": []}`
("nothing to grade") and records that the reward basis is what excludes it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "fixtures" / "tau2_scorer_probe.py"
sys.path.insert(0, str(ROOT))

from data.datasets import tau2_bench as dataset  # noqa: E402


def _available() -> str | None:
    """`None` when the payload is here, else why it is not. Resolving the root may raise when
    storage is unconfigured at all, which is the same answer as "not provisioned"."""
    try:
        runtime, root = dataset.RUNTIME, dataset.DATASET_ROOT
        has_runtime = (runtime / "bin/python").is_file()
        has_bundle = (root / "bundle/manifest.json").is_file()
    except Exception:  # noqa: BLE001 - an unconfigured root is simply "no payload here"
        return "dataset storage is not configured on this machine"
    if not has_runtime:
        return f"no τ² runtime at {runtime}"
    if not has_bundle:
        return f"no prepared τ² bundle under {root}"
    return None


@pytest.fixture(scope="module")
def python() -> str:
    reason = _available()
    if reason:
        pytest.skip(reason)
    return str(dataset.RUNTIME / "bin/python")


def _probe(python: str, tid: str, arm: str) -> dict:
    row = dataset.roster()[tid]
    case = dataset.DATASET_ROOT / row["path"] / "private.json"
    proc = subprocess.run([python, "-I", str(PROBE), str(dataset.DATASET_ROOT / "bundle"),
                           str(dataset.ASSETS / "lib"), str(case), row["domain"], arm],
                          capture_output=True, text=True, errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("tid", ["tau2_bench/airline/8", "tau2_bench/retail/0"])
def test_the_released_gold_reaches_one_and_an_empty_episode_reaches_zero(python, tid):
    gold = _probe(python, tid, "gold")
    empty = _probe(python, tid, "empty")
    assert gold["task_id"] == tid and empty["task_id"] == tid
    assert gold["release"] == "v0.1.0"
    assert gold["reward"] == 1.0, gold
    assert empty["reward"] == 0.0, empty
    # The check that separates them is the database half, not the phrase half: both arms have no
    # `communicate_info`, so the two rewards differ only because the gold state does.
    assert gold["db_match"] is True and empty["db_match"] is False


def test_the_telecom_basis_is_the_environment_assertion_and_the_gold_action(python):
    """The one domain whose reward basis is not the default, so a product over one part would
    look correct on the other two tasks and be wrong here."""
    tid = "tau2_bench/telecom/[service_issue]break_apn_settings|lock_sim_card_pin[PERSONA:None]"
    gold = _probe(python, tid, "gold")
    empty = _probe(python, tid, "empty")
    assert gold["basis"] == ["ENV_ASSERTION", "ACTION"]
    assert gold["reward"] == 1.0 and gold["env_assertions"] == [True]
    assert gold["actions"] == [True]
    assert empty["reward"] == 0.0, empty
    # The environment assertion alone is satisfied by doing nothing -- the gold state *is* the
    # initial state here -- so the action half is what makes the empty arm fail. That is why the
    # two halves are reported separately instead of as one number.
    assert empty["env_assertions"] == [True] and empty["actions"] == [False]


def test_the_probe_never_counts_a_fabricated_judge_verdict(python):
    """The probe's judge answer is `{"results": []}`; if a selected task's basis ever includes
    `NL_ASSERTION`, that answer would become part of the reward and the control would be lying."""
    for tid in ("tau2_bench/airline/8", "tau2_bench/retail/0",
                "tau2_bench/telecom/[service_issue]break_apn_settings|lock_sim_card_pin[PERSONA:None]"):
        basis = _probe(python, tid, "empty")["basis"]
        assert "NL_ASSERTION" not in basis, (tid, basis)
