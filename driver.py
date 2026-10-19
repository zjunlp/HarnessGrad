#!/usr/bin/env python3
"""The sampling loop. Framework-owned (INTERFACE.md §2).

    H0 evaluate -> curve point 0 -> trainer edits -> commit -> evaluate -> ...
                                                              -> curve point t

Two execution modes, because real methods come in two shapes (trainers/base.py):

    A  framework-driven   the platform owns the round loop and calls improve()
    B  method-driven      the method runs its own loop and returns a Trajectory

Both write the same `curve.jsonl`, so curves from either mode land on one axis.

The Trainer never sees this file. Everything it is allowed to know is staged
into <workspace>/_harnessgrad/ before each call.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import data.scaffolds.registry as datasets
from tools.improver import forbidden_reason
from tools.improver import load_config as load_improvers
from tools.improver import resolve as resolve_improver
from ckpt.git_state import STATE_DIRNAME, commit_state, manifest, stage_workspace
from eval.observe.console import CONSOLE as console
from eval.run.metrics import select_task_set
from eval.run import metrics
from eval.policy import diagnostic_policy
from eval.policy.unobserved_diagnostic import Authorization
from eval.run.integrity import PLATFORM_ROOT, workspace_is_outside_platform
from eval.run.runner import evaluate
import eval.containers.container as container
import eval.service.modelgate as modelgate
import eval.containers.sandbox as sandbox
import eval.service.services as services_mod
import tools.web_gateway as web_gateway_mod


# ------------------------------------------------------------------- layers ---
#
# The implementation lives in `harnessgrad/`, layered so the dependencies point one way
# (`harnessgrad/__init__.py` draws the picture). This file is the entrypoint: argparse, the
# wiring, and the order in which the platform refuses things -- the name that INTERFACE.md,
# `tools/serve_ui.py` and every recorded run's argv use, so it stays where it is.
#
# Only what this file itself wires up is imported. Everything else is reached where it
# lives (`from harnessgrad.loops.mode_a import ...`), so the layer a change belongs to is
# visible in the import line rather than flattened through the entrypoint.
from harnessgrad import FRAMEWORK_VERSION
from harnessgrad import config as platform_config
from harnessgrad import slots
from harnessgrad.budget_integration import (
    BudgetBinding,
    BudgetConfigurationError,
    bind_budget,
)
from harnessgrad.environments import (
    _check_env_kinds, _env_record, _narrow_to_run, _report_harness_failed,
    _report_invalid, _required_env_kinds, _resolve_envs, _warn_missing_overlay)
from harnessgrad.identity import _identity
from harnessgrad.loops.eval_side import _eval_from_record, _eval_source, _run_eval_side
from harnessgrad.loops.mode_a import _run_mode_a
from harnessgrad.methods import PlatformTampered, _check_method_entrypoint
from harnessgrad.records import (
    _cost_of, _curve_point, _require_untampered, _save_round_state, _save_traces,
    _save_verdicts, _scored, _write_curve, _write_run_meta, test_rate_fields)


# --------------------------------------------------- improver capability ---

def _cli_provider(improver: dict) -> dict:
    """What provider a CLI improver's *own* config names: `base_url` and `env_key`.

    Derived rather than asked for, because the CLI reads its model and its endpoint from
    its own config (`tools/improver.py:declared_model` already reads the model that way).
    A `model_provider` whose definition has a `base_url` is a custom/local endpoint and can
    be probed; a name with no definition (e.g. `OPENAI`) is the CLI's built-in and cannot.

    **`env_key` comes back too, because probing with the wrong credential is how a valid
    run gets refused at the door.** The probe below used the *agent's* key for the
    *improver's* endpoint. Those are two budgets by design (`.env.example`), and when they
    name different providers the probe authenticates with a credential the endpoint does
    not accept, gets 401, and reports "this endpoint cannot emit tool calls" -- a refusal
    about the wrong fact. Measured 2026-10-06: it passed only because this deployment
    happens to set `HG_AGENT_API_KEY` and `DEEPSEEK_API_KEY` to the same value.

    **`~/.codex/config.toml` is codex's file, so only a codex improver may be read for it.**
    Any `kind == "cli"` improver used to come through here, and with the `dsh` entry resolved
    this returned the custom provider out of the operator's codex config
    (`https://naiccc.com/v1`) -- so the gate below would have probed *that* endpoint, one
    belonging to someone else, and written its answer onto the curve as a fact about
    `deepseek-flash`. An improver that declares its own `provider` (dsh declares
    `deepseek-official`) uses that CLI's built-in endpoint, which is exactly the
    "cannot be probed from here" case the caller already records as *unprobed*.
    """
    if improver.get("provider") or improver.get("argv_style", "codex") != "codex":
        return {}
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    cfg = Path(home) / "config.toml"
    if not cfg.is_file():
        return {}
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    provider = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("model_provider") and "=" in stripped:
            provider = stripped.partition("=")[2].strip().strip('"').strip("'")
    if not provider:
        return {}
    section = f"[model_providers.{provider}]"
    inside = False
    found: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == section
            continue
        if not inside or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        if name in ("base_url", "env_key"):
            found[name] = value.strip().strip('"').strip("'")
    return found


def _cli_provider_base_url(improver: dict) -> str:
    """The endpoint a CLI improver will call, or `""` when it uses a built-in provider."""
    return _cli_provider(improver).get("base_url", "")


def _probe_tool_calls(base_url: str, model: str) -> tuple[bool, str]:
    """Ask the endpoint whether it returns structured tool_calls. Never raises.

    Reuses `tools/probe_tool_calls.py`'s request shapes so the door and the CLI agree on
    what "supports tool calls" means. `responses` decides, because that is the wire a codex
    improver uses; `chat` is reported in the detail for a reader who needs to know which
    path failed.
    """
    try:
        sys.path.insert(0, str(PLATFORM_ROOT))
        import tools.probe_tool_calls as probe
    except Exception as exc:                                             # noqa: BLE001
        return False, f"the probe itself could not be imported: {exc}"
    # The improver's own credential, falling back to the agent's only when the CLI's
    # config names none -- a local endpoint does not check the key, and a hosted one that
    # does will have said so in `env_key`. Not `HG_AGENT_API_KEY`: see `_cli_provider`.
    key = ""
    key_env = _cli_provider({}).get("env_key", "")
    if key_env:
        key = os.environ.get(key_env, "")
    if not key:
        key = platform_config.current().agent.api_key or "local"
    try:
        resp_ok, resp_why = probe._calls_responses(base_url, model, key, 120.0)
        chat_ok, chat_why = probe._calls_chat(base_url, model, key, 120.0)
    except Exception as exc:                                             # noqa: BLE001
        return False, f"the probe raised: {type(exc).__name__}: {exc}"
    detail = f"responses: {resp_why} | chat: {chat_why}"
    return resp_ok, detail


# --------------------------------------------------------- model config ---

def load_env(path: Path) -> dict:
    """Load `.env` into the environment the harness will run in.

    The harness reads its model configuration from the environment, so somebody
    has to put it there. Doing it in the driver rather than in the harness keeps
    the harness free of a dotenv dependency and means a method that edits the
    harness cannot accidentally change how the model is reached.

    Real environment variables win: an explicit `HG_AGENT_MODEL=... ./driver.py`
    must not be silently overridden by a file on disk.
    """
    if not path.exists():
        return {}
    loaded = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    # **`.env` is now part of the process environment, so the configuration snapshot
    # has to be taken after this.** `harnessgrad/config` may already have been imported
    # -- `harnessgrad.channel` reads one variable at import -- and without the reset the
    # run would use a snapshot taken before `.env` existed. `reset()` drops it; the next
    # `current()` rebuilds from the environment this function just filled in.
    platform_config.reset()
    return loaded


def _read_trace_bodies(directory: Path) -> tuple[dict, dict]:
    """Read back the trace bodies this platform wrote for one round.

    `_save_traces` writes `traces[task_id]` **verbatim** to `<task>.jsonl`, so the file is
    the value and the read is lossless. Needed when a measurement is shared: the method's
    trace channel is staged from the in-memory dict, not from the run directory, so a
    shared H0 without its traces would hand the methods an empty channel -- the same
    unfairness sharing exists to remove.
    """
    traces: dict = {}
    trials: dict = {}
    if not directory.is_dir():
        return traces, trials
    for path in sorted(directory.glob("*.jsonl")):
        name = path.name[: -len(".jsonl")]
        if ".trial" in name:
            task, _, index = name.partition(".trial")
            try:
                trials.setdefault(task, []).append((int(index), path.read_text()))
            except ValueError:
                continue
        else:
            traces[name] = path.read_text()
    return traces, {task: [body for _, body in sorted(parts)]
                    for task, parts in trials.items()}


def _platform_content_sha() -> str:
    """One digest for the whole platform tree (see `eval/run/integrity.py`).

    Written into every run's `run_meta` so a later `--resume` can refuse to continue a
    curve across a platform change: two code versions in one curve is the confound this
    platform exists to remove, and nothing on disk said which version a run used.
    """
    from eval.run.integrity import content_sha
    return content_sha(PLATFORM_ROOT)


class ResumeRefused(RuntimeError):
    """A resume this run cannot honour, with a sentence a reader can act on."""


class ResumeNothingToDo(RuntimeError):
    """The run already measured every round the budget asks for."""


def _resume_train_state(args, run_dir: Path, split, work: Path):
    """What continuing this run needs: its points, its last state, and the base it began from.

    A resume may not re-measure a state that is already on the curve -- two measurements of
    one state are two numbers, and picking either is a choice the record cannot defend --
    so everything the round-0 path would normally produce is rebuilt **from the record**:

      * `base` from the round-0 point plus `round0_traces/` (`_base_from_point`),
      * `h0_sha` from that point's content address,
      * the workspace from `state_after_round<last>/`, re-staged and committed.

    `commit_state` returns a content address, so re-staging the same tree reproduces the
    same `harness_sha` the curve already carries. The caller asserts that, because a
    different address would mean the saved state is not the state the score belongs to.
    """
    curve_path = run_dir / "curve.jsonl"
    if not curve_path.is_file():
        raise ResumeRefused(f"{run_dir.name} has no curve.jsonl; there is nothing to continue")
    existing = [json.loads(line) for line in curve_path.read_text().splitlines() if line.strip()]
    if not existing:
        raise ResumeRefused(f"{run_dir.name}/curve.jsonl holds no measured point")

    meta_path = run_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    recorded_split = meta.get("split_sha")
    now_split = datasets.split_sha(split) if split else None
    if recorded_split and now_split and recorded_split != now_split:
        raise ResumeRefused(
            f"the run was measured on split_sha {recorded_split[:12]}, this invocation on "
            f"{now_split[:12]}; the old points are not the same task set")
    recorded_platform = meta.get("platform_content_sha")
    if recorded_platform:
        now_platform = _platform_content_sha()
        if recorded_platform != now_platform:
            raise ResumeRefused(
                f"the platform content changed since this run started "
                f"({recorded_platform[:12]} -> {now_platform[:12]}); continuing would stitch "
                f"two code versions into one curve")

    last = max(int(point.get("round") or 0) for point in existing)
    if last >= args.rounds:
        raise ResumeNothingToDo(
            f"round {last} of a {args.rounds}-round budget is already measured")
    view = run_dir / f"state_after_round{last}"
    if not view.is_dir():
        raise ResumeRefused(
            f"{run_dir.name} has no state_after_round{last}/; the state round {last} scored "
            f"was not kept, so there is nothing to continue from")

    if work.exists():
        shutil.rmtree(work)

    # **The base tree has to be reachable in *this* repository, and after `--h0-from` it was
    # not.** `mode_a` calls `reset_hard(work, h0_sha)` after every round and once more after
    # the loop, and `reset_hard` starts with `git cat-file -t <sha>`. A resume stages only
    # `state_after_round<last>/` into a fresh repo, so the object store holds that state and
    # nothing else -- while `h0_sha` comes from the round-0 point, which on a shared-H0 run
    # was measured in another repository. The result was `CalledProcessError` from
    # `ckpt/git_state.py` twenty minutes into a paid run, on the *last* thing the run does
    # (measured: both A-line runs, 2026-10-18 12:50Z and 12:52Z).
    #
    # The fix restages the base first: a tree hash is content-addressed, so committing the
    # same files here reproduces the same `h0_sha`, and the object then exists for
    # `reset_hard`. Restaging it is also a *check* the old path never made -- if the saved
    # base does not hash to the address the curve already published, the two are not the
    # same state and continuing would stitch a score to a tree it does not belong to.
    h0_sha = (existing[0].get("identity") or {}).get("harness_sha")
    base_state = run_dir / "state_after_round0"
    restored_base = None
    if h0_sha and base_state.is_dir():
        stage_workspace(base_state, work)
        restored_base = commit_state(work, "resume: base")
        if restored_base != h0_sha:
            raise ResumeRefused(
                f"{run_dir.name}/state_after_round0/ hashes to {restored_base[:12]} but the "
                f"run's round-0 point scored {h0_sha[:12]}; the saved base is not the state "
                f"the curve published, so continuing would attach new rounds to a different "
                f"harness")
        # Keep `.git` (that is where the base object now lives) and put the state we
        # continue from on top of it, exactly as the round loop does after measuring.
        for child in list(work.iterdir()):
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(view, work, dirs_exist_ok=True, symlinks=True)
        # `stage_workspace` creates this when it initialises a repo, and the copy above can
        # remove it on a state saved by an older platform. The sandbox mounts a writable
        # tmpfs over it, and a mount target has to exist.
        (work / STATE_DIRNAME).mkdir(exist_ok=True)
        staged_sha = commit_state(work, f"resume: round {last}")
    else:
        stage_workspace(view, work)
        staged_sha = commit_state(work, f"resume: round {last}")
        if h0_sha and not _tree_present(work, h0_sha):
            raise ResumeRefused(
                f"{run_dir.name} keeps no state_after_round0/, and the tree its round-0 point "
                f"scored ({h0_sha[:12]}) is not in this repository's object store; the run "
                f"cannot be restored to the state that score belongs to")

    base = _base_from_point(existing[0])
    traces_dir = run_dir / "round0_traces"
    base["traces"], base["trial_traces"] = _read_trace_bodies(traces_dir)
    recorded_sha = (existing[last].get("identity") or {}).get("harness_sha")
    # **Restaging is only a resume if it reproduces the state the last score belongs to.**
    # The docstring above promised this check and the code did not make it; a saved state
    # that hashes differently would have continued the curve from a tree no published point
    # describes -- two code versions in one curve, one level below the `platform_content_sha`
    # check above.
    if recorded_sha and staged_sha != recorded_sha:
        raise ResumeRefused(
            f"state_after_round{last}/ hashes to {staged_sha[:12]} but round {last} was "
            f"measured on {recorded_sha[:12]}; the saved state is not the state that score "
            f"belongs to")
    h0_sha = h0_sha or staged_sha
    return {"existing": existing, "last": last, "base": base, "h0_sha": h0_sha,
            "recorded_sha": recorded_sha,
            "staged_sha": staged_sha, "restored_base": restored_base}


def _tree_present(repo: Path, sha: str) -> bool:
    """Is this tree object in `repo`'s object store? (`cat-file -e` exits 1, never raises.)"""
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{tree}}"],
                          capture_output=True).returncode == 0


