"""Actor-role environment and interpreter projection; no workspace/resource allocation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from harnessgrad import python_exec_runtime, task_python


def _files_python() -> str:
    """Keep the host's python selection; python3-only hosts use this interpreter."""
    return "python" if shutil.which("python") else sys.executable


def harness_env(*, credential_names) -> dict:
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
           and k not in credential_names()}
    return env




def select_interpreter(*, is_exec, image_python, managed, managed_artifact, task_artifact, files_python):
    python = (managed_artifact['python'] if managed else
              image_python or (sys.executable if is_exec else files_python()))
    if task_artifact is not None and not managed:
        python = str(Path(shutil.which(python) or python).resolve(strict=True))
    return python


def native_home(environment, home):
    value = dict(environment)
    value.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                 XDG_DATA_HOME=str(home / '.local' / 'share'), XDG_CACHE_HOME=str(home / '.cache'),
                 TMPDIR=str(home / 'tmp'))
    return value


def with_task_state(environment, *, bun_environment, worker_environment, deadline_s, state_path):
    value = dict(environment)
    value.update(bun_environment)
    value.update(worker_environment)
    value['HG_AGENT_DEADLINE_S'] = str(deadline_s)
    value['HARNESSGRAD_STATE'] = state_path
    return value


def with_python(environment, *, overlay_environment, managed, managed_artifact, is_exec, task_artifact):
    value = dict(environment)
    if overlay_environment is not None:
        value.update(overlay_environment)
    if managed:
        value = python_exec_runtime.actor_environment(value, managed_artifact, image=is_exec)
    return task_python.environment(value, task_artifact)
