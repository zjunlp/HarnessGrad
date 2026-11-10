"""Execute a harness on a task set. Framework-owned (INTERFACE.md §0)."""
from __future__ import annotations
from harnessgrad import slots
from harnessgrad import dataset_runtime_identity as dataset_runtime_identity_mod
from harnessgrad import candidate_runtime
from harnessgrad import python_exec_runtime
from harnessgrad import verification_context as verification_context_mod

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import eval.containers.container as container_mod
import eval.containers.harness_runtime as harness_runtime
import eval.containers.openclaw_runtime as openclaw_runtime
from eval.containers import external_runtime
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
from eval.run import evaluation_plan, measurement_cache, trial_worker, aggregation, evaluation_usage
from eval.run import actor_environment, actor_spec, actor_execution
from eval.run.task_workspace import TaskWorkspace
from eval.run.task_session import TaskSession
from eval.run.task_output import TaskOutputCapture
from eval.run import task_verification
from eval.run.actor_environment import _files_python
from eval.run.scheduling import _held, _in_parallel, _all_unstarted
from eval.run.measurement_cache import native_cache_matches as _native_cache_matches
import tools.web_gateway as web_gateway_mod
from eval.ckpt.git_state import STATE_DIRNAME
from eval.run.integrity import PLATFORM_ROOT, diff, snapshot
# The three layers this module used to contain, now separate modules. `runner` decides
# what a round is and what a curve point says; `tracing` reads what a run left behind,
# `verifier` decides what a task's score is, and `container_state` is the long path for
# a task whose state is the container's own filesystem.
from eval.containers.container_state import _run_one_container_state
from eval.run.task_contract import runtime_task, with_cli_task
from eval.run.outcomes import (
    _PROVIDER_FAILURES, _provider_failure, _stopped_by_signals, _invalid,
    timeout_verdict, _spread, _harness_failure,
)
from harnessgrad import task_python
from eval.policy.runtime_policy import admit_native_network, phase_timeout, stage_agent_runtime
from eval.observe.tracing import (_last_identity, _last_usage, _materialize,
                                  _write_progress, process_outcome, harness_usage_totals, environment_usage_totals)
from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, projections, targets
from eval.verification.verifier import _verify, test_rate_of
from eval.verification.isolation import common_class, observed_class, declared_class
from eval.verification.pristine import enforce_verdict_binding
from eval.policy.unobserved_diagnostic import Authorization




