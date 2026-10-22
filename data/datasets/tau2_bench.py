r"""tau2-bench -- **refused, conditionally**: a two-party benchmark with no simulator for *it* yet.

    python3 tools/datasets.py --fetch --dataset tau2_bench
    python3 data/scaffolds/qa/control.py --dataset tau2_bench          # says why there is no control
    python3 tools/dataset_lint.py --dataset tau2_bench       # REFUSED, not FAIL

Not a permanent exclusion any more: the operator lifted that on 2026-10-22 and wrote the condition
down (`docs/refusal-lift-decision.md`). What `REFUSED` still reports is a code fact -- `load()`
cannot assemble these tasks without a second party -- not a policy about the benchmark.

The pin is the point
--------------------
`sierra-research/tau2-bench`, **tag `v0.1.0`** -- not `main`. Measured, and it is the kind of
fact a directory name hides: the repository's HEAD is now **τ³-bench v1.0.1**, and its own README
says results below 1.0.1 are not comparable with results at or above it. Only v0.1.0 also has the
paper's split in the obvious place (`tasks.json` **is** the base split: airline 50 + retail 114 +
telecom 114 = **278**, the paper's number).

Why this dataset cannot run here
--------------------------------
τ²-bench is a **dual-control** benchmark: an LLM **user simulator** plays the customer. The
platform's contract is one goal and one artifact from one harness, so there is no second party.
Three measured consequences, in order of how hard they are to work around:

1. **Some gold actions are the *user's*, not the agent's.** Measured on the telecom tasks, whose
   `reward_basis` is `["ENV_ASSERTION"]` and whose assertions are calls into a running
   environment:

       {"action_id": "toggle_data_0", "requestor": "user", "name": "toggle_data", ...}

   `calculate_reward` replays **both** parties' actions to build the gold state
   (`gold_environment`), and 55 of the 278 tasks carry `requestor: "user"` actions. An agent
   cannot perform them and there is nobody else in the episode, so the gold state is
   unreachable by construction -- not merely unreached.
2. **The user's side is not in the goal.** The agent's prompt is the domain **policy**; each
   task's `user_scenario` (persona plus `task_instructions`) belongs to the simulator. This
   repository's own τ-bench adapter (skill §11: `data/datasets/tau_bench.py`) got around the absence of a
   simulator by **giving the user id in the goal**, because τ-bench's reward compares database
   states and the read-only lookups never move the hash. That trick does not transfer: τ²-bench's
   `user_scenario` says what the customer reveals *when*, so putting it in the goal hands over
   the discovery the dialogue is supposed to create -- and the number would go **up**.
3. **The metric is a product of three things, one of which needs an LLM judge.**
   `calculate_reward` multiplies the database-hash match by the `env_assertion` result:
   `reward = db_reward * env_assertion_reward`, each term gated on
   `evaluation_criteria.reward_basis`. Measured per domain, `nl_assertions` appear in **all 50
   airline tasks** and 8 retail tasks, and those are judged by a model.

So the honest reading is the same as §15.9's: the official number is not reachable from this
contract, and reporting a nearby one -- the database half alone, or a `communicate_info`
substring check -- would be a different benchmark under the same name.

What the data is, measured (so a future attempt starts here)
------------------------------------------------------------
* **278 tasks**: airline 50, retail 114, telecom 114 -- the paper's count.
* `initial_state` is present for **all 114 telecom** tasks and **0** airline/retail ones (those
  start from the domain `db.json`).
* Gold `actions` in 43 / 112 / 114 tasks; `communicate_info` in 6 / 38 / 0; `nl_assertions` in
  50 / 8 / 0; `env_assertions` in 0 / 0 / 114 (each with a non-empty `reward_basis`).
* `telecom` ships `db.toml`, not `db.json`, and its policy is a directory of markdown files --
  the two artifacts a future attempt would have to learn to read.

The two constructive shapes, neither taken yet
----------------------------------------------
Both are decisions about *what is being benchmarked*, which is why they are written here and not
taken silently. The exclusion is lifted as of 2026-10-22, so either may now be proposed.

1. **A staged second party.** Declare the user's side as **data the platform re-materializes** (a
   scripted action list, exactly as ALFWorld's walkthrough and Jericho's gold commands are staged)
   rather than an LLM simulator, and restrict the task set to the **223 tasks whose gold actions are
   all the agent's**. A real, measurable subset with a reproducible metric.
2. **A real simulator.** All 278 tasks through a platform-owned user simulator plus the judge channel.
   Closer to the published benchmark, and the number then carries simulator identity.

The facility for shape 2 is no longer hypothetical: `data/datasets/tau3_bench/lib/runtime.py` runs
the official `UserSimulator` in a private trusted process and already constructs **these** domain
environments (`tau2.domains.airline.environment.get_environment`, retail, telecom), and
`eval/service/judge.py` supplies the judge. What is missing is this benchmark's own pin and split:
tau3 reads `split_tasks.json` at `fc0055dc...`, while the tasks here are v0.1.0's `tasks.json`, and
upstream states that results below 1.0.1 are not comparable with results at or above it -- so mixing
the two revisions would produce a number under this name that neither paper supports.
"""
from __future__ import annotations

