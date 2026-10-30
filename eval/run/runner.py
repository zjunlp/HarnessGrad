"""Execute a harness on a task set. Framework-owned (INTERFACE.md §0)."""
from __future__ import annotations
from harnessgrad import slots

import concurrent.futures as futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import eval.containers.container as container_mod
import eval.containers.harness_runtime as harness_runtime
import eval.service.modelgate as modelgate_mod
from eval.service import container_observation as container_observation_mod
from eval.service.native_model_route import NativeModelRoute
import eval.observe.progress as progress_mod
import eval.containers.sandbox as sandbox_mod
from eval.containers.process import run as run_process
import eval.service.services as services_mod
from eval.service.unix_stepwise_relay import StepwiseRelay
from eval.run import tool_relay as tool_relay_mod
from eval.run import chess_stepwise, trial_evidence, model_failure
import tools.web_gateway as web_gateway_mod
from eval.ckpt.git_state import STATE_DIRNAME
from eval.run.integrity import PLATFORM_ROOT, diff, snapshot
# The three layers this module used to contain, now separate modules. `runner` decides
# what a round is and what a curve point says; `tracing` reads what a run left behind,
# `verifier` decides what a task's score is, and `container_state` is the long path for
# a task whose state is the container's own filesystem.
from eval.containers.container_state import _run_one_container_state
from eval.run.task_contract import runtime_task
from eval.policy.runtime_policy import admit_native_network, phase_timeout, stage_agent_runtime
from eval.observe.tracing import (_last_identity, _last_usage, _materialize,
                                  _write_progress, process_outcome)
from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, projections, targets
from eval.verification.verifier import _verify, test_rate_of
from eval.verification.isolation import common_class, observed_class, declared_class
from eval.verification.pristine import enforce_verdict_binding
from eval.policy.unobserved_diagnostic import Authorization


def _refuse_unobserved_policies(task: dict, env: dict | None,
                               verifier: dict | None, *, run_id: str = "",
                               diagnostic_authorization: Authorization | None = None,
                               observation: dict | None = None) -> dict | None:
    """Refuse a declared policy when, and only when, this process does not apply it.

    Phase deadlines and native files network isolation are applied by the platform
    itself. Exec/container network and resource/state policies depend on an external
    backend; declarations the native path does not implement also remain refused.
    The offline runner_admission checks cannot discover enforcement. Launch flags,
    adapter observations and cached verdicts are not a substitute for that observer.
    An operator may authorize one explicitly unmeasured diagnostic attempt. That
    permission is never treated as backend observation or production admission.

    `observation` is that backend observation, when the caller has one: a receipt from
    `eval.service.container_observation` for *this* environment, whose `ok` is True and whose
    `covers` names the policy fields it measured back from Docker and from the kernel. It
    admits only the fields it actually covers and only when every declared field is covered;
    anything left over is still refused. Omitting it keeps today's behaviour exactly.
    """
    env_spec = env or {}
    fields = {"env." + key for key in (
        "cpus", "memory_mb",
        "resources", "filesystems", "resource_policy", "network_policy", "artifact_policy",
    ) if key in env_spec}
    # Even direct callers that omit normalized fields request these default bounds
    # by choosing exec. The registry normally fills them before reaching the runner.
    if env_spec.get("kind") == "exec":
        fields.add("env.exec_defaults")
    if env_spec.get("state") == "container":
        fields.add("env.state=container")
    if "network" in env_spec and (env_spec.get("kind") == "exec"
                                 or env_spec.get("state") == "container"):
        fields.add("env.network")
    # env.agent_timeout_s, env.verify_timeout_s and verifier.timeout_s flow through
    # phase_timeout to the platform's subprocess deadlines. Native files network
    # isolation is built here through sandbox.command's --unshare-net; unsupported
    # native network values and unsandboxed isolation are refused by the runtime.
    # Neither needs an external backend observation. The remaining declarations
    # either depend on that backend or have no native implementation at all.
    if "artifact_policy" in (verifier or {}):
        fields.add("verifier.artifact_policy")
    if not fields:
        return None
    covered = set()
    if observation is not None and observation.get("ok") is True:
        covered = set(observation.get("covers") or ()) & fields
    remaining = fields - covered
    if observation is not None and observation.get("ok") is True and not remaining:
        # Every declared bound was read back from the backend and the kernel. That is the
        # observation this refusal exists to demand, so there is nothing left to refuse.
        return None
    if diagnostic_authorization is not None:
        # Still refuse every ordinary run, pilot, unbound task/run and cached
        # measurement. Only the bound one-task diagnostic may attempt execution;
        # its caller records non-observation and forbids comparable publication.
        diagnostic_authorization.require_scope(run_id, [task["task_id"]])
        return None
    result = _invalid(task, "backend_observation_unavailable", stage="admission", service=None)
    receipt = {
        "schema": "harnessgrad.runner-policy-refusal.v1", "stage": "admission",
        "status": "blocked", "reason": "backend_observation_unavailable",
        "measured": False, "production_authorized": False, "observation": "unknown",
        "policy_fields": sorted(remaining if covered else fields),
    }
    if observation is not None:
        # Keep the failed observation with the refusal: "we looked and it did not match" is a
        # different finding from "nobody looked", and only one of them is worth retrying.
        receipt["observation"] = "failed" if not observation.get("ok") else "partial"
        receipt["observation_receipt"] = observation
    result["invalid"]["receipt"] = receipt
    return result


#: The observation receipts this process has taken, keyed by the container shape each one
#: measured. **Module-level, because a probe costs about 1.3 s and a run is one process**:
#: a per-round cache would re-probe on every round of a twenty-round run for a value that
#: cannot change while the daemon and the image do not.
_OBSERVATIONS: dict[tuple, dict | None] = {}


def _environment_shape(env: dict) -> dict | None:
    """The container `_run_one` gives the harness, or `None` when it cannot be described.

    Every field comes from the **launcher**, never from the declaration, because the
    receipt is only worth something as evidence about the container the harness really ran
    in:

    * the image is `env["image"]`, which `container_mod.run` starts;
    * the bounds are `container_mod.envelope(container_mod.limits_from(env))` -- the same
      envelope the launcher passes -- **not** the raw declaration. Until `container_mod.run`
      took a `limits` keyword it forwarded none (signature at `container.py:607-615`, the
      `command()` call at `:638`), so a `cpus: 4` environment ran at `--cpus 2`; the receipt
      has to describe the flags the container gets, so it is derived from them and
      `_declared_but_not_applied` checks the two against each other;
    * `read_only` is that launcher's own default (it passes no `read_only`), which is what
      decides whether the rootfs in the receipt is the one the harness saw;
    * the network is the declared **policy word** (`none`/`services`/`bridge`) -- which is
      what `_run_one` hands the launcher once it has resolved `services` to a per-task
      internal network. The probe resolves it the same way
      (`eval/service/container_observation.py:observe_for`).
    """
    # Only the launcher this module can describe. `state: "container"`
    # (`_run_one_container_state`) has its own envelope, a writable rootfs, an
    # always-internal network and an optional second `bridge` connection, so a probe of
    # the launch below would put a `NetworkMode` in the receipt that that container does
    # not have. Its receipt is a separate change, not a guess; it keeps today's refusal.
    if env.get("state", "mount") != "mount":
        return None
    if not env.get("image"):
        return None
    limits = container_mod.envelope(container_mod.limits_from(env))
    return {"image": env["image"], "cpus": limits["cpus"], "memory_mb": limits["memory_mb"],
            "pids": limits["pids"], "read_only": True,
            "network": env.get("network", "none")}


def _declared_but_not_applied(env: dict, shape: dict) -> str | None:
    """Why the environment's declared bounds are not the launcher's, or `None`.

    A **tripwire**, not the mechanism: with the declaration now forwarded to
    `container_mod.run` this cannot fire for a launcher that was given it, and it is kept
    because the defect it catches is silent by construction -- a launch that drops the
    envelope produces a container whose receipt is about different numbers, and `ok: True`
    on the wrong container is worse than a refusal. One path can still trip it, and only
    because the declaration cannot be expressed: `limits_from` truncates `memory_mb` with
    `int()`, so a fractional value (4096.5) is applied as 4096 and the task keeps its
    refusal with this reason rather than a receipt for a bound it did not get.
    """
    for field, default in (("cpus", 2), ("memory_mb", 4096)):
        declared, applied = float(env.get(field, default)), float(shape[field])
        if abs(declared - applied) > 1e-9:
            return (f"the environment declares {field}={declared:g} but the launcher "
                    f"applies {field}={applied:g}; the declaration did not survive the "
                    f"trip through `limits_from` to the container, and a receipt for the "
                    f"declared number would describe a container the score does not come "
                    f"from")
    return None


def _observations_for(envs: dict | None, tasks: list[dict], *,
                      run_id: str = "") -> dict[str, dict]:
    """One backend observation per distinct container shape, for the tasks that use it.

    Admission refuses `env.cpus`/`env.memory_mb`/`env.exec_defaults`/`env.network` unless
    the caller shows a receipt from `eval/service/container_observation.py` whose `covers`
    names every declared field. Nothing in production supplied one, which is why AppWorld
    stopped at `backend_observation_unavailable` (2026-10-18) instead of running.

    A probe costs about 1.3 s, so it is taken once per distinct `(image, cpus, memory_mb,
    pids, read_only, network)` and cached for the process; a 585-task set must not pay it
    585 times. Failures are cached as `None` for the same reason: one broken daemon must
    not become 585 identical waits before a refusal that was already decided.
    """
    observations: dict[str, dict] = {}
    said: set[str] = set()
    for task in tasks:
        task_id = task["task_id"]
        env = (envs or {}).get(task_id) or {}
        if env.get("kind") != "exec":
            continue
        if not (env.get("services") or []):
            # The refusal that does not change: a plain `exec` environment's declared
            # bounds are still unobserved. Only the declaration whose missing observation
            # blocked the services contract (AppWorld, 2026-10-18) is probed here;
            # widening the receipt to every exec environment would admit datasets nobody
            # has measured, which is a separate change with its own review.
            continue
        shape = _environment_shape(env)
        if shape is None:
            # Once per run, not once per task: a property of the dataset's state kind, and
            # 585 copies of one sentence would bury the rest of the log.
            if "state" not in said:
                said.add("state")
                print(f"  admission: environments with state {env.get('state')!r} keep "
                      f"today's refusal in this commit -- their launcher is a different "
                      f"container shape -- so nothing is probed for them",
                      file=sys.stderr)
            continue
        key = tuple(shape[field] for field in
                    ("image", "cpus", "memory_mb", "pids", "read_only", "network"))
        if key not in _OBSERVATIONS:
            problem = _declared_but_not_applied(env, shape)
            if problem is not None:
                _OBSERVATIONS[key] = None
                print(f"  admission: no observation for {shape['image']}: {problem}",
                      file=sys.stderr)
            else:
                try:
                    _OBSERVATIONS[key] = container_observation_mod.observe_for(
                        shape["image"],
                        {"cpus": shape["cpus"], "memory_mb": shape["memory_mb"]},
                        network=shape["network"], run_id=run_id,
                        read_only=shape["read_only"]).receipt()
                except (container_observation_mod.ObservationError, OSError,
                        subprocess.TimeoutExpired) as exc:
                    _OBSERVATIONS[key] = None
                    print(f"  admission: the observation probe could not run for "
                          f"{shape['image']} ({type(exc).__name__}: {exc}); nothing was "
                          f"observed, so every task that needs it stays refused",
                          file=sys.stderr)
        if _OBSERVATIONS[key] is not None:
            observations[task_id] = _OBSERVATIONS[key]
    return observations


