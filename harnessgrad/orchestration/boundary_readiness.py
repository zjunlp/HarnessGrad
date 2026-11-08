"""Run boundary readiness and truthful public boundary descriptions.

These checks own no run directory, process configuration or resource lifetime.
The root chooses when to check, after selected capabilities and before publication.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import eval.containers.container as container
import eval.containers.sandbox as sandbox
from eval.run.integrity import PLATFORM_ROOT
from harnessgrad.environments import _required_env_kinds


def check_container_boundaries(envs: dict, *, reporter) -> bool:
    """Check each selected exec image; refuse the first unusable boundary."""
    if "exec" in _required_env_kinds(envs):
        version = container.available()
        if version is None:
            reporter.note(
                "refusing to run: this dataset needs an `exec` environment but no "
                "docker daemon is reachable. Starting the run anyway would either "
                "score zero on every task or fall back to the host, and both look "
                "like a weak harness.", level="error")
            return False
        for image in sorted({spec["image"] for spec in envs.values()
                             if spec.get("kind") == "exec"}):
            # `PLATFORM_ROOT`, not `PLATFORM`: the latter is the tuple of names that
            # makes up the platform, and passing it here crashed every `exec` run with
            # a `tuple / str` TypeError before a single container started.
            check = container.self_check(PLATFORM_ROOT, image)
            if not check["ok"]:
                reporter.note(f"refusing to run: container boundary for {image!r} "
                             f"does not hold: {check['detail']}", level="error")
                return False
            reporter.note(f"container boundary holds (docker {version}): "
                         f"{check['detail']}", level="info")

    return True


@dataclass(frozen=True)
class SandboxDescription:
    state: dict
    line: str


def check_sandbox_boundary(requested: bool, work_root: Path, envs: dict,
                           *, reporter) -> SandboxDescription | None:
    """Check the host boundary and describe host and task boundaries separately."""
    sandbox_state = {"requested": requested, "active": False,
                     "plan_version": sandbox.PLAN_VERSION}
    if requested:
        report = sandbox.self_check(PLATFORM_ROOT, work_root)
        if not report["ok"]:
            reporter.note(f"refusing to run: {report['detail']}", level="error")
            return None
        sandbox_state["active"] = True
        sandbox_state["detail"] = report["detail"]
        # The cap, on the run itself rather than only on a task: a reader asking "could a
        # runaway harness have taken the machine" is asking about the run, and `applied`
        # is the answer -- false means the scope could not be created and nothing bounded
        # the subject.
        envelope = sandbox.envelope()
        sandbox_state["envelope"] = envelope
        sandbox_line = "bwrap active — the harness cannot see this directory"
        if envelope["applied"]:
            sandbox_line += (f" · capped at {envelope['limits']['memory']}, "
                             f"{envelope['limits']['cpus']:g} cpus, "
                             f"{envelope['limits']['pids']} tasks")
        else:
            reporter.note(f"the sandbox has no resource envelope: {envelope['reason']}. "
                         f"Nothing bounds a runaway harness on this run.",
                         level="warn")
    else:
        sandbox_line = ("OFF — the harness can read the driver and .env; only the "
                        "hash check stands between it and the scorer")
        reporter.note(
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
        # `requested`, **not** `sandbox`: the bare name in this module is
        # `import eval.containers.sandbox as sandbox`, so a truthiness test on it tests a *module
        # object* and is always true. Measured: this condition silently never fired, so
        # the header named the container boundary and left out that the host-side one
        # was off. It is the same shadowing that produced `'bool' object has no
        # attribute 'SandboxUnavailable'` in Step 3, which is why it is spelled out
        # here instead of left to look obvious.
        if not requested:
            # `--no-sandbox` does not disable the container: the container *is* the
            # task's environment (INTERFACE.md §2.5.5), so removing it would not make
            # the task run unisolated, it would make the task not run at all. Saying
            # so beats a line that reads as "no boundary anywhere".
            where += " (host-side bwrap OFF for the method)"
        sandbox_line = where

    return SandboxDescription(sandbox_state, sandbox_line)
