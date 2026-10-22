r"""The shared pieces: the JVM, the environment factory, and the log format.

ScienceWorld is not pure Python. `setup.py` declares
`package_data={'scienceworld': [JAR_FILE, ...]}` and the wheel ships `scienceworld.jar`
(7.5 MB) plus `tasks.json`; the environment is a **JVM** the Python layer talks to. So a
dataset built on it has two prerequisites the pure-Python ones do not: a Java runtime,
and a Java runtime that **both sides can find** (`docs/dataset-onboarding-skill.md` §12:
a JVM only the harness can find is a check that fails for a reason nothing records).

Which JVM, and why it travels with the task
-------------------------------------------
This machine has no system `java` and no root, so a pinned **Adoptium JRE 17** tarball is
staged under the dataset root:

    <root>/jre/jdk-17.0.20.1+1-jre/bin/java   ->  openjdk 17.0.20.1 Temurin

JDK **17**, not the newest: the JAR is built in 2025 and a newer JDK may refuse an older
target in a way that does not name Java.

The platform's verification namespace hides everything outside `/usr`, the interpreter
prefix and the task directory (`eval/containers/sandbox.py`), and the dataset root is none of those.
So the JRE is materialized **into the workdir** by both `SETUP` and `VERIFY.inputs` and
found through `java_home(workdir)` below. That is the same rule the QA batch learned for
its check (§15.1): anything the check needs must be in its inputs, because the platform
tree -- and the data root -- are not on its disk.

The step budget is declared once, here, and imported by the CLI, the check and the oracle
so the three cannot drift (§12: "three files each holding their own number is how
ALFWorld's step budget would have drifted").
"""
from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path

#: The step budget, **declared once here** and imported by the CLI, the check and the
#: control. The JVM reports `done` when the goal is met **or** when this many moves are
#: spent, which is the stopping condition the official example loops on.
#:
#: **100**, following the ScienceWorld paper: "we used 100 in all experiments".
#: Limit: long oracle paths truncate at 100, including archived test paths up to 796 moves.
#: Keep those tasks in the fold; reference length must not select the evaluation pool.
STEP_BUDGET = 100

#: The action log the CLI appends to and the check replays. Same shape as ALFWorld's: one
#: JSON object per line, `{"kind": "action", "step": n, "action": "..."}`.
LOG_FILENAME = "scw_actions.jsonl"

#: The task identity travels **in the workdir**, not in the agent's argv: the CLI needs
#: the name and the variation to build the world, the check needs them to rebuild it, and
#: neither may read them from the other's bytes. `SETUP` and `VERIFY.inputs` both write
#: this file, and `VERIFY` re-materializes it.
TASK_FILENAME = "scw_task.json"

#: The Adoptium release the staged JRE came from. Pinned by URL, which **is** the version.
JRE_VERSION = "17.0.20.1+1"
JRE_DIRNAME = "jdk-17.0.20.1+1-jre"
JRE_URL = ("https://github.com/adoptium/temurin17-binaries/releases/download/"
           "jdk-17.0.20.1%2B1/OpenJDK17U-jre_x64_linux_hotspot_17.0.20.1_1.tar.gz")
#: The staged archive's digest (46640574 bytes), so `tools/datasets.py --check` can tell a
#: re-download that changed bytes from the runtime this dataset was validated against.
JRE_SHA256 = "0b2b640e3046b64c8ec504de0ab9d91bb5610182bda21fad454681ce54d45a62"
JRE_TARBALL = "OpenJDK17U-jre_x64_linux_hotspot_17.0.20.1_1.tar.gz"
JRE_SIZE = "~47 MB compressed, 136 MB unpacked"

#: Where the JRE is unpacked inside the workdir, and the environment variable the
#: `scienceworld` package reads. `JAVA_HOME` is the package's own contract: it launches
#: `<JAVA_HOME>/bin/java -jar scienceworld.jar`.
JRE_RELDIR = "jre"


def java_home(workdir: Path) -> Path:
    """The JVM to use, or a raised error naming every place that was tried.

    Order, and each one has a reason:

    1. the copy in the workdir, which is what the verification namespace can see;
    2. `HG_SCIENCEWORLD_JAVA`, so a reader debugging by hand can point elsewhere;
    3. the staged JRE under the dataset root, for the by-hand control and the oracle.

    A missing JVM is **raised**, never silently downgraded to "the environment did not
    start": a check that cannot build the world writes no reward file and says why
    (`verify/check.py`), and that only works if this is an exception.
    """
    # The closed runtime sets this only after authenticating its manifest. Refuse
    # missing sealed bytes; never fall through to an actor's prepared JRE.
    if os.environ.get('HG_SEALED_JAVA_HOME'):
        sealed = Path(os.environ['HG_SEALED_JAVA_HOME'])
        if not (sealed / 'bin/java').is_file():
            raise RuntimeError('sealed Java runtime is missing')
        return sealed.resolve()
    candidates = [workdir / JRE_RELDIR / JRE_DIRNAME,
                  Path(os.environ["HG_SCIENCEWORLD_JAVA"]) if
                  os.environ.get("HG_SCIENCEWORLD_JAVA") else None,
                  staged_jre()]
    for candidate in candidates:
        if candidate and (candidate / "bin" / "java").is_file():
            # **Resolved, always.** py4j launches Java with `cwd=<the package's BASEPATH>`
            # (`/tmp`, from `scienceworld.constants`), not with the caller's directory, so
            # a relative `JAVA_HOME` produces `FileNotFoundError: 'java'` from a subprocess
            # whose cwd nobody set -- measured, and it cost a control run. Every path this
            # returns is absolute for that reason.
            return candidate.resolve()
    raise RuntimeError(
        "no Java runtime found; tried " +
        ", ".join(str(c) for c in candidates if c) +
        ". The pinned JRE is provisioned by `python3 tools/datasets.py --fetch "
        "--dataset scienceworld`, and in a platform run it is materialized into the "
        "workdir by SETUP/VERIFY.inputs.")


