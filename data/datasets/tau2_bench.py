r"""tau2-bench v0.1.0 -- dual control, measured with the release's own simulator and scorer.

    python3 tools/datasets.py --fetch --dataset tau2_bench
    python3 data/datasets/tau2_bench/control.py --oracle     # 3 tasks, expect 1 (add --platform for the sandboxed path)
    python3 data/datasets/tau2_bench/control.py --noop       # 3 tasks, expect 0
    python3 tools/dataset_lint.py --dataset tau2_bench

Where the second party lives
----------------------------
τ²-bench is dual control: an LLM **user simulator** plays the customer, and some gold actions are the
*user's*, not the agent's. Measured on the prepared inventory: 94 of telecom's 114 tasks carry
`requestor: "user"` actions; airline and retail carry none. An agent alone cannot reach those gold
states, and each task's `user_scenario` -- which states what the customer reveals *when* -- must
never be in the agent's goal, because putting it there hands over the discovery the dialogue exists
to create.

So the simulator does not live in the agent's workspace. `relay.py` runs in a private trusted
process outside the sandbox, drives the release's own `UserSimulator` and the domain's tools, and
exposes a half-duplex socket (`start`, `say`, `call`, `calls`, `status`). The agent's
workspace holds the domain policy and the business-tool schemas (`cases/<domain>/<id>/public.json`)
plus a CLI that speaks to that socket. The verifier replays the sealed conversation through
`evaluate_simulation(..., EvaluationType.ALL)`. The prepared case row keeps the user's scenario and
every evaluation criterion in `private.json`, which is never copied into the workspace.

The pin is the point
--------------------
`sierra-research/tau2-bench`, **tag `v0.1.0`** -- not `main`. Measured, and it is the kind of fact a
directory name hides: the repository's HEAD is now **τ³-bench v1.0.1**, and its own README says
results below 1.0.1 are not comparable with results at or above it. Only v0.1.0 also has the paper's
pool in the obvious place (`tasks.json` **is** the base split: airline 50 + retail 114 + telecom 114
= **278**, the paper's number). Later trees add `split_tasks.json` and move the base out of it,
which changes *which* 278 tasks those are. τ³ is a **separate** adapter on a separate revision;
mixing the two would produce a number neither paper supports. `METRIC` therefore carries the
release: `tau2.v0.1.0.ALL.binary`, incomparable with any `tau3.*` number by construction.

No division, so no exam side
----------------------------
Every τ² tag publishes **no** train/test division: `tasks.json` is the whole 278-task pool. A hash
cut of that pool is not the benchmark's held-out exam, so this adapter declares none
(`SPLIT_SOURCE = "none"`, `SPLIT = None`): a run covers all 278 tasks, and `OFFICIAL_EVAL = None`
says the pool *is* the scored set. Mixing the two revisions would produce a number under this name
that neither paper supports.

What the metric needs, as fields rather than prose
--------------------------------------------------
* **58 of 278 tasks make a judge call** -- all 50 airline and 8 retail. `EvaluationType.ALL` runs
  *every* evaluator, `NLAssertionsEvaluator` included, and only then multiplies the parts by the
  reward basis, so a task with `nl_assertions` calls a model even when its basis is the default
  `[DB, COMMUNICATE]` and the verdict is multiplied out. Measured, not inferred: scoring an empty
  `airline/8` episode raises from `evaluator_nl_assertions` on an unconfigured endpoint. The flag is
  `needs_judge` in `manifest.json`, and those rows declare `VERIFY[...]["judge"]`. The other 220
  tasks -- every telecom task -- need no judge, because `nl_assertions` is empty there.
* **The user simulator is the other model call**, and it is the actor's second party. Its identity
  is `model_spec("environment", ...)`, recorded per run, with `temperature: 0`.
* Each evaluator returns **1.0 when its own criterion list is empty** (upstream's `all([])`), and the
  basis then multiplies that in. For airline/retail tasks without `communicate_info`, the
  COMMUNICATE half contributes 1.0 and the database half decides; telecom tasks with
  user-performed gold actions need the simulator to reach theirs. That is the release's rule, and
  `lib/scoring.py` records it rather than patching it. What it means here: a task whose every named
  half is empty would be a number that cannot fail, which is why the control selects tasks with a
  state-changing gold action and the official-scorer test measures an empty episode at 0, not 1.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import random
import subprocess

from harnessgrad.paths import configured_path, dataset_root

KIND = "dataset"
ORACLE_SOURCE = "released"
#: No held-out division exists in this release; the pool is the scored set.
OFFICIAL_EVAL = None
SPLIT_SOURCE = "none"
SPLIT = None
ASSETS = Path(__file__).resolve().parent / "tau2_bench"

DATASET_ROOT = dataset_root("tau2-bench")
RUNTIME = configured_path("HG_DATA_TAU2_BENCH_RUNTIME", DATASET_ROOT / "runtime")

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

METRIC = "tau2.v0.1.0.ALL.binary"
_rng = random.Random(200)
TRIAL_PROTOCOL = {"version": 1, "metric": "pass^k", "trials": 3, "ks": [1, 2, 3],
                  "seeds": [_rng.randint(0, 1000000) for _ in range(3)]}
DEFAULT_ENV = {"kind": "files", "network": "none"}
TRANSPORT = Path(__file__).resolve().parents[2] / "eval/service/tool_transport.py"
GOAL = """Help the customer complete their service request while following the domain policy.
Read `python3 tau2.py show` for the policy and business-tool schemas. Start with
`python3 tau2.py start`. Converse using `python3 tau2.py say 'MESSAGE'`; call a
business tool using `python3 tau2.py call TOOL_NAME '{"argument":"value"}'`.
For an official multi-tool assistant turn use
`python3 tau2.py calls '[{"id":"a","name":"TOOL_NAME","arguments":{}}]'`.
The simulated user has independent tools; their private scenario is not available.
Use `python3 tau2.py status` to inspect the conversation's termination status.
Finish by sending the user your final response and `###STOP###`, unless the user
has already stopped. The service fixes 200 orchestrator steps per episode. Tool-call
emission and environment execution consume separate steps. The release declares a
10-error threshold but never increments its official error counter; observed tool errors
are reported separately and do not silently change the pinned release's termination rule.
Scoring replays the trusted conversation and applies the release's own reward basis.
answer.txt is optional bookkeeping; it cannot replace conversation/tool actions.
"""


#: The prepared inventory, written by `prepare.py` from the pinned tarball. It records which
#: tasks carry gold actions a *user* performs -- 94 of telecom's 114, none in airline or retail
#: -- so "does this task need the simulator" is a lookup rather than a re-derivation. Its
#: `needs_judge` flag (58 tasks: 50 airline + 8 retail) records what the release's ALL evaluator
#: asks for, which is not the same question as what the reward basis uses.
MANIFEST = Path(__file__).resolve().parent / "tau2_bench" / "manifest.json"
#: The digest of the prepared manifest itself, so a hand-edited inventory is caught
#: rather than believed.
MANIFEST_SHA256 = "887dae934c5e376daecde58169a1c13f550282dc85f1e7d02a5f5813fd5204bd"


@lru_cache(maxsize=1)
def inventory() -> dict:
    """The prepared case manifest: one row per task, with the digests of both halves."""
    raw = MANIFEST.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    if digest != MANIFEST_SHA256:
        raise ValueError("tau2 manifest digest mismatch")
    if manifest.get("release") != REPO_TAG or manifest.get("archive_sha256") != TARBALL_SHA256:
        raise ValueError("tau2 manifest identity does not match the pin")
    if manifest.get("counts") != N_TASKS or len(manifest.get("tasks") or []) != sum(N_TASKS.values()):
        raise ValueError("tau2 manifest task count does not match the domains")
    manifest["digest"] = digest
    return manifest


def roster() -> dict:
    """`task_id -> manifest row`, the shape the rest of the adapter uses."""
    return {row["task_id"]: row for row in inventory()["tasks"]}


def control_task(tid: str) -> dict:
    if tid not in roster():
        raise KeyError(tid)
    return {"task_id": tid, "goal": GOAL, "inputs": []}


def load() -> tuple[list[dict], dict[str, str]]:
    return [control_task(tid) for tid in roster()], {tid: METRIC for tid in roster()}


def model_spec(role: str, tools: list | None = None) -> dict:
    """Two roles: the user simulator the actor talks to, and the NL-assertion judge."""
    from harnessgrad.config import current
    if role not in ("environment", "judge"):
        raise ValueError("unsupported model role")
    cfg = current()
    if role == "environment":
        model = cfg.environment.model
        if not model:
            raise ValueError("configure an explicit HG_ENV_MODEL for the tau2 user simulator")
    else:
        model = cfg.judge.resolve_model(override=cfg.judge.tau2_model)
    # `evaluator_nl_assertions.evaluate_nl_assertions` generates once for the whole assertion
    # list, and v0.1.0 binds its model to a module constant rather than passing it in.
    spec = {"model": model, "max_calls": 1 if role == "judge" else None,
            "max_output_tokens": None, "timeout_s": None, "max_retries": 0,
            "max_attempts": None, "response_max_bytes": None, "max_line_bytes": None,
            "parameters": {"temperature": 0}}
    if role == "environment":
        spec["seed_source"] = "trial"
    if tools:
        spec.update(tools=tools, tool_choice="auto")
    return spec


def _case(tid: str, row: dict) -> Path:
    return DATASET_ROOT / row["path"]


def _setup(tid: str, row: dict) -> dict:
    public = _case(tid, row).joinpath("public.json").read_bytes()
    if hashlib.sha256(public).hexdigest() != row["public_sha256"]:
        raise ValueError("public task digest mismatch")
    prepared_public = json.loads(public)
    model = model_spec("environment", prepared_public.get("user_tools"))
    # The pinned preparation record also carries the simulator's tool schemas for the
    # host model broker. They are not actor capabilities: project only the policy and
    # assistant toolkit into the workspace, without changing the prepared bytes/digest.
    actor_public = {key: value for key, value in prepared_public.items() if key != "user_tools"}
    return {"files": [{"path": "tau2.py", "from": str(ASSETS / "cli/tau2.py")},
                      {"path": "tau2_transport.py", "from": str(TRANSPORT.resolve())},
                      {"path": "tau2_public.json", "content": json.dumps(actor_public)}],
            "tool_relay": {"program": str(ASSETS / "relay.py"), "python": str(RUNTIME / "bin/python"),
                           "content_flag": "--case-spec", "filename": "case-spec.json",
                           "content": json.dumps({"case": str(_case(tid, row) / "private.json"),
                                                 "sha256": row["private_sha256"],
                                                 "bundle": str(DATASET_ROOT / "bundle"),
                                                 "user_model": model["model"]}),
                           "record": {"path": "trusted_episode", "max_bytes": None},
                           "model": model, "max_calls": None, "deadline_s": None,
                           "max_line_bytes": None}}


@lru_cache(maxsize=1)
def _python_runtime(runtime: str) -> tuple[str, tuple[str, ...]]:
    """Inspect the selected scorer Python once; the driver may use another runtime."""
    runtime = Path(runtime)
    if not runtime.is_absolute():
        raise ValueError("tau2 runtime must be an absolute directory")
    python = runtime / "bin/python"
    probe = ('import json, sys; print(json.dumps({"version": list(sys.version_info[:2]), '
             '"prefix": sys.prefix, "base_prefix": sys.base_prefix, '
             '"base_exec_prefix": sys.base_exec_prefix}))')
    try:
        # Startup inspection only, not an episode/model/verification budget.
        proc = subprocess.run([str(python), "-I", "-B", "-c", probe], capture_output=True,
                              text=True, errors="replace", check=True, timeout=10)
        info = json.loads(proc.stdout)
        if info["version"] != [3, 13]:
            # v0.1.0 declares `requires-python = ">=3.13"`; τ³ runs on 3.12.
            raise ValueError("tau2 v0.1.0 requires Python 3.13 in its selected runtime")
        prefixes = [info[key] for key in ("prefix", "base_prefix", "base_exec_prefix")]
        if any(not isinstance(p, str) or not Path(p).is_absolute() or not Path(p).is_dir()
               for p in prefixes) or Path(prefixes[0]).resolve() != runtime.resolve():
            raise ValueError("tau2 interpreter prefixes do not match the selected runtime")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot bind tau2 Python runtime {python}: {exc}") from exc
    paths = []
    for value in [str(runtime), *prefixes]:
        path = Path(value)
        # uv may report an unversioned symlink as base_prefix. The verifier admits only
        # non-symlink mount roots, but Python still needs the original alias inside its
        # trusted namespace. Bind the alias's parent plus the resolved interpreter tree;
        # never broaden this to the filesystem root.
        aliases = [part for part in (path, *path.parents) if part.is_symlink()]
        for alias in aliases:
            parent = alias.parent.resolve()
            if parent == Path("/"):
                raise ValueError("tau2 runtime alias would require a filesystem-root mount")
            paths.append(str(parent))
        paths.append(str(path.resolve()))
    return str(python), tuple(dict.fromkeys(paths))


def _verify(tid: str, row: dict) -> dict:
    python, runtime_paths = _python_runtime(RUNTIME)
    inputs = [{"path": "tau2_code", "from": str(ASSETS), "immutable": True},
              {"path": "tau2_private.json", "from": str(_case(tid, row) / "private.json")},
              {"path": "tau2_transport_runtime", "from": str(TRANSPORT.resolve().parent),
               "immutable": True},
              {"path": "tau2_bundle", "from": str(DATASET_ROOT / "bundle"), "immutable": True}]
    return {"kind": "command",
            "argv": [python, "-I", "tau2_code/verify/check.py", "--task-id", tid,
                     "--case-sha256", row["private_sha256"]],
            "runtime_paths": list(runtime_paths), "inputs": inputs,
            "reward_file": "/verifier/reward.txt",
            "evidence_file": "/verifier/evidence.json",
            "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True},
            "trial_protocol": dict(TRIAL_PROTOCOL), "trial_evidence": {"version": 1},
            "model_failure_policy": {"version": 1, "invalidate_on_http_status": [429, *range(500, 600)]}}


class _Tasks(Mapping):
    def __init__(self, build): self.build = build
    def __iter__(self): return iter(roster())
    def __len__(self): return len(roster())
    def __getitem__(self, tid): return self.build(tid, roster()[tid])


SETUP = _Tasks(_setup)


def _verify_with_judge(tid: str, row: dict) -> dict:
    result = _verify(tid, row)
    # `EvaluationType.ALL` evaluates the NL assertions of every task that has them, then
    # multiplies the verdict out when the basis excludes `NL_ASSERTION`. The call happens
    # either way, so the channel is declared on exactly those rows.
    if row["needs_judge"]:
        result["judge"] = model_spec("judge")
    return result


VERIFY = _Tasks(_verify_with_judge)
ENV = _Tasks(lambda tid, row: dict(DEFAULT_ENV))


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


def provision_status() -> dict:
    """Validate prepared bytes and runtime metadata, not model/endpoint health.

    A directory and an executable are not execution readiness. Reuse the read-only
    preparation gate in a child so provider/package imports and bytecode settings cannot
    leak into catalogue callers. Do not cache this result: deleted/tampered bytes must be
    visible on the next check.
    """
    import sys
    root = None
    try:
        root, runtime = str(DATASET_ROOT), str(RUNTIME)
        proc = subprocess.run(
            [sys.executable, "-B", str(ASSETS / "prepare.py"), "--check-only",
             "--target", root, "--runtime", runtime],
            capture_output=True, text=True, errors="replace", timeout=60)
        if proc.returncode != 0:
            raise ValueError(proc.stderr.strip() or "tau2 preparation check failed")
        checked = json.loads(proc.stdout)
        if checked.get("manifest_sha256") != MANIFEST_SHA256:
            raise ValueError("tau2 readiness receipt manifest mismatch")
        tarball = DATASET_ROOT / "_src" / TARBALL_NAME
        return {"root": root, "present": True, "tasks": checked["tasks"],
                "runtime": runtime, "bundle": str(DATASET_ROOT / "bundle"),
                "tarball": str(tarball) if tarball.is_file() else None,
                "domains": list(DOMAINS), "user_action_tasks": inventory()["user_action_tasks"],
                "validation": "pinned-assets-and-runtime-metadata", "model_health_checked": False}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return {"root": root, "present": False, "tasks": 0, "detail": str(exc)}


__all__ = ["KIND", "DATASET_ROOT", "RUNTIME", "REPO_TAG", "METRIC", "GOAL", "SPLIT",
           "SPLIT_SOURCE", "load", "control_task", "downloads", "provision_status", "DOMAINS",
           "N_TASKS", "MANIFEST", "inventory", "roster", "SETUP", "VERIFY", "ENV"]