def harness_env() -> dict:
    """Keep the user manager's connection settings on the host launcher."""
    env = actor_environment.harness_env(credential_names=lambda: _improver_credentials())
    for name in sandbox_mod.MANAGER_ENV:
        env.pop(name, None)
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
    state_receipt = ((env_spec.get("state") != "container") or (
        observation is not None and observation.get("launch_mode") == "container-state"
        and observation.get("image") == env_spec.get("image")
        and observation.get("declared", {}).get("rootfs_readonly") is False
        and observation.get("declared", {}).get("network_policy") == env_spec.get("network", "none")
        and observation.get("observed", {}).get("network_internal") is True))
    if observation is not None and observation.get("ok") is True and state_receipt:
        covered = set(observation.get("covers") or ()) & fields
    remaining = fields - covered
    if observation is not None and observation.get("ok") is True and state_receipt and not remaining:
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
    state = env.get("state", "mount")
    if state not in ("mount", "container"):
        return None
    if not env.get("image"):
        return None
    limits = container_mod.envelope(container_mod.limits_from(env))
    return {"image": env["image"], "cpus": limits["cpus"], "memory_mb": limits["memory_mb"],
            "pids": limits["pids"], "read_only": state == "mount",
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
        if env.get("state") != "container" and not (env.get("services") or []):
            # Preserve the mount-run scope. Container-state has its own observed
            # writable create/start policy even when it declares no services.
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
        # Validate each raw declaration before normalized shape-cache reuse:
        # fractional memory bounds can share an integer launch shape with a valid
        # declaration, but cannot inherit its permission or poison its receipt.
        problem = _declared_but_not_applied(env, shape)
        if problem is not None:
            print(f"  admission: no observation for {shape['image']}: {problem}",
                  file=sys.stderr)
            continue
        key = tuple(shape[field] for field in
                    ("image", "cpus", "memory_mb", "pids", "read_only", "network"))
        if key not in _OBSERVATIONS:
            try:
                _OBSERVATIONS[key] = container_observation_mod.observe_for(
                    shape["image"],
                    {"cpus": shape["cpus"], "memory_mb": shape["memory_mb"]},
                    network=shape["network"], run_id=run_id,
                    read_only=shape["read_only"],
                    **({"container_state": True} if env.get("state") == "container" else {})).receipt()
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
            harness_runtime_identity: dict | None = None,
            _worker: dict | None = None,
            _private_paths: tuple[Path, ...] = (), dataset_runtime_identity=None) -> dict:
    # A direct caller must learn configuration faults before paying for actor work.
    # Construction reuses the broker's frozen declaration/config checks; entering
    # the broker (socket creation and requests) remains verifier-only. Evaluation
    # cache hits never reach run_one and need no newly configured judge.
    judge_spec = (verifier or {}).get("judge")
    if judge_spec is not None:
        from eval.service.judge import Judge, JudgeUnavailable
        try:
            Judge(judge_spec)
        except (JudgeUnavailable, ValueError, TypeError, OSError) as exc:
            return _invalid(task, exc, stage="judge configuration admission", service=None)
    try:
        task_python.admit(env, sandbox=sandbox, setup=setup, worker=_worker)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return _invalid(task, exc, stage="task Python runtime admission", service=None)
    try:
        if _worker is not None and candidate_runtime.has_python(repo):
            raise ValueError("managed Python candidates do not support workers")
        selected_harness = candidate_runtime.admit(
            repo, harness_runtime_identity, envs={task['task_id']: env or {}},
            setups={task['task_id']: setup or {}}, sandbox=sandbox)
    except (OSError, TypeError, ValueError) as exc:
        return _invalid(task, exc, stage="harness runtime preparation", service=None)
    if dataset_runtime_identity is not None:
        try:
            dataset_runtime_identity = dataset_runtime_identity_mod.for_evaluation(
                dataset_runtime_identity, [task], {task['task_id']: setup or {}},
                {task['task_id']: verifier or {}}, {task['task_id']: env or {}})
        except (OSError, TypeError, ValueError) as exc:
            return _invalid(task, exc, stage="dataset runtime admission", service=None)
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
    if observation is None and (env or {}).get("kind") == "exec" and (env or {}).get("state") == "container":
        observation = _observations_for({task["task_id"]: env}, [task], run_id=run_id).get(task["task_id"])
    refusal = _refuse_unobserved_policies(
        task, env, verifier, run_id=run_id,
        diagnostic_authorization=diagnostic_authorization, observation=observation)
    external_identities = {}
    if refusal is None:
        try:
            external_identities = external_runtime.identities(repo)
        except external_runtime.RuntimeUnavailable as exc:
            refusal = _invalid(task, exc, stage="harness runtime preparation", service=None)
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
        if dataset_runtime_identity is not None:
            cooperative.update(dataset_runtime_identity_mod.fields(dataset_runtime_identity))
        if observation is not None:
            cooperative.setdefault("env_observation", observation)
        try:
            external_runtime.require_current(repo, external_identities)
            # Admission is not evidence that every peer executed this runtime.
            # cooperative.run promotes Bun only after all actual worker receipts
            # agree; retain its refusal and per-worker diagnostics on failure.
            if not cooperative.get('invalid'):
                if ('bun' in external_identities and
                        (cooperative.get('harness_runtime') or {}).get('bun') != external_identities['bun']):
                    cooperative['invalid'] = {'stage': 'worker runtime identity',
                        'detail': 'Worker group did not establish the admitted Bun runtime identity'}
                    cooperative.pop('verdict', None)
                else:
                    external_runtime.stamp(cooperative, external_identities)
        except external_runtime.RuntimeUnavailable as exc:
            cooperative.setdefault('invalid', {'stage': 'harness runtime identity', 'detail': str(exc)})
        return cooperative
    # Bun receipts come from the actual preparation/launch path. Admission alone
    # cannot fill an early refusal or a missing peer receipt for the group reducer.
    runtime_receipt = {kind: {'identity': value} for kind, value in external_identities.items()
                       if kind != 'bun'}
    try:
        result = refusal if refusal is not None else _run_one(
            repo, task, timeout_s, sandbox, setup, verifier, verify_timeout_s,
            env, run_id, run_seed, recordings_root, trial_id=trial_id,
            runtime_receipt=runtime_receipt, worker=_worker,
            dataset_runtime_identity=dataset_runtime_identity,
            **candidate_runtime.fields(selected_harness),
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
    if refusal is None:
        try:
            external_runtime.require_current(repo, external_identities)
            external_runtime.stamp(result, {kind: value for kind, value in external_identities.items()
                                           if not (_worker is not None and kind == 'bun')})
        except external_runtime.RuntimeUnavailable as exc:
            result.setdefault('invalid', {'stage': 'harness runtime identity', 'detail': str(exc)})
    if capture_evidence:
        result['trial_evidence_output'] = evidence_output
    result.update(candidate_runtime.fields(selected_harness))
    if dataset_runtime_identity is not None:
        result.update(dataset_runtime_identity_mod.fields(dataset_runtime_identity))
    if runtime_receipt:
        result["runtime_services"] = runtime_receipt
        for kind in external_runtime.KINDS:
            if runtime_receipt.get(kind):
                result["harness_runtime"] = {**(result.get("harness_runtime") or {}),
                                             kind: runtime_receipt[kind]["identity"]}
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
            harness_runtime_identity: dict | None = None,
            private_paths: tuple[Path, ...] = (), dataset_runtime_identity=None) -> dict:
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
    try:
        task_artifact = task_python.admit(env_spec, sandbox=sandbox, setup=setup, worker=worker)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return _invalid(task, exc, stage="task Python runtime admission", service=None)
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
            task, verifier, sandbox=sandbox, env=env_spec,
            dataset_runtime_identity=dataset_runtime_identity)
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
    managed_python = harness_runtime_identity
    if managed_python is None and candidate_runtime.has_python(repo):
        try:
            managed_python = candidate_runtime.admit(
                repo, envs={task['task_id']: env_spec}, setups={task['task_id']: setup or {}},
                sandbox=sandbox)
        except (OSError, TypeError, ValueError) as exc:
            return _invalid(task, exc, stage="harness runtime preparation", service=None)
    bun_environment, bun_preparation = {}, None
    if managed_python is None:
        try:
            bun_environment = bun_runtime.admit(repo, os.environ)
            bun_preparation = bun_runtime.prepare(repo)
        except bun_runtime.RuntimeUnavailable as exc:
            return _invalid(task, exc, stage="harness runtime preparation", service=None)
    managed_placement = (python_exec_runtime.placement(managed_python, env_spec)
                         if managed_python is not None else None)
    managed_artifact = (managed_placement or {}).get('artifact')
    python_paths = tuple(Path(path) for path in (managed_artifact or {}).get('runtime_paths', []))
    task_python_paths = tuple(Path(path) for path in (task_artifact or {}).get('runtime_paths', []))
    if task_artifact is not None:
        runtime_receipt['task_python'] = task_python.receipt(task_artifact)
    if managed_python is not None:
        runtime_receipt['python'] = {'identity': managed_python}
    bun_paths = tuple(Path(p) for p in (bun_preparation or {}).get("runtime_paths", []))
    if bun_preparation:
        runtime_receipt["bun"] = bun_preparation
        execution_binary = bun_preparation.get("execution_binary", bun_preparation["binary"])
        bun_environment["HG_HARNESS_BUN"] = execution_binary
        if bun_preparation.get("responses_overlay"):
            bun_environment["HG_RESPONSES_OVERLAY_SHA256"] = bun_preparation["responses_overlay"]["identity"]["sha256"]
    runtime_paths = tuple(Path(p).expanduser()
                          for p in (_manifest.get("runtime_paths") or [])) + bun_paths + python_paths + task_python_paths
    if is_exec and env_spec.get("state") == "container":
        return _run_one_container_state(
            repo, task, env_spec=env_spec, setup=setup, verifier=verifier,
            timeout_s=timeout_s, verify_timeout_s=verify_timeout_s,
            run_id=run_id, run_seed=run_seed, recordings_root=recordings_root,
            runtime_paths=runtime_paths, runtime_env=bun_environment,
            runtime_receipt=runtime_receipt,
            **({'harness_runtime_identity': managed_python} if managed_python is not None else {}))
    service_specs = list(env_spec.get("services") or [])
    web_spec = env_spec.get("web") or {}
    # The internal network, whoever created it: `services.start` when there are
    # services, the model gateway when there are not. Tracked separately from `running`
    # so teardown knows which of the two it owns.
    held = progress_mod.task_of()
    phases = progress_mod.phases_for(held)
    stepwise_dir = None
    package_overlays = ()
    workspace = TaskWorkspace()
    workdir = workspace.path
    session = TaskSession(workspace, runtime_receipt)
    capture = TaskOutputCapture(workdir, evidence_output)
    try:
        if (bun_preparation or {}).get("responses_overlay"):
            bun_environment["HG_RESPONSES_ARTIFACT_DIR"] = workspace.responses_directory(task_mount, image=is_exec)
        try:
            targets(workdir, (*immutable_runtimes, *verifier_runtimes))
        except (ImmutableRuntimeUnavailable, OSError) as exc:
            return _invalid(task, exc, stage="immutable runtime admission", service=None)
        if agent_runtime:
            package_overlays = (workspace.stage_runtime(agent_runtime, stage=stage_agent_runtime),)
        # The task's initial state, before the harness sees anything. It used to be an
        # empty directory, which is why the old contract could only express "answer a
        # question": a dataset had nowhere to put the thing the task is *about*.
        with phases("setup"):
            if setup:
                try:
                    workspace.materialize(setup.get("files"), platform=PLATFORM_ROOT, materialize=_materialize)
                except ImmutableRuntimeUnavailable as exc:
                    return _invalid(task, exc, stage="immutable runtime setup", service=None)

            actor_task = ({**task, "goal": chess_stepwise.GOAL}
                          if stepwise_session is not None else task)
            task_file = workspace.write_public_task(
                with_cli_task(runtime_task(actor_task, setup, verdict_kind=(verifier or {}).get("kind"))))

        # Services come up before the harness and are health-checked before it starts
        # (INTERFACE.md §2.5.7, steps 1-3). A harness that starts against a service
        # that is still initialising does not produce a bad measurement, it produces a
        # measurement of a race -- so the failure is refused here rather than scored.
        if service_specs:
            try:
                session.start_services(service_specs,
                    recordings_root=(recordings_root or workdir.parent / f".recordings-{run_id or 'run'}"),
                    run_id=run_id or 'run', task_id=task['task_id'], run_seed=run_seed or '0')
            except services_mod.ServiceUnavailable as exc:
                return _invalid(task, exc, stage=exc.stage, service=exc.service)

        if web_spec:
            try:
                session.ensure_network(run_id=run_id or 'run', task_id=task['task_id'])
                record_base = recordings_root or workdir.parent
                session.start_web(factory=web_gateway_mod.WebGateway,
                    record_path=record_base / task['task_id'] / 'web' / 'requests.jsonl',
                    search=bool(web_spec.get('search')), fetch=bool(web_spec.get('fetch')))
            except Exception as exc:
                return _invalid(task, exc, stage="start", service="web")

        manifest = json.loads((repo / "harness.json").read_text())
        entry = manifest["entrypoint"]
        # Trees this harness needs in order to run at all — a CLI it drives, a
        # runtime it was built against. Declared, not inferred: the platform cannot
        # know where somebody's agent CLI is installed.
        runtime_paths = tuple(Path(p).expanduser()
                              for p in (manifest.get("runtime_paths") or [])) + bun_paths + python_paths + task_python_paths
        # Inside a container the entrypoint is still `<repo>/<entry>` because the
        # harness tree is mounted at its own host path; only the task's paths move,
        # because the dataset named a location *in the environment*.
        #
        # The interpreter is **the platform's own** for `exec`, mounted in read-only,
        # rather than whatever `python` the image happens to have -- which may be
        # nothing (measured: the real Terminal-Bench image has no `python3` at all).
        # `files` keeps the host's python selection when that alias exists. A fresh
        # machine with only python3 can use the interpreter running the platform.
        task_arg = (container_mod.to_container(task_file, workdir, task_mount)
                    if is_exec else str(task_file))
        workdir_arg = task_mount if is_exec else str(workdir)
        # The image's interpreter when it has one (§2.5.3), the platform's when it does
        # not. Preserve a files run's existing PATH selection when available.
        if managed_placement is not None:
            image_python = (managed_placement['base'] or {}).get('executable')
        else:
            image_python = container_mod.image_python(env_spec['image']) if is_exec else None
        python = actor_environment.select_interpreter(
            is_exec=is_exec, image_python=image_python, managed=managed_python is not None,
            managed_artifact=managed_artifact,
            task_artifact=task_artifact, files_python=_files_python)
        argv = actor_spec.argv_for(repo, entry, python=python, task_argument=task_arg,
            workdir_argument=workdir_arg,
            managed_argv=(python_exec_runtime.actor_argv(managed_python, env_spec, repo, entry)
                          if managed_python is not None else None))

        # The harness's own dependencies, from a directory outside the task's
        # environment, resolved for **this** interpreter's Python version. `None` is the
        # ordinary case: nobody configured one, and the run then behaves exactly as it
        # did before this existed. See `eval/containers/harness_runtime.py` for why this is not a
        # derived image and not the platform's interpreter.
        overlay = None if managed_python is not None else harness_runtime.lookup(
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
        state_dir = (workdir / '.harnessgrad-state' if managed_python is not None and is_exec
                     else repo / STATE_DIRNAME)
        # Created on demand, not just at staging time: a workspace committed before
        # `.state/` existed would otherwise make every run refuse to start, and the
        # sandbox needs a real directory to mount over. Empty and gitignored, so it
        # cannot move a sha.
        workspace.ensure_state(state_dir)

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
        home = workspace.home()

        if is_exec:
            child_env = container_mod.container_env(task_mount, deadline_s=timeout_s)
        else:
            child_env = actor_environment.native_home(harness_env(), home)
        child_env = actor_environment.with_task_state(child_env, bun_environment=bun_environment,
            worker_environment=({'HG_WORKER_ID': worker['id']} if worker is not None else {}), deadline_s=timeout_s,
            state_path=(container_mod.to_container(state_dir, workdir, task_mount)
                        if managed_python is not None and is_exec else str(state_dir)))
        child_env = actor_environment.with_python(child_env,
            overlay_environment=(harness_runtime.env_for(overlay) if overlay else None),
            managed=managed_python is not None, managed_artifact=managed_artifact,
            is_exec=is_exec, task_artifact=task_artifact)

        # Publish the agent's model onto the task's network. A containerised harness
        # cannot reach a model on the host's loopback -- measured, every obvious route
        # fails -- and the wrong fix is `--network bridge`, which would give the task the
        # internet and silently change what it is (`eval/service/modelgate.py`).
        if is_exec:
            base = child_env.get("HG_AGENT_BASE_URL")
            if base and os.environ.get("HG_AGENT_BACKEND", "mock") != "mock" \
                    and modelgate_mod.is_host_local(base):
                child_env['HG_AGENT_BASE_URL'] = session.start_container_model(
                    base, run_id=run_id or 'run', task_id=task['task_id'])

        elif env_spec.get("network") == "none" \
                and os.environ.get("HG_AGENT_BACKEND", "mock") != "mock":
            try:
                base = child_env.get("HG_AGENT_BASE_URL")
                if not base:
                    raise modelgate_mod.GatewayUnavailable("live offline agent needs HG_AGENT_BASE_URL")
                session.start_native_model(base, factory=NativeModelRoute,
                                           observe_http=model_failure.enabled(verifier))
            except (modelgate_mod.GatewayUnavailable, ValueError) as exc:
                return _invalid(task, exc, stage="native model route admission", service=None)

        # How the harness finds the services, and the whole of what it is told about
        # them. `HG_SVC_*` rather than `HG_ENV_*`: the environment's own model,
        # credential and seed go to the service containers only (§2.5.7).
        if session.services is not None:
            child_env.update(services_mod.harness_env(service_specs))
        if session.web is not None:
            child_env["HG_SVC_WEB_URL"] = session.web.url

        # On a `services` environment the harness joins the task's internal network,
        # which is how "reachable by name, not on the internet" is delivered. Passing
        # `env_spec["network"]` straight through would ask docker for a network called
        # `services`, which does not exist.
        # With a gateway published, the harness joins the internal network so it can
        # reach it. Otherwise the declared value stands.
        network = (session.network_name if session.network_name is not None
                   else env_spec.get("network", "none"))

        # Everything the harness needs to run at all, plus the platform's interpreter.
        # The platform's interpreter is mounted only when it is going to be used, so an
        # image that brings its own does not also get the host's Python on its disk.
        runtime = ((tuple(runtime_paths)
                    + (() if image_python or managed_python is not None else container_mod.interpreter_paths()))
                   if is_exec else ())

        if stepwise_session is not None:
            # Project only this read-only socket directory. Neither gold nor session
            # state is written beneath the actor's writable task directory.
            stepwise_dir = session.prepare_stepwise(stepwise_session, factory=StepwiseRelay,
                contract=chess_stepwise.STEPWISE, public_fields=chess_stepwise.PUBLIC_FIELDS, timeout_s=timeout_s)
            child_env["HG_STEPWISE_SOCKET"] = str(session.stepwise.path)

        # The dataset's interaction environment, if it declares one. Started here and not in
        # the harness: the whole point is that the agent gets a socket and never the state.
        # A declared relay that cannot start is `unmeasured`, not a task scored against a hole.
        if tool_relay_mod.declaration(setup) is not None:
            try:
                session.start_tool(setup, context=({
                    'task_id': task['task_id'], 'run_id': run_id or 'direct',
                    'trial_id': trial_id or 'trial-1', 'seed': run_seed}
                    if record_spec is not None or model_trial_seed else None),
                    private_diagnostics=evidence_output is not None)
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
                spec = actor_spec.container(argv, image=env_spec['image'], task_dir=workdir,
                    task_mount=task_mount, harness_root=repo, state_dir=state_dir,
                    managed=managed_python is not None, environment=child_env, timeout_s=timeout_s,
                    network=network, limits=container_mod.limits_from(env_spec), readonly_paths=runtime,
                    immutable_runtimes=immutable_runtimes,
                    actor_mounts=tuple(services_mod.mounts_for(
                        'harness', service_specs, session.services.recordings if session.services else {})))
            elif not sandbox:
                spec = actor_spec.process(argv, timeout_s=timeout_s, cwd=workdir, environment=child_env)
            else:
                work_root = Path(os.environ.get('HARNESSGRAD_WORK_ROOT') or repo.parent.parent)
                if worker is not None:
                    work_root = worker['root']
                surfaced_runtime = harness_runtime.surfaced_paths(
                    overlay, work_root=work_root, platform=PLATFORM_ROOT)
                if managed_python is not None or task_artifact is not None:
                    hidden = sandbox_mod.hidden_trees(work_root, PLATFORM_ROOT)
                    surfaced_runtime += tuple(path for path in (*python_paths, *task_python_paths)
                        if any(path.is_relative_to(tree) for tree in hidden))
                spec = actor_spec.sandbox(argv, platform=PLATFORM_ROOT, work_root=work_root,
                    task_dir=workdir, environment=child_env, timeout_s=timeout_s, repo=repo,
                    state_dir=state_dir, runtime_paths=runtime_paths, limits=subject_limits,
                    stepwise_dir=stepwise_dir, surfaced_runtime=surfaced_runtime,
                    network=env_spec.get('network'),
                    model_socket=(session.model.socket_path if isinstance(session.model, NativeModelRoute) else None),
                    model_path=(session.model.path if isinstance(session.model, NativeModelRoute) else ''),
                    tool_socket=(worker['socket'] if worker is not None else
                                 session.tool.path if session.tool is not None else None),
                    package_overlays=package_overlays, immutable_runtimes=immutable_runtimes,
                    managed=managed_python is not None, private_paths=private_paths)
            return actor_execution.execute(spec, container_run=container_mod.run,
                                            process_run=run_process, sandbox_run=sandbox_mod.run)

        try:
            with phases("agent"):
                if task_artifact is not None:
                    try:
                        task_python.admit(env_spec, sandbox=sandbox, setup=setup, worker=worker)
                    except (OSError, TypeError, ValueError, RuntimeError) as exc:
                        return _invalid(task, exc, stage="task Python runtime admission", service=None)
                if managed_python is not None:
                    try:
                        if not candidate_runtime.matches_source(repo, managed_python):
                            raise ValueError('Managed candidate source changed after runtime admission')
                    except (OSError, TypeError, ValueError) as exc:
                        return _invalid(task, exc, stage='harness runtime identity', service=None)
                if session.stepwise is not None:
                    session.stepwise.start()
                try:
                    capture.attempted = True
                    try:
                        capture.process = launch()
                    except BaseException as exc:
                        capture.error = exc
                        raise
                finally:
                    if session.stepwise is not None:
                        session.stepwise.stop()
                    if session.tool is not None:
                        session.tool.stop()
            exit_code, stderr = capture.process.returncode, capture.process.stderr[-2000:]
            if session.tool is not None:
                fault = session.tool.fault()
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
            if session.stepwise is None:
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
            output = capture.collect(stepwise=session.stepwise is not None)
            answer_path, answer, trace = output.answer_path, output.answer, output.trace

        # The harness has exited. Grade the controller's completed live exchange,
        # or its frozen artifacts on the existing offline path (§2.2 and §2.4).
        with phases("verify"):
            if task_artifact is not None:
                try:
                    task_python.admit(env_spec, sandbox=sandbox, setup=setup, worker=worker)
                except (OSError, TypeError, ValueError, RuntimeError) as exc:
                    return _invalid(task, exc, stage="task Python runtime admission", service=None)
            if session.tool is not None and record_spec is not None:
                try:
                    verifier = task_verification.with_tool_recording(
                        workdir, verifier, session.tool, archive_root=recordings_root)
                except (OSError, ValueError, RuntimeError) as exc:
                    return _invalid(task, exc, stage="tool recording", service=None)
            if session.stepwise is not None:
                verdict = chess_stepwise.finish(session.stepwise, exit_code)
            else:
                verify_spec = task_verification.VerifySpec(
                    verifier, env_spec, sandbox, verify_timeout_s, task['task_id'], run_id,
                    trial_id, immutable_runtimes)
                verdict = task_verification.verify_output(workdir, verify_spec, answer,
                    service_specs=service_specs, running=session.services, verify=_verify)
                if verdict.get("immutable_runtime_error"):
                    return _invalid(task, verdict["unmeasured"],
                                    stage="immutable runtime verification", service=None)
                if protocol is not None:
                    verdict["protocol"] = protocol

        if (verifier or {}).get("result_contract") is not None:
            verdict["submission"] = {"answer_file": "missing" if not answer_path.exists() else
                                     "empty" if not answer.strip() else "present"}
        if session.tool is not None and session.tool.recording is not None:
            verdict["interaction_record"] = session.tool.recording
        # The environment's own spend, read from the recordings **before teardown**:
        # the containers are about to be removed and their evidence with them.
        env_usage = session.environment_usage(project_model=_environment_model_usage)

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
                **candidate_runtime.fields(managed_python),
                "harness_runtime": (python_exec_runtime.record(managed_python, env_spec,
                                        python_minor=manifest['runtime']['python'])
                                    if managed_python is not None else harness_runtime.record(
                    overlay, platform_interpreter=(image_python is None),
                    # The `files` case runs on the host's `python` and the caller is the
                    # only one that can ask which version that is; for an image the
                    # overlay already carries it. Left unasked, a `files` point said
                    # `python: null`, which is the one field two such points differ in.
                    interpreter=(None if is_exec
                                 else harness_runtime.interpreter_version(python)))),
                "runtime_boundary": {
                    "agent_timeout_s": timeout_s,
                    "verifier_timeout_s": verify_timeout_s,
                    "agent_network": env_spec.get("network", "inherited") if not is_exec else network,
                    "agent_package_projected": bool(package_overlays),
                    "verifier_package": "trusted"},
                # `account()` and not just "published": the connection counts are what
                # turn a harness-side `Connection error` into a named cause. Read while
                # the gateway is still up -- `finally` stops it right after this returns.
                "model_gateway": session.model_evidence(),
                "web_gateway": session.web_evidence(),
                "identity": _last_identity(trace)}
    finally:
        session.close(capture,
            responses_enabled=lambda: (bun_preparation or {}).get('responses_overlay'),
            native_model=lambda model: isinstance(model, NativeModelRoute),
            task_label=lambda: task['task_id'])



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
             persist_trial_report: bool = True,
             dataset_runtime_identity: dict | None = None,
             harness_runtime_identity: dict | None = None,
             verification_context: dict | None = None) -> dict:
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
                         dataset_runtime_identity=dataset_runtime_identity,
                         **({'verification_context': verification_context}
                            if verification_context is not None else {}),
                         **({'harness_runtime_identity': harness_runtime_identity}
                            if harness_runtime_identity is not None else {}),
                         diagnostic_authorization=diagnostic_authorization)
        if diagnostic_authorization is not None:
            result["enforcement"] = diagnostic_authorization.record()
        return result