def _base_from_point(point: dict) -> dict:
    """Rebuild the measurement dictionary from a curve point.

    A curve point keeps everything the rest of the round-0 path reads: the per-task
    scores, the invalid and harness-failed records, the runtime and protocol records,
    and the trial spread. It does not keep the trace maps -- those live in the run's
    `round0_traces/`, which `--h0-from` copies. This exists so a run measured **before**
    `h0.json` was written can still be shared instead of being measured again: the
    number is the same number.
    """
    identity = point.get("identity") or {}
    base = {
        "per_task": point.get("per_task") or {},
        "invalid": point.get("invalid"),
        "harness_failed": point.get("harness_failed"),
        "model_gateway": point.get("model_gateway"),
        "harness_runtime": point.get("harness_runtime"),
        "evaluation_protocols": point.get("evaluation_protocols"),
        "identity_declared": identity.get("harness_identity"),
        "trials": point.get("n_trials"),
        "score_std": point.get("score_std"),
        "per_task_std": point.get("per_task_std"),
        "per_task_trials": point.get("per_task_trials"),
        "cached_tasks": point.get("cached_tasks"),
    }
    return base


def describe_model() -> dict:
    """What the platform will record as `agent_model`, and whether it is real.

    Reported at the top of a run because the alternative is discovering three
    rounds later that every score came from the mock stand-in.
    """
    agent = platform_config.current().agent
    backend = agent.backend
    model = agent.model
    has_key = bool(agent.api_key)
    return {
        "backend": backend,
        "model": model,
        "credentials": has_key,
        "live": backend != "mock" and bool(model) and has_key,
    }


# The curve table is rendered by `eval/observe/console.py`, next to the header and the
# round lines, so that the human text and `events.jsonl` cannot drift apart. It
# used to live here and carried a `sel` column that read `?` on every row of
# every run this platform has produced, because mode A has no selection step to
# report. A column that is constant noise teaches the reader to skip the table,
# which is the opposite of what a table is for.


# ----------------------------------------------------------------- main ---

def _config_help() -> str:
    """The configuration surface, rendered for `--help`.

    Derived from `harnessgrad.config.describe()`, so the help text and the declared
    defaults are one thing: a second hand-written table is how `--help` starts lying.
    """
    lines = ["configuration (every value is an environment variable; the effective set "
             "of a run is recorded in run_meta.json under `config`):"]
    for row in platform_config.describe():
        default = row["default"]
        shown = "" if default in (None, "<redacted>") else f"={default}"
        lines.append(f"  {row['variable'] + shown:<36} {row['what']}")
    return "\n".join(lines)