def staged_jre() -> Path:
    """The provisioned JRE under the dataset root (the by-hand path)."""
    root = Path(os.environ.get("HG_SCIENCEWORLD_DATA",
                               "/mnt/20t/xubuqiang/datasets/scienceworld"))
    return root / "jre" / JRE_DIRNAME


def make_env(workdir: Path, *, step_budget: int = STEP_BUDGET,
             server_path: Path | None = None):
    """A `ScienceWorldEnv` with the JVM pointed at the workdir's copy.

    The import is inside the function on purpose: `data/datasets/scienceworld.py` is imported by
    `data/scaffolds/registry.py::published` on machines that have not provisioned anything, and a
    module-level `import scienceworld` there would be a second thing that can fail.
    """
    home = java_home(workdir)
    os.environ["JAVA_HOME"] = str(home)
    os.environ["PATH"] = str(home / "bin") + os.pathsep + os.environ.get("PATH", "")
    from scienceworld import ScienceWorldEnv

    return ScienceWorldEnv("", str(server_path) if server_path else None, envStepLimit=step_budget)


def load_task(workdir: Path, *, generate_gold: bool = False,
              step_budget: int = STEP_BUDGET, config_path: Path | None = None,
              server_path: Path | None = None):
    """Build the world named by `scw_task.json` in `workdir`. Returns the live env.

    `generate_gold=True` asks the JVM for the reference action sequence, which is the
    oracle (`docs/dataset-onboarding-skill.md` §12: `get_gold_action_sequence()` is "the
    analogue of ALFWorld's `walkthrough`"). It is requested only by the control and by the
    tests -- the agent's CLI never sets the flag, which is the leak boundary: the sequence
    is reachable through an API the agent's process does not call.
    """
    spec = json.loads((config_path or workdir / TASK_FILENAME).read_text())
    env = (make_env(workdir, step_budget=step_budget, server_path=server_path)
           if server_path else make_env(workdir, step_budget=step_budget))
    env.load(spec["task_name"], int(spec["variation"]), spec.get("simplifications", ""),
             generateGoldPath=generate_gold)
    return env


# ------------------------------------------------------------------- the log ---


def action_log_records(path: Path) -> list[dict]:
    """Every record in the log, ignoring blank lines and unparseable ones.

    Unlike ALFWorld -- where the log *is* the state -- a truncated final line here can
    only come from a crash mid-append, and a crash means the episode is over. Skipped
    lines are counted by the check so the evidence says so.
    """
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_action(path: Path, action: str, step: int) -> None:
    """Append one action. Raises on failure: a dropped append changes the episode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"kind": "action", "step": step, "action": action},
                                ensure_ascii=False) + "\n")


def actions_in(records: list[dict]) -> list[str]:
    return [str(r["action"]) for r in records
            if r.get("kind") == "action" and isinstance(r.get("action"), str)]


def unpack_jre(tarball: Path, workdir: Path) -> Path:
    """Extract the pinned JRE into `workdir/jre`, once. Returns its home.

    Idempotent: `SETUP` runs before the harness and `VERIFY.inputs` re-materializes the
    tarball before the check, and the second call must not re-extract 136 MB.
    """
    target = workdir / JRE_RELDIR
    home = target / JRE_DIRNAME
    if (home / "bin" / "java").is_file():
        return home
    target.mkdir(parents=True, exist_ok=True)
    if not tarball.is_file():
        raise RuntimeError(f"no JRE tarball at {tarball}; provision it with "
                           f"`python3 tools/datasets.py --fetch --dataset scienceworld`")
    with tarfile.open(tarball, "r:gz") as archive:
        _safe_extract(archive, target)
    if not (home / "bin" / "java").is_file():
        raise RuntimeError(f"the JRE tarball did not contain {JRE_DIRNAME}/bin/java")
    return home


def _safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    """Extract, refusing any member that would escape `target`.

    A tarball from a pinned URL is as trusted as anything else in this repository, and
    the check is three lines: a member named `../../etc/...` in a runtime archive would
    otherwise write wherever the process can.
    """
    base = target.resolve()
    for member in archive.getmembers():
        destination = (base / member.name).resolve()
        if not str(destination).startswith(str(base) + os.sep):
            raise RuntimeError(f"refusing to extract {member.name!r} outside {base}")
    archive.extractall(base)


__all__ = ["STEP_BUDGET", "LOG_FILENAME", "TASK_FILENAME", "JRE_VERSION", "JRE_DIRNAME",
           "JRE_URL", "JRE_SHA256", "JRE_TARBALL", "JRE_SIZE", "JRE_RELDIR",
           "java_home", "staged_jre", "make_env", "load_task",
           "action_log_records", "append_action", "actions_in", "unpack_jre"]
