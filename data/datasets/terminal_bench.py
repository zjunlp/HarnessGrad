"""Terminal-Bench 2 as a dataset. INTERFACE.md §2.5.9.

    python tools/import_env.py ...          # not needed: every task ships an image
    driver.py --dataset terminal_bench ...

Where the task set lives
------------------------
A checkout of `harbor-framework/terminal-bench-2` (measured: 89 tasks, 83 MB, 117 MB for
a sample image). It is deliberately outside the platform tree:

    HG_TERMINAL_BENCH   default /mnt/20t/xubuqiang/datasets/terminal-bench

Each task is a directory with a uniform shape -- measured across all 89, with no
exceptions: `instruction.md`, `task.toml`, `environment/` (a Dockerfile, often with
encrypted data), `tests/test.sh` + `tests/test_outputs.py`, and `solution/`.

What it costs to get the images
-------------------------------
Every task ships a prebuilt image, so nothing is *built* — but 89 images have to be
**pulled**, once, by `tools/import_env.py` (§2.5.5). Measured: ~104 GB on disk, and the
distribution is very uneven — most are hundreds of megabytes while `mteb-retrieve` is
**21.6 GB** and the `qemu-*` pair are 3.15 GB each. On a host whose registry access is a
flaky mirror, those large blobs are exactly what fails: three tasks
(`mteb-leaderboard`, `pytorch-model-recovery`, `reshard-c4-data`) could not be fetched here
at all, failing with `TLS handshake timeout` partway through a layer. That is an external
limit with a name, not a platform defect, and it is worth checking disk before starting:
the images are the run's largest fixed cost by two orders of magnitude.

What maps to what
-----------------
| Terminal-Bench | here |
| --- | --- |
| `instruction.md` | the task's `goal` |
| `task.toml` `environment.docker_image` | `env.image` (a tag; the driver pins it and refuses to start without a digest) |
| `task.toml` `environment.allow_internet` | `env.network` (`bridge` / `none`) |
| `task.toml` `environment.cpus` / `memory_mb` | `env.cpus` / `env.memory_mb` |
| `task.toml` `agent.timeout_sec` / `verifier.timeout_sec` | `env.agent_timeout_s` / `env.verify_timeout_s` |
| the image's `WORKDIR` | `env.workdir`, and `state: "container"` because the task lives there |
| `tests/` | `VERIFY.inputs` with `dst: /tests` -- copied in **after** the harness stops |
| `/logs/verifier/reward.txt` | `VERIFY.reward_file` |

`SETUP` is empty on purpose: the environment is the image's, and every task's Dockerfile
does its own `COPY`. That is also why the state has to be `container` rather than a bind
mount -- measured: the image ships `/app` **empty** and the instruction says to write
`/app/ars.R`, so the task's own idea of where it lives is the container.

All 89 are single-container, and a metadata flag said otherwise
--------------------------------------------------------------
The first version of this adapter excluded `mcmc-sampling-stan` and `rstan-to-pystan`
because their `task.toml` carries `metadata.custom_docker_compose = True`. That was
**wrong**, and it was wrong in the direction that quietly shrinks a benchmark: the flag is
stale. Measured over the whole checkout:

    metadata.custom_docker_compose true:  2 tasks
    docker-compose files present:         0

Both tasks' `environment/` holds a self-contained `Dockerfile` -- `FROM ubuntu:24.04`,
its packages, `WORKDIR /app`, `COPY task-deps/...` -- and each one carries the line
`# Fields moved from docker-compose.yaml`. They were migrated to single containers and
the flag was never cleared.

So nothing is excluded, and `EXCLUDED` is kept with nothing in it because the mechanism
is still the right one: if a task genuinely needs a multi-container environment it should
be named here rather than silently dropped, so that a curve's task count can be
reconciled against the benchmark's own. It is empty because no task needs it -- which is
a finding, and one that cost two tasks when it was assumed rather than checked.

A split the benchmark does not have
-----------------------------------
Terminal-Bench is an exam, not a training set: it declares no train/eval split, and the
platform's contract wants one because a method needs diagnostics to work from and must not
be scored on them (§2.3). So the split is **imposed here**, derived from the task id so it
is stable across runs and reproducible from the record, and it is stated loudly because a
score on the eval side is a score on a third of the benchmark, not on the benchmark.
`HG_TB_SPLIT=none` runs everything as eval and accepts the warning the driver prints.
"""


from __future__ import annotations


#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"


#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the 89 official tasks.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

import os
import re
import tomllib
from pathlib import Path

#: Where the checkout is. Outside the platform tree on purpose: it is dataset content,
#: not platform code, and the platform hashes its own tree.
ROOT = Path(os.environ.get("HG_TERMINAL_BENCH",
                           "/mnt/20t/xubuqiang/datasets/terminal-bench"))