import os
from pathlib import Path

KIND = "dataset"

DATASET_ROOT = Path(os.environ.get("HG_TAU2_DATA",
                                   "/mnt/20t/xubuqiang/datasets/tau2-bench"))

#: The tag, and the archive digest that pins the bytes.
REPO_TAG = "v0.1.0"
TARBALL_URL = ("https://codeload.github.com/sierra-research/tau2-bench/tar.gz/"
               "refs/tags/v0.1.0")
TARBALL_NAME = "tau2-bench-v0.1.0.tar.gz"
TARBALL_SHA256 = "ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377"

#: Inside the tarball.
PREFIX = "tau2-bench-0.1.0/data/tau2/domains"
DOMAINS = ("airline", "retail", "telecom")
N_TASKS = {"airline": 50, "retail": 114, "telecom": 114}

REFUSED = (
    "tau2_bench: it is a dual-control benchmark whose second party -- an LLM **user simulator** "
    "-- does not exist on this platform, and the official metric is unreachable without it. "
    "Measured: `calculate_reward` replays both parties' actions to build the gold state, and 55 "
    "of the 278 tasks carry gold actions with `requestor: \"user\"` (telecom's `toggle_data`, "
    "`refuel_data`), which no agent can perform, so the gold database state is unreachable by "
    "construction. Each task's `user_scenario` belongs to the simulator and states what the "
    "customer reveals *when*; putting it in the agent's goal would hand over the discovery the "
    "dialogue is supposed to create and raise the score. And the metric is a product of the "
    "database-hash match with `env_assertion` results gated on `reward_basis`, while "
    "`nl_assertions` (all 50 airline tasks) need a model to judge. This repository's own "
    "tau-bench adapter avoided the simulator by giving the user id in the goal, because that "
    "benchmark's reward compares database states only -- that does not transfer here. The refusal "
    "is conditional, not permanent: it lifts when this platform supplies an independent user "
    "simulator and the judge the metric names, both recorded in the receipt's identity, and the "
    "module docstring records the two shapes it could be measured in -- staged user actions over a "
    "subset, or a real simulator. Until then no nearby number "
    "-- the database half alone, a communicate_info substring check -- may be reported under this "
    "name, because each of those is a different benchmark."
)


def _domains_present() -> dict:
    root = DATASET_ROOT / PREFIX
    return {d: (root / d / "tasks.json").is_file() for d in DOMAINS}


def provision_status() -> dict:
    """What is present locally. A pure filesystem answer."""
    tarball = DATASET_ROOT / "_src" / TARBALL_NAME
    present = _domains_present()
    return {
        "root": str(DATASET_ROOT),
        "present": tarball.is_file() and all(present.values()),
        "split_dir": str(DATASET_ROOT / PREFIX),
        "tarball": str(tarball) if tarball.is_file() else None,
        "domains": present,
        "tasks": 0,
        "refused": True,
    }


def downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "tau2-bench v0.1.0",
            "version": REPO_TAG,
            "url": TARBALL_URL,
            "size": "~56 MB tarball; `data/tau2/domains/` is what matters",
            "sha256": TARBALL_SHA256,
            "dst": "_src/" + TARBALL_NAME,
            "extract_to": ".",
            "note": "Trap 1 (**the whole reason for the pin**): the repository's HEAD is now "
                    "**τ³-bench v1.0.1**, whose README says results below 1.0.1 are not "
                    "comparable with results at or above it -- so a loader pinned to `main` "
                    "would produce numbers that cannot be compared with the paper's, and the "
                    "directory name would not say so. Trap 2: only at v0.1.0 is `tasks.json` "
                    "**the base split** (airline 50 + retail 114 + telecom 114 = 278, the "
                    "paper's number); later trees add `split_tasks.json` and the base has to be "
                    "selected out of it, which changes *which* 278 tasks those are. Trap 3: "
                    "telecom ships `db.toml` and a directory of policy markdown, not `db.json` "
                    "and `policy.md`.",
        },
    ]


def load() -> tuple[list[dict], dict[str, str]]:
    raise ValueError(REFUSED)


__all__ = ["KIND", "DATASET_ROOT", "REPO_TAG", "REFUSED", "load", "downloads",
           "provision_status", "DOMAINS", "N_TASKS"]