def _environment_model_usage(report):
    """Preserve broker attempt coverage; its usage sums alone are lower bounds."""
    complete = report.get("usage_complete") is True
    usage = report.get("usage") or {}
    return {"input_tokens": usage.get("prompt_tokens") if complete else None,
            "output_tokens": usage.get("completion_tokens") if complete else None,
            "input_tokens_reported": usage.get("prompt_tokens"),
            "output_tokens_reported": usage.get("completion_tokens"),
            "calls": len(report["calls"]), "attempts": len(report["attempts"]),
            "unknown_usage_attempts": sum(not attempt["usage_known"] for attempt in report["attempts"]),
            "usage_complete": complete, "services_reported": 1}


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
              persist_trial_report: bool = True,
              dataset_runtime_identity: dict | None = None,
              harness_runtime_identity: dict | None = None,
              verification_context: dict | None = None) -> dict:
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
    context_check = evaluation_plan.capture_verification(
        tasks, scorable, setups, verifiers, envs, sandbox=sandbox, expected=verification_context)
    context, context_error = context_check.value, context_check.error
    dataset_check = evaluation_plan.capture_dataset(
        tasks, setups, verifiers, envs, sandbox=sandbox, expected=dataset_runtime_identity)
    runtime_identity, runtime_identity_error = dataset_check.value, dataset_check.error
    task_runtimes = dataset_runtime_identity_mod.project_many(
        runtime_identity, [task['task_id'] for task in tasks])
    harness_check = evaluation_plan.capture_harness(
        repo, tasks, setups, envs, sandbox=sandbox, expected=harness_runtime_identity)
    selected_harness, harness_runtime_error = harness_check.value, harness_check.error
    evaluation_protocols = evaluation_plan.protocols_for(tasks)

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

    external_check = evaluation_plan.capture_external(repo, harness_error=harness_runtime_error)
    runtime_identities, runtime_error = external_check.value, external_check.error
    admission_errors = evaluation_plan.AdmissionErrors(
        context_error, runtime_identity_error, harness_runtime_error, runtime_error)
    cache_allowed = external_runtime.cacheable(runtime_identities)

    native_identities, native_errors, native_profiles = {}, {}, {}
    for task in tasks:
        tid = task['task_id']
        spec = (verifiers or {}).get(tid) or {}
        # The stepwise controller scores in process, bypassing the offline
        # verifier and its native declaration. Only the executed scorer supplies
        # an admission identity for cache keys and trial receipt checks below.
        if (evaluation_protocols.get(tid) == chess_stepwise.STEPWISE
                or 'native_runtime' not in spec or runtime_error is not None):
            continue
        from eval.verification import native_runtime
        profile_key = None
        try:
            binding = native_runtime.validate_route(
                spec, sandbox=sandbox, env=(envs or {}).get(tid))
            profile_key = json.dumps(binding, sort_keys=True, default=str)
            if profile_key not in native_profiles:
                # Validate before cache lookup, once per selected declaration.
                # Fresh scoring admits/seals again after the actor has stopped.
                with native_runtime.admit(binding) as runtime:
                    identity = json.loads(json.dumps(runtime.receipt))
                if identity.get('verified') is not True:
                    raise native_runtime.NativeRuntimeUnavailable('native runtime identity is unverified')
                native_profiles[profile_key] = identity, None
            identity, error = native_profiles[profile_key]
        except (native_runtime.NativeRuntimeUnavailable, TypeError, ValueError) as exc:
            identity, error = None, str(exc)
            if profile_key is not None:
                native_profiles[profile_key] = identity, error
        if error is not None:
            native_errors[tid] = error
        else:
            native_identities[tid] = identity

    def measurement_identity(task, task_context):
        tid = task['task_id']
        return measurement_cache.MeasurementIdentity(
            harness_sha, tid, trials, task_context, task_runtimes[tid], selected_harness,
            runtime_identities, ((verifiers or {}).get(tid) or {}).get('scorer_bundle'),
            native_identities.get(tid), evaluation_protocols.get(tid))

    def measurement_key(task):
        return measurement_identity(task, context).key()

    def trial_identity(trial: int) -> str:
        return trial_options.trial_identity(trial)

    cache = cache if cache is not None else {}
    scores = aggregation.Scores(trials)
    evidence = aggregation.TaskEvidence(trials)
    runtime_evidence = aggregation.RuntimeEvidence()
    coverage = aggregation.OutcomeCoverage()
    environment_usage = evaluation_usage.EnvironmentUsage()

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
    observations = ({} if diagnostic_authorization is not None or harness_runtime_error is not None
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
    trial_options = trial_worker.RunOptions(
        repo, sandbox, run_id, run_seed, round_no, harness_sha, trials, len(tasks),
        recordings_root, diagnostic_authorization, private_paths)
    task_worker = trial_worker.TaskTrialWorker(
        trial_options, trial_worker.RunBindings(selected_harness, runtime_identities),
        measure=lambda *a, **kw: run_one(*a, **kw),
        write_progress=lambda *a, **kw: _write_progress(*a, **kw))

    def _measure(task_index: int, task: dict) -> dict:
        tid = task["task_id"]
        task_runtime = task_runtimes[tid]
        task_context = verification_context_mod.project(context, [tid]) if context is not None else None
        runtime_fields = {**dataset_runtime_identity_mod.fields(task_runtime),
                          **candidate_runtime.fields(selected_harness),
                          **verification_context_mod.fields(task_context)}
        enforcement = (diagnostic_authorization.record()
                       if diagnostic_authorization is not None else None)
        refusal, observation = evaluation_plan.admit_before_cache(
            repo, task, scorable, setups, verifiers, envs, sandbox=sandbox,
            task_context=task_context, task_runtime=task_runtime, errors=admission_errors,
            native_errors=native_errors, external_identities=runtime_identities,
            run_id=run_id, authorization=diagnostic_authorization, observations=observations,
            protocol=evaluation_protocols.get(tid), policy_refusal=_refuse_unobserved_policies)
        if refusal is not None:
            _write_progress(progress_dir, round_no, tid, [], refusal.reason,
                            **refusal.progress_fields(runtime_fields, enforcement))
            return {'task_id': tid, 'invalid': refusal.reason}
        cached = measurement_cache.lookup(
            cache, measurement_identity(task, task_context), fresh=fresh_measurement(tid),
            allowed=cache_allowed, verifier=(verifiers or {}).get(tid), run_id=run_id,
            trial_identity=trial_identity)
        if cached.invalid is not None:
            return {'task_id': tid, 'invalid': cached.invalid}
        if cached.hit is not None:
            return {'task_id': tid, 'cached': cached.hit}

        plan = trial_worker.TaskPlan(
            task, task_index, task_runtime, native_identities.get(tid),
            evaluation_protocols.get(tid), observation, runtime_fields, enforcement)
        inputs = trial_worker.TaskInputPort(
            read=lambda kind: ({'setup': setups, 'verifier': verifiers, 'environment': envs}[kind] or {}).get(tid),
            fresh=lambda: fresh_measurement(tid),
            check_context=lambda: trial_worker.check_final_context(
                task, scorable, setups, verifiers, envs, sandbox=sandbox, expected=task_context, task_id=tid))
        journal = trial_worker.TaskEvidenceJournal(
            trial_options, tid, progress_dir, trial_protocol, ordinary_plan, evidence_plan,
            tid in evidence_tasks, trial_rows.get(tid, []))
        return task_worker.run(plan, inputs, journal)

    outcomes = _in_parallel(tasks, _measure, jobs)

    # The merge. Sequential and in **task order** on purpose: a run that used `--jobs 3`
    # and one that did not must produce comparable records, down to the order of
    # `runtime_records` and `declarations` -- an order decided by whichever thread finished
    # first would not be.
    for task in tasks:
        tid = task['task_id']
        key = measurement_key(task)
        outcome = outcomes[tid]
        environment_usage.observe(tid, outcome)
        kind = coverage.classify(tid, outcome)
        if kind == 'not_run':
            continue
        if kind == 'cached':
            scores.cached(tid, outcome['cached'])
            evidence.cached(tid, outcome['cached'], evaluation_protocols.get(tid))
            runtime_evidence.record(tid, outcome['cached'])
            continue
        if kind in ('invalid', 'timeout'):
            runtime_evidence.record(tid, outcome)
            if kind == 'timeout':
                scores.timeout(tid)
            continue
        measured = outcome['measured']
        result = measured[-1]
        verdict = result.get('verdict') or {}
        scores.measured(tid, measured)
        failure = evidence.measured(tid, measured, result, verdict)
        runtime_evidence.measured(tid, result)
        if cache_allowed and not fresh_measurement(tid):
            runtime_fields = {**dataset_runtime_identity_mod.fields(task_runtimes[tid]),
                **verification_context_mod.fields(verification_context_mod.project(context, [tid])),
                **candidate_runtime.fields(selected_harness)}
            cache[key] = aggregation.measured_cache_entry(tid, result, failure, scores, evidence, runtime_fields)

    harness_usage_by_task = evaluation_usage.harness_reports(tasks, outcomes, evidence.trial_traces, evidence.traces)
    tokens = evaluation_usage.harness_totals(harness_usage_by_task, trials=trials)
    env_usage = environment_usage.totals()

    # A cached task never ran, so a fully-cached evaluation reports nothing --
    # correct: the platform did not execute anything this time.
    tampered = {k: v for k, v in diff(before, snapshot(PLATFORM_ROOT)).items() if v}
    identity_declared, identity_variants = runtime_evidence.identities()
    scorer_isolation_class = common_class(scores.per_task_isolation.values())
    score_std = scores.run_spread()
    startup_failure = aggregation.startup_failure(evidence.harness_failed, scores.per_task, coverage.not_run)
    repeated = {}
    if ordinary_plan:
        repeated['trial_evidence'] = aggregation.ordinary_report(
            ordinary_plan, tasks, trial_rows, progress_dir, persist=persist_trial_report)
    if trial_protocol:
        repeated = {'repeated_trials': aggregation.repeated_report(
            trial_protocol, tasks, trial_rows, trials=trials, trial_identity=trial_identity,
            run_id=run_id, round_no=round_no, harness_sha=harness_sha,
            progress_dir=progress_dir, persist=persist_trial_report)}
    return {**repeated, **dataset_runtime_identity_mod.fields(runtime_identity),
            **verification_context_mod.fields(context),
            **candidate_runtime.fields(selected_harness),
            "per_task": scores.per_task, "per_task_std": scores.per_task_std, "score_std": score_std,
            "scorer_isolation_class": scorer_isolation_class,
            # Preserve unobserved diagnostic facts without making them comparable.
            "scorer_isolation_comparable": scorer_isolation_class != "unknown",
            "per_task_scorer_isolation": scores.per_task_isolation,
            "per_task_trials": scores.per_task_trials, "trials": trials,
            # The same measurement at the check's own resolution. `test_rate` averages only
            # the tasks whose check reports a tally, so `test_rate_n_tasks` travels with it.
            "per_task_test_rate": scores.tests.per_task,
            "per_task_test_rate_trials": scores.tests.per_task_trials,
            "test_rate": (sum(scores.tests.per_task.values()) / len(scores.tests.per_task)
                          if scores.tests.per_task else None),
            "test_rate_n_tasks": len(scores.tests.per_task),
            "tests_passed": scores.tests.passed,
            "tests_total": scores.tests.total,
            "traces": evidence.traces, "tampered": tampered,
            "trial_traces": evidence.trial_traces, "trial_verdicts": evidence.trial_verdicts,
            "cached_tasks": coverage.cached_tasks,
            "verdicts": evidence.verdicts,
            "evaluation_protocols": evaluation_protocols,
            "invalid": coverage.invalid,
            "timeouts": coverage.timeouts,
            "not_run": coverage.not_run,
            "startup_failure": startup_failure,
            "harness_failed": evidence.harness_failed,
            "harness_output": evidence.harness_output,
            "harness_runtime": runtime_evidence.records,
            "runtime_diagnostics": runtime_evidence.diagnostics,
            "env_usage": env_usage,
            "harness_usage_by_task": harness_usage_by_task,
            "environment_usage_by_task": environment_usage.by_task,
            "model_gateway": runtime_evidence.model_gateway,
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