#: Tasks to leave out, by name, with the reason. **Empty**, and deliberately kept empty
#: rather than deleted: a task that genuinely needs a multi-container environment should
#: be named here so the omission is visible, and no Terminal-Bench 2 task does -- see the
#: module docstring for the stale flag that made it look otherwise.
EXCLUDED: dict[str, str] = {}

#: A subset for iterating without pulling 89 images. Comma-separated task ids.
ONLY = [t for t in (os.environ.get("HG_TB_TASKS") or "").split(",") if t.strip()]

#: `bridge` when the task wants the internet, `none` when it does not. Measured: all 89
#: declare `allow_internet = true`, so this is a mapping and not a coincidence.
_NETWORK = {True: "bridge", False: "none"}

_WORKDIR_RE = re.compile(r"^\s*WORKDIR\s+(\S+)\s*$", re.MULTILINE | re.IGNORECASE)

#: The reward convention, measured across all 89: every `tests/test.sh` ends by writing
#: `1` or `0` here and **exits 0 either way** (§2.5.9).
REWARD_FILE = "/logs/verifier/reward.txt"

#: Every task's `test.sh` runs `pytest --ctrf <this>`, so the failing test and its
#: assertion are on disk in a machine-readable form. The platform copies it out and puts
#: the failures in the verdict, because the alternative -- the last 600 characters of
#: stdout -- is whatever the check printed last, and before the tests run every task
#: `apt-get install`s its way to a test runner. Measured on `cancel-async-tasks`: the
#: whole verdict was `Setting up libcurl4 ... Processing triggers for libc-bin`, so the
#: improver could not see that the harness was one test away. A task ID is a task ID, but
#: a verdict a method cannot learn from is a wasted round.
CHECK_REPORT = "/logs/verifier/ctrf.json"


def _workdir(task_dir: Path) -> str:
    """The image's `WORKDIR`, which is where the task's state lives.

    Read from the Dockerfile because that is where Terminal-Bench declares it -- measured:
    the Dockerfile sets `WORKDIR /app` and the image's own config agrees. Falling back to
    `/app` rather than to the platform's default, because a task that forgot to set one
    would be broken in the benchmark too.
    """
    dockerfile = task_dir / "environment" / "Dockerfile"
    if dockerfile.is_file():
        found = _WORKDIR_RE.findall(dockerfile.read_text(errors="replace"))
        if found:
            return found[-1]
    return "/app"


def _task_dirs() -> list[Path]:
    """Every task directory in the checkout, under either layout.

    Terminal-Bench 2 keeps its tasks at the repository root; Terminal-Bench **2.1**
    keeps them under `tasks/` — measured: 89 at the root of `terminal-bench-2` and 91
    under `tasks/` in `terminal-bench-2-1`, with the *same* `20251031` image tags in
    both. Looking in both places rather than pinning one means `HG_TERMINAL_BENCH` can
    point at either checkout, and the version a run measured is already on every curve
    point as the task ids.
    """
    if not ROOT.is_dir():
        raise ValueError(
            f"no Terminal-Bench checkout at {ROOT}. Clone one, or point "
            f"HG_TERMINAL_BENCH at it:\n"
            f"    git clone --depth 1 https://github.com/harbor-framework/"
            f"terminal-bench-2.git {ROOT}")
    for candidate in (ROOT, ROOT / "tasks"):
        if not candidate.is_dir():
            continue
        dirs = sorted(d for d in candidate.iterdir()
                      if d.is_dir() and (d / "task.toml").is_file())
        if dirs:
            return dirs
    raise ValueError(
        f"{ROOT} has no task directories (looked for `task.toml` in {ROOT} and "
        f"{ROOT / 'tasks'})")