def _main(attempt: dict) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Ordinary runs have no universal filesystem-quota or backend-observer\n"
            "prerequisite. Existing declared-policy refusals still apply.\n"
            "Ordinary local development runs as your user. Native/files demos and --help\n"
            "require no sudo, dedicated quota volume, root Docker access, or strict runtime mode.\n"
            "Sandboxed runs still require working bubblewrap/user namespaces; container-backed\n"
            "tasks and image/overlay builds have separate Docker prerequisites.\n"
            "Local/demo success grants no production/H0 authorization. Declared runtime policies\n"
            "still fail closed without trusted backend observations: missing evidence remains\n"
            "blocked and invalid/unmeasured, never an ordinary zero score.\n"
            "Synthetic checks do not authorize production diagnostics or H0."),
        epilog=_config_help())
    ap.add_argument("--harness", default="loop")
    ap.add_argument("--dataset", default="demo")
    ap.add_argument("--sampling", default="diagnostic",
                    help="diagnostic (one task, default) | pilot_10pct (temporary rerun: "
                         "ceil(10%%), capped at 30; reviewed diagnostic required) | "
                         "all | below_X | above_X | random_N. "
                         "For the local demo, explicitly select all; this grants no "
                         "production/H0 authorization")
    ap.add_argument("--policy-evidence", default=None,
                    help="required for diagnostic/pilot_10pct sampling: staged policy "
                         "JSON with context and passing dataset gates; "
                         "pilot_10pct also requires receipt and separate hash-bound review")
    ap.add_argument("--authorize-unobserved-diagnostic", metavar="OPERATOR_REASON",
                    help="explicitly authorize one train-side diagnostic attempt with policy "
                         "enforcement NOT observed; no measured score, curve or production authorization")
    ap.add_argument("--rounds", type=int, default=None,
                    help="improvement rounds (default 0 for staged runs, 3 otherwise)")
    #: How many times each (harness, task) pair is measured. One is one sample, and one
    #: sample cannot tell an edit's effect from the noise: measured here, the same
    #: unmodified harness scored 0.333 in one run and 0.000 in the next because the agent
    #: model intermittently emits a tool call the harness cannot parse. Costs `trials`x
    #: the wall clock -- 88% of a run is the harness loop -- so a first look uses 1 and a
    #: claim about an edit uses 3.
    ap.add_argument("--trials", type=int, default=1,
                    help="measure each task N times; the point carries n_trials and "
                         "score_std. Default 1 (one sample, no spread recorded)")
    #: How many tasks are measured **at the same time**. The other axis from `--trials`,
    #: and they multiply: trials buy a spread, jobs buy wall clock. Measured here, 87.8%
    #: of a run's wall clock is the harness's own agent loop (2578 s of 2936 s on a
    #: three-task round) and the platform does nothing while it waits, so a task set run
    #: four at a time is close to four times shorter. The tasks are independent by
    #: construction -- own container, own network, own gateway, own workspace -- but they
    #: share the model endpoint and this host's CPU, which is why the default stays 1: a
    #: number produced under contention is not comparable with one produced without it,
    #: and the run records the value it used.
    ap.add_argument("--gpu-budget", type=int, default=None, metavar="N",
                    help="machine-wide ceiling on concurrently *measured* tasks across every "
                         "run sharing this host, enforced with file locks "
                         "(harnessgrad/slots.py). `--jobs` stays this run's own ceiling; the "
                         "budget is what keeps N runs from each measuring `--jobs` at once. "
                         "Unset keeps the old behaviour: `--jobs` alone")
    ap.add_argument("--gpu-budget-dir", default=None, metavar="DIR",
                    help="where the budget's slot locks live (default: $TMPDIR/hg-gpu-slots). "
                         "Two batches with different capacities must not share a directory")
    ap.add_argument("--jobs", type=int, default=1,
                    help="measure up to N tasks concurrently (default 1). The tasks share "
                         "one model endpoint and one host, so raise it deliberately")
    ap.add_argument("--mode", default="A", choices=["A", "B"],
                    help="DEPRECATED and ignored: there is one engine. A method that "
                         "submits its whole sequence in one call behaves as mode B did; "
                         "one that submits a step per call behaves as mode A did, and "
                         "`steps_in_submission` on each point says which happened.")
    ap.add_argument("--method-timeout", type=int, default=86400,
                    help="mode B: seconds before the method's own loop is killed")
    # **哪个改进器。** 方法自带改进器时这三个开关是无害的;用一种"把方法拆成规则+
    # 改进器"的方法时,它们决定谁来做那一步 —— 而解析出来的
    # 版本和哈希会进每个曲线点(INTERFACE.md §4.49)。放在命令行而不是只读配置:
    # 一次实验"用哪个改进器"正是最该被显式记录、最不该靠环境变量碰运气的东西。
    ap.add_argument("--improver", default=None,
                    help="improvers/improvers.json 里的名字,或一个可执行文件的路径")
    ap.add_argument("--improver-model", default=None, help="覆盖改进器自己的模型")
    ap.add_argument("--improver-effort", default=None,
                    help="codex 系:low|medium|high|xhigh")
    ap.add_argument("--method-entrypoint", default=None,
                    help="mode B only: argv for the method's own runner, e.g. "
                         "'python /path/to/rrsi_wrap.py'. Defaults to "
                         "harness.json's improve_entrypoint.")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--budget-profile", default=None,
                    help="JSON trusted-dispatch profile; required for approved Task25 runs")
    ap.add_argument("--budget-ledger", default=None,
                    help="path to the process-shared trusted-dispatch ledger")
    ap.add_argument("--budget-arm", default="agent",
                    help="identity of this run arm in the trusted-dispatch ledger")
    ap.add_argument("--tasks", default=None,
                    help="a comma-separated subset of task ids to run, instead of the "
                         "whole side. Refused if any id is not in the dataset: silently "
                         "dropping one would produce a curve whose task count does not "
                         "match what was asked for, which is the kind of difference "
                         "nobody notices until they compare two runs.")
    ap.add_argument("--side", choices=("train", "eval"), default="train",
                    help="which side of the dataset's split this run is about. A run "
                         "evaluates ONE side: a train run evolves the harness and shows "
                         "the method the traces of the tasks it is scored on, and an "
                         "eval run measures one state of a train run on the exam. They "
                         "are separate runs so that the exam's traces are not in the "
                         "train run at all -- an absence, not a withheld field.")
    ap.add_argument("--split", default=None,
                    help="which division of the dataset this run uses, when the caller "
                         "does not want the one the dataset declares. `declared` (the "
                         "default) uses the adapter's own SPLIT; `default` uses the "
                         "platform's documented rule (a deterministic third, by hash of "
                         "the task id); `official` uses the benchmark's own evaluation set "
                         "as the adapter declares it, which is the comparable run -- the "
                         "whole official set, with no study material in this pool. Anything "
                         "else is a path to a JSON file {\"train\": [...], \"eval\": [...]}. "
                         "The division is a property of the experiment protocol, not of the "
                         "benchmark, so the run may supply it; whichever is used is recorded "
                         "on the run as `split_source` and `split_sha`, and two points are "
                         "comparable only when those agree.")
    ap.add_argument("--from-run", default=None,
                    help="--side eval: the train run whose harness state to measure")
    ap.add_argument("--reuse-identical", action="store_true",
                    help="a candidate whose content address equals an already-measured "
                         "state still gets a curve point, but that point reuses the earlier "
                         "measurement instead of spending another task-set run. Off by "
                         "default: it changes what a re-measured state means, and the "
                         "isolation tests rely on the measurement actually happening")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted run instead of starting clean: the train "
                         "side continues from its last measured round, the eval side scores "
                         "only the tasks whose progress file is missing")
    ap.add_argument("--from-round", type=int, default=None,
                    help="--side eval: which round of that run (default: its last)")
    ap.add_argument("--h0-from", default=None, metavar="RUN_ID",
                    help="reuse another run's round-0 measurement instead of measuring one. "
                         "Same base tree, same study tasks, so every method must be offered "
                         "the *same* first score: re-measuring it per method lets batching "
                         "non-determinism hand one method a different number and a different "
                         "trace set, which is the one thing a comparison cannot absorb. The "
                         "reused point records `shared_from` naming the run it came from. "
                         "Refused unless both runs share the base harness sha")
    ap.add_argument("--baseline", default=None,
                    help="a frozen reference point to measure this run against, as a JSON "
                         "file holding one curve point (the object a `curve.jsonl` line "
                         "carries). Declared rather than inferred: without it the baseline "
                         "is round 0 of this same run, which is the control for one method "
                         "and a different reference for every other one. A frozen reference "
                         "puts every method on one axis, which is what cross-method "
                         "comparison needs -- and because nothing re-measures it, this run "
                         "checks and records whether it still describes the run in front of "
                         "it (agent model, backend, harness family, framework version and "
                         "the task set). Measure the frozen reference well: it is amortized "
                         "over every method that uses it, so its own sampling noise is "
                         "baked into all of them.")
    ap.add_argument("--seed", default=None,
                    help="the environment's seed (INTERFACE.md §2.5.7). A per-task "
                         "seed is derived from it and given to services that declare "
                         "`needs_seed`; recorded on every curve point. Drawn at random "
                         "when omitted, and re-supplyable so a recorded run can be "
                         "reproduced.")
    ap.set_defaults(sandbox=True)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--no-sandbox", dest="sandbox", action="store_false",
                    help="run the harness WITHOUT a mount namespace. A sandboxed "
                         "harness cannot see the platform at all; without one it "
                         "can read the driver and .env, and only the hash check in "
                         "eval/integrity.py stands between it and the scorer. "
                         "Explicit debugging opt-out, never an automatic fallback. "
                         "Exec tasks retain their container boundary and declared-policy "
                         "refusals still apply. Numbers are not comparable across the two modes.")
    ap.add_argument("--work-root", default="../harnessgrad_work",
                    help="where harness working trees live; must be OUTSIDE the "
                         "platform tree, or a harness can reach the platform")
    ap.add_argument("--show-config", action="store_true",
                    help="print the effective configuration (the declared `HG_*` "
                         "environment variables, after `.env` is folded in) as JSON "
                         "and exit 0 without running anything")
    args = ap.parse_args()

    if (args.run_id or "").startswith("task25-") and not args.budget_profile:
        print("refusing approved Task25 run without --budget-profile", file=sys.stderr)
        return 2

    if args.show_config:
        # The effective configuration, not the declared one: `.env` is folded in first,
        # because that is what a run would actually read. Nothing else runs -- no
        # dataset is loaded, no image is resolved, no improver is probed.
        load_env(Path(__file__).parent / ".env")
        print(json.dumps(platform_config.dump(), indent=2))
        return 0

    staged = args.sampling in ("diagnostic", "pilot_10pct")
    if args.rounds is None:
        args.rounds = 0 if staged else 3
    if args.authorize_unobserved_diagnostic is not None and (
            args.sampling != "diagnostic" or args.side != "train"
            or not args.authorize_unobserved_diagnostic.strip()
            or args.method_entrypoint):
        print("refusing unobserved diagnostic: explicit operator reason, diagnostic sampling, "
              "train side and no method entrypoint are required", file=sys.stderr)
        return 2
    policy_evidence = None
    if staged:
        try:
            diagnostic_policy.staged_task_set([], args.sampling, trials=args.trials,
                                               jobs=args.jobs, rounds=args.rounds)
            if not args.policy_evidence:
                raise ValueError("staged runs require --policy-evidence with passing dataset gates")
            policy_evidence = json.loads(Path(args.policy_evidence).read_text())
            if not isinstance(policy_evidence, dict):
                raise ValueError("policy evidence must be an object")
            context = policy_evidence.get("context")
            diagnostic_policy.validate_context(context)
            if (context["dataset"] != args.dataset or context["side"] != args.side
                    or (context["gates"]["status"] != "passed" and not (
                        args.authorize_unobserved_diagnostic
                        and context["gates"]["status"] == "unknown"))):
                raise ValueError("passing dataset gates must bind this dataset and side")
        except (OSError, ValueError) as exc:
            print(f"refusing staged policy: {exc}", file=sys.stderr)
            return 2
        # A diagnostic/pilot measures one state; it cannot invoke an improver.
        args.improver = "none"

    runs_root = Path(args.runs_root)
    eval_from = None
    if args.side == "eval":
        # The "harness" of an eval run is a state a train run produced. Pointing
        # `harness_src` at it before anything else means staging, the manifest, the
        # header, the sandbox and the method channel all work on it unchanged -- there
        # is no second code path for "a harness that came out of a run".
        try:
            harness_src, _round = _eval_source(args, runs_root)
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 2
        eval_from = _eval_from_record(runs_root, args.from_run, _round)
    else:
        harness_src = Path(__file__).parent / "base_harness" / args.harness
        if not harness_src.is_dir():
            print(f"no such harness: {harness_src}", file=sys.stderr)
            return 2

    env_file = Path(__file__).parent / ".env"
    load_env(env_file)
    model = describe_model()
    if staged and (not model["live"] or policy_evidence["context"]["endpoint"]
                   != platform_config.current().agent.base_url):
        print("refusing staged policy: bind evidence to the live local agent endpoint",
              file=sys.stderr)
        return 2
    # The project's model rule, enforced at the door next to the credential check: the
    # harness's model is a run-level choice and the same rule covers it. `improvers.json`
    # holds the list and the reason (`tools/improver.py:forbidden_reason`).
    banned = forbidden_reason(load_improvers(), model.get("model") or "")
    if banned:
        print(f"refusing to run: {banned}", file=sys.stderr)
        return 2
    if model["backend"] != "mock" and not model["live"]:
        missing = [n for n, ok in (("HG_AGENT_MODEL", model["model"]),
                                   ("HG_AGENT_API_KEY", model["credentials"]))
                   if not ok]
        print(f"backend is {model['backend']!r} but {', '.join(missing)} "
              f"{'is' if len(missing) == 1 else 'are'} unset; every score would "
              f"come from a harness that cannot reach a model. See .env.example.",
              file=sys.stderr)
        return 2

    tasks_all, scorable, split = datasets.load_split(args.dataset)
    oracle_source, oracle_coverage = datasets.oracle_source(args.dataset)
    # **Whose division this run uses.** The train/eval division is a property of the
    # experiment protocol, not of the benchmark: the same dataset supports "study the
    # official train split and report on the official test split" and "study a third of the
    # pool and report on another third", and those are different claims. So the adapter's
    # declaration is the *default* answer rather than the only one, and the run may name a
    # different division. What it may not do is leave the choice unrecorded: `split_source`
    # and `split_sha` go on the run, because two curve points are comparable only if they
    # scored the same tasks -- the same rule §2.1 already applies to the sampling policy.
    # `None` when nothing divided the pool: a dataset with no declaration and no `--split`
    # runs on everything, and calling that source "declared" would name a declaration that
    # does not exist. The warning below still fires, because that run cannot support a
    # generalisation claim.
    split_source = "declared" if split is not None else None
    if args.split and args.split != "declared":
        if args.split == "default":
            # Both derived divisions go through the same guard as a supplied file: the
            # platform's hash rule can put every task on one side of a small pool, and that
            # is a message about the dataset, not a traceback out of the driver.
            try:
                split = datasets.validate_split(
                    args.dataset, tasks_all, datasets.default_split(tasks_all),
                    require_nonempty=(args.side,))
            except ValueError as exc:
                console.note(str(exc), level="error")
                return 2
            split_source = "default"
        elif args.split == "official":
            try:
                split = datasets.validate_split(
                    args.dataset, tasks_all, datasets.official_split(args.dataset, tasks_all),
                    require_nonempty=(args.side,))
            except ValueError as exc:
                console.note(str(exc), level="error")
                return 2
            split_source = "official"
        else:
            path = Path(args.split)
            if not path.is_file():
                console.note(
                    f"--split {args.split!r} is neither `declared`, `default`, `official`, "
                    f"nor a readable file path", level="error")
                return 2
            try:
                supplied = json.loads(path.read_text())
                split = datasets.validate_split(args.dataset, tasks_all, supplied,
                                                require_nonempty=(args.side,))
            except (json.JSONDecodeError, ValueError) as exc:
                console.note(f"--split {path}: {exc}", level="error")
                return 2
            split_source = f"file:{path.resolve()}"
    # **A run is about one side, and the narrowing happens here**, before round 0 and
    # before the workspace is staged. That placement is the design: the other side is
    # never loaded, never evaluated, and its traces are never written, so "the method
    # cannot see the exam" stops being a field this platform withholds and becomes a
    # side that is not in the run at all. The same upgrade as hiding the platform by not
    # mounting it, rather than by checking what was read.
    side_ids = list(split[args.side]) if split else [t["task_id"] for t in tasks_all]

    # An explicit subset, applied *after* the side so a request can never pull a task
    # from the other side by naming it -- the side is the run's identity, not a filter
    # the caller can override.
    if args.tasks:
        asked = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = sorted(set(asked) - set(side_ids))
        if unknown:
            print(f"--tasks names {unknown}, which are not on the {args.side} side of "
                  f"{args.dataset!r}. That side has {sorted(side_ids)}",
                  file=sys.stderr)
            return 2
        side_ids = [t for t in side_ids if t in set(asked)]

    # Early staged selection happens before setup, image admission and H0 execution.
    # Other policies retain their historical score-based behavior.
    eligible_ids = list(side_ids)
    if args.sampling == "diagnostic":
        side_ids = metrics.diagnostic_task_set(side_ids)
    if args.sampling == "pilot_10pct":
        try:
            diagnostic_policy.require_review(policy_evidence,
                                             context=policy_evidence["context"],
                                             eligible=eligible_ids, run_id=args.run_id)
        except ValueError as exc:
            print(f"refusing staged policy: {exc}", file=sys.stderr)
            return 2
        side_ids = metrics.pilot_task_set(side_ids)

    tasks_all = [t for t in tasks_all if t["task_id"] in set(side_ids)]
    if not tasks_all:
        print(f"dataset {args.dataset!r} has no tasks on side {args.side!r}",
              file=sys.stderr)
        return 2

    # Resolve only the selected task declarations. Lazy adapters may require private
    # inputs, runtimes or model configuration when a value is read; an unselected
    # task must not block this run before the subsequent runtime preflight.
    _, setups, verifiers = datasets.load_tasks(args.dataset, task_ids=side_ids)
    envs = datasets.load_envs(args.dataset, task_ids=side_ids)
    setups, verifiers, envs = _narrow_to_run(setups, verifiers, envs, side_ids)
    verifier_kinds = sorted({v.get("kind", "answer") for v in verifiers.values()})
    # The record says **unified**, not the flag. `--mode` is accepted so existing scripts
    # keep working, but it selects nothing any more, and a point that said `run_mode: B`
    # while the unified loop ran A-shaped steps would be the same class of lie as
    # `agent_model: mock`. Old records keep their own `A`/`B`; these say what is true now.
    mode = "unified" if args.mode in ("A", "B") else args.mode

    run_id = args.run_id or (
        f"{args.harness}-{args.dataset}-{args.sampling}-m{mode}"
        + (f"-{secrets.token_hex(8)}" if staged else ""))
    run_dir = Path(args.runs_root) / run_id
    try:
        if staged:
            run_dir = diagnostic_policy.reserve_run_directory(Path(args.runs_root), run_id)
        else:
            run_dir.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, ValueError) as exc:
        print(f"refusing staged policy: cannot reserve fresh run ID {run_id!r}: {exc}",
              file=sys.stderr)
        return 2

    diagnostic_authorization = (Authorization(run_id, side_ids[0],
                                args.authorize_unobserved_diagnostic)
                                if args.authorize_unobserved_diagnostic else None)
    enforcement = diagnostic_authorization.record() if diagnostic_authorization else None

    def save_policy_receipt(res: dict | None = None) -> bool:
        if not staged:
            return True
        unknown = {"status": "unknown", "receipt_sha256": None,
                   "detail": "driver has no verified lifecycle receipt; trusted orchestration must attest"}
        receipt = diagnostic_policy.build_receipt(
            run_id=run_id, stage=args.sampling, eligible=eligible_ids, selected=side_ids,
            context=policy_evidence["context"],
            outcomes=(diagnostic_policy.evaluation_outcomes(res, side_ids) if res is not None else []),
            cleanup=unknown, snapshot=unknown, enforcement=enforcement)
        # Keep the pre-execution plan even if evaluation is interrupted or refused.
        diagnostic_policy.write_receipt(
            run_dir / ("diagnostic_receipt.json" if res is not None else "staged_plan.json"), receipt)
        return (enforcement is None and receipt["lifecycle"] is not None
                and receipt["lifecycle"]["publication_ready"]
                and receipt["coverage"]["measured_tasks"] == len(side_ids))

    save_policy_receipt()
    if diagnostic_authorization is not None:
        # Persist intent before any runtime preflight; interruption/refusal remains
        # evidence of an incomplete diagnostic, never an inferred successful run.
        _write_run_meta(run_dir, run_id=run_id, dataset=args.dataset,
                        sampling_policy="diagnostic", enforcement=enforcement)
        def finalize_incomplete(detail):
            if not (run_dir / "diagnostic_receipt.json").exists():
                save_policy_receipt({"trials": 1, "invalid": {
                    side_ids[0]: {"stage": "preflight_or_execution", "detail": detail}}})
        attempt["finalize"] = finalize_incomplete

    # The trusted dispatcher is opt-in for ordinary runs.  An approved Task25
    # run is the one deliberate exception: its profile is part of the approval
    # contract and the run must refuse to start without a configured ledger.
    budget_profile_source = args.budget_profile
    budget_arm = args.budget_arm or "agent"
    budget_ledger = args.budget_ledger or str(run_dir / "budget_ledger.json")
    budget_binding: BudgetBinding | None = None
    if budget_profile_source:
        try:
            budget_binding = bind_budget(
                budget_profile_source, budget_ledger, run_id=run_id, arm_id=budget_arm)
        except BudgetConfigurationError as exc:
            print(f"refusing budget profile run: {exc}", file=sys.stderr)
            return 2
        # These counters describe orchestration units.  Provider calls and token
        # usage are populated only by the trusted gateway itself.
        budget_binding.record("rounds", args.rounds)
        budget_binding.record("trials", args.trials)
        budget_binding.record("jobs", args.jobs)
        args.budget_profile = str(budget_profile_source)
        args.budget_ledger = str(budget_binding.ledger_path)
        args.budget_arm = budget_binding.arm_id
        args.budget_profile_hash = budget_binding.profile_hash

    # **The machine-wide measurement budget, armed here and not only for budget-profile
    # runs.** It sat inside the `if budget_profile_source:` block above for a while, so a run
    # without `--budget-profile` -- which is every run we launch -- silently measured with no
    # ceiling at all: `--gpu-budget 40` was accepted, recorded on the point, and enforced
    # nowhere. Measured 2026-10-17: the slot directory was never created, so a nine-method
    # batch at `--jobs 24` each would have offered the endpoint 216 concurrent tasks instead
    # of 40.
    slots.configure(args.gpu_budget, args.gpu_budget_dir)

    # From here on the run narrates itself through the console, which writes the
    # human text and `events.jsonl` together.
    console.attach(run_dir / "events.jsonl")

    # **Which improver this run's edits came from, resolved before anything is paid for.**
    #
    # A method is a decision rule *and* an improver, and the platform used to record only
    # the rule's model. Resolved here rather than at the first method call so that a
    # machine with no usable improver refuses at the door -- the same reason images are
    # pinned above: a run that discovers its tooling is missing three rounds in has
    # already spent the rounds.
    #
    # A refusal is only for a run that *needs* an improver and cannot find one. The
    # reference methods carry their own (`methods/editor.py` calls `HG_METHOD_*` directly),
    # so an unresolvable improver is a warning plus an empty identity field, not a stop --
    # and `improver` stays empty so no curve point can claim an improver it did not use.
    #
    # Held in a local and handed to the loops that write points. It used to be a module
    # global here that `_identity` read from the other side of the call stack: a curve
    # point's improver field then depended on a mutable global, and "what does this point
    # claim" could not be answered without running the program that produced it.
    improver: dict = {}
    # **`--improver none` is a statement, not a failure to resolve.** A method may carry
    # its own improver -- `methods/noop` carries nothing at all, and the control harness
    # is the measurement. Without this the control recorded the *default* improver on
    # every point: a run in which deepseek was never called still said
    # `identity.improver: deepseek`, which is exactly the "the record names a party that
    # did no work" defect the improver field exists to prevent. Distinct from the
    # unresolvable case below, which is reported as a warning because it is unexpected.
    carries_its_own = (args.improver or "").strip().lower() in ("none", "noop", "null")
    # **These stay `os.environ` writes on purpose: they are an output of the driver,
    # not a read of configuration.** The CLI flag has to reach the *method's own
    # subprocess*, which is a different process that reads `HG_IMPROVER*` from its
    # inherited environment (via `harnessgrad.methods._method_env`). `harnessgrad.config`
    # is a frozen snapshot of this process's configuration and cannot be the transport.
    if carries_its_own:
        os.environ.pop("HG_IMPROVER_NAME", None)
    # The request travels through the environment, because that is how the improver
    # itself is configured (`HG_IMPROVER*` are what the CLI improver reads) -- resolving
    # and executing must not disagree about which one was chosen.
    elif args.improver:
        os.environ["HG_IMPROVER_NAME"] = args.improver
    if args.improver_model:
        os.environ["HG_IMPROVER_MODEL"] = args.improver_model
    if args.improver_effort:
        os.environ["HG_IMPROVER_EFFORT"] = args.improver_effort
    if carries_its_own:
        console.note("improver none  ← the method carries its own; nothing recorded")
    else:
        try:
            improver = resolve_improver(load_improvers(), version="",
                                        requested=args.improver,
                                        model_override=args.improver_model)
        except Exception as exc:                                # noqa: BLE001
            improver = {"ok": False,
                        "reason": f"could not read the improver config: {exc}"}
    if improver and improver.get("ok"):
        console.note(
            f"improver {improver['name']} {improver['version']}"
            + (f" · model {improver['model']}" if improver.get("model") else "")
            + f" · sha {improver['sha256'][:12]}"
            + ("  ← declared MODIFIED" if improver.get("modified") else ""))
    elif improver:
        console.note(
            f"no improver resolved ({improver.get('reason')}); a method that carries its "
            f"own improver still runs, but this run carries no improver identity",
            level="warn")
        improver = {}

    # **A CLI improver needs a model that emits standard tool_calls, and that is a gate --
    # not a convention.** Measured (2026-10-04): codex pointed at the local 9B connected,
    # spent 8,699 tokens and did nothing, because the endpoint returned the model's call as
    # `<tool_call><function=...>` **text** and codex needs structured calls on
    # `wire_api = "responses"`. The requirement had been raised before and was written down
    # nowhere (`grep -rn tool_call docs/ INTERFACE.md` was empty), so it kept being
    # rediscovered instead of enforced. `tools/probe_tool_calls.py` is the check.
    #
    # What it can and cannot decide: a **custom** provider in the CLI's own config names a
    # `base_url`, so that endpoint can be probed and refused. A hosted provider's endpoint
    # is the CLI's built-in and cannot be reached from here -- then the result is recorded
    # as *unprobed*, which is a fact rather than a silent pass.
    # `HG_IMPROVER_SKIP_TOOL_GATE=1` overrides, for an operator who knows an endpoint's
    # tool support better than a one-shot probe does.
    improver_tool_calls: bool | None = None
    improver_tool_calls_detail = ""
    if improver.get("ok") and improver.get("kind") == "cli":
        base_url = _cli_provider_base_url(improver)
        if base_url:
            if platform_config.current().improver.skip_tool_gate == "1":
                improver_tool_calls_detail = "gate skipped by HG_IMPROVER_SKIP_TOOL_GATE"
                console.note("improver tool-call gate skipped by request", level="warn")
            else:
                improver_tool_calls, improver_tool_calls_detail = _probe_tool_calls(
                    base_url, improver.get("model") or "")
                if not improver_tool_calls:
                    console.note(
                        "refusing to run: the improver "
                        f"{improver.get('name')!r} would use model "
                        f"{improver.get('model')!r} at {base_url}, and that endpoint does "
                        f"not return structured tool_calls "
                        f"({improver_tool_calls_detail}).\n"
                        "  A CLI improver edits files by calling tools; a model whose calls "
                        "come back as text cannot drive it, and the run would spend its "
                        "budget producing nothing.\n"
                        "  Fix the endpoint (server-side parsing: `--enable-auto-tool-choice "
                        "--tool-call-parser <name>`), or set HG_IMPROVER_SKIP_TOOL_GATE=1 "
                        "to run anyway.",
                        level="error")
                    return 2
        else:
            improver_tool_calls_detail = ("the CLI's provider is built in, so its endpoint "
                                          "cannot be probed from here")
        console.note(
            "improver tool calls: "
            + ("yes" if improver_tool_calls else
               ("no" if improver_tool_calls is False else "unprobed"))
            + f" ({improver_tool_calls_detail[:90]})")

    # **The capability check goes first, and the order is the whole point.**
    #
    # Both of these refuse before anything is paid for, so either order is "correct" in
    # the sense that nothing runs. They are not equivalent to a reader: a harness that
    # declares it cannot operate on this environment is **unfixable by importing
    # anything**, while a missing image is fixed by one command. Reported the other way
    # round -- which is what happened, because the image resolution was moved up here
    # for `run_meta.json`'s sake and the gate was left behind -- a user whose harness
    # could never work was told to import an image, imported it, came back, and was told
    # to import the next one. Measured: two UI runs, two different missing images, and
    # the real reason (the harness declares `files` only) never appeared.
    #
    # Read from `harness_src` rather than the staged `work`, because staging happens
    # later and nothing has copied anything yet. The two are the same bytes at this
    # point: `stage_workspace` is a plain copy.
    # A harness that declares dependencies, running where none were provided.
    #
    # A warning rather than a refusal, because it is not always a problem: a `files`
    # dataset runs the harness on the platform's interpreter, which may already have
    # them. What it must not be is silent -- measured, `loop` on Terminal-Bench died on
    # its first model call and the run reported nothing but `score 0.000`, which reads
    # exactly like a method that decided not to edit.
    _warn_missing_overlay(harness_src, envs, args.dataset)

    man_at_door = manifest(harness_src)
    problem = _check_env_kinds(man_at_door, _required_env_kinds(envs))
    if problem:
        console.note(f"refusing to run: {problem}", level="error")
        return 2

    # Pin the images here, before anything describes the run. The first version of this
    # resolved them further down, next to the `env_kinds` gate -- and the header and
    # `run_meta.json` were both written before it, so they reported `image_digest: null`
    # and named the image's *tag* while every curve point carried the digest. Two records
    # of one run disagreeing about what it ran in is the exact failure §2.5.4 exists to
    # prevent.
    envs, env_problem = _resolve_envs(envs)
    if env_problem:
        console.note(f"refusing to run: {env_problem}", level="error")
        return 2

    # The container boundary is built once, at the door, for the same reason the bwrap
    # one is: a boundary that cannot be built must stop the run before it is paid for,
    # and must never degrade into running on the host.
    if "exec" in _required_env_kinds(envs):
        version = container.available()
        if version is None:
            console.note(
                "refusing to run: this dataset needs an `exec` environment but no "
                "docker daemon is reachable. Starting the run anyway would either "
                "score zero on every task or fall back to the host, and both look "
                "like a weak harness.", level="error")
            return 2
        for image in sorted({spec["image"] for spec in envs.values()
                             if spec.get("kind") == "exec"}):
            # `PLATFORM_ROOT`, not `PLATFORM`: the latter is the tuple of names that
            # makes up the platform, and passing it here crashed every `exec` run with
            # a `tuple / str` TypeError before a single container started.
            check = container.self_check(PLATFORM_ROOT, image)
            if not check["ok"]:
                console.note(f"refusing to run: container boundary for {image!r} "
                             f"does not hold: {check['detail']}", level="error")
                return 2
            console.note(f"container boundary holds (docker {version}): "
                         f"{check['detail']}", level="info")

    if split is None:
        console.note(
            f"dataset {args.dataset!r} declares no train/eval split: every task is "
            f"scored and the method is shown traces for all of them. The run is "
            f"still measured, but a score from it cannot support a claim about "
            f"generalisation -- the method studied its own exam. Pass `--split default` "
            f"for the platform's documented division, `--split official` to score the "
            f"benchmark's own evaluation set, or `--split <file.json>` to supply one.",
            level="warn")

    # The harness's workspace is deliberately NOT under the platform's own tree.
    # It used to be `runs/<id>/workspace`, which put it two `..` from the driver,
    # the scorer and `.env` -- measured: a harness could read all three and write
    # anywhere in the platform directory. A subprocess cannot walk out of a tree it
    # was never placed inside, so this is the half of isolation that actually holds
    # without a container. The run record stays under `runs/`; only the working
    # tree moves.
    #
    # Checked before anything is created, so a refusal leaves no partial state
    # inside the platform for the next run to trip over.
    work = (Path(args.work_root) / run_id / "workspace").resolve()
    if not workspace_is_outside_platform(work, PLATFORM_ROOT):
        print(f"refusing to run: the workspace {work} is inside the platform "
              f"({PLATFORM_ROOT}). A harness there can read the driver and .env "
              f"and write anywhere in the platform. Pass --work-root outside it.",
              file=sys.stderr)
        return 2

    if work.exists():
        if staged:
            print(f"refusing staged policy: workspace {work} already exists; "
                  "preserve incomplete evidence and use a fresh run ID", file=sys.stderr)
            return 2
        shutil.rmtree(work)
    try:
        # Reserve the staged workspace identity too, including incomplete parent
        # directories. Exclusive creation also prevents two runs sharing it.
        work.parent.mkdir(parents=True, exist_ok=not staged)
    except FileExistsError:
        print(f"refusing staged policy: workspace directory {work.parent} already exists; "
              "preserve incomplete evidence and use a fresh run ID", file=sys.stderr)
        return 2
    curve_path = run_dir / "curve.jsonl"

    # The sandbox is verified before the first paid call, not once per task. A
    # namespace that cannot be built stops the run at the door; the alternative is
    # a run that quietly measured something other than what its metadata claims.
    sandbox_state = {"requested": args.sandbox, "active": False,
                     "plan_version": sandbox.PLAN_VERSION}
    if args.sandbox:
        report = sandbox.self_check(PLATFORM_ROOT, work.parent.parent)
        if not report["ok"]:
            console.note(f"refusing to run: {report['detail']}", level="error")
            return 2
        sandbox_state["active"] = True
        sandbox_state["detail"] = report["detail"]
        sandbox_line = "bwrap active — the harness cannot see this directory"
    else:
        sandbox_line = ("OFF — the harness can read the driver and .env; only the "
                        "hash check stands between it and the scorer")
        console.note(
            "running with --no-sandbox: the harness can read the platform and "
            ".env, and only the hash check stands between it and the scorer",
            level="warn")

    # A run whose tasks are in containers must not describe itself as a bwrap run.
    # The two are different boundaries with different properties, and the header is
    # the one line a reader takes the run's conditions from -- this exact class of
    # misdescription is what `agent_model` already cost once.
    env_kinds = _required_env_kinds(envs)
    if "exec" in env_kinds:
        images = sorted({str(spec.get("image_digest") or spec.get("image"))
                         for spec in envs.values() if spec.get("kind") == "exec"})
        where = f"exec — {len(images)} image digest(s)" if len(images) > 1 \
            else f"exec — {images[0][:26] if images else 'unresolved'}"
        svc_names = sorted({svc["name"] for spec in envs.values()
                            for svc in (spec.get("services") or [])})
        if svc_names:
            # Named, not counted: "a mock API was reachable" and "a database was
            # reachable" are different tasks, and the header is where a reader takes
            # the run's conditions from (§2.5.4).
            where += f" + services[{', '.join(svc_names)}]"
        # `args.sandbox`, **not** `sandbox`: the bare name in this module is
        # `import eval.containers.sandbox as sandbox`, so a truthiness test on it tests a *module
        # object* and is always true. Measured: this condition silently never fired, so
        # the header named the container boundary and left out that the host-side one
        # was off. It is the same shadowing that produced `'bool' object has no
        # attribute 'SandboxUnavailable'` in Step 3, which is why it is spelled out
        # here instead of left to look obvious.
        if not args.sandbox:
            # `--no-sandbox` does not disable the container: the container *is* the
            # task's environment (INTERFACE.md §2.5.5), so removing it would not make
            # the task run unisolated, it would make the task not run at all. Saying
            # so beats a line that reads as "no boundary anywhere".
            where += " (host-side bwrap OFF for the method)"
        sandbox_line = where

    # `run_one` needs the work root to know which tree to leave visible. Exported
    # rather than passed through every layer: it is a property of this run, and the
    # runner is the only reader. Kept as an environment *write*: the value is produced
    # by this run (below), not read as configuration, and it must reach subprocesses.
    os.environ["HARNESSGRAD_WORK_ROOT"] = str(work.parent.parent)
    # The environment's seed is a run-level fact, and it has to exist before any
    # environment is described. `secrets` rather than `random`: this seeds an
    # environment a method may be trying to fit to, and a predictable seed is a
    # predictable environment. A write for the same reason as the work root above.
    os.environ["HARNESSGRAD_SEED"] = args.seed or secrets.token_hex(8)
    if any(services_mod.requires_model(spec.get("services") or [])
           for spec in envs.values()):
        # An environment-side model is a third budget. Refused when a service needs one
        # and none is configured, rather than passed as an empty string: a simulator
        # that silently has no model produces a task that fails for a reason nothing
        # records, and §2.5.7 requires `env.model` to be non-null exactly when a
        # service declared it needs one.
        if not platform_config.current().environment.model:
            console.note(
                "refusing to run: a service declares needs_model but HG_ENV_MODEL is "
                "unset. The environment's model is its own budget -- configure it in "
                ".env (HG_ENV_MODEL, HG_ENV_API_KEY) rather than reusing the agent's.",
                level="error")
            return 2

    # **Same decision as `fresh=True` below, applied to docker objects.** A killed run
    # leaves its per-task networks (and any service container) behind, and the next run
    # under the same id then dies of the *previous* crash: `docker network create ...
    # already exists`. Reusing an id means "start clean", so say it once here.
    # Staged IDs never resume. A fresh receipt directory does not establish
    # ownership of leftover backend objects with the same ID under another root.
    leftovers = services_mod.reap(run_id) if not staged else []
    if leftovers:
        console.note("could not remove everything a previous run left behind:\n  "
                     + "\n  ".join(leftovers[:6]), level="warn")

    web_declarations = [spec.get("web") for spec in envs.values() if spec.get("web")]
    web_record = {}
    if web_declarations:
        web_search, web_fetch, web_endpoint = web_gateway_mod.selected_providers()
        web_record = {"web_search_provider": web_search,
                      "web_fetch_provider": web_fetch,
                      "web_searxng_url": web_endpoint}

    _write_run_meta(
        run_dir, run_id=run_id, mode=mode, harness=args.harness,
        dataset=args.dataset, sampling_policy=args.sampling,
        enforcement=enforcement,
        diagnostic_policy=({"receipt_schema": diagnostic_policy.SCHEMA,
                            "eligible_tasks": len(set(eligible_ids)),
                            "eligible_task_ids_sha256": diagnostic_policy.eligible_digest(eligible_ids),
                            "selected_tasks": len(side_ids),
                            "selected_task_ids": list(side_ids),
                            "expected_trials": len(side_ids), "trials": 1, "jobs": 1,
                            "evidence_sha256": diagnostic_policy.receipt_digest(policy_evidence),
                            "context": policy_evidence["context"]} if staged else None),
        workspace=str(work), work_root=str(work.parent.parent),
        platform_root=str(PLATFORM_ROOT),
        sandbox=sandbox_state,
        # **The effective configuration of this run, recorded in one block.** The
        # environment half is the declared snapshot (`harnessgrad/config`); the argv
        # half enters as `run=`, so "what was in force?" is answered from the run
        # directory alone. Credentials are recorded as present/absent and never by
        # value -- `dump()` redacts every secret field.
        config=platform_config.dump(
            run=platform_config.run_config(
                args, run_id, seed=os.environ.get("HARNESSGRAD_SEED"), mode=mode)),
        budget=budget_binding.metadata() if budget_binding is not None else None,
        # The run-level view of §2.5.4, beside the per-point one. With no task ids this
        # falls back to every distinct environment the dataset declares, which is the
        # right granularity for a run: a reader should be able to tell what kind of
        # world this run measured in without reading the curve.
        env=_env_record(envs, []),
        # The resolved split, not the selected tasks: a reader of the run record
        # needs to know what the two sides were, before any sampling policy narrowed
        # it -- and, now that a run may supply its own division, *which* division this
        # was. `split_sha` is the part a comparison actually needs: two points belong
        # on one axis only when it matches.
        split=split,
        split_declared=split is not None,
        split_source=split_source,
        split_sha=datasets.split_sha(split) if split else None,
        # **What this run is a measurement of, and of which platform.** `evaluated_from`
        # is what an interrupted eval run needs to verify a `--resume` target: today the
        # join lives only on the finished point, so a run killed at 147/200 recorded
        # nowhere which state it was measuring. `platform_content_sha` is the same idea
        # one level up: continuing a run after the platform changed would stitch two
        # code versions into one curve, and this is the field that lets a resume refuse.
        evaluated_from=({"run_id": args.from_run, "round": args.from_round}
                        if getattr(args, "from_run", None) else None),
        platform_content_sha=_platform_content_sha(),
        # **Where the oracle's reference artifact came from.** Gate 1 is the oracle
        # control, and "the benchmark's own reference scores 1 here" and "a reference the
        # platform wrote scores 1 here" are different claims about the check -- so the run
        # records which, together with how much of the pool an authored reference covers.
        # `None` is a refused dataset (no oracle at all); the lint refuses silence from a
        # runnable one (`data/dataset.schema.json:module.oracle_about`).
        oracle_source=oracle_source,
        oracle_coverage=oracle_coverage,
        **web_record,
        agent_model=model["model"] or None,
        agent_backend=model["backend"],
        framework_version=FRAMEWORK_VERSION,
        method_entrypoint=args.method_entrypoint,
        # **The improver the run resolved, not the one a reader assumes from the
        # method's name.** A curve point already carries it, and this is the run-level
        # twin: without it, answering "which improver did this run use?" meant opening a
        # curve point -- and when the field was absent entirely, the answer was
        # `improver: null` for a run that had used one.
        improver=improver or None,
        # A run-level fact about the *improver's* endpoint, probed once and recorded
        # beside the improver's identity. `None` is "not probed" (a hosted provider),
        # never "false" -- the distinction the whole platform keeps making.
        improver_tool_calls=improver_tool_calls,
        improver_tool_calls_detail=(improver_tool_calls_detail[:300] or None),
        # The two measurement parameters, on the run itself: `n_trials` rides on each
        # curve point, but "how many tasks at once" is not a property of a point -- it is
        # a property of the run that produced all of them, and a reader comparing two
        # runs needs to know whether one was under four-way contention.
        trials=args.trials, jobs=args.jobs,
        # The run's first write: drop anything a previous run left under this id.
        fresh=True,
    )

    stage_workspace(harness_src, work)
    man = manifest(work)
    # `_check_env_kinds` already ran, against this same manifest read from the source --
    # see the note beside `envs, env_problem = _resolve_envs(envs)` for why it has to be
    # the first refusal rather than this one.

    # A containerised harness whose model is not on this host needs the internet to
    # reach it, and a task that did not ask for the internet must not silently get it --
    # that would change what the task *is* (§2.5.4). Refused rather than attempted: the
    # harness would have no model, so every score would be a zero that means nothing,
    # and it would look like a weak harness.
    if "exec" in _required_env_kinds(envs) \
            and platform_config.current().agent.backend != "mock":
        base_url = platform_config.current().agent.base_url or ""
        if base_url and not modelgate.is_host_local(base_url):
            offline = sorted(tid for tid, spec in envs.items()
                             if spec.get("kind") == "exec"
                             and spec.get("network") != "bridge")
            if offline:
                console.note(
                    f"refusing to run: the agent's model is at {base_url!r}, which is "
                    f"not on this host, so a containerised harness needs the internet "
                    f"to reach it. Task(s) {offline} are 'exec' with network != "
                    f"'bridge', so the harness would have no model at all. Either set "
                    f"network='bridge' and accept that the task is a networked task, or "
                    f"point HG_AGENT_BASE_URL at a model on this host.", level="error")
                return 2

    console.header(
        run_id=run_id, harness=man["name"], version=man["version"], mode=mode,
        dataset=args.dataset, n_tasks=len(tasks_all),
        model=model["model"], backend=model["backend"], live=model["live"],
        sandbox=sandbox_line, split=split, argv=sys.argv, side=args.side)

    if args.side == "eval":
        result = _run_eval_side(
            work=work, man=man, run_id=run_id, run_dir=run_dir, tasks=tasks_all,
            scorable=scorable, setups=setups, verifiers=verifiers, envs=envs,
            side_ids=side_ids, split=split, args=args, eval_from=eval_from,
            improver=improver, after_evaluation=save_policy_receipt)
        if budget_binding is not None:
            _write_run_meta(run_dir, budget=budget_binding.metadata())
        return result

    cache: dict = {}
    # `env_generation_tokens` is its own axis rather than part of `generation_tokens`:
    # the x-axis is compute, and an environment that spends on every task adds a
    # constant per round. Adding it to the *method's* number would move the axis by an
    # amount the method did not spend (INTERFACE.md §2.5.7).
    cumulative = {"evaluation_trials": 0, "generation_tokens": 0,
                  "env_generation_tokens": 0, "wall_clock_s": 0.0}

    # Staged runs already fixed their task set; score-based policies select after H0.
    # Committed **before** the measurement, so the measurement can be cached under the
    # content it actually measured.
    #
    # The cache key used to be the literal string `"H0"`, while every later round keyed on
    # the content sha -- so a candidate that was byte-identical to H0 (a control method,
    # or a method whose candidates were all rejected) missed the cache and paid for a
    # second full measurement of the same tree. Measured on `B2-noop` (2026-10-04): round
    # 1 re-ran all five tasks and scored 0.467 against round 0's 0.267, on the same
    # `harness_sha`, and the second number was read as the control "moving". Which of
    # those two numbers a run reports must not depend on whether a cache key happened to
    # match: content-addressed, an unchanged tree has one measurement, and the spread of
    # a measurement is what `--trials` is for.
    # **A resumed run rebuilds round 0 from the record instead of measuring it.**
    # `--h0-from` shares another run's first score; `--resume` continues *this* run,
    # so the same rebuild applies to its own curve, and no state is measured twice.
    resume_state = None
    if args.resume:
        try:
            resume_state = _resume_train_state(args, run_dir, split, work)
        except ResumeNothingToDo as exc:
            console.note(f"--resume: {exc}", level="info")
            return 0
        except ResumeRefused as exc:
            console.note(f"refusing --resume: {exc}", level="error")
            return 2
        console.note(
            f"resuming {run_id} at round {resume_state['last']}"
            f" ({(resume_state['recorded_sha'] or '')[:12]}); the workspace was re-staged"
            f" from state_after_round{resume_state['last']}", level="info")
    if resume_state is None:
        h0_sha = commit_state(work, "H0")
        t0 = time.time()
        # **The shared round 0.** This measurement is a method's first input, so if every run
        # measures its own, batching non-determinism can hand one method a different number and
        # a different trace set while the record still says they saw the same evidence.
        # `--h0-from RUN` reuses one run's measurement for all of them.
        shared_point = None
        if args.h0_from:
            donor_dir = Path(args.runs_root) / args.h0_from
            payload_path = donor_dir / "h0.json"
            payload = None
            if payload_path.is_file():
                payload = json.loads(payload_path.read_text())
                if payload.get("h0_sha") != h0_sha:
                    print(f"refusing --h0-from {args.h0_from!r}: it measured base "
                          f"{payload.get('h0_sha')!r}, this run's base is {h0_sha!r}",
                          file=sys.stderr)
                    return 2
            donor_point = None
            donor_curve = donor_dir / "curve.jsonl"
            if donor_curve.is_file():
                for line in donor_curve.read_text().splitlines():
                    if line.strip():
                        donor_point = json.loads(line)
                        break
            if donor_point is None:
                print(f"refusing --h0-from {args.h0_from!r}: it has no curve point", file=sys.stderr)
                return 2
            if payload is not None:
                base = payload["base"]
            else:
                # No raw payload: this run predates `h0.json`. Its own base sha is checked
                # against `h0_sha` below -- through the point's identity -- and the number is
                # the same number, so re-measuring it would only spend compute to produce a
                # second value for one measurement.
                if (donor_point.get("identity") or {}).get("harness_sha") != h0_sha:
                    print(f"refusing --h0-from {args.h0_from!r}: its point measured base "
                          f"{(donor_point.get('identity') or {}).get('harness_sha')!r}, this "
                          f"run's base is {h0_sha!r}", file=sys.stderr)
                    return 2
                base = _base_from_point(donor_point)
                console.note(f"{args.h0_from} has no h0.json; rebuilt the measurement from its "
                             f"curve point (same base tree, same number)", level="info")
            # The traces travel with the measurement: a method that reads a trace channel to
            # decide what to edit must see the same traces as every other method.
            donor_traces = donor_dir / "round0_traces"
            if donor_traces.is_dir() and not (run_dir / "round0_traces").exists():
                shutil.copytree(donor_traces, run_dir / "round0_traces")
            # The method's trace channel is staged from the *dict*, not from the directory.
            base["traces"], base["trial_traces"] = _read_trace_bodies(donor_traces)
            # **The identity has two kinds of field, and they belong to different runs.** The
            # measurement facts -- which tree, which agent model, which jobs and budget produced
            # the number -- are the donor's, and copying them is the point. The *run* facts are
            # not: `improver`, `skill_sha` and `method_model` describe who is proposing edges in
            # **this** run, and the platform stages them into the method's `round.json`, which is
            # how a method learns whether it has a CLI improver at all. Copying the donor's
            # `improver: None` (the donor ran with `--improver none`) told all nine methods they
            # had no CLI improver, so each fell back to the chat endpoint with a prompt sized for
            # a CLI and died on the 16k window -- every method, every round 1, on 2026-10-17.
            run_identity = _identity(man, h0_sha, mode, improver=improver,
                                     jobs=args.jobs, trials=args.trials,
                                     gpu_budget=args.gpu_budget)
            merged_identity = dict(donor_point.get("identity") or {})
            for key in ("improver", "skill_sha", "method_model"):
                if key in run_identity:
                    merged_identity[key] = run_identity[key]
                else:
                    merged_identity.pop(key, None)
            shared_point = {**donor_point, "run_id": run_id, "identity": merged_identity,
                            "shared_from": {"run_id": args.h0_from, "path": str(donor_dir),
                                            "h0_sha": h0_sha,
                                            # The donor's own measurement conditions, kept so a
                                            # reader can tell "measured at jobs=13" from "this
                                            # run's jobs".
                                            "measured_with": {
                                                "jobs": (donor_point.get("identity") or {}).get("jobs"),
                                                "gpu_budget": (donor_point.get("identity") or {}).get("gpu_budget")}}}
            console.note(f"round 0 reused from {args.h0_from} — one first score for every method",
                         level="info")
        else:
            base = evaluate(work, tasks_all, scorable, cache, harness_sha=h0_sha,
                            trials=args.trials, jobs=args.jobs, progress_dir=run_dir,
                            sandbox=args.sandbox, setups=setups, verifiers=verifiers,
                            envs=envs, run_id=run_id, run_seed=os.environ["HARNESSGRAD_SEED"],
                            recordings_root=run_dir / "recordings",
                            **({"diagnostic_authorization": diagnostic_authorization}
                               if diagnostic_authorization is not None else {}))
            # Kept so a later run can share *this* measurement instead of repeating it.
            try:
                (run_dir / "h0.json").write_text(
                    json.dumps({"base": base, "h0_sha": h0_sha, "run_id": run_id}, default=str))
            except (TypeError, OSError) as exc:
                console.note(f"could not write h0.json ({exc}); this run cannot be a --h0-from "
                             f"donor", level="warn")
        if enforcement is not None:
            # Preserve raw execution/scorer facts with their comparison prohibition.
            # No aggregate score is published and the curve path below cannot run.
            base["enforcement"] = enforcement
            with (run_dir / "diagnostic_execution.json").open("x") as stream:
                json.dump(base, stream, indent=2)
        complete_measurement = save_policy_receipt(base)
        _require_untampered(base)
        _report_invalid(base, args.dataset)
        _report_harness_failed(base, args.dataset)
        if not complete_measurement:
            console.note("refusing staged curve: lifecycle publication gate or measurement incomplete; "
                         "invalid/unmeasured outcomes are preserved in the receipt",
                         level="error")
            return 2
        if not base["per_task"]:
            # Nothing was measurable, so there is no curve to draw. Refused rather than
            # recorded as a point: `mean([])` is 0.0, and a zero here would say "the
            # harness scored nothing" about a run in which the harness never ran
            # (INTERFACE.md §2.5.7).
            console.note(
                f"refusing to run: none of the {len(tasks_all)} tasks in "
                f"{args.dataset!r} produced a scoreable result. The curve would have to "
                f"report a score for a task set that was never measured.", level="error")
            return 2
        cumulative["evaluation_trials"] += len(tasks_all)
        cumulative["env_generation_tokens"] += (base.get("env_usage") or {}).get(
            "input", 0) + (base.get("env_usage") or {}).get("output", 0)
        cumulative["wall_clock_s"] += time.time() - t0

        # The sampling policy chooses what the run is *scored* on, within the run's own side.
        # Two things about the cost, and the first one is a whole-side sweep:
        #
        # * Score-based policies measure the whole eligible side at round 0. They need scores
        #   to select on, and §2.1 fixes
        #   the selection at H0, so the whole side is paid for exactly once, at round 0.
        # * from round 1 the method is shown only the selected subset (`train_ids` below). To
        #   fix a subset for the *whole* run -- including round 0, which is what a study of "how
        #   many study tasks does a capability need" wants -- pass `--tasks` or a `--split` file;
        #   `--sampling random_N` does not narrow round 0.
        #
        # The comment here used to claim the policy "does not reach into the train side" and that
        # the cost was "all of train, every round". That described the eval-side-only version of
        # this call; both sentences had been false since `within` became the run's own side, and
        # the second one sent a reader looking for a cost that is not paid.
        #
        # `tasks_all` was narrowed to the side before round 0, so the policy selects within it and
        # `within` can never reach across -- the cross-side leak this guards against is not a rule
        # here, it is an absence of the other side.
        if args.sampling in ("diagnostic", "pilot_10pct"):
            task_ids = list(side_ids)
        else:
            task_ids = select_task_set(base["per_task"], args.sampling,
                                       within=(side_ids or None))
        if not task_ids:
            console.note(f"sampling policy {args.sampling!r} selected no tasks on H0",
                         level="error")
            return 2
        # A train run's scored set **is** its diagnostics set: the method is shown the tasks
        # it is scored on, which is what training means and is exactly why the exam has to be
        # a different run. An eval run has no method at all (see the `--side eval` path), so
        # this is only ever populated for `train`.
        train_ids = list(task_ids) if args.side == "train" else []
        tasks = [t for t in tasks_all if t["task_id"] in set(task_ids + train_ids)]
        sampling = {"dataset": args.dataset, "policy": args.sampling, "task_ids": task_ids,
                    "selection_basis_round": 0,
                    "selected_within": args.side if split else "all"}
        if split and len(task_ids) < 8:
            # Not a warning about the platform, a warning about the number the reader is
            # about to see. `task_ids` is what *this run* scores -- on a train run that is
            # the train side, on an eval run the exam -- so the wording has to stay
            # side-neutral. It used to say "eval side is N tasks" while printing the run's
            # own task count: a one-task train run announced "eval side is 1 tasks" about a
            # 37-task exam, which reads as a measurement of the wrong thing.
            #
            # A 2-task run moves in steps of 0.5, and a 0.5 step read as a result is the
            # most expensive mistake this console can help someone avoid.
            console.note(
                f"this run scores {len(task_ids)} tasks — the score moves in steps of "
                f"{1.0 / len(task_ids):.3f}", level="warn")
    else:
        base = resume_state["base"]
        h0_sha = resume_state["h0_sha"]
        shared_point = None
        # **The task set is the one this run already used.** Re-selecting would let a resume
        # quietly measure a different set of tasks under the same curve -- and the original
        # selection is on the record (`sampling.task_ids` of round 0), so there is nothing
        # to re-derive.
        task_ids = list((resume_state["existing"][0].get("sampling") or {}).get("task_ids") or [])
        if not task_ids:
            task_ids = select_task_set(base["per_task"], args.sampling,
                                       within=(side_ids or None))
        if not task_ids:
            console.note("refusing --resume: the run's round 0 recorded no task set",
                         level="error")
            return 2
        train_ids = list(task_ids) if args.side == "train" else []
        tasks = [t for t in tasks_all if t["task_id"] in set(task_ids + train_ids)]
        sampling = {"dataset": args.dataset, "policy": args.sampling, "task_ids": task_ids,
                    "selection_basis_round": 0,
                    "selected_within": args.side if split else "all"}
    # Round 0's state too: "measure H0 on the exam" is the baseline every later eval is
    # read against, and it needs a state to measure exactly like any other round.
    # (`h0_sha` was committed before the measurement, above.)
    if resume_state is None:
        _save_round_state(run_dir, 0, work)
    if resume_state is not None:
        # The points already on disk are the record; a resumed run appends to them.
        curve = list(resume_state["existing"])
    elif shared_point is not None:
        # The donor's point verbatim except for the id: its `identity` describes *how that
        # number was produced* (jobs, budget, model), which is the honest record, and
        # `shared_from` says which run produced it.
        curve = [shared_point]
    else:
        curve = [_curve_point(
            run_id=run_id, round_index=0,
            identity=_identity(man, h0_sha, mode, improver=improver,
                                declared=base.get("identity_declared"),
                                jobs=args.jobs, trials=args.trials,
                                gpu_budget=args.gpu_budget),
            sampling=sampling,
            scores=[base["per_task"][t] for t in _scored(base, task_ids)],
            task_ids=_scored(base, task_ids),
            train_scores=[base["per_task"][t] for t in _scored(base, train_ids)],
            train_ids=_scored(base, train_ids), split=split,
            invalid=base.get("invalid"),
            harness_failed=base.get("harness_failed"),
            model_gateway=base.get("model_gateway"),
            harness_runtime=base.get("harness_runtime"),
            evaluation_protocols=base.get("evaluation_protocols"),
            env_record=_env_record(envs, task_ids),
            cost=_cost_of(base, len(tasks_all), cumulative["wall_clock_s"]),
            cumulative=cumulative, label="H0 (base harness)",
            n_trials=base.get("trials"), score_std=base.get("score_std"),
            per_task_std=base.get("per_task_std"),
            per_task_trials=base.get("per_task_trials"),
            cached_tasks=base.get("cached_tasks"),
            **test_rate_fields(base))]
    # The base harness is always evaluated by the platform, so its point is a
    # platform measurement like any other. Leaving the field unset made the
    # exported record claim the score was the method's -- the opposite of true.
    curve[0]["measured_by_platform"] = True
    # The verifier's *kind*, never its spec. This point is written into the method's
    # channel, so an argv or an expected answer here would hand a method the grade it
    # is being measured against -- the same leak the eval traces are withheld for.
    curve[0]["verifier_kinds"] = verifier_kinds
    console.round_line(0, "H0 (base harness)", curve[0])
    # Written now, not only at the end. A run that is interrupted used to leave the
    # *previous* run's curve.jsonl on disk, so the console showed a curve belonging
    # to a different method than the one in run_meta -- read as "I picked RRSI and
    # it ran DGM". The record has to be durable at the moment it is produced.
    _write_curve(curve_path, curve)
    _save_verdicts(run_dir, 0, base, [t["task_id"] for t in tasks])
    _save_traces(run_dir, 0, base.get("traces") or {}, train_ids, split,
                 trial_traces=base.get("trial_traces"))

    # 在分派之前挡住相对的入口路径。放在 main 里,因为 `_run_mode_a/b` 返回的是
    # 曲线,不是退出码 —— 第一次把检查放进 mode 函数时,`return 2` 让 main 拿一个
    # 整数当曲线去迭代,报的是 "TypeError: 'int' object is not iterable" 而不是
    # 那句本该出现的拒绝理由。
    if args.method_entrypoint:
        problem = _check_method_entrypoint(shlex.split(args.method_entrypoint))
        if problem:
            console.note(f"refusing to run:\n  {problem}", level="error")
            return 2

    # **One engine.** `--mode A` and `--mode B` selected two loops, and the difference
    # they encoded was never a property of the *method*: it was (a) which tree the method
    # may build on -- the incumbent or the pristine base -- and (b) whether it sees a score
    # between the states it submits. Both are decisions a method makes, and both are now on
    # the record: `steps_in_submission > 1` means it saw no score between them, and the
    # request carries the pristine tree as well as the incumbent. So the flag is read as
    # *nothing*: a B-flavoured method is simply one that submits its whole sequence in one
    # call, which the loop already allows.
    curve = _run_mode_a(args, work, man, run_id, mode, curve, h0_sha,
                        base, tasks, task_ids, scorable, sampling, cache,
                        cumulative, train_ids, split, setups, verifiers,
                        verifier_kinds, envs, improver=improver,
                        start_steps=(resume_state["last"] if resume_state else 0),
                        resumed_from=(resume_state["last"] if resume_state else None))

    # **A resumed curve says how it was stitched.** `from_round` and the two content
    # addresses are the whole claim: the state re-staged from the record reproduced the
    # address the earlier points already named, and the platform content matched, so no
    # second platform version entered this curve.
    if resume_state is not None:
        _write_run_meta(run_dir, resumed=[{
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from_round": resume_state["last"],
            "recorded_sha": resume_state["recorded_sha"],
            "staged_sha": resume_state["staged_sha"],
            "platform_content_sha": _platform_content_sha(),
        }])

    if budget_binding is not None:
        _write_run_meta(run_dir, budget=budget_binding.metadata())

    _write_curve(curve_path, curve)
    console.results(curve, split=split)
    console.artifact("curve", curve_path)
    console.artifact("workspace", work)
    console.artifact("events", run_dir / "events.jsonl")
    return 0


def main() -> int:
    attempt = {}
    detail = "driver interrupted before evaluation completed; see command output"
    try:
        result = _main(attempt)
        detail = f"driver exited {result} before evaluation completed; see command output"
        return result
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if "finalize" in attempt:
            attempt["finalize"](detail)


if __name__ == "__main__":
    # A tampered run has no partial value to salvage: the curve it printed would
    # be the attacker's self-assessment. Exit non-zero and say so.
    try:
        sys.exit(main())
    except PlatformTampered:
        sys.exit(3)