def harness_env() -> dict:
    """The environment a harness runs in: the agent's model, and not the method's.

    The two are configured separately on purpose (`.env.example`), and separate
    configuration is not separation until something enforces it. Without this the
    harness inherits `HG_METHOD_API_KEY` and can spend the method's quota, or be
    pointed at the method's endpoint -- so a harness that misbehaves under
    measurement would look like a harness problem when it is a plumbing one.

    The reverse direction is deliberately left open: a method is allowed to see how
    the harness will be run, because it is being asked to reason about the harness.
    One-way isolation is the useful half, and it is the half that keeps the two
    budgets from being confused for each other.

    `HG_ENV_*` is stripped here for the same reason, and it closed a real leak: this
    function forwards everything that is not the *method's*, so the moment a third
    budget existed for the environment, a `files` task's harness inherited the
    environment's credential. Services only exist on `exec` -- where
    `container_env`'s allowlist already excluded it -- so the leak would have been on
    the path with no services at all, which is exactly where nobody looks for one.

    **A fourth name, and the reason the rule is no longer only a prefix list.** The
    improver registry declares each improver's credential variable (`api_key_env`), and
    the `deepseek` entry names `DEEPSEEK_API_KEY` -- which matches none of the prefixes
    above, so the harness inherited the *improver's* key. Measured: it took making
    `deepseek` the default improver (so the name had to exist in `.env`) for the
    isolation probe to see it. Both earlier leaks were a new budget with a new prefix;
    this one was the same budget under a name the prefix rule could not know. So the
    variables the registry declares are stripped by name, and a new improver is covered
    the moment it is added rather than when someone remembers the prefix.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HG_METHOD_", "HG_ENV_", "HG_JUDGE_", "HARNESSGRAD_SEED"))
           and k not in _improver_credentials()}
    return env


def _improver_credentials() -> set[str]:
    """Every environment variable the improver registry declares as a credential.

    Read lazily and defensively: the harness environment must not fail to build because a
    config file is missing, and `harness_env()` is called once per task on paths that have
    nothing to do with improvers. The registry is read through `tools/improver.py`'s own
    loader so there is one reader of that file, not two.
    """
    try:
        from tools.improver import load_config
        config = load_config() or {}
    except Exception:                                          # noqa: BLE001
        return set()
    names = {(entry or {}).get("api_key_env")
             for entry in (config.get("improvers") or {}).values()}
    return {name for name in names if name}









def run_one(repo: Path, task: dict, timeout_s: int | None = None,
            sandbox: bool = True, setup: dict | None = None,
            verifier: dict | None = None,
            verify_timeout_s: int | None = None,
            env: dict | None = None,
            run_id: str = "", run_seed: str = "",
            recordings_root: Path | None = None,
            diagnostic_authorization: Authorization | None = None,
            trial_id: str | None = None,
            observation: dict | None = None,
            _worker: dict | None = None,
            _private_paths: tuple[Path, ...] = ()) -> dict:
    evidence_output = None
    if model_failure.enabled(verifier):
        model_failure.admit(sandbox=sandbox, setup=setup, env=env)
    capture_evidence = trial_evidence.enabled(verifier)
    if capture_evidence or _private_paths:
        trial_evidence.admit_execution(sandbox=sandbox, setup=setup, env=env)
        # Host collection has the same private boundary as actor execution,
        # including tasks that do not themselves write an archive.
        evidence_output = {'launch_attempted': False, 'trace_status': 'not_started'}
    if diagnostic_authorization is not None:
        diagnostic_authorization.require_scope(run_id, [task["task_id"]])
    refusal = _refuse_unobserved_policies(
        task, env, verifier, run_id=run_id,
        diagnostic_authorization=diagnostic_authorization, observation=observation)
    # **Both branches of this merge are kept, and neither is optional.** Ours is the admission
    # receipt (`appworld-observe`): a container observation that admits only the policy fields
    # it actually measured back. Theirs is the cooperative/worker execution path (`workers` in
    # a setup) and its runtime receipt. Resolving by dropping either would have silently
    # removed a capability -- one an AppWorld run needs to be admitted at all, the other a
    # multi-peer evaluation that only a dataset declaring `workers` can reach.
    if (setup or {}).get("workers") is not None and refusal is None:
        from eval.run.cooperative import run
        cooperative = run(repo, task, setup=setup, verifier=verifier, env=env,
                          timeout_s=timeout_s, verify_timeout_s=verify_timeout_s,
                          sandbox=sandbox, run_id=run_id, run_seed=run_seed,
                          trial_id=trial_id, recordings_root=recordings_root)
        if observation is not None:
            cooperative.setdefault("env_observation", observation)
        return cooperative
    runtime_receipt = {}
    try:
        result = refusal if refusal is not None else _run_one(
            repo, task, timeout_s, sandbox, setup, verifier, verify_timeout_s,
            env, run_id, run_seed, recordings_root, trial_id=trial_id,
            runtime_receipt=runtime_receipt, worker=_worker,
            **({'private_paths': _private_paths} if _private_paths else {}),
            **({'evidence_output': evidence_output} if evidence_output is not None else {}))
    except BaseException as exc:
        # Expected launch failures retain their original exception behavior. The
        # collector has already run in finally; carry its private result to the
        # evaluation owner so it can persist it before propagating the failure.
        launch_failure = isinstance(exc, (OSError, sandbox_mod.SandboxUnavailable,
                                         container_mod.ContainerUnavailable)) or (
            isinstance(exc, SystemExit) and isinstance(exc.__context__, sandbox_mod.SandboxUnavailable))
        if (capture_evidence and launch_failure and evidence_output.get('launch_attempted')
                and evidence_output.get('launch_error')):
            exc.trial_evidence_result = {'task_id': task['task_id'],
                'exception': {'type': type(exc).__name__, 'detail': str(exc)},
                'trial_evidence_output': evidence_output, 'runtime_services': runtime_receipt}
        raise
    if capture_evidence:
        result['trial_evidence_output'] = evidence_output
    if runtime_receipt:
        result["runtime_services"] = runtime_receipt
        if runtime_receipt.get("bun"):
            result["harness_runtime"] = {**(result.get("harness_runtime") or {}),
                                         "bun": runtime_receipt["bun"]["identity"]}
        capture = runtime_receipt.get("responses_capture")
        if capture is not None and not capture["complete"]:
            result.setdefault("invalid", {"stage": "Responses capture", "service": None,
                "detail": "Protocol capture incomplete; retained available artifacts and original task outcome"})
    if observation is not None and refusal is None:
        result.setdefault("env_observation", observation)
    if result.get("verdict") is not None:
        result["verdict"].setdefault("scorer_isolation_class", "unknown")
    if diagnostic_authorization is not None:
        result["enforcement"] = diagnostic_authorization.record()
    if model_failure.enabled(verifier):
        failure = model_failure.failure(result, verifier['model_failure_policy'])
        if failure is not None:
            result['invalid'] = failure
    return result


def _run_one(repo: Path, task: dict, timeout_s: int | None = None,
            sandbox: bool = True, setup: dict | None = None,
            verifier: dict | None = None,
            verify_timeout_s: int | None = None,
            env: dict | None = None,
            run_id: str = "", run_seed: str = "",
            recordings_root: Path | None = None, *, trial_id: str | None = None,
            runtime_receipt: dict | None = None, worker: dict | None = None,
            evidence_output: dict | None = None,
            private_paths: tuple[Path, ...] = ()) -> dict:
    """Run the harness on one task in a throwaway dir, off the platform's disk.

    Returns {task_id, answer, trace, exit_code}. A non-zero exit is recorded as
    a harness failure rather than raised: one broken task must not lose a whole
    paid round.

    `sandbox=True` runs the subject inside its own mount namespace, so the platform
    is not on its disk at all. Paths are bound at their host locations, so the
    repository path, `--workdir`, and anything the harness writes into its trace
    keep the same spelling they would have had unsandboxed -- a translated path is
    a place where a harness's own record stops matching the filesystem it ran on.

    Native network='none' agents use a fixed platform model route over AF_UNIX
    and namespace-local HTTP, without exposing a task network capability.
    """
    # Resolve before the subprocess switches cwd: a relative repo path would
    # otherwise be interpreted against the sandbox directory and the harness
    # would silently fail to launch, scoring zero for the wrong reason.
    repo = Path(repo).resolve()
    # The environment is a property of the task (INTERFACE.md §2.5). `files` is the
    # absence of a declaration, not a separate code path invented for old datasets.
    env_spec = env or {"kind": "files"}
    is_exec = env_spec.get("kind") == "exec"
    relay_decl = (setup or {}).get("tool_relay") or {}
    record_spec = relay_decl.get("record")
    model_trial_seed = (relay_decl.get("model") or {}).get("seed_source") == "trial"
    if record_spec is not None:
        try:
            tool_relay_mod.validate_record(record_spec)
            if not sandbox or is_exec or (verifier or {}).get("kind") != "command":
                raise ValueError("recorded tool relay requires sandboxed files and a command verifier")
            if (verifier or {}).get("scorer_bundle", {}).get("isolation_class") == "pristine-offline":
                raise ValueError("recorded tool relay cannot use pristine-offline verification")
        except ValueError as exc:
            return _invalid(task, exc, stage="tool recording admission", service=None)
    runtime_receipt = runtime_receipt if runtime_receipt is not None else {}
    try:
        immutable_runtimes = projections((setup or {}).get("files"))
        verifier_runtimes = projections((verifier or {}).get("inputs"))
        if immutable_runtimes or verifier_runtimes:
            if not is_exec and not sandbox:
                raise ImmutableRuntimeUnavailable("immutable runtimes require a read-only sandbox bind")
            if env_spec.get("state") == "container" \
                    or ((verifier or {}).get("scorer_bundle") or {}).get("isolation_class") == "pristine-offline":
                raise ImmutableRuntimeUnavailable("this verification boundary cannot express immutable runtime binds")
    except (ImmutableRuntimeUnavailable, OSError) as exc:
        return _invalid(task, exc, stage="immutable runtime admission", service=None)
    try:
        protocol = chess_stepwise.protocol_of(task)
        stepwise_session = chess_stepwise.session_for(
            task, verifier, sandbox=sandbox, env=env_spec)
    except ValueError as exc:
        return _invalid(task, exc, stage="stepwise admission", service=None)
    # Inside a container the task is mounted at the dataset's `workdir` (default
    # `/app`), so the harness is told the path it can actually see. Outside one the
    # task *is* the host temporary directory. This is the only path translation in
    # the runner, and it exists because the dataset names a location in the
    # environment rather than on the host.
    task_mount = env_spec.get("workdir") or container_mod.DEFAULT_WORKDIR
    # Declared per environment: the real task set spans 600-12000 s, and the module
    # default of 300 would report a harness failing a task it was never given time to
    # attempt (INTERFACE.md §2.5.9). `phase_timeout` already returns `min(explicit)` and
    # falls back to 3600 only when *nothing* declares a bound, so a longer declared task
    # deadline is never clamped here.
    timeout_s = phase_timeout(timeout_s, env_spec.get("agent_timeout_s"))
    verify_timeout_s = phase_timeout(verify_timeout_s, env_spec.get("verify_timeout_s"),
                                    (verifier or {}).get("timeout_s"))
    agent_runtime = env_spec.get("agent_runtime")
    if agent_runtime and is_exec:
        raise ValueError("agent_runtime package projections require a files environment")
    if not is_exec and not sandbox and (agent_runtime or env_spec.get("network") == "none"):
        raise ValueError("declared native runtime/network isolation requires sandbox=True")
    # Read before the dispatch: both state kinds need it, and it used to be computed
    # further down in the `mount` path only.
    _manifest = json.loads((repo / "harness.json").read_text())
    if not is_exec:
        admit_native_network(env_spec, _manifest, os.environ.get("HG_AGENT_BACKEND", "mock"))
    from eval.containers import bun_runtime
    try:
        bun_environment = bun_runtime.admit(repo, os.environ)
        bun_preparation = bun_runtime.prepare(repo)
    except bun_runtime.RuntimeUnavailable as exc:
        return _invalid(task, exc, stage="harness runtime preparation", service=None)
    bun_paths = tuple(Path(p) for p in (bun_preparation or {}).get("runtime_paths", []))
    if bun_preparation:
        runtime_receipt["bun"] = bun_preparation
        execution_binary = bun_preparation.get("execution_binary", bun_preparation["binary"])
        bun_environment.update(HG_AGENT_OPENCODE_BUN=execution_binary,
                               HG_HARNESS_BUN=execution_binary)
        if bun_preparation.get("responses_overlay"):
            bun_environment["HG_RESPONSES_OVERLAY_SHA256"] = bun_preparation["responses_overlay"]["identity"]["sha256"]
    runtime_paths = tuple(Path(p).expanduser()
                          for p in (_manifest.get("runtime_paths") or [])) + bun_paths
    if is_exec and env_spec.get("state") == "container":
        return _run_one_container_state(
            repo, task, env_spec=env_spec, setup=setup, verifier=verifier,
            timeout_s=timeout_s, verify_timeout_s=verify_timeout_s,
            run_id=run_id, run_seed=run_seed, recordings_root=recordings_root,
            runtime_paths=runtime_paths, runtime_env=bun_environment,
            runtime_receipt=runtime_receipt)
    service_specs = list(env_spec.get("services") or [])
    web_spec = env_spec.get("web") or {}
    running = None
    # The internal network, whoever created it: `services.start` when there are
    # services, the model gateway when there are not. Tracked separately from `running`
    # so teardown knows which of the two it owns.
    network_name: str | None = None
    owns_network = False
    model_gateway = None
    web_gateway = None
    held = progress_mod.task_of()
    phases = progress_mod.phases_for(held)
    runtime_dir = None
    stepwise_relay = None
    stepwise_dir = None
    tool_relay = None
    package_overlays = ()
    proc, launch_error, launch_attempted = None, None, False
    output_collected = False
    workdir = Path(tempfile.mkdtemp(prefix="hg-task-"))
    try:
        if (bun_preparation or {}).get("responses_overlay"):
            (workdir / ".responses").mkdir()
            bun_environment["HG_RESPONSES_ARTIFACT_DIR"] = str(
                Path(task_mount) / ".responses" if is_exec else workdir / ".responses")
        try:
            targets(workdir, (*immutable_runtimes, *verifier_runtimes))
        except (ImmutableRuntimeUnavailable, OSError) as exc:
            return _invalid(task, exc, stage="immutable runtime admission", service=None)
        if agent_runtime:
            runtime_dir = Path(tempfile.mkdtemp(prefix="hg-agent-runtime-"))
            package_overlays = (stage_agent_runtime(agent_runtime, runtime_dir),)
        # The task's initial state, before the harness sees anything. It used to be an
        # empty directory, which is why the old contract could only express "answer a
        # question": a dataset had nowhere to put the thing the task is *about*.
        with phases("setup"):
            if setup:
                try:
                    _materialize(workdir, setup.get("files"), PLATFORM_ROOT)
                except ImmutableRuntimeUnavailable as exc:
                    return _invalid(task, exc, stage="immutable runtime setup", service=None)

            task_file = workdir / "task.json"
            actor_task = ({**task, "goal": chess_stepwise.GOAL}
                          if stepwise_session is not None else task)
            task_file.write_text(json.dumps(runtime_task(actor_task, setup)))

        # Services come up before the harness and are health-checked before it starts
        # (INTERFACE.md §2.5.7, steps 1-3). A harness that starts against a service
        # that is still initialising does not produce a bad measurement, it produces a
        # measurement of a race -- so the failure is refused here rather than scored.
        if service_specs:
            try:
                running = services_mod.start(
                    service_specs,
                    # A caller that names no root gets a throwaway one beside the task
                    # directory: an in-process caller (a test) wants the lifecycle
                    # exercised, not a directory in somebody's run tree.
                    recordings_root=(recordings_root
                                     or workdir.parent / f".recordings-{run_id or 'run'}"),
                    run_id=run_id or "run", task_id=task["task_id"],
                    run_seed=run_seed or "0")
                network_name = running.network
            except services_mod.ServiceUnavailable as exc:
                return _invalid(task, exc, stage=exc.stage, service=exc.service)

        if web_spec:
            try:
                if network_name is None:
                    network_name = services_mod.create_network(run_id or "run", task["task_id"])
                    owns_network = True
                record_base = recordings_root or workdir.parent
                web_gateway = web_gateway_mod.WebGateway(
                    bind_host=services_mod.gateway_ip(network_name),
                    record_path=record_base / task["task_id"] / "web" / "requests.jsonl",
                    enable_search=bool(web_spec.get("search")),
                    enable_fetch=bool(web_spec.get("fetch"))).start()
            except Exception as exc:
                return _invalid(task, exc, stage="start", service="web")

        manifest = json.loads((repo / "harness.json").read_text())
        entry = manifest["entrypoint"]
        # Trees this harness needs in order to run at all — a CLI it drives, a
        # runtime it was built against. Declared, not inferred: the platform cannot
        # know where somebody's agent CLI is installed.
        runtime_paths = tuple(Path(p).expanduser()
                              for p in (manifest.get("runtime_paths") or [])) + bun_paths
        # Inside a container the entrypoint is still `<repo>/<entry>` because the
        # harness tree is mounted at its own host path; only the task's paths move,
        # because the dataset named a location *in the environment*.
        #
        # The interpreter is **the platform's own** for `exec`, mounted in read-only,
        # rather than whatever `python` the image happens to have -- which may be
        # nothing (measured: the real Terminal-Bench image has no `python3` at all).
        # `files` keeps the bare name, because there the host's PATH is the right answer
        # and changing it would change what every existing dataset measures.
        task_arg = (container_mod.to_container(task_file, workdir, task_mount)
                    if is_exec else str(task_file))
        workdir_arg = task_mount if is_exec else str(workdir)
        # The image's interpreter when it has one (§2.5.3), the platform's when it does
        # not. `files` keeps the bare name: there the host's PATH is the right answer and
        # changing it would change what every existing dataset measures.
        image_python = container_mod.image_python(env_spec["image"]) if is_exec else None
        python = image_python or (sys.executable if is_exec else "python")
        argv = [python, str(repo / entry), "--task", task_arg,
                "--workdir", workdir_arg]

        # The harness's own dependencies, from a directory outside the task's
        # environment, resolved for **this** interpreter's Python version. `None` is the
        # ordinary case: nobody configured one, and the run then behaves exactly as it
        # did before this existed. See `eval/containers/harness_runtime.py` for why this is not a
        # derived image and not the platform's interpreter.
        overlay = harness_runtime.lookup(
            repo, python, image=(env_spec["image"] if is_exec else None),
            image_digest=(env_spec.get("image_digest") if is_exec else None))
        if overlay:
            # Only the paths are collected here; the environment is applied below, where
            # `child_env` exists. It used to be written here, one screen above its own
            # definition -- a `NameError` on the one branch nobody had configured yet,
            # which is how it survived: `overlay` is `None` unless a harness runtime is
            # declared, and then the run that declares one is the run that crashes.
            runtime_paths = tuple(runtime_paths) + harness_runtime.extra_paths(overlay)

        # The harness's own writable scratch space, inside its otherwise read-only
        # tree. Exported so a harness need not know the platform's layout, and
        # stated in the environment rather than passed as an argument because the
        # entrypoint contract (INTERFACE.md §1.2) is `--task` and `--workdir` only.
        state_dir = repo / STATE_DIRNAME
        # Created on demand, not just at staging time: a workspace committed before
        # `.state/` existed would otherwise make every run refuse to start, and the
        # sandbox needs a real directory to mount over. Empty and gitignored, so it
        # cannot move a sha.
        state_dir.mkdir(exist_ok=True)

        # A writable HOME, and the XDG directories, per task.
        #
        # This is what makes an agent CLI usable as a harness at all. The sandbox
        # starts from an empty root -- only /usr, /bin, /sbin, /lib*, /etc and the
        # trees it is told to bind exist inside -- so the inherited `HOME`, pointing at
        # a real `/home/<user>` that does not exist there, is a path a CLI cannot even
        # `mkdir` under. Every one of them (Claude Code, Codex, OpenCode, ...) writes
        # session state, config and caches under HOME, and dies immediately if it
        # cannot.
        #
        # Under `workdir` rather than somewhere of its own, because `workdir` is
        # already bound read-write and is already a per-task temporary directory: the
        # isolation this needs is the isolation it already has, and inventing a second
        # mount for it would be a second thing to get wrong. Named with the platform's
        # prefix so it cannot be mistaken for a task's own file.
        #
        # The same directory is created for a container, where it is reached as
        # `<workdir>/.harnessgrad-home` through the task bind mount -- so the two
        # environments agree on where a CLI keeps its state without agreeing on how
        # that path is spelled on the host.
        home = workdir / ".harnessgrad-home"
        (home / "tmp").mkdir(parents=True, exist_ok=True)

        if is_exec:
            # An allowlist, and container-side HOME/XDG/TMPDIR: forwarding the host
            # values would name directories that do not exist inside the image.
            child_env = container_mod.container_env(task_mount, deadline_s=timeout_s)
        else:
            child_env = harness_env()
            child_env.update({
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                # A CLI's scratch files should not land in the directory the task is
                # about, where a verifier would then be reading them.
                "TMPDIR": str(home / "tmp"),
            })
        child_env.update(bun_environment)
        if worker is not None:
            child_env["HG_WORKER_ID"] = worker["id"]
        child_env["HG_AGENT_DEADLINE_S"] = str(timeout_s)
        child_env["HARNESSGRAD_STATE"] = str(state_dir)
        if overlay:
            child_env.update(harness_runtime.env_for(overlay))

        # Publish the agent's model onto the task's network. A containerised harness
        # cannot reach a model on the host's loopback -- measured, every obvious route
        # fails -- and the wrong fix is `--network bridge`, which would give the task the
        # internet and silently change what it is (`eval/service/modelgate.py`).
        if is_exec:
            base = child_env.get("HG_AGENT_BASE_URL")
            if base and os.environ.get("HG_AGENT_BACKEND", "mock") != "mock" \
                    and modelgate_mod.is_host_local(base):
                if network_name is None:
                    network_name = services_mod.create_network(
                        run_id or "run", task["task_id"])
                    owns_network = True
                target, path = modelgate_mod.split(base)
                model_gateway = modelgate_mod.ModelGateway(
                    bind_host=services_mod.gateway_ip(network_name),
                    target=target, path=path).start()
                # The harness is handed the published address, and nothing else about
                # the mechanism: `HG_AGENT_*` is the contract it already reads.
                child_env["HG_AGENT_BASE_URL"] = model_gateway.base_url

        elif env_spec.get("network") == "none" \
                and os.environ.get("HG_AGENT_BACKEND", "mock") != "mock":
            try:
                base = child_env.get("HG_AGENT_BASE_URL")
                if not base:
                    raise modelgate_mod.GatewayUnavailable("live offline agent needs HG_AGENT_BASE_URL")
                model_gateway = NativeModelRoute(
                    base, **({'observe_http': True} if model_failure.enabled(verifier) else {})).start()
            except (modelgate_mod.GatewayUnavailable, ValueError) as exc:
                return _invalid(task, exc, stage="native model route admission", service=None)

        # How the harness finds the services, and the whole of what it is told about
        # them. `HG_SVC_*` rather than `HG_ENV_*`: the environment's own model,
        # credential and seed go to the service containers only (§2.5.7).
        if running is not None:
            child_env.update(services_mod.harness_env(service_specs))
        if web_gateway is not None:
            child_env["HG_SVC_WEB_URL"] = web_gateway.url

        # On a `services` environment the harness joins the task's internal network,
        # which is how "reachable by name, not on the internet" is delivered. Passing
        # `env_spec["network"]` straight through would ask docker for a network called
        # `services`, which does not exist.
        # With a gateway published, the harness joins the internal network so it can
        # reach it. Otherwise the declared value stands.
        network = (network_name if network_name is not None
                   else env_spec.get("network", "none"))

        # Everything the harness needs to run at all, plus the platform's interpreter.
        # The platform's interpreter is mounted only when it is going to be used, so an
        # image that brings its own does not also get the host's Python on its disk.
        runtime = ((tuple(runtime_paths)
                    + (() if image_python else container_mod.interpreter_paths()))
                   if is_exec else ())

        if stepwise_session is not None:
            # Project only this read-only socket directory. Neither gold nor session
            # state is written beneath the actor's writable task directory.
            stepwise_dir = Path(tempfile.mkdtemp(prefix="hg-stepwise-"))
            stepwise_relay = StepwiseRelay(
                stepwise_dir, stepwise_session, contract=chess_stepwise.STEPWISE,
                public_fields=chess_stepwise.PUBLIC_FIELDS, timeout_s=timeout_s)
            child_env["HG_STEPWISE_SOCKET"] = str(stepwise_relay.path)

        # The dataset's interaction environment, if it declares one. Started here and not in
        # the harness: the whole point is that the agent gets a socket and never the state.
        # A declared relay that cannot start is `unmeasured`, not a task scored against a hole.
        if tool_relay_mod.declaration(setup) is not None:
            try:
                tool_relay = tool_relay_mod.start(setup, **({"context": {
                    "task_id": task["task_id"], "run_id": run_id or "direct",
                    "trial_id": trial_id or "trial-1", "seed": run_seed}}
                    if record_spec is not None or model_trial_seed else {}),
                    **({'private_diagnostics': True} if evidence_output is not None else {}))
            except RuntimeError as exc:
                if getattr(exc, 'report', None) is not None:
                    runtime_receipt['tool_relay'] = exc.report
                if evidence_output is not None and getattr(exc, 'diagnostic', None) is not None:
                    evidence_output['tool_relay'] = exc.diagnostic
                reason = getattr(exc, 'safe_message', exc) if evidence_output is not None else exc
                return _invalid(task, reason, stage="tool relay", service=None)

        # The subject's resource envelope, read once: the receipt and the launch must
        # describe the same numbers, and two reads of the config are two chances for them
        # to disagree. A container gets its envelope from Docker
        # (`--memory 4g --cpus 2 --pids-limit 1024`); the native path had **nothing**, so
        # one runaway CLI could take the machine, the model server and every concurrent run
        # with it -- and the run that did it was not the one that would have reported the
        # damage. The verifier is deliberately not capped (`sandbox.run`'s `limits`).
        subject_limits = sandbox_mod.configured_limits()

        def launch():
            if is_exec:
                # `runtime_paths` is deliberately not applied: it exists because a
                # host CLI lives outside `/usr`, and inside a container the runtime is
                # the image's. Mounting a host tree in would defeat the point of
                # having an image at all.
                return container_mod.run(
                    argv, image=env_spec["image"], task_dir=workdir,
                    task_mount=task_mount, harness_root=repo,
                    state_dirs=(state_dir,), cwd=task_mount, env=child_env,
                    timeout_s=timeout_s, network=network,
                    # The declared envelope, finally reaching the launcher. Until
                    # `container_mod.run` took a `limits` keyword it had no way to pass
                    # one on, so every mount-state `exec` container got `command()`'s
                    # defaults whatever `env.cpus`/`env.memory_mb` said -- measured: a
                    # `cpus: 4` declaration ran at `--cpus 2`.
                    limits=container_mod.limits_from(env_spec),
                    # Stated rather than omitted: the harness gets **no** view of a
                    # service's recording, and the empty list is that decision.
                    readonly_paths=runtime,
                    immutable_runtimes=immutable_runtimes,
                    extra_mounts=tuple(services_mod.mounts_for(
                        "harness", service_specs,
                        running.recordings if running else {})))
            if not sandbox:
                # Same launcher as the sandboxed path: a timed-out or killed launch must
                # take its whole process group with it. `subprocess.run` reaps only the
                # direct child, so a harness that spawned a daemon (a JVM, an engine, a
                # `--die-with-parent` escapee) left that descendant running after the task
                # was over. It did **not** make the call hang - on POSIX `run` calls
                # `process.wait()` after killing the child, not an unbounded drain - so
                # this is a cleanup fix, not a liveness one
                # (`eval/containers/process.py`, `tests/quality/test_timeout_w_banhung.py`).
                return run_process(argv, timeout_s=timeout_s, cwd=workdir, env=child_env)
            # `repo` is `<work_root>/<run-id>/workspace`, so the work root -- the one
            # tree that must stay visible -- is two levels up. Read from the
            # platform's own environment rather than guessed from the run id.
            work_root = Path(os.environ.get("HARNESSGRAD_WORK_ROOT")
                             or repo.parent.parent)
            if worker is not None:
                work_root = worker["root"]
            return sandbox_mod.run(argv, platform=PLATFORM_ROOT,
                                   work_root=work_root, task_dir=workdir,
                                   env=child_env, timeout_s=timeout_s, cwd=workdir,
                                   repo=repo, state_dirs=(state_dir,),
                                   runtime_paths=runtime_paths,
                                   limits=subject_limits,
                                   visible_paths=(((stepwise_dir,) if stepwise_dir else ())
                                                  + harness_runtime.surfaced_paths(
                                                      overlay, work_root=work_root,
                                                      platform=PLATFORM_ROOT)),
                                   network=env_spec.get("network"),
                                   model_socket=(model_gateway.socket_path
                                                 if isinstance(model_gateway, NativeModelRoute) else None),
                                   model_path=(model_gateway.path
                                               if isinstance(model_gateway, NativeModelRoute) else ""),
                                   tool_socket=(worker["socket"] if worker is not None else
                                                tool_relay.path if tool_relay is not None else None),
                                   package_overlays=package_overlays,
                                   immutable_runtimes=immutable_runtimes,
                                   **({'private_paths': private_paths} if private_paths else {}))

        try:
            with phases("agent"):
                if stepwise_relay is not None:
                    stepwise_relay.start()
                try:
                    launch_attempted = True
                    try:
                        proc = launch()
                    except BaseException as exc:
                        launch_error = exc
                        raise
                finally:
                    if stepwise_relay is not None:
                        stepwise_relay.stop()
                    if tool_relay is not None:
                        tool_relay.stop()
            exit_code, stderr = proc.returncode, proc.stderr[-2000:]
            if tool_relay is not None:
                fault = tool_relay.fault()
                if fault is not None:
                    return _invalid(task, fault, stage="tool relay", service=None)
        except subprocess.TimeoutExpired:
            exit_code, stderr = -1, f"timeout after {timeout_s}s"
        except modelgate_mod.GatewayUnavailable as exc:
            return _invalid(task, exc, stage="native model route launch", service=None)
        except ImmutableRuntimeUnavailable as exc:
            return _invalid(task, exc, stage="immutable runtime launch", service=None)
        except OSError:
            if immutable_runtimes:
                return _invalid(task, "immutable runtime backend could not launch",
                                stage="immutable runtime launch", service=None)
            if stepwise_relay is None:
                raise
            # The stepwise harness never launched, so nothing graded it: return the
            # platform's own invalid result, exactly as the two `OSError` branches above
            # do. It previously became the `-1` sentinel, and `-1` *without* the
            # `timeout after Ns` marker is how `process_outcome` recognises a foreign
            # signal -- so the record claimed "terminated by signal 1 (exit code -1)"
            # for a launch failure that killed nothing. The outcome was already right
            # (unmeasured, not a score); the reason was fabricated.
            return _invalid(task, "stepwise harness launch failed",
                            stage="harness launch", service=None)
        except sandbox_mod.SandboxUnavailable as exc:
            if immutable_runtimes or verifier_runtimes:
                return _invalid(task, exc, stage="immutable runtime launch", service=None)
            # Not swallowed. A run that asked for a sandbox and silently got none
            # produces numbers that mean something other than what they say.
            raise SystemExit(f"refusing to run: sandbox requested but unavailable: "
                             f"{exc}. Pass --no-sandbox to accept that a harness "
                             f"can reach the platform.")
        except container_mod.ContainerUnavailable as exc:
            if immutable_runtimes or verifier_runtimes:
                return _invalid(task, exc, stage="immutable runtime launch", service=None)
            raise

        with phases("collect"):
            if evidence_output is not None:
                # Use the safe collector on the normal path too. An earlier
                # read_text() could raise/block before finally could archive it.
                evidence_output.update(trial_evidence.capture_output(
                    workdir, attempted=launch_attempted, process=proc, error=launch_error))
                output_collected = True
            answer_path = workdir / "answer.txt"
            trace_path = workdir / "trace.jsonl"
            if evidence_output is not None:
                # Preserve read_text's universal-newline semantics for existing
                # feedback. The independent archive retains the original bytes.
                answer = ((evidence_output.get('answer') or '').replace('\r\n', '\n').replace('\r', '\n')
                          if stepwise_relay is None else "")
                trace = (evidence_output.get('trace') or '').replace('\r\n', '\n').replace('\r', '\n')
            else:
                answer = (answer_path.read_text()
                          if stepwise_relay is None and answer_path.exists() else "")
                trace = trace_path.read_text() if trace_path.exists() else ""

        # The harness has exited. Grade the controller's completed live exchange,
        # or its frozen artifacts on the existing offline path (§2.2 and §2.4).
        with phases("verify"):
            if tool_relay is not None and record_spec is not None:
                try:
                    entry = tool_relay.recording_input(archive_root=recordings_root)
                    targets(workdir, projections([entry]))
                    (workdir / entry["path"]).mkdir(exist_ok=True)
                    verifier = {**verifier, "inputs": [*(verifier.get("inputs") or []), entry]}
                except (OSError, ValueError, RuntimeError) as exc:
                    return _invalid(task, exc, stage="tool recording", service=None)
            if stepwise_relay is not None:
                verdict = chess_stepwise.finish(stepwise_relay, exit_code)
            else:
                verdict = _verify(workdir, verifier, answer, sandbox=sandbox, task_id=task["task_id"],
                                  run_id=run_id, trial_id=trial_id,
                                  timeout_s=verify_timeout_s, env=env_spec,
                                  service_specs=service_specs, running=running,
                                  immutable_runtimes=immutable_runtimes)
                if verdict.get("immutable_runtime_error"):
                    return _invalid(task, verdict["unmeasured"],
                                    stage="immutable runtime verification", service=None)
                if protocol is not None:
                    verdict["protocol"] = protocol

        if (verifier or {}).get("result_contract") is not None:
            verdict["submission"] = {"answer_file": "missing" if not answer_path.exists() else
                                     "empty" if not answer.strip() else "present"}
        if tool_relay is not None and tool_relay.recording is not None:
            verdict["interaction_record"] = tool_relay.recording
        # The environment's own spend, read from the recordings **before teardown**:
        # the containers are about to be removed and their evidence with them.
        env_usage = running.usage() if running is not None else {}
        if tool_relay is not None and tool_relay.model is not None:
            model_report = tool_relay.model.report()
            env_usage = {"input_tokens": model_report["usage"]["prompt_tokens"],
                         "output_tokens": model_report["usage"]["completion_tokens"],
                         "calls": len(model_report["calls"]), "services_reported": 1}

        worker_artifacts = {}
        if worker is not None:
            from eval.run.artifacts import collect
            try:
                worker_artifacts = {"artifacts": collect(workdir, worker["artifacts"],
                    max_bytes=worker.get("artifact_max_bytes", 16 << 20))}
            except (ValueError, OSError) as exc:
                return _invalid(task, exc, stage="worker artifacts", service=None)
        return {**worker_artifacts, "task_id": task["task_id"], "answer": answer,
                "trace": trace, "exit_code": exit_code, "stderr": stderr,
                # A wrapper (the sandbox) is the direct child when `sandbox` is set, and a
                # wrapper relays an inner kill as `128+signal`. Recording which launch
                # shape produced the code is what lets `process_outcome` tell the
                # convention apart from a positive code a harness chose for itself.
                "sandbox": bool(sandbox),
                # What the launch was bounded by, and whether the bound applied. On this
                # record it matters for one number: `relayed_signal` (137/143) is
                # *undetermined* by construction, and an envelope turns "killed by
                # something" into "killed while capped at 4096M -- so possibly the cap".
                "sandbox_envelope": (sandbox_mod.envelope(limits=subject_limits)
                                     if sandbox else
                                     {"applied": False, "reason": "--no-sandbox"}),
                "verdict": verdict, "env_usage": env_usage,
                "harness_runtime": harness_runtime.record(
                    overlay, platform_interpreter=(image_python is None),
                    # The `files` case runs on the host's `python` and the caller is the
                    # only one that can ask which version that is; for an image the
                    # overlay already carries it. Left unasked, a `files` point said
                    # `python: null`, which is the one field two such points differ in.
                    interpreter=(None if is_exec
                                 else harness_runtime.interpreter_version(python))),
                "runtime_boundary": {
                    "agent_timeout_s": timeout_s,
                    "verifier_timeout_s": verify_timeout_s,
                    "agent_network": env_spec.get("network", "inherited") if not is_exec else network,
                    "agent_package_projected": bool(package_overlays),
                    "verifier_package": "trusted"},
                # `account()` and not just "published": the connection counts are what
                # turn a harness-side `Connection error` into a named cause. Read while
                # the gateway is still up -- `finally` stops it right after this returns.
                "model_gateway": ({"published": True,
                                   "target": f"{model_gateway.target[0]}:{model_gateway.target[1]}",
                                   **model_gateway.account()}
                                  if model_gateway is not None else None),
                "web_gateway": ({"published": True, "url": web_gateway.url,
                                 "search": web_gateway.search, "fetch": web_gateway.fetch}
                                if web_gateway is not None else None),
                "identity": _last_identity(trace)}
    finally:
        if evidence_output is not None and not output_collected:
            evidence_output.update(trial_evidence.capture_output(
                workdir, attempted=launch_attempted, process=proc, error=launch_error))
        if (bun_preparation or {}).get("responses_overlay"):
            from eval.containers.responses_overlay import collect_capture
            runtime_receipt["responses_capture"] = collect_capture(workdir)
        if tool_relay is not None:
            tool_relay.cleanup()
            runtime_receipt["tool_relay"] = tool_relay.report()
            if evidence_output is not None and getattr(tool_relay, 'diagnostic', None) is not None:
                evidence_output['tool_relay'] = tool_relay.diagnostic
        if stepwise_relay is not None:
            stepwise_relay.stop()
        if stepwise_dir is not None:
            shutil.rmtree(stepwise_dir, ignore_errors=True)
        # Step 8, and in a `finally` for the reason the table in §2.5.7 gives: a leaked
        # network would be inherited by the next task, where it would look like
        # isolation that is not there. `stop()` never raises.
        if model_gateway is not None:
            model_gateway.stop()
            if isinstance(model_gateway, NativeModelRoute) and model_gateway.http is not None:
                runtime_receipt['agent_model_http'] = model_gateway.http.report()
        if web_gateway is not None:
            web_gateway.stop()
        if owns_network and network_name is not None:
            leaked_net = services_mod.remove_network(network_name)
            if leaked_net:
                print(f"warning: could not remove the model gateway network for "
                      f"{task['task_id']}: {leaked_net}", file=sys.stderr)
        if running is not None:
            leaked = running.stop()
            if leaked:
                # Not raised. The task's result is the platform's report about the
                # *task*, and a teardown problem after it is a fact about the run.
                print(f"warning: could not fully tear down services for "
                      f"{task['task_id']}: {leaked}", file=sys.stderr)
        shutil.rmtree(workdir, ignore_errors=True)
        if runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)





















#: Markers for failures to reach the model from inside the harness.
#:
#: On 2026-10-02 (`loop-terminal_bench-submit-1`), two harnesses died with
#: `RuntimeError: 3 attempts failed, last: Connection error.` but received 0.0.
#: Section 2.5.7 separates tasks that could not be measured from measured zeros.
#:
#: Reproduction details are in `eval/service/modelgate.py`: uvicorn closed idle
#: keep-alive connections after five seconds, the gateway forwarded upstream EOF,
#: and a later harness call reused the dead connection after a lengthy command.
_PROVIDER_FAILURES = (
    "APIConnectionError", "APITimeoutError", "Connection error",
    "RemoteDisconnected", "Connection refused", "Connection reset",
    "connect timeout", "Server disconnected",
    # The OpenCode adapter emits this only for an unhandled provider error event.
    # Authentication, HTTP and malformed-stream failures must not become benchmark 0s.
    "OpenCode provider failure:",
)


def _provider_failure(result: dict) -> dict | None:
    """Identify a harness death caused by an unreachable model endpoint, or return None.

    Deliberately require both a nonzero exit code and client connection/timeout
    markers at the end of stderr. Earlier markers may describe an error that the
    harness recovered from successfully.

    A zero exit code never qualifies: the harness completed and keeps its score.
    """
    if not result.get("exit_code"):            # Neither None (invalid) nor zero qualifies.
        return None
    tail = (result.get("stderr") or "")[-1500:]
    for marker in _PROVIDER_FAILURES:
        if marker in tail:
            # Preserve the matching line so the record identifies the exact error.
            line = next((l.strip() for l in reversed(tail.splitlines())
                         if marker in l), marker)
            return {"marker": marker, "line": line[:240],
                    "exit_code": result.get("exit_code")}
    return None


def _stopped_by_signals(measured: list[dict]) -> dict | None:
    """Was this task killed by something the platform never graded as a task failure?

    `process_outcome` separates five endings. Three are scores under `INTERFACE.md` §2.2 --
    the harness ran, the harness crashed on its own, and the harness blew through the
    `agent_timeout_s` budget it was given (a *declared* bound, so exceeding it is failing to
    finish in budget). Two are not: a negative return code that is not the platform's own
    timeout marker, and the sandbox wrapper's `128+signal` relay of an inner signal. In
    both, no trial got the chance to leave an artifact and nothing graded one.

    The relayed case is deliberately weaker than the negative one. A wrapper exit says a
    signal ended the process inside the wrapper and says nothing about who sent it -- a
    harness that killed itself and an outside kill are the same number -- so the record
    names the convention and leaves the cause undetermined instead of asserting one. Only
    `sandbox=True` records can take this branch; on the host the same kill arrives negative.

    That is the case `_cancel` already names -- "a cancelled task is not a task that
    scored nothing ... rather than given a zero the platform never measured" (§2.5.7).
    Nothing checked the sign of the return code until now, so all endings fell through to
    the verifier and were written as ordinary zeros. Measured on our own AgentDojo passes:
    the tasks an operator watchdog killed (156,157,159,160 and
    294,295,296,297,301,302,303,307, eval 257,259) are recorded as clean `0.0` and are
    the only zeros in that collection that are actually wrong.

    A passing verdict is never overruled, for the same reason the provider rule declines
    to: it is positive evidence that the artifact was correct.
    """
    if any((res.get("verdict") or {}).get("passed") for res in measured or []):
        return None
    for res in reversed(measured or []):
        outcome = process_outcome(res.get("exit_code"), res.get("stderr"),
                                  wrapper=bool(res.get("sandbox")))
        if outcome == "relayed_signal":
            # The sandbox wrapper's account of an inner signal. It says a signal ended the
            # process; it does not say who sent it, and a harness that killed itself is
            # the same number here as an outside kill. The rule the operator chose is to
            # keep the task unmeasured when nothing passed *and say that the cause is
            # undetermined*, rather than pick one reading and hide the other.
            code = res.get("exit_code")
            return {"stage": "harness", "outcome": "relayed_signal",
                    "detail": (f"the sandbox wrapper reported exit {code} "
                               f"({code - 128} = the 128+signal convention) and no trial "
                               f"passed, so no artifact was ever graded; whether the task "
                               f"killed itself or was killed from outside is not "
                               f"distinguishable from a wrapper exit, and this is not a score")}
        if outcome != "stopped_by_signal":
            continue
        code = res.get("exit_code")
        return {"stage": "harness", "outcome": "stopped_by_signal",
                "detail": (f"the task was terminated by signal {-code} (exit code {code}) "
                           f"from outside the measurement, so no artifact was ever "
                           f"graded; this is not a score")}
    return None


def _invalid(task: dict, exc, *, stage: str, service: str | None) -> dict:
    """A task the platform could not measure, as opposed to one it measured as zero.

    INTERFACE.md §2.5.7: the result is **not** a `per_task` score and **not** a zero,
    and it is attributed to the dataset and the service by name. The distinction is the
    entire reason this platform exists — a broken mock API and a weak harness must not
    be the same number — and it is independently attested: JarvisBench reports that
    "harness or provider failures are marked invalid rather than assigned a score of
    zero" (`INTERFACE.md` §2.5.7).
    """
    return {"task_id": task["task_id"], "invalid": {
                "stage": stage, "service": service, "detail": str(exc)},
            "answer": "", "trace": "", "exit_code": None, "stderr": "",
            "env_usage": {}, "model_gateway": None, "identity": None}




def timeout_verdict(reason) -> bool:
    """Is this unmeasured verdict a **timeout**, which is scored 0 rather than excluded?

    The verifier reports `{"outcome": "timeout", "unmeasured": "the verifier timed out after
    900s", ...}`; the process-level path reports the bare string `"timeout"` when the wrapper was
    killed at its budget. Both are the harness's failure to finish, and both are scored 0
    (operator decision 2026-10-23). Everything else -- `"unmeasured"` (the check wrote no reward
    file), `"relayed_signal"`, `"stopped_by_signal"` -- stays `invalid`, because the platform,
    not the method, failed it.
    """
    if isinstance(reason, dict):
        return reason.get("outcome") == "timeout"
    return reason == "timeout"


def _spread(xs: list[float]) -> float:
    """Population standard deviation, with no dependency and no ambiguity.

    The population form because these are *all* the trials this run took, not a sample
    from a larger population of runs -- `statistics.pstdev` is the honest name for it, and
    the difference from `stdev` matters most at small n, which is exactly where a reader
    compares two harnesses.
    """
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def _harness_failure(result: dict) -> dict | None:
    """Why a harness left nothing to read, when it left nothing.

    A task can be **measured** and still say nothing about the harness: the check reads
    the artifact, finds it wrong, and records a 0.0 -- and that 0.0 is a real score. But
    if the harness *also* wrote no trace, then no method has anything to read while the
    curve reports a number, and those two situations are indistinguishable on the curve.

    Measured, and the reason this exists: a harness that needed a package the task image
    did not have died on its first model call with `ModuleNotFoundError`, exit code 1.
    The trace was 0 bytes, the score was 0.000, and the run reported *nothing* -- the
    exit code and stderr were computed here and then dropped, so "the method decided not
    to edit" was the only reading available. A harness that cannot start is a fact about
    the run and belongs in the record.

    Every harness the platform ships writes `trace.jsonl`, so an empty one is always
    abnormal; a harness that simply chose to write nothing is worth knowing about too.
    Deliberately **not** folded into `invalid`: the task was measured, so it keeps its
    score. This is the attribution, not a reason to drop the measurement (§2.5.7).

    **The second trigger, and the measured case that forced it.** A non-empty trace is
    not proof that the harness ran. Measured 2026-10-04 on the current platform:
    `--harness cli_agent` with no CLI writes exactly one line,
    `{"error": "HG_CLI is unset: no CLI to run"}`, never calls a model, exits 2 -- and the
    curve said `score 0.0, harness_failed: null, invalid: null`. The console warned and
    the verdict file carried the exit code, so the fact existed in two places and not on
    the point, which is where a reader compares runs.

    So: a harness that **exited non-zero without recording a single model call** is a
    harness failure too. "A model call" is read from the `usage` block, which the harness
    contract requires (`INTERFACE.md` §1.2) -- the platform is entitled to rely on it, and
    it is the only schema-independent evidence that a harness talked to a model. A
    scripted harness that legitimately makes no calls (`base_harness/oracle`) exits 0 and
    is not flagged.
    """
    trace = (result.get("trace") or "").strip()
    reason = None
    if not trace:
        reason = "the harness left no trace at all"
    elif result.get("exit_code") and _last_usage(trace) is None:
        # State only what the platform knows. Usage is written after completion,
        # so a crash can hide real calls. On 2026-10-04, three local-model tasks
        # had 15-23 calls and 120-140 KB traces, yet the old message incorrectly
        # reported "left no trace" because their usage blocks were absent.
        reason = (f"the harness exited {result['exit_code']} and its trace has no `usage` "
                  f"block, so the run did not reach its end")
    if reason is None:
        return None
    return {"reason": reason, "exit_code": result.get("exit_code"),
            "stderr": (result.get("stderr") or "")[-1500:]}


def _reporter():
    """The reporter an evaluation should narrate through.

    The context first, then the console's own. Both are needed and neither is
    redundant:

    * the **context** is how a task's phases reach the code that runs them
      (`eval/containers/container.py` beats while it waits inside a single `docker exec`);
    * the **console** is what makes that context exist at all when the driver has not
      set one. Measured: the driver's own calls to `evaluate` (`driver.py` round 0, the
      mode B loop, the eval side) run on the main thread with no context set, and the
      first version of this put the progress events *only* in the context -- so a real
      run reported the method call's phases and nothing at all for the four tasks it
      spent the hour on. A run that narrates its shortest wait and hides its longest
      is worse than one that narrates neither.
    """
    from eval.observe.console import CONSOLE
    return progress_mod.current() or CONSOLE.reporter


def evaluate(repo: Path, tasks: list[dict], scorable: dict[str, str],
             cache: dict | None = None, harness_sha: str = "",
             sandbox: bool = True, setups: dict | None = None,
             verifiers: dict | None = None,
             envs: dict | None = None,
             run_id: str = "", run_seed: str = "",
             recordings_root: Path | None = None,
             round_no: int = 0, trials: int = 1, jobs: int = 1,
             progress_dir: Path | None = None,
             diagnostic_authorization: Authorization | None = None,
             persist_trial_report: bool = True) -> dict:
    """Run a harness over a task set and score it.

    `scorable` maps task_id -> expected answer. Scoring lives here, not in the
    Trainer, because a subject that scores itself destroys the comparison.

    Two functions so that the whole evaluation -- not just each task's own phases --
    runs inside one reporting context, which is what lets the container client beat
    while it waits on a `docker exec`. The signature and the body are the same as
    before; only the `with` is new.
    """
    with progress_mod.reporting(_reporter()):
        result = _evaluate(repo, tasks, scorable, cache=cache, harness_sha=harness_sha,
                         sandbox=sandbox, setups=setups, verifiers=verifiers,
                         envs=envs, run_id=run_id, run_seed=run_seed,
                         recordings_root=recordings_root, round_no=round_no,
                         trials=trials, jobs=jobs, progress_dir=progress_dir,
                         persist_trial_report=persist_trial_report,
                         diagnostic_authorization=diagnostic_authorization)
        if diagnostic_authorization is not None:
            result["enforcement"] = diagnostic_authorization.record()
        return result


def _held(measure, index: int, task: dict) -> dict:
    """One task, holding one global measurement slot while it runs.

    When no budget is configured (`slots.current()` is `None`) this is a bare call and the
    platform behaves exactly as before: `--jobs` is the only bound.
    """
    with slots.slot():
        return measure(index, task)


def _in_parallel(tasks: list[dict], measure, jobs: int) -> dict[str, dict]:
    """Measure every task, up to `jobs` at a time, and collect the outcomes by task id.

    `jobs=1` stays a plain sequential loop, because the platform's default record must not
    depend on a thread pool to be the record it has always been.

    Why this axis and not another: on a measured three-task run the agent phase was 87.8% of
    the wall clock (2578 s of 2936 s) and the platform sat idle for all of it -- the tasks are
    independent by construction (own container, own network, own model gateway, own workspace,
    own cache key), so this is the cheap parallelism. Splitting *inside* a task is not
    available: the harness is a process the platform does not control.

    The first exception is re-raised after cancelling what has not started. A task already
    running is left to finish: `run_one` tears its own containers down in a `finally`, and
    abandoning a thread mid-teardown is how a leaked network gets inherited by whatever runs
    next (§2.5.7).
    """
    if jobs <= 1:
        # Computed in task order, which is what makes the merge reproducible at one
        # `--jobs` setting as well as comparable across two of them.
        return {task["task_id"]: _held(measure, index, task)
                for index, task in enumerate(tasks, start=1)}
    pool = futures.ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="hg-task")
    pending = [pool.submit(_held, measure, index, task)
               for index, task in enumerate(tasks, start=1)]
    outcomes: dict[str, dict] = {}
    try:
        for done in futures.as_completed(pending):
            outcome = done.result()
            outcomes[outcome["task_id"]] = outcome
            # **Stop paying for a harness that cannot start.** See `_all_unstarted`: the
            # failure is task-independent, so `jobs` agreements are enough, and the rest
            # of the round can only return the same `0.000` at full price.
            if _all_unstarted(outcomes, jobs):
                for fut in pending:
                    fut.cancel()
                break
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    # **A cancelled task is not a task that scored nothing.** The merge indexes `outcomes`
    # by every task, so the cancelled ones are marked `not_run` and kept out of `per_task`
    # rather than given a zero the platform never measured (§2.5.7).
    for task in tasks:
        outcomes.setdefault(task["task_id"],
                            {"task_id": task["task_id"], "not_run": True})
    return outcomes


def _all_unstarted(outcomes: dict[str, dict], jobs: int) -> bool:
    """Has the harness failed to *start* on every task measured so far, `jobs` of them?

    This is the platform's own question (`_harness_failure`) asked about a round instead of
    a task. A harness whose entrypoint cannot be invoked fails identically on every task --
    that is the task-independent part of a run -- so `jobs` agreements are enough to stop
    the round. Measured (`codex-dgm`, 2026-10-05): the improver's edit dropped the
    `--task`/`--workdir` contract, all ten tasks produced `FileNotFoundError: '--task'`
    with no trace, and that happened in **two consecutive rounds** -- twenty
    task-measurements whose only possible contribution was `0.000` over and over.

    Deliberately not "any task failed to start": a task-specific failure (an image with no
    interpreter, say) is a fact about *that task*, and stopping the round on it would trade
    one wrong record for another.
    """
    if len(outcomes) < jobs:
        return False
    for outcome in outcomes.values():
        if outcome.get("not_run") or outcome.get("invalid") or "cached" in outcome:
            return False
        measured = outcome.get("measured") or []
        if not measured or not _harness_failure(measured[-1]):
            return False
    return True


def _evaluate(repo: Path, tasks: list[dict], scorable: dict[str, str],
              cache: dict | None = None, harness_sha: str = "",
              sandbox: bool = True, setups: dict | None = None,
              verifiers: dict | None = None,
              envs: dict | None = None,
              round_no: int = 0,
              run_id: str = "", run_seed: str = "",
              progress_dir: Path | None = None,
              recordings_root: Path | None = None,
              trials: int = 1, jobs: int = 1,
              diagnostic_authorization: Authorization | None = None,
              persist_trial_report: bool = True) -> dict:
    """Score a harness on a task set.

    `trials` is how many times each `(harness, task)` pair is measured. One is the
    default and one is not enough: measured on this platform, **the same unmodified
    harness scored 0.333 in one run and 0.000 in the next**, because the agent model
    intermittently emits a tool call in a dialect the harness cannot parse and the harness
    then ends the task. Nothing about that is a property of the dataset; it is the floor of
    what a single sample can tell you, and a curve that does not say how noisy it is cannot
    support a claim that an edit helped. With `trials > 1` a task's score is the mean over
    trials and `per_task_std` is their spread (population standard deviation).

    `jobs` is how many tasks are measured **at the same time**, and it multiplies with
    `trials` rather than replacing it: trials buy a spread, jobs buy wall clock. Both are
    recorded -- `trials` on the point, `jobs` in the run's own parameters -- because a run
    that used four containers at once is a different claim about this host than one that
    used one.
    """
    if diagnostic_authorization is not None:
        diagnostic_authorization.require_scope(
            run_id, [t["task_id"] for t in tasks], trials=trials, jobs=jobs, round_no=round_no)
        if cache:
            raise ValueError("unobserved diagnostic requires an empty cache; no cached measurements")
        # A later ordinary caller must not reuse a diagnostic's provisional score.
        cache = {}
    if trials < 1:
        raise ValueError(f"trials must be at least 1, not {trials}")
    if jobs < 1:
        raise ValueError(f"jobs must be at least 1, not {jobs}")
    evaluation_protocols = {task["task_id"]: protocol for task in tasks
                            if (protocol := chess_stepwise.protocol_of(task)) is not None}
    if len(set(evaluation_protocols.values())) > 1:
        raise ValueError("cannot aggregate interactive and offline ChessBench protocols")

    from eval.run import repeated_trials
    trial_protocol = repeated_trials.protocol_for(tasks, verifiers or {}, trials)
    for task in tasks:
        tid = task['task_id']
        if model_failure.enabled((verifiers or {}).get(tid)):
            model_failure.admit(sandbox=sandbox, setup=(setups or {}).get(tid), env=(envs or {}).get(tid))
    evidence_tasks = {t['task_id'] for t in tasks
                      if trial_evidence.enabled((verifiers or {}).get(t['task_id']))}
    archive_root = Path(progress_dir) / 'trial-evidence' if progress_dir is not None else None
    private_paths = ()
    # Every actor shares the same private run storage. On resume, all tasks that
    # opted into archival may already be complete and absent from this task list.
    if evidence_tasks or (archive_root is not None and os.path.lexists(archive_root)):
        trial_evidence.admit_archive(repo, progress_dir)
        private_paths = (archive_root,)
        for task in tasks:
            tid = task['task_id']
            trial_evidence.admit_execution(sandbox=sandbox, setup=(setups or {}).get(tid),
                                           env=(envs or {}).get(tid))
    ordinary_plan = (trial_evidence.ordinary_plan(
        [task for task in tasks if task['task_id'] in evidence_tasks], trials=trials,
        run_seed=run_seed, run_id=run_id, round_no=round_no, harness_sha=harness_sha)
        if evidence_tasks and trial_protocol is None else None)
    evidence_plan = ordinary_plan or {'configuration': trial_protocol, 'run_id': run_id,
                                    'round': round_no, 'harness_sha': harness_sha}
    trial_rows = {t["task_id"]: [] for t in tasks} if trial_protocol or ordinary_plan else {}
    def fresh_measurement(tid):
        spec = (verifiers or {}).get(tid) or {}
        relay = ((setups or {}).get(tid) or {}).get("tool_relay") or {}
        return bool(trial_protocol or tid in evidence_tasks or spec.get("judge") or spec.get("result_contract") or spec.get("model_failure_policy") or relay.get("record")
                    or (relay.get("model") or {}).get("seed_source") == "trial"
                    or ((setups or {}).get(tid) or {}).get("workers"))

    from eval.containers import bun_runtime
    bun_identity, bun_error = None, None
    try:
        bun_runtime.admit(repo, os.environ)
        bun_identity = bun_runtime.identity(repo)
    except bun_runtime.RuntimeUnavailable as exc:
        bun_error = str(exc)

    def measurement_key(task):
        key = f"{harness_sha}:{task['task_id']}:{trials}"
        if bun_identity is not None:
            key += ":runtime:" + hashlib.sha256(json.dumps(
                bun_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # A changed scorer declaration must not reuse another runtime's score.
        bundle = ((verifiers or {}).get(task["task_id"]) or {}).get("scorer_bundle")
        if bundle is not None:
            key += ":scorer:" + hashlib.sha256(json.dumps(
                bundle, sort_keys=True, default=str).encode()).hexdigest()
        if evaluation_protocols.get(task["task_id"]) == chess_stepwise.STEPWISE:
            key += ":" + chess_stepwise.STEPWISE
        return key

    def trial_identity(trial: int) -> str:
        # The ordinal repeats in each round and harness revision within an outer run.
        return f"round-{round_no}.harness-{harness_sha}.trial-{trial + 1}"

    cache = cache if cache is not None else {}
    per_task, traces, verdicts = {}, {}, {}
    per_task_scorer_isolation = {}
    #: One score per trial, per task, kept so a reader can see the spread rather than
    #: only its mean. Absent when `trials == 1`.
    per_task_trials: dict[str, list[float]] = {}
    per_task_std: dict[str, float] = {}
    #: **Every** trial's trace and verdict, so a task that passed twice out of three can
    #: be explained (`traces`/`verdicts` keep only the last trial, for compatibility).
    #: Absent when `trials == 1`, where they would repeat the two dicts above.
    trial_traces: dict[str, list[str]] = {}
    trial_verdicts: dict[str, list[dict]] = {}
    #: Tasks whose measurement was **reused** rather than taken, by task id. Recorded on
    #: the point because a reused measurement is not an independent one: with the cache
    #: content-addressed (see `driver.py`), an unchanged tree has exactly one measurement,
    #: and a reader who sees the same number twice must be able to tell that from two
    #: measurements that happened to agree.
    cached_tasks: list[str] = []
    #: Tasks the platform could not measure, as opposed to ones it measured as zero.
    #: Kept out of `per_task` entirely (INTERFACE.md §2.5.7): a task that never ran is
    #: not a task that scored nothing, and putting a `0.0` here is the specific lie
    #: this whole mechanism exists to prevent.
    invalid: dict[str, dict] = {}
    #: Tasks whose **verifier timed out**, which are scored 0 instead of excluded.
    #: Operator decision, 2026-10-23: a harness that does not leave the environment in a state
    #: the verifier can score inside the 900 s budget has failed that task, so the task is a 0
    #: in the denominator rather than an exclusion. Only `outcome == "timeout"` moves; an
    #: `unmeasured` verdict (the check wrote no reward file) or a SIGKILLed relay stays
    #: `invalid`, because those are platform failures and charging them to the method is the
    #: error `invalid` exists to prevent. Kept as its own dict so the score can include the 0
    #: while the record still says *why* it is a 0.
    timeouts: dict[str, dict] = {}
    #: Tasks the round **stopped short of**, because the harness had already failed to
    #: start on every task it had tried. Kept out of `per_task` for the same reason
    #: `invalid` is: those tasks were never measured, and a `0.0` for them would be the
    #: platform's invention rather than its verdict.
    not_run: list[str] = []
    #: Tasks the platform *did* measure but whose harness left no trace, with the exit
    #: code and the tail of its stderr. Kept apart from `invalid` on purpose: an invalid
    #: task is absent from `per_task` because there is no measurement, while these keep
    #: their score. Same split the platform makes everywhere else -- the attribution is
    #: not a reason to throw the number away.
    harness_failed: dict[str, dict] = {}
    #: What each harness **printed**, per task, kept whether it failed or not.
    #:
    #: `harness_failed` is the narrow version of this: it fires only when the trace is
    #: empty, and it answers "did the harness start?". A task that scored 0 with a
    #: non-empty trace still has a question attached to it ("it ran, so what did it
    #: say?"), and the answer was in `result["stderr"]` and thrown away. Measured: four
    #: of five tasks at 0.0 on `loop-terminal_bench-26452c` with no explanation anywhere
    #: in the run; the explanation had to be reconstructed by hand from the traces.
    harness_output: dict[str, dict] = {}
    #: The check's own tally, as a rate per task. **A second, finer signal beside
    #: `score`.** A `command` check usually reports "4 passed 1 failed" and the reward file
    #: collapses that to 0. Measured on this platform: a task that passed 4 of 5 tests
    #: scored 0.0, and a harness edit that took it to 3 of 5 also scored 0.0 -- the curve
    #: said "no change" while the artifact got *worse*, and a task set of ten binary scores
    #: (resolution 0.1, noise +/-0.1) cannot resolve the effect the platform exists to
    #: measure. `score` stays the headline and stays comparable; this is the same
    #: measurement at the resolution the check already provides.
    #:
    #: Only tasks whose check reports tests appear, so `test_rate_n_tasks` travels beside
    #: the mean -- a 0.8 over three tasks and a 0.8 over ten are not the same claim.
    per_task_test_rate: dict[str, float] = {}
    per_task_test_rate_trials: dict[str, list[float]] = {}
    tests_passed_total = 0
    tests_total_total = 0
    #: Which interpreter ran the harness on each task and where its own dependencies
    #: came from (`eval/containers/harness_runtime.py`). Distinct records, because the version is a
    #: property of the task's image and one run may cross several.
    runtime_records: list[dict] = []
    runtime_diagnostics: dict[str, dict] = {}
    declarations: list[dict] = []
    tampered: dict[str, list[str]] = {}
    env_usage = {"input": 0, "output": 0, "calls": 0, "services_reported": 0}
    #: Whether the platform had to publish the agent's model onto the task's network.
    #: A fact about the measurement: a containerised harness that could not reach a
    #: model would score zero for a reason that is not about the harness.
    model_gateway: dict | None = None

    # The harness is arbitrary code the platform did not write, and it runs with
    # the platform on disk. One pair of hashes around the whole task set, not one
    # pair per task: a 400-task benchmark would otherwise hash the platform 400
    # times, and the cost of the check would scale with the cost of the run it is
    # protecting. Granularity is not lost -- the report names files, and which
    # task touched them is not something a failing run needs.
    before = snapshot(PLATFORM_ROOT)

    # The backend observations, taken **once per distinct container shape and here**, not
    # in `_measure`: a probe starts a container and costs about 1.3 s, and `_measure` runs
    # on up to `jobs` threads. A diagnostic authorization bypasses admission by design, so
    # it needs none and this does not spend the probe on it.
    observations = ({} if diagnostic_authorization is not None
                    else _observations_for(envs, tasks, run_id=run_id))
    if observations:
        admitted = len({id(receipt) for receipt in observations.values()})
        print(f"  admission: {admitted} container observation(s) admit "
              f"{len(observations)} of {len(tasks)} task(s)", file=sys.stderr)

    # ---- one task, `trials` times, as a function ----------------------------------
    #
    # Extracted from the loop this used to be so that `--jobs N` can measure several tasks
    # at once, and the extraction is what makes that safe: this function mutates **nothing**
    # the other tasks share. It returns what it measured and the merge below writes it,
    # whereas the loop it came from wrote into eight shared dicts as it went. Two threads
    # assigning different keys of one dict is not a race, but the *order* of two writes is,
    # and a curve that pairs one task's score with another task's trace is exactly the kind
    # of number this platform exists to refuse.
    def _measure(task_index: int, task: dict) -> dict:
        tid = task["task_id"]
        enforcement = (diagnostic_authorization.record()
                       if diagnostic_authorization is not None else None)
        # The backend observation for *this* environment, when the platform has one. It is
        # handed to both admission checks -- this one, which decides whether the task runs
        # at all, and `run_one`'s, which is the same check made once more where the task is
        # actually executed -- because a receipt that admits a task here and is dropped
        # there is the same refusal one call later.
        observation = observations.get(tid)
        # Cache entries carry scores, not fresh backend enforcement observations.
        refusal = _refuse_unobserved_policies(task, (envs or {}).get(tid),
                                              (verifiers or {}).get(tid), run_id=run_id,
                                              diagnostic_authorization=diagnostic_authorization,
                                              observation=observation)
        if refusal is not None:
            _write_progress(progress_dir, round_no, tid, [], refusal["invalid"],
                            diagnostic=refusal, enforcement=enforcement)
            return {"task_id": tid, "invalid": refusal["invalid"]}
        # A cache hit cannot bypass the interactive isolation/input requirements.
        # Construction is local and lazy; it neither loads the CSV nor launches I/O.
        if evaluation_protocols.get(tid) == chess_stepwise.STEPWISE:
            try:
                chess_stepwise.session_for(task, (verifiers or {}).get(tid),
                                           sandbox=sandbox, env=(envs or {}).get(tid) or {})
            except ValueError as exc:
                refusal = _invalid(task, exc, stage="stepwise admission", service=None)
                _write_progress(progress_dir, round_no, tid, [], refusal["invalid"],
                                diagnostic=refusal)
                return {"task_id": tid, "invalid": refusal["invalid"]}
        # The trial count is part of the key: a cached single measurement is not a
        # cached mean of three, and reusing one for the other would quietly answer a
        # different question than the run asked.
        if bun_error is not None:
            invalid_runtime = {"stage": "harness runtime preparation", "detail": bun_error}
            _write_progress(progress_dir, round_no, tid, [], invalid_runtime)
            return {"task_id": tid, "invalid": invalid_runtime}
        key = measurement_key(task)
        hit = None if fresh_measurement(tid) else cache.get(key)
        if (hit is not None and bun_identity is not None
                and (hit.get("harness_runtime") or {}).get("bun") != bun_identity):
            hit = None
        if hit is not None:
            if declared_class((verifiers or {}).get(tid)) == "pristine-offline":
                cached = hit.get("trial_verdicts") or [hit.get("verdict") or {}]
                if len(cached) != trials:
                    return {"task_id": tid, "invalid": {"stage": "check",
                            "detail": "pristine cached verdict execution binding coverage mismatch"}}
                for index, verdict in enumerate(cached):
                    checked = enforce_verdict_binding(verdict, task_id=tid, run_id=run_id,
                                                      trial_id=trial_identity(index))
                    if checked.get("unmeasured"):
                        return {"task_id": tid, "invalid": {"stage": "check",
                                "detail": checked["unmeasured"]}}
            return {"task_id": tid, "cached": hit}

        # One task, one record, and the phases inside it belong to it. Where this
        # context is set, `eval/containers/container.py` starts beating while it waits, so a
        # task that spends 40 minutes inside a single `docker exec` stops being
        # indistinguishable from a dead driver. See `eval/observe/progress.py`.
        # `round_no` is what makes the event stream readable: every phase of every task
        # used to be labelled round 0, including the candidate rounds, so a log could not
        # say which round a task belonged to. The traces and verdicts were stored per
        # round all along; only the live record had lost the number.
        # One task, `trials` times. See `evaluate` for why one is not enough.
        measured: list[dict] = []
        unmeasured: dict | None = None
        unmeasured_result: dict | None = None
        trial_marker = None
        ordinary_task_plan = None
        if trial_protocol and progress_dir is not None:
            trial_marker = repeated_trials.start_task(
                repeated_trials.report_root(progress_dir, round_no, harness_sha),
                repeated_trials.plan([task], trial_protocol, run_id=run_id,
                                     round_no=round_no, harness_sha=harness_sha))
        elif ordinary_plan and tid in evidence_tasks:
            ordinary_task_plan = {**ordinary_plan, 'manifest': [row for row in ordinary_plan['manifest']
                                                               if row['task_id'] == tid]}
            trial_marker = repeated_trials.start_task(
                trial_evidence.ordinary_root(progress_dir, round_no, harness_sha), ordinary_task_plan)
        for trial in range(trials):
            with progress_mod.task_progress(tid, round_no=round_no, index=task_index,
                                            total=len(tasks)) as held:
                try:
                    result = run_one(repo, task, sandbox=sandbox,
                                     **({'_private_paths': private_paths} if private_paths else {}),
                                     setup=(setups or {}).get(tid),
                                     verifier=(verifiers or {}).get(tid),
                                     env=(envs or {}).get(tid),
                                     run_id=run_id, run_seed=(str(trial_protocol["seeds"][trial]) if trial_protocol else run_seed),
                                     recordings_root=recordings_root,
                                     **({"trial_id": trial_identity(trial)}
                                        if fresh_measurement(tid) or declared_class((verifiers or {}).get(tid)) == "pristine-offline" else {}),
                                     **({"diagnostic_authorization": diagnostic_authorization}
                                        if diagnostic_authorization is not None else {}),
                                     **({"observation": observation}
                                        if observation is not None else {}))
                except BaseException as exc:
                    # The record says the task died and why; the exception still travels.
                    held.failed(f"{type(exc).__name__}: {exc}")
                    captured = getattr(exc, 'trial_evidence_result', None)
                    if tid in evidence_tasks and captured is not None:
                        row = {'task_id': tid, 'trial_id': trial_identity(trial),
                               'seed': str(trial_protocol['seeds'][trial]) if trial_protocol else str(run_seed),
                               'status': 'raised', 'exception_type': type(exc).__name__}
                        exc.trial_evidence = trial_evidence.save(progress_dir, evidence_plan, row, captured)
                    raise

                # Validate the trial identity before either report consumes its
                # score; a refused verdict must remain unmeasured in pass^k too.
                if not result.get("invalid") and declared_class((verifiers or {}).get(tid)) == "pristine-offline":
                    result["verdict"] = enforce_verdict_binding(
                        result.get("verdict") or {}, task_id=tid, run_id=run_id,
                        trial_id=trial_identity(trial))

                # A task the platform could not measure never reaches scoring. It is *not*
                # cached either: the cache key names the harness, and the reason this
                # task failed was not the harness, so caching it would carry a dataset bug
                # into every later round and hide the round where it was introduced.
                if bun_identity is not None and (result.get("harness_runtime") or {}).get("bun") != bun_identity:
                    result.setdefault("invalid", {"stage": "harness runtime identity",
                        "detail": "Runtime changed after evaluation admission; refusing mixed measurements"})
                if trial_protocol or ordinary_task_plan:
                    plan = {"task_id": tid, "trial_id": trial_identity(trial),
                            "seed": str(trial_protocol["seeds"][trial]) if trial_protocol else str(run_seed)}
                    row = (repeated_trials.result_row(result, plan, provider_failure=_provider_failure(result))
                           if trial_protocol else trial_evidence.ordinary_row(plan, result))
                    if tid in evidence_tasks:
                        row['trial_evidence'] = trial_evidence.save(
                            progress_dir, evidence_plan, row, result)
                    trial_rows[tid].append(row)
                if result.get("invalid"):
                    unmeasured = result["invalid"]
                    unmeasured_result = result
                    held.failed(str(unmeasured.get("detail") or "")[:200])
                    # Declared trials are independent planned measurements. Keep
                    # this failure without cancelling the remaining trial seeds.
                    if trial_protocol:
                        continue
                    break
                # **The harness died because it could not reach the model endpoint.**
                # `_provider_failure` has existed since the bug it describes was measured
                # and was, until this call, only ever exercised by its own tests -- so a run
                # in which 20 of 91 task-runs died on `APIConnectionError` recorded them as
                # ordinary non-zero exits, and the curve read a gateway outage as 20 tasks
                # the harness could not do. Three guard rails, each measured:
                #
                #   * a `passed` verdict is kept, because a harness that crashed *after*
                #     writing the right artifact finished the task (INTERFACE.md §2.2);
                #   * a trial that already produced a verdict is kept: the endpoint is
                #     exactly the noise `--trials` exists to average, and discarding the one
                #     good sample to report a blip is the wrong trade;
                #   * the gateway's own counts ride along in `detail`, so the record says
                #     *who* hung up rather than only that a connection failed.
                provider = _provider_failure(result)
                if (provider is not None and not (result.get("verdict") or {}).get("passed")
                        and (result.get("verdict") or {}).get("protocol") != chess_stepwise.STEPWISE):
                    if measured:
                        print(f"warning: {tid}: trial {trial + 1} died reaching the model "
                              f"endpoint ({provider['line']}); keeping the "
                              f"{len(measured)} measured trial(s)", file=sys.stderr)
                        continue
                    unmeasured = {
                        "stage": "harness model call",
                        "detail": (f"the harness could not reach the model endpoint and "
                                   f"exited {provider['exit_code']}: {provider['line']}")
                                  + modelgate_mod.describe(result.get("model_gateway"))}
                    unmeasured_result = result
                    held.failed(str(unmeasured["detail"])[:200])
                    if trial_protocol:
                        continue
                    break
                # **A check that never ran is not a zero.** The platform can prove it when the
                # check was supposed to leave a machine-readable report and did not
                # (`_verify_container_state` marks the verdict): then nothing tested the
                # artifact, and recording 0.0 would blame the harness for a task nobody
                # measured. Same distinction §2.5.7 draws for a task the platform could not
                # start -- this is the check's own half of it.
                if (result.get("verdict") or {}).get("unmeasured"):
                    unmeasured = {"stage": "check",
                                  "detail": (result["verdict"] or {})["unmeasured"]}
                    if result["verdict"].get("outcome"):
                        unmeasured["outcome"] = result["verdict"]["outcome"]
                    if result["verdict"].get("protocol"):
                        unmeasured.update(protocol=result["verdict"]["protocol"],
                                          receipt=result["verdict"].get("evidence"))
                    unmeasured_result = result
                    held.failed(str(unmeasured["detail"])[:200])
                    if trial_protocol:
                        continue
                    break

                # The score the platform computed for this task. Set on the holder so the
                # task's closing record carries it: a panel showing "task 3/5 done" without
                # the number is a progress bar; with it, it is the round forming.
                held.score = float((result.get("verdict") or {}).get(
                    "score", 1.0 if (result.get("verdict") or {}).get("passed") else 0.0))

            measured.append(result)

        # A task the platform's own terms did not stop, and never graded, is not a zero.
        # Checked after the trial loop so a passing trial keeps its pass, and only when
        # nothing above already decided this task was unmeasured.
        if unmeasured is None:
            stopped = _stopped_by_signals(measured)
            if stopped is not None:
                unmeasured = stopped

        if trial_protocol and progress_dir is not None:
            repeated_trials.save_task(
                repeated_trials.report_root(progress_dir, round_no, harness_sha),
                repeated_trials.plan([task], trial_protocol, run_id=run_id,
                                     round_no=round_no, harness_sha=harness_sha),
                trial_rows[tid])

        if unmeasured is not None:
            # Not cached, and deliberately: the cache key names the harness, and the reason
            # this task failed was not the harness, so caching it would carry a dataset bug
            # into every later round and hide the round where it was introduced.
            # Later declared trials may have succeeded; diagnostics must still
            # come from the trial that supplied this unmeasured reason.
            diagnostic = unmeasured_result or result
            _write_progress(progress_dir, round_no, tid, [], unmeasured,
                            diagnostic=diagnostic, enforcement=enforcement,
                            **({'trial_evidence': trial_rows[tid]} if ordinary_task_plan else {}))
            if ordinary_task_plan:
                trial_evidence.save_ordinary_task(progress_dir, ordinary_task_plan, trial_rows[tid])
            return {"task_id": tid, "invalid": unmeasured,
                    "harness_runtime": (diagnostic or {}).get("harness_runtime"),
                    "runtime_services": (diagnostic or {}).get("runtime_services")}
        _write_progress(progress_dir, round_no, tid, measured, None, enforcement=enforcement,
                        **({'trial_evidence': trial_rows[tid]} if ordinary_task_plan else {}))
        if ordinary_task_plan:
            trial_evidence.save_ordinary_task(progress_dir, ordinary_task_plan, trial_rows[tid])
        if trial_marker is not None:
            trial_marker.unlink()
        return {"task_id": tid, "measured": measured}

    outcomes = _in_parallel(tasks, _measure, jobs)

    # The merge. Sequential and in **task order** on purpose: a run that used `--jobs 3`
    # and one that did not must produce comparable records, down to the order of
    # `runtime_records` and `declarations` -- an order decided by whichever thread finished
    # first would not be.
    for task_index, task in enumerate(tasks, start=1):
        tid = task["task_id"]
        key = measurement_key(task)
        outcome = outcomes[tid]

        # The round stopped before this task: the harness had already failed to start on
        # `jobs` others, so there is no measurement here to keep and none to invent.
        if outcome.get("not_run"):
            not_run.append(tid)
            continue

        # A cached task never ran: its records are copied back exactly as they were stored.
        if "cached" in outcome:
            entry = outcome["cached"]
            cached_tasks.append(tid)
            per_task[tid] = entry["score"]
            cached_verdicts = entry.get("trial_verdicts") or [entry.get("verdict") or {}]
            per_task_scorer_isolation[tid] = common_class(observed_class(v) for v in cached_verdicts)
            if entry.get("per_task_trials"):
                per_task_trials[tid] = list(entry["per_task_trials"])
                per_task_std[tid] = float(entry.get("per_task_std") or 0.0)
            if entry.get("trial_traces"):
                trial_traces[tid] = list(entry["trial_traces"])
            if entry.get("trial_verdicts"):
                trial_verdicts[tid] = [dict(v) for v in entry["trial_verdicts"]]
                for v in trial_verdicts[tid]:
                    v.setdefault("scorer_isolation_class", "unknown")
            traces[tid] = entry["trace"]
            if entry.get("harness_failed"):
                harness_failed[tid] = entry["harness_failed"]
            cached_record = entry.get("harness_runtime")
            if cached_record and cached_record not in runtime_records:
                runtime_records.append(cached_record)
            if entry.get("runtime_services"):
                runtime_diagnostics[tid] = entry["runtime_services"]
            if entry.get("harness_output"):
                harness_output[tid] = entry["harness_output"]
            if entry.get("verdict"):
                verdicts[tid] = dict(entry["verdict"])
                verdicts[tid].setdefault("scorer_isolation_class", "unknown")
                if tid in evaluation_protocols:
                    verdicts[tid]["protocol"] = evaluation_protocols[tid]
            continue

        if outcome.get("invalid"):
            reason = outcome["invalid"]
            # Both sides of this conflict are kept: the runtime records are the OpenCode
            # admission evidence and must be collected whatever the verdict is, and the timeout
            # rule decides whether the verdict is a score or an exclusion.
            runtime_record = outcome.get("harness_runtime")
            if runtime_record and runtime_record not in runtime_records:
                runtime_records.append(runtime_record)
            if outcome.get("runtime_services"):
                runtime_diagnostics[tid] = outcome["runtime_services"]
            if timeout_verdict(reason):
                # A timeout is a measured failure: score it 0 and keep the reason beside it.
                timeouts[tid] = reason if isinstance(reason, dict) else {
                    "outcome": "timeout", "detail": str(reason)}
                per_task[tid] = 0.0
                per_task_std[tid] = 0.0
                continue
            invalid[tid] = reason
            continue

        measured = outcome["measured"]
        # The records a reader inspects (trace, verdict, harness output) come from the
        # **last** trial when there is more than one; only one of them can be on disk, and
        # saying which is the whole of the rule.
        result = measured[-1]
        # The score is the *platform's* verdict on what the harness left behind, and
        # the harness's exit code deliberately does not enter it: a run that crashed
        # but produced a working artifact completed the task. See INTERFACE.md §2.2.
        verdict = result.get("verdict") or {}

        def _score_of(res: dict) -> float:
            """`score` first: a `reward_file` check returns a number and the platform must
            use it, not collapse it to "did it pass". A fractional reward is a score
            (INTERFACE.md §2.5.9), and discarding it would silently turn a 0.5 into a 1.
            """
            v = res.get("verdict") or {}
            return float(v.get("score", 1.0 if v.get("passed") else 0.0))

        scores = [_score_of(res) for res in measured]
        per_task_scorer_isolation[tid] = common_class(
            observed_class(res.get("verdict")) for res in measured)
        score = sum(scores) / len(scores)
        if trials > 1:
            per_task_trials[tid] = scores
            per_task_std[tid] = _spread(scores)
        per_task[tid] = score
        # The same trials, read for the check's own tally instead of its reward. Kept
        # beside the score rather than replacing it: `score` is what the task is worth and
        # is what every existing reader and every cross-run comparison uses.
        rates = [test_rate_of(res.get("verdict")) for res in measured]
        rates = [r for r in rates if r is not None]
        if rates:
            per_task_test_rate[tid] = sum(rates) / len(rates)
            if trials > 1:
                per_task_test_rate_trials[tid] = rates
            for res in measured:
                tally = (res.get("verdict") or {}).get("tests") or {}
                passed_n, failed_n = tally.get("passed"), tally.get("failed")
                if isinstance(passed_n, int) and isinstance(failed_n, int):
                    tests_passed_total += passed_n
                    tests_total_total += passed_n + failed_n
        verdicts[tid] = verdict
        traces[tid] = result["trace"]
        # **Every trial, not only the last one.** Measured on `B2-noop` (2026-10-04):
        # `cobol-modernization` scored 0.667 -- two of three trials passed -- and the
        # saved verdict was the *last* trial's, which passed. So the record said
        # "passed" beside a score of 0.667, and the failing trial's trace and reason
        # were nowhere: the one question `--trials` exists to answer ("it passed twice
        # out of three -- what happened the third time?") had no answer. Worse, the
        # same artifact scored 2/3 in one measurement and 3/3 in the next with a
        # byte-identical trajectory, which is a statement about the *check* rather than
        # the harness, and it cannot be checked at all without the other trials.
        #
        # Kept as a list beside `traces`/`verdicts` (which stay the last trial, so
        # every existing reader keeps working) rather than replacing them.
        if trials > 1:
            trial_traces[tid] = [res["trace"] for res in measured]
            trial_verdicts[tid] = [dict(res.get("verdict") or {}) for res in measured]
        # A harness that left no trace is recorded next to its score rather than
        # instead of it. See `_harness_failure` for what this looked like when it was
        # dropped: a crashed harness and a harness that chose not to edit were the same
        # reading, and the run said nothing either way.
        failure = _harness_failure(result)
        # Both ends. A tail alone lost the harness's own prints on the one run that
        # needed them most: measured on an eval run of `sanitize-git-repo`, 6 KB of
        # `openai` retry traceback filled the capture completely and the first thing the
        # harness said was gone. The head is where a harness states what it is doing.
        raw = result.get("stderr") or ""
        harness_output[tid] = {"exit_code": result.get("exit_code"),
                               "stderr": (raw if len(raw) <= 8000
                                          else raw[:2000] + "\n...\n" + raw[-6000:])}
        if failure:
            harness_failed[tid] = failure
        runtime_record = result.get("harness_runtime")
        if runtime_record and runtime_record not in runtime_records:
            runtime_records.append(runtime_record)
        if result.get("runtime_services"):
            runtime_diagnostics[tid] = result["runtime_services"]
        for key_name in ("input", "output", "calls"):
            value = (result.get("env_usage") or {}).get(
                {"input": "input_tokens", "output": "output_tokens",
                 "calls": "calls"}[key_name])
            if isinstance(value, int):
                env_usage[key_name] += value
        env_usage["services_reported"] += (result.get("env_usage") or {}).get(
            "services_reported", 0)
        if result.get("model_gateway"):
            model_gateway = result["model_gateway"]
        declared = result.get("identity")
        if declared:
            declarations.append(declared)
        if not fresh_measurement(tid):
            cache[key] = {"score": score, "trace": result["trace"],
                          "per_task_trials": per_task_trials.get(tid),
                          "per_task_std": per_task_std.get(tid),
                          # The other trials travel with the score too. A cached round used to
                          # restore only the last trial's trace and verdict, so the round that
                          # *reused* a measurement was exactly the round whose 2/3 could not be
                          # explained.
                          "trial_traces": trial_traces.get(tid),
                          "trial_verdicts": trial_verdicts.get(tid),
                          "harness_failed": failure,
                          "harness_output": harness_output.get(tid),
                          # The verdict travels with the score. Without this, a cached round
                          # writes `score 0.0, kind null, detail ""` -- the exact "a number
                          # with no explanation" this record exists to prevent. Measured on
                          # `loop-terminal_bench-41133` round 5: all five verdict files were
                          # empty because the round was entirely cache hits.
                          "verdict": verdicts.get(tid) or {},
                          "harness_runtime": result.get("harness_runtime"),
                          "runtime_services": result.get("runtime_services")}

    # What the harness spent. Read from the traces rather than asked for: a harness
    # that reports its own cost can under-report, and every harness already writes
    # `usage` into its trace because the contributor spec requires it. A method's
    # acceptance rule may be cost-aware (RRSI's is: it permits a candidate to spend
    # more tokens only in proportion to the score it gains), and a cost rule fed a
    # constant zero is not a weak rule, it is no rule -- measured: the platform
    # reported `generation_tokens: 0` for every point, so `Delta C` was always 0 and
    # `beta0 + beta1 * Delta S` was satisfied by every candidate.
    tokens = {"input": 0, "output": 0, "calls": 0, "tasks_reported": 0}
    for trace in traces.values():
        usage = _last_usage(trace)
        if usage is None:
            continue
        tokens["tasks_reported"] += 1
        for key, field in (("input", "input_tokens"), ("output", "output_tokens")):
            value = usage.get(field)
            if isinstance(value, int):
                tokens[key] += value
        if isinstance(usage.get("calls"), int):
            tokens["calls"] += usage["calls"]

    # A cached task never ran, so a fully-cached evaluation reports nothing --
    # correct: the platform did not execute anything this time.
    tampered = {k: v for k, v in diff(before, snapshot(PLATFORM_ROOT)).items() if v}
    # What the harness said it was. One declaration is the normal case; more than one
    # distinct one means the harness changed what ran it mid-run, which is a fact a
    # reader needs rather than something to average away.
    identity_declared = None
    identity_variants: list[dict] = []
    for declared in declarations:
        if declared not in identity_variants:
            identity_variants.append(declared)
    if identity_variants:
        identity_declared = identity_variants[0]

    #: The spread of the *run-level* score across trials: per trial, the mean over the
    #: tasks measured in it. A reader comparing two harnesses needs this number, not the
    #: per-task one, because the curve is drawn from run-level scores.
    scorer_isolation_class = common_class(per_task_scorer_isolation.values())
    score_std = 0.0
    if trials > 1 and per_task_trials:
        width = min(len(v) for v in per_task_trials.values())
        run_scores = [sum(v[i] for v in per_task_trials.values()) / len(per_task_trials)
                      for i in range(width)]
        score_std = _spread(run_scores)

    # **A round whose harness never started anywhere is not a weak harness.** The per-task
    # facts were already recorded (`harness_failed`) and warned about one by one; what was
    # missing is the round-level sentence, so a candidate that never ran once reached the
    # curve as an ordinary `0.000` -- the same number as one that ran and lost. Those two
    # readings call for different decisions (a weak harness is a result; a harness that
    # cannot start is a candidate to reject), and `codex-dgm` produced the first while
    # reading as the second for two rounds running.
    #
    # The score is kept rather than dropped: those tasks *were* measured, the platform's
    # verdict on what the harness left behind stands, and a method that broke the artifact
    # should see it in its own history. What changes is that the point now names which of
    # the two it is.
    startup_failure = None
    if harness_failed and not (set(per_task) - set(harness_failed)):
        first = sorted(harness_failed)[0]
        why = harness_failed[first] or {}
        startup_failure = {
            "tasks": len(harness_failed),
            "first_task": first,
            "reason": why.get("reason"),
            "exit_code": why.get("exit_code"),
            "stderr_tail": " ".join((why.get("stderr") or "").split())[-400:],
            "not_run": sorted(not_run),
        }
    repeated = {}
    if ordinary_plan:
        index = trial_evidence.ordinary_index(ordinary_plan,
            [row for task in tasks for row in trial_rows[task['task_id']]])
        repeated['trial_evidence'] = index
        if persist_trial_report:
            trial_evidence.persist_ordinary(progress_dir, index)
    if trial_protocol:
        manifest = [{"task_id": t["task_id"], "trial_id": trial_identity(i),
                     "seed": str(trial_protocol["seeds"][i])}
                    for t in tasks for i in range(trials)]
        report = repeated_trials.aggregate(manifest,
                    [row for task in tasks for row in trial_rows[task["task_id"]]],
                    trial_protocol["ks"])
        report["configuration"] = trial_protocol
        report["run_id"] = run_id
        report["round"] = round_no
        report["harness_sha"] = harness_sha
        repeated = {"repeated_trials": report}
        if progress_dir is not None and persist_trial_report:
            report_root = repeated_trials.report_root(progress_dir, round_no, harness_sha)
            repeated_trials.persist(report_root, report)
    return {**repeated, "per_task": per_task, "per_task_std": per_task_std, "score_std": score_std,
            "scorer_isolation_class": scorer_isolation_class,
            # Preserve unobserved diagnostic facts without making them comparable.
            "scorer_isolation_comparable": scorer_isolation_class != "unknown",
            "per_task_scorer_isolation": per_task_scorer_isolation,
            "per_task_trials": per_task_trials, "trials": trials,
            # The same measurement at the check's own resolution. `test_rate` averages only
            # the tasks whose check reports a tally, so `test_rate_n_tasks` travels with it.
            "per_task_test_rate": per_task_test_rate,
            "per_task_test_rate_trials": per_task_test_rate_trials,
            "test_rate": (sum(per_task_test_rate.values()) / len(per_task_test_rate)
                          if per_task_test_rate else None),
            "test_rate_n_tasks": len(per_task_test_rate),
            "tests_passed": tests_passed_total,
            "tests_total": tests_total_total,
            "traces": traces, "tampered": tampered,
            "trial_traces": trial_traces, "trial_verdicts": trial_verdicts,
            "cached_tasks": cached_tasks,
            "verdicts": verdicts,
            "evaluation_protocols": evaluation_protocols,
            "invalid": invalid,
            "timeouts": timeouts,
            "not_run": not_run,
            "startup_failure": startup_failure,
            "harness_failed": harness_failed,
            "harness_output": harness_output,
            "harness_runtime": runtime_records,
            "runtime_diagnostics": runtime_diagnostics,
            "env_usage": env_usage,
            "model_gateway": model_gateway,
            # The receipts that admitted these tasks, deduplicated. They are the evidence
            # for the `env.cpus`/`env.memory_mb`/`env.exec_defaults`/`env.network` fields
            # `_refuse_unobserved_policies` demands, and a run that ran because of one has
            # to carry it: "we observed this" is a claim a reader can only check against
            # the record. Absent when nothing was probed, so an unobserved round's result
            # is unchanged.
            **({"env_observations": [
                   receipt for index, receipt in enumerate(observations.values())
                   if receipt not in list(observations.values())[:index]]}
               if observations else {}),
            "identity_declared": identity_declared,
            "identity_variants": identity_variants,
            "tokens": tokens}