def _load_one(task_dir: Path) -> tuple[dict, dict, dict, dict]:
    meta = tomllib.loads((task_dir / "task.toml").read_text())
    env_meta = meta.get("environment") or {}
    tid = task_dir.name

    instruction = (task_dir / "instruction.md").read_text(errors="replace")
    goal = instruction.strip()

    env = {
        "kind": "exec",
        # The tag from `task.toml`. The driver resolves it to a digest before the run
        # starts and refuses to start if it cannot (§2.5.4), so no curve point can be
        # recorded without one.
        "image": env_meta["docker_image"],
        "workdir": _workdir(task_dir),
        # The task's state is the container's filesystem, not a bind mount: the image
        # ships the workdir empty and the instruction says to write into it.
        "state": "container",
        "network": _NETWORK[bool(env_meta.get("allow_internet"))],
        "cpus": int(env_meta.get("cpus", 2)),
        "memory_mb": int(env_meta.get("memory_mb", 4096)),
        "placement": "inside",
        "services": [],
        # Declared, not defaulted: the real set spans 600-12000 s and a 300 s default
        # would report a harness failing a task it was never given time to attempt.
        "agent_timeout_s": int((meta.get("agent") or {}).get("timeout_sec", 900)),
        "verify_timeout_s": int((meta.get("verifier") or {}).get("timeout_sec", 900)),
    }

    task = {"task_id": tid, "goal": goal}

    # Empty by design: every task's Dockerfile builds its own environment, and the
    # platform copying files in would duplicate -- or fight -- what the image already did.
    #
    # Except for the oracle control, which is the one thing that must be handed in: with
    # `HG_TB_ORACLE=1` the task's own reference solution is placed in the workdir so
    # `base_harness/oracle` can run it. That leaks the answer, which is the entire point
    # -- it exists to prove the check can produce a pass at all, and a dataset whose
    # oracle also scores 0 has an unreachable check, not a weak harness.
    setup = {"files": []}
    if os.environ.get("HG_TB_ORACLE"):
        solution = task_dir / "solution"
        if not solution.is_dir():
            raise ValueError(f"{tid} has no solution/ for the oracle to run")
        setup = {"files": [{"path": ".oracle", "from": str(solution)}]}

    verifier = {
        "kind": "command",
        "scorer_bundle": {"isolation_class": "snapshot-service"},
        "argv": ["bash", "/tests/test.sh"],
        # A real path in the checkout, copied in after the harness stops. `dst` because
        # the check expects its tests at `/tests`, which a path relative to the workdir
        # cannot express (§2.5.9).
        "inputs": [{"dst": "/tests", "from": str(task_dir / "tests")}],
        # The exit code carries nothing here: `test.sh` exits 0 whether the reward is 1
        # or 0, because the `if` that writes the file is its last statement.
        "reward_file": REWARD_FILE,
        # Where the check records *which* of its tests failed. Declared per task rather
        # than assumed by the platform: it is the task's `test.sh` that writes it.
        "evidence_file": CHECK_REPORT,
        "timeout_s": env["verify_timeout_s"],
    }
    return task, setup, verifier, env


def _all() -> list[tuple[dict, dict, dict, dict]]:
    out = []
    for d in _task_dirs():
        if d.name in EXCLUDED:
            continue
        if ONLY and d.name not in ONLY:
            continue
        out.append(_load_one(d))
    if not out:
        raise ValueError(
            f"no runnable Terminal-Bench tasks under {ROOT}"
            + (f" matching HG_TB_TASKS={ONLY}" if ONLY else ""))
    return out


def load():
    """`(tasks, scorable)`. The adapter's half of the dataset interface."""
    rows = _all()
    tasks = [r[0] for r in rows]
    return tasks, {t["task_id"]: "" for t in tasks}


SETUP = {r[0]["task_id"]: r[1] for r in _all()}
VERIFY = {r[0]["task_id"]: r[2] for r in _all()}
ENV = {r[0]["task_id"]: r[3] for r in _all()}

#: **No division.** Terminal-Bench declares no train/eval split -- it is an exam, and its
#: own documentation says so -- so the whole pool is evaluation material and this adapter
#: declares nothing (2026-10-24). `HG_TB_SPLIT=none` selected exactly this and is gone; a
#: run that wants a division passes `--split default` (the platform's hash rule, which is
#: what the receipts recorded under `HG_TB_SPLIT=none`'s absence) or `--split <file.json>`.
SPLIT = None
SPLIT_SOURCE = "none"


#: The checkout commit this adapter was validated against, measured with
#: `git -C /mnt/20t/xubuqiang/datasets/terminal-bench rev-parse HEAD`
#: (remote: `harbor-framework/terminal-bench-2`). Kept as a constant rather than read
#: from `.git`, because the pin has to survive the checkout being copied without its
#: history -- a provenance that disappears with a `cp` is not a pin.
COMMIT = "2fd12b88aafdd04a52c298e3940bcb189f9766d6"


def downloads() -> list[dict]:
    """The checkout, plus the cost this adapter's docstring already measures.

    The 89 task images are the run's real cost (~104 GB, pulled once by
    `tools/import_env.py`, not by this dataset) and there is no download kind that
    describes "89 tagged images"; they are named in the note rather than hidden, so a
    reader who follows this list does not think a 3.8 MB tarball is the whole story.
    """
    return [{
        "kind": "http",
        "name": "terminal-bench-2 checkout",
        "version": COMMIT,
        "url": f"https://github.com/harbor-framework/terminal-bench-2/archive/"
               f"{COMMIT}.tar.gz",
        "size": "~83 MB unpacked",
        "dst": str(ROOT),
        "extract_to": ".",
        "note": "The task instructions, Dockerfiles and checks. Pinned by commit sha. "
                "The task IMAGES are separate and much larger -- measured ~104 GB, "
                "pulled once by `tools/import_env.py`, three of them unfetchable on "
                "this host -- and they are not represented here.",
    }]
