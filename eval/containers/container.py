"""Run a task's harness and its check inside a container. INTERFACE.md §2.5.

This is the `exec` environment kind. It is a **second, independent boundary**, not a
replacement for `eval/containers/sandbox.py`:

* `files` -- the harness runs on the host inside a bwrap namespace. The platform is
  hidden by not being mounted (`eval/containers/sandbox.py`), and the runtime is the host's.
* `exec`  -- the harness runs **inside a container**. The platform is hidden the same
  way (it is not mounted), but the runtime, the interpreter and the tools are the
  image's, and `network: none` becomes available because the container has its own
  network namespace rather than borrowing the host's.

Three properties are load-bearing and each is a decision rather than an accident:

**The container is mounted from a digest, never a tag.** `:latest` moves, and two runs
under one tag are not two measurements of the same thing -- the same failure this
platform already shipped once for `agent_model`. `resolve_digest` is therefore not a
convenience: a run whose digest cannot be resolved **refuses to start** (§2.5.4).

**Nothing is built here.** A task image is built by `tools/import_env.py`, once, by a
human, at import time (§2.5.5). `docker run` on a missing image would silently pull
whatever the registry served that day; this module does not pull and does not build.
If the image is not on the local daemon, that is a refusal with instructions.

**The container is not given a way out of itself.** No docker socket, no host home, no
platform. It gets exactly three mounts (§2.5.3), and the order they are applied in is
load-bearing -- see `_mounts`.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from ckpt.git_state import STATE_DIRNAME
from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, targets

#: The mount plan's version, in the same sense as `eval/containers/sandbox.py`'s: bump it when
#: what the subject can see changes. A curve made under plan 1 and one made under plan
#: 2 are measurements in two different worlds, so the number goes on the record.
CONTAINER_PLAN = 2  # Explicit read-only task-relative immutable runtime projections.

#: Where the task's files land inside the container when the dataset does not say.
DEFAULT_WORKDIR = "/app"

#: The harness tree is mounted at **its own host path** rather than somewhere tidy
#: like `/harness`. A translated path is a place where a harness's own record stops
#: matching the filesystem it ran on (`eval/run/runner.py` says the same of the
#: unsandboxed case), and it is not necessary: the host path is already unique per
#: run, and Docker creates the mount point.

#: What crosses from the host environment into the container. An **allowlist**, not
#: `os.environ` minus something: the harness needs the agent's model configuration and
#: nothing else, and a denylist forwards every host variable invented later.
#: `HG_METHOD_` cannot cross under this rule even though `harness_env()` has already
#: stripped it -- the two budgets are separated by two independent mechanisms because
#: separate configuration is not separation until something enforces it.
PASS_THROUGH_PREFIXES = ("HG_AGENT_",)
PASS_THROUGH_EXACT = ("LANG", "LC_ALL", "TZ")


class ContainerUnavailable(RuntimeError):
    """Docker is not usable, or the image cannot be pinned to a content address."""


class SnapshotUnavailable(ContainerUnavailable):
    """The container could not be stopped or admitted/committed as a snapshot."""


# A snapshot is a Docker image layer. Bound both the writable layer and the space Docker
# can consume while committing it; otherwise a large GAIA attachment or JVM cache can turn
# commit into a host OOM/disk failure and the task is incorrectly reported as a harness score.
SNAPSHOT_MAX_BYTES = 16 << 30
SNAPSHOT_RESERVE_BYTES = 1 << 30
SNAPSHOT_INSPECT_TIMEOUT_S = 60
SNAPSHOT_COMMIT_TIMEOUT_S = 1800


def _snapshot_limit(key: str, default: int) -> int:
    try:
        value = int(os.environ.get(key, default))
        if not 0 < value <= 2**63 - 1:
            raise ValueError("outside positive signed 64-bit range")
        return value
    except (TypeError, ValueError) as exc:
        raise SnapshotUnavailable(f"snapshot admission failed kind=resource: invalid {key}") from exc


def _snapshot_failure(stage: str, proc: subprocess.CompletedProcess | None = None,
                      exc: BaseException | None = None) -> SnapshotUnavailable:
    text = " ".join(filter(None, [
        str(exc) if exc else "",
        (proc.stderr if proc else "") if proc else "",
        (proc.stdout if proc else "") if proc else "",
    ])).strip()
    lowered = text.lower()
    if ((proc is not None and proc.returncode == 137)
            or "out of memory" in lowered or "cannot allocate memory" in lowered
            or "oom" in lowered):
        kind = "oom"
    elif any(marker in lowered for marker in
             ("no space left", "disk quota", "quota exceeded", "insufficient space")):
        kind = "disk"
    elif any(marker in lowered for marker in
             ("resource temporarily unavailable", "too many processes", "pids limit")):
        kind = "resource"
    else:
        kind = "docker"
    code = f" exit={proc.returncode}" if proc is not None else ""
    return SnapshotUnavailable(f"snapshot {stage} failed kind={kind}{code}: {text[-1200:]}")


def snapshot_admission(name: str) -> dict[str, int | str | bool]:
    """Check bounded writable size and Docker-root headroom before ``docker commit``.

    This is byte-capacity preflight, not a filesystem quota or inode/resource gate.
    A commit killed by the daemon can leave an incomplete image and used to surface
    as an unexplained exit 137. The shared runner admission contract covers the
    additional observations a backend must supply for those stronger gates.
    """
    maximum = _snapshot_limit("HG_SNAPSHOT_MAX_BYTES", SNAPSHOT_MAX_BYTES)
    reserve = _snapshot_limit("HG_SNAPSHOT_RESERVE_BYTES", SNAPSHOT_RESERVE_BYTES)
    try:
        proc = docker("inspect", "--size", "-f", "{{.SizeRw}}", name,
                      timeout=SNAPSHOT_INSPECT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _snapshot_failure("admission inspect", exc=exc) from exc
    if proc.returncode != 0:
        raise _snapshot_failure("admission inspect", proc)
    try:
        writable = int(proc.stdout.strip())
    except ValueError:
        raise SnapshotUnavailable(
            "snapshot admission failed kind=docker: invalid writable size")
    if not 0 <= writable <= 2**63 - 1:
        raise SnapshotUnavailable("snapshot admission failed kind=docker: invalid writable size")
    if writable > maximum:
        raise SnapshotUnavailable(
            f"snapshot admission rejected kind=disk writable_bytes={writable} "
            f"max_bytes={maximum}")

    try:
        info = docker("info", "--format", "{{.DockerRootDir}}",
                      timeout=SNAPSHOT_INSPECT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _snapshot_failure("admission docker-info", exc=exc) from exc
    if info.returncode != 0:
        raise _snapshot_failure("admission docker-info", info)
    root_text = info.stdout.strip()
    root = Path(root_text)
    if (not root.is_absolute() or len(root_text) > 1024
            or any(ord(c) < 32 for c in root_text)):
        raise SnapshotUnavailable("snapshot admission failed kind=docker: invalid Docker root")
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise _snapshot_failure("admission disk-usage", exc=exc) from exc
    if type(free) is not int or not 0 <= free <= 2**63 - 1:
        raise SnapshotUnavailable("snapshot admission failed kind=disk: invalid free bytes")
    required = writable + reserve
    if free < required:
        raise SnapshotUnavailable(
            f"snapshot admission rejected kind=disk docker_root={root} free_bytes={free} "
            f"required_bytes={required} writable_bytes={writable}")
    return {"writable_bytes": writable, "free_bytes": free, "required_bytes": required,
            "docker_root": str(root), "max_bytes": maximum, "reserve_bytes": reserve,
            "inspect_timeout_s": SNAPSHOT_INSPECT_TIMEOUT_S,
            "commit_timeout_s": SNAPSHOT_COMMIT_TIMEOUT_S,
            "preflight_only": True, "quota_enforced": False}


# --------------------------------------------------------------- the client ---

#: How much of a streamed command's output is kept. `docker exec` on a long task can
#: print for hours, and only the tail is ever read (the runner keeps `stderr[-2000:]`).
#: A cap rather than a spool to disk: the point of the cap is that a chatty subject
#: cannot fill the machine's memory with output nobody will read.
_STREAM_KEEP = 4 << 20


def _drain(pipe, sink: list[str], limit: int = _STREAM_KEEP) -> None:
    """Read a pipe to EOF, keeping the last `limit` bytes. Runs on its own thread.

    Interleaving stdout and stderr is what `subprocess.run` would not do, and it is
    what makes a live log readable: `docker exec` writes its own errors and the
    subject's output to two different pipes, and a reader wants them in the order they
    were said.
    """
    try:
        while True:
            chunk = pipe.readline()
            if not chunk:
                break
            sink.append(chunk)
            if sum(len(c) for c in sink) > limit:
                del sink[:max(1, len(sink) // 2)]
    except (ValueError, OSError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _run_streaming(argv: list[str], timeout: int, _label: str = ""):
    """`subprocess.run` shape, but in slices so this thread stays interruptible.

    Only used while a task is in flight (`eval/observe/progress.py`). The beats themselves come
    from the *phase*, not from here -- one thread that beats every 10s regardless of
    which call is blocking, which is why this loop only has to wait and check a
    deadline. The first version beat here as well and produced two heartbeats per
    interval, each with a different elapsed time, which is a log that contradicts
    itself: measured, `elapsed_s: 10.0` twice in a row.

    The cost -- two reader threads, merged output -- is paid only inside a task, so the
    30-odd short docker calls a task makes (`create`, `cp`, `commit`) are untouched.
    """
    if timeout:
        deadline = time.monotonic() + timeout
    else:
        deadline = None
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", bufsize=1)
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    readers = [threading.Thread(target=_drain, args=(proc.stdout, out_chunks),
                                daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, err_chunks),
                                daemon=True)]
    for r in readers:
        r.start()
    #: One second, so that a timeout is enforced to within about a second -- the same
    #: promise `subprocess.run`'s own timeout makes -- and short enough that a stopping
    #: driver is noticed promptly.
    SLICE = 1.0
    while True:
        try:
            proc.wait(timeout=SLICE)
            break
        except subprocess.TimeoutExpired:
            if deadline is not None and time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                for r in readers:
                    r.join(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout)
    for r in readers:
        r.join(timeout=5)
    return subprocess.CompletedProcess(argv, proc.returncode, "".join(out_chunks),
                                       "".join(err_chunks))


def docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the docker client.

    Public because `eval/service/services.py` needs the same client, and two modules each
    shelling out to `docker` with their own idea of how to report a failure is two
    error surfaces to keep in step.

    Long calls made *inside a task* stream and beat; everything else is one
    `subprocess.run`. The branch is on the presence of a task rather than on a flag per
    call site, because the calls that need it are the two that block for minutes
    (`exec` a harness, `exec` a check) and they are made from three places, none of
    which knows whether a watcher exists.
    """
    from eval.observe import progress as progress_mod

    if progress_mod.task_of() is None:
        return subprocess.run(["docker", *args], capture_output=True, text=True, errors="replace",
                              timeout=timeout)
    return _run_streaming(["docker", *args], timeout, f"docker {args[0]}")


#: Internal alias, so the call sites below read the way they always have.
_docker = docker


def available() -> str | None:
    """The daemon's version, or None. `docker` present but no daemon is not available."""
    if shutil.which("docker") is None:
        return None
    try:
        proc = _docker("version", "--format", "{{.Server.Version}}", timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.strip() or None


def looks_pinned(image: str) -> bool:
    """True for `repo@sha256:<64 hex>` and for a bare `sha256:<64 hex>` image id."""
    if "@sha256:" in image:
        return _is_hex(image.rsplit("@sha256:", 1)[1])
    if image.startswith("sha256:"):
        return _is_hex(image[len("sha256:"):])
    return False


def _is_hex(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def resolve_digest(image: str) -> str:
    """Pin `image` to a content address, locally, without pulling or building.

    Returns a reference `docker run` accepts -- `repo@sha256:...` when the image
    carries a registry digest, else the bare image ID, which is itself a content hash
    of the local image and therefore pinnable.

    Refuses rather than pulling. A pull during a run is a network dependency that
    makes the run depend on what the registry served that day; importing the image is
    a one-time step (`tools/import_env.py`), which is where §2.5.5 puts it.
    """
    if looks_pinned(image):
        return image
    if image in _DIGEST_CACHE:
        return _DIGEST_CACHE[image]

    try:
        proc = _docker("image", "inspect", "--format",
                       "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}",
                       image, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ContainerUnavailable(f"timed out inspecting image {image!r}") from exc
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"image {image!r} is not on the local daemon, and this platform does not "
            f"pull during a run. Import it first:\n"
            f"    python tools/import_env.py --image {image}\n"
            f"docker said: {proc.stderr.strip()[:300]}")

    pinned = proc.stdout.strip()
    if not looks_pinned(pinned):
        raise ContainerUnavailable(
            f"could not pin image {image!r} to a content address; "
            f"`docker image inspect` returned {pinned!r}")
    _DIGEST_CACHE[image] = pinned
    return pinned


#: One `docker image inspect` per distinct reference, not one per task: resolution is
#: a startup step, and a 400-task benchmark should not fork the docker client 400
#: times to learn something that cannot change during a run.
_DIGEST_CACHE: dict[str, str] = {}


def digest_of(pinned: str) -> str:
    """Just the `sha256:...` part, for the record (§2.5.4 records the digest)."""
    if "@sha256:" in pinned:
        return "sha256:" + pinned.rsplit("@sha256:", 1)[1]
    return pinned


# ------------------------------------------------------------------ running ---

def interpreter_paths() -> tuple[Path, ...]:
    """The trees the platform's own interpreter needs, so a container can run it.

    A harness is a Python program -- the platform already assumes that when it invokes
    `python <entry>` -- and **a task image need not have a Python at all**. Measured: the
    real Terminal-Bench image is `ubuntu:24.04` plus a `COPY`, with no `python3`, no `R`
    and no `gcc`; the task explicitly expects the agent to `apt-get install` what it
    needs. So assuming the image provides the interpreter is assuming something the
    benchmark does not promise.

    Both `sys.prefix` and `sys.base_prefix` are needed, and that is measured too: a
    virtualenv's `bin/python3` resolves to its base prefix, so mounting the venv alone
    gives `not found` inside the container. Together they give a working interpreter
    **and** the packages the platform installed for its harnesses.
    """
    import sys as _sys
    out: list[Path] = []
    for candidate in (_sys.prefix, _sys.base_prefix):
        path = Path(candidate)
        if path.is_dir() and path not in out:
            out.append(path)
    return tuple(out)


#: One probe per image, not one per task.
_PYTHON_CACHE: dict[str, str | None] = {}


def image_python(image: str) -> str | None:
    """The interpreter the image itself provides, or None if it has none.

    The platform injects its own interpreter into a container, because a task image need
    not have one -- measured: the real Terminal-Bench image is `ubuntu:24.04` plus a
    `COPY`, with no `python3` at all. But injecting it unconditionally would contradict
    §2.5.3: the reason the harness runs *inside* the environment is so that "`bash`, the
    file editor and **the interpreter** are the image's".

    So the image wins when it has an interpreter, and the platform's is the fallback.
    Both halves are measured: a `files`-style image with its own Python keeps it (and
    keeps its own installed packages), and an image with none still runs the harness.
    """
    if image in _PYTHON_CACHE:
        return _PYTHON_CACHE[image]
    found: str | None = None
    try:
        proc = docker("run", "--rm", "--network", "none", "--entrypoint", "sh", image,
                      "-c", "command -v python3 || command -v python || true",
                      timeout=180)
        out = proc.stdout.strip()
        found = out.splitlines()[-1].strip() if out else None
    except (subprocess.TimeoutExpired, OSError):
        found = None
    _PYTHON_CACHE[image] = found or None
    return _PYTHON_CACHE[image]


def env_home(task_mount: str) -> str:
    """The harness's HOME inside the container: a subdirectory of the task mount.

    Under the task mount rather than somewhere of its own, for the reason
    `eval/run/runner.py` gives for the `files` case: that directory is already writable,
    already per-task and already temporary. Named with the platform's prefix so it
    cannot be mistaken for one of the task's own files.
    """
    return f"{task_mount.rstrip('/')}/.harnessgrad-home"


def container_env(task_mount: str, deadline_s: int | None = None) -> dict[str, str]:
    """Everything a harness sees in its environment inside the container.

    Built from an allowlist rather than forwarded, and with `PATH` deliberately absent:
    the container has its own, the host's names directories that do not exist inside,
    and forwarding it would let a harness pick up a host binary if the two ever
    overlapped.

    **`HG_AGENT_DEADLINE_S` is the environment's own `agent_timeout_s`.** It is not a
    hint: the platform kills the container at that second, and until this existed a
    harness that spawns a subprocess had no way to know the number it was being measured
    against. Measured (`codex-task-dgm`, 2026-10-05): an adapter that gave its own child a
    900 s timeout raced the platform's 900 s kill -- the container died first and the
    curve recorded `exit code -1 -- timeout after 900s`, a killed container instead of a
    harness that decided to stop. The budget a harness is subject to is a fact about the
    measurement, so it is stated where the harness can read it (INTERFACE.md §3.3).
    """
    env = {k: v for k, v in os.environ.items()
           if k.startswith(PASS_THROUGH_PREFIXES) or k in PASS_THROUGH_EXACT}
    home = env_home(task_mount)
    env.update({
        "HOME": home,
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "XDG_CACHE_HOME": f"{home}/.cache",
        # A CLI's scratch files should not land in the directory the task is about,
        # where a check would then be reading them.
        "TMPDIR": f"{home}/tmp",
    })
    if deadline_s:
        env["HG_AGENT_DEADLINE_S"] = str(int(deadline_s))
    return env


#: The `container_env` entries whose value is a **directory a harness is invited to write
#: into**. `PATH` and the model variables are deliberately absent: a name belongs here only
#: when something is expected to put a file underneath it.
SCRATCH_ENV_VARS = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "TMPDIR")


def writable_env_dirs(task_mount: str) -> tuple[str, ...]:
    """Every directory `container_env` names that a harness may write into.

    **A variable that names a directory is not a directory.** `docker create --tmpfs
    <home>` brings its own mount point and nothing below it, so `HOME` exists while
    `HOME/tmp` -- the value this module hands out as `TMPDIR` -- does not. A harness is
    entitled to believe the environment it was given: `mkdtemp(TMPDIR + "/scratch-XXXXXX")`
    needs the *parent* to be there, and `ENOENT` when it is not is a failure caused by the
    platform, not by the harness.

    Measured 2026-10-06, on the `dsh_agent` harness: DSH's spill store died on exactly that
    `mkdtemp` and took its whole plugin tree with it, reporting `dsh: plugin tree failed to
    load: ... failed to apply loader entry spill-local` -- a message naming neither `TMPDIR`
    nor this module, at the end of a four-layer hunt. Returning the list instead of doing
    the `mkdir` here is deliberate: `mkdir_in` is a `docker exec`, so the container has to
    be **running**, and the caller that owns the start/stop order is the only one that knows
    when that is true.
    """
    env = container_env(task_mount)
    seen: list[str] = []
    for var in SCRATCH_ENV_VARS:
        value = env.get(var)
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _mounts(task_dir: Path, task_mount: str, harness_root: Path | None,
            state_dirs: tuple[Path, ...], task_dir_writable: bool = True,
            readonly_paths: tuple[Path, ...] = ()) -> list[str]:
    """The mount plan. Parent before child, so a reader can follow it.

    **The order is not what makes the `.state/` hole work here, and this docstring
    used to claim it was.** `eval/containers/sandbox.py` needs its holes bound after the
    read-only parent, because bwrap's binds are last-one-wins and the reverse order
    silently produced a `Read-only file system` on the harness's own scratch space.
    Docker is not bwrap: it *sorts* nested mounts by destination depth, so the same
    plan in the opposite CLI order gives the same result. Measured, both orders:
    `state_writable=yes`, with the `readonly` parent bind given last.

    So the property comes from Docker's nesting-aware sort, not from this list, and
    the ordering below is kept only because a deterministic plan is easier to read and
    to test than an arbitrary one. It is stated this way rather than copied over from
    the other module because "it worked there for this reason" is exactly how a
    correct-looking plan acquires a false explanation.

    The task directory is mounted first of all: it is the one tree that must always be
    writable, because it is what the harness is being asked to change.
    """
    plan = ["--mount",
            f"type=bind,src={task_dir},dst={task_mount}"
            + ("" if task_dir_writable else ",readonly")]
    if harness_root is not None:
        plan += ["--mount", f"type=bind,src={harness_root},dst={harness_root},readonly"]
    for state in state_dirs:
        plan += ["--mount", f"type=bind,src={state},dst={state}"]
    # Trees the harness needs to run at all: a CLI it drives, the interpreter it was
    # written against. Read-only, and **also for `exec`** -- an earlier version applied
    # them only to `files` on the theory that "inside a container the runtime is the
    # image's", which is false for a task image that ships no runtime.
    for extra in readonly_paths:
        plan += ["--mount", f"type=bind,src={extra},dst={extra},readonly"]
    return plan


def command(argv: list[str], *, image: str, task_dir: Path, task_mount: str,
            harness_root: Path | None = None, state_dirs: tuple[Path, ...] = (),
            cwd: str | None = None, env: dict[str, str] | None = None,
            network: str = "none", read_only: bool = True,
            task_dir_writable: bool = True,
            extra_mounts: tuple[str, ...] = (),
            readonly_paths: tuple[Path, ...] = (),
            immutable_runtimes: tuple[tuple[Path, str], ...] = (),
            limits: dict | None = None) -> list[str]:
    """The `docker run` argv. Split out so `self_check` and the tests can read it."""
    limits = envelope(limits)
    cmd = ["docker", "run", "--rm", "--network", network]

    if read_only:
        # The environment is the image (§2.5.3). A rootfs the harness can write to
        # would make the check's world depend on what the harness did to it, and two
        # runs of one task would stop being comparable. `/tmp` is the exception
        # because enough software hardcodes it that refusing would only produce
        # confusing failures; it is a tmpfs, so it dies with the container.
        cmd += ["--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,size=512m,mode=1777"]

    cmd += ["--pids-limit", str(limits["pids"])]
    cmd += ["--memory", limits["memory"]]
    cmd += ["--cpus", _number(limits["cpus"])]

    cmd += _mounts(task_dir, task_mount, harness_root, tuple(state_dirs),
                   task_dir_writable, tuple(readonly_paths))
    # Mounts this module does not know the meaning of: a service's recording, whose
    # audience (`eval/service/services.py`) is the design decision and not this layer's to
    # make. Appended after the task mount so a recording can never shadow it.
    cmd += list(extra_mounts)

    # Nested read-only children of the task mount, never a mount of its parent:
    # Docker sorts by destination depth, so these exact shared runtime sources
    # cannot shadow the task mount and remain read-only in both agent and verifier.
    for source, target in targets(task_dir, immutable_runtimes,
                                  writable_paths=state_dirs):
        if any(c in str(source) + task_mount + str(target.relative_to(task_dir.resolve()))
               for c in (",", "\n", "\r")):
            raise ImmutableRuntimeUnavailable("immutable runtime path cannot be expressed in Docker mount syntax")
        destination = to_container(target, task_dir.resolve(), task_mount)
        if not Path(task_mount).is_absolute():
            raise ImmutableRuntimeUnavailable("immutable runtime container workdir must be absolute")
        for index, option in enumerate(extra_mounts):
            if option != "--mount" or index + 1 == len(extra_mounts):
                continue
            fields = dict(part.split("=", 1) for part in extra_mounts[index + 1].split(",")
                          if "=" in part)
            other = Path(fields.get("dst", fields.get("destination", fields.get("target", "/"))))
            here = Path(destination)
            if other == here or other in here.parents or here in other.parents:
                raise ImmutableRuntimeUnavailable("extra mount overlaps immutable runtime destination")
            other_source = fields.get("src", fields.get("source"))
            if other_source and "readonly" not in extra_mounts[index + 1].split(","):
                other_source = Path(other_source).resolve()
                if other_source == source or other_source in source.parents or source in other_source.parents:
                    raise ImmutableRuntimeUnavailable("extra mount exposes immutable runtime source writable")
        cmd += ["--mount", f"type=bind,src={source},dst={destination},readonly"]

    # Run as the invoking user rather than root, for two reasons that are both
    # correctness rather than hygiene: files the harness creates would otherwise land
    # on the host owned by root, which the platform then cannot delete (its cleanup is
    # `shutil.rmtree` as this user), and root-in-container is a strictly larger
    # privilege set than the task needs.
    cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for key, value in sorted((env or {}).items()):
        cmd += ["-e", f"{key}={value}"]
    cmd += ["-w", cwd or task_mount]
    cmd += [image, *argv]
    return cmd


def run(argv: list[str], *, image: str, task_dir: Path, task_mount: str,
        harness_root: Path | None = None, state_dirs: tuple[Path, ...] = (),
        cwd: str | None = None, env: dict[str, str] | None = None,
        timeout_s: int = 3600, network: str = "none",
        read_only: bool = True, task_dir_writable: bool = True,
        extra_mounts: tuple[str, ...] = (),
        readonly_paths: tuple[Path, ...] = (),
        immutable_runtimes: tuple[tuple[Path, str], ...] = (),
        limits: dict | None = None) -> subprocess.CompletedProcess:
    """`subprocess.run` shape, inside a **fresh** container. Same return type.

    `limits` is the caller's resource envelope, forwarded to `command()`. It is a keyword
    with the same `None` default `command()` has, and that default is load-bearing: before
    it existed, **this function had no way to pass one on**, so every container it started
    got `command()`'s defaults (2 cpus / 4g / 1024 pids) no matter what the environment
    declared. `limits=None` therefore still means "the defaults", which is what
    `eval/verification/verifier.py` relies on for the check's container.

    Fresh per call, not one container reused for the harness and then the check. The
    property §2.5.6 asks for -- that nothing the harness started is still running when
    the check runs -- cannot be *guaranteed* by killing the process tree of a
    `docker exec`: anything the harness backgrounded is reparented to the container's
    PID 1 and survives the exec. Starting the check in a new container from the same
    image makes the guarantee structural instead of best-effort, and it costs one
    container start.

    What survives between the two is the task directory, because it is on a bind mount
    -- which is exactly the set of things a check is supposed to grade.
    """
    # The safety net, not the mechanism. The driver resolves every dataset image once
    # at startup (§2.5.4: no digest, no start); this refuses anyway, because the cost
    # of being wrong is a silent pull and the cost of checking is a string compare.
    if not looks_pinned(image):
        error = ImmutableRuntimeUnavailable if immutable_runtimes else ContainerUnavailable
        raise error(
            f"refusing to run {image!r}: it is not pinned to a content address, and "
            f"`docker run` on a tag would pull whatever the registry served today. "
            f"Resolve it with `resolve_digest` first (INTERFACE.md §2.5.4).")
    cmd = command(argv, image=image, task_dir=task_dir, task_mount=task_mount,
                  harness_root=harness_root, state_dirs=state_dirs, cwd=cwd,
                  env=env, network=network, read_only=read_only,
                  task_dir_writable=task_dir_writable, extra_mounts=extra_mounts,
                  readonly_paths=readonly_paths, immutable_runtimes=immutable_runtimes,
                  limits=limits)
    # A client timeout kills `docker run`, not the daemon-side container. `--rm`
    # only takes effect when that container exits, so retain an exact per-call
    # identity and reap it before callers tear down recordings or their network.
    name = f"hg-run-{uuid.uuid4().hex}"
    cmd[2:2] = ["--name", name]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s)
        except OSError as exc:
            if immutable_runtimes:
                raise ImmutableRuntimeUnavailable(str(exc)) from exc
            raise
        if immutable_runtimes and proc.returncode == 125:
            raise ImmutableRuntimeUnavailable(f"immutable runtime container failed: {proc.stderr.strip()}")
        return proc
    finally:
        remove_container(name)


def to_container(path: Path | str, task_dir: Path, task_mount: str) -> str:
    """Translate a host path under `task_dir` to its container path.

    Only the task directory is translated. The harness tree is mounted at its own host
    path precisely so that the entrypoint argv needs no rewriting, and a translation
    is the one place a harness's own record can stop matching the filesystem it ran on.
    """
    path = Path(path)
    try:
        rel = path.relative_to(task_dir)
    except ValueError:
        return str(path)
    return task_mount if str(rel) == "." else f"{task_mount.rstrip('/')}/{rel}"


# --------------------------------------------------------------- self check ---

#: The probe the self-check runs inside a throwaway container. `sh` rather than
#: Python: the image is the task's, and a task image need not have a Python we can
#: rely on. Every line is `key=value` so the result can be read without a parser.
#:
#: `rootfs_writable` writes to `/var/tmp` and not `/usr`, and that is not arbitrary:
#: the container runs as a non-root uid, which cannot write `/usr` in *any* image, so
#: a `/usr` probe reports "read-only" whether or not `--read-only` was passed -- a
#: check that cannot fail. `/var/tmp` is 1777 in a Debian-family image, so it is
#: writable by that uid exactly when the rootfs is not read-only. Measured both ways.
_PROBE = r"""
p() { if [ -e "$1" ]; then echo "$2=yes"; else echo "$2=no"; fi; }
p __PLATFORM__ platform_visible
p __SIBLING__ sibling_visible
p /var/run/docker.sock docker_socket
p __HARNESS__/agent.py harness_readable
if ( echo x > __HARNESS__/probe 2>/dev/null ); then echo harness_writable=yes; else echo harness_writable=no; fi
if ( echo x > __HARNESS__/.state/probe 2>/dev/null ); then echo state_writable=yes; else echo state_writable=no; fi
if ( echo x > __TASK__/written 2>/dev/null ); then echo task_writable=yes; else echo task_writable=no; fi
if ( echo x > /var/tmp/probe 2>/dev/null ); then echo rootfs_writable=yes; else echo rootfs_writable=no; fi
if ( echo x > /tmp/probe 2>/dev/null ); then echo tmp_writable=yes; else echo tmp_writable=no; fi
echo uid=$(id -u)
"""


def self_check(platform: Path, image: str) -> dict:
    """Prove the container boundary holds before a paid run depends on it.

    Returns {"ok": bool, "detail": str}. Called at startup for a dataset that needs
    `exec`: a boundary that cannot be built must stop the run at the door, not once
    per task, and never by falling back to running on the host.

    Two of the probes exist because the obvious version of this check passes for the
    wrong reason:

    * `platform_visible` probes the **real platform path**, not a marker under `/tmp`.
      The container gets a tmpfs on `/tmp`, so a marker placed there would be shadowed
      and reported invisible whether or not anything was mounted -- a check that
      cannot fail. The real path is also the fact we actually care about.
    * `sibling_visible` probes a file next to the harness mount. Without it, "the
      platform is invisible" is satisfied by a plan that mounts the harness's whole
      parent directory, which would be a far larger hole than intended.
    """
    try:
        pinned = resolve_digest(image)
    except ContainerUnavailable as exc:
        return {"ok": False, "detail": str(exc)}

    # `/var/tmp`, not `/tmp`: everything under `/tmp` is behind the container's tmpfs.
    with tempfile.TemporaryDirectory(prefix="hg-container-check-", dir="/var/tmp") as tmp:
        tmp_path = Path(tmp)
        task_dir = tmp_path / "task"
        task_dir.mkdir()

        harness = tmp_path / "harness"
        state = harness / STATE_DIRNAME
        state.mkdir(parents=True)
        (harness / "agent.py").write_text("# a harness\n")
        # A file the harness mount must NOT expose: its sibling.
        sibling = tmp_path / "sibling.txt"
        sibling.write_text("not the harness\n")

        probe = (_PROBE
                 .replace("__PLATFORM__", str(platform / "driver.py"))
                 .replace("__SIBLING__", str(sibling))
                 .replace("__HARNESS__", str(harness))
                 .replace("__TASK__", str(task_dir)))
        # The task is mounted at its own host path here, unlike a real run where it is
        # mounted at the dataset's `workdir`: the probe asserts host paths that the
        # module did not choose.
        try:
            proc = run(["sh", "-c", probe], image=pinned, task_dir=task_dir,
                       task_mount=str(task_dir), harness_root=harness,
                       state_dirs=(state,), cwd=str(task_dir), timeout_s=180)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "detail": f"the probe container did not run: {exc}"}

        if proc.returncode != 0:
            return {"ok": False,
                    "detail": (f"the probe container exited {proc.returncode}: "
                               f"{(proc.stderr or proc.stdout).strip()[:400]}")}

    facts = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            facts[key.strip()] = value.strip()

    problems = []
    if facts.get("platform_visible") != "no":
        problems.append("the platform is visible inside the container")
    if facts.get("sibling_visible") != "no":
        problems.append("a file beside the harness mount is visible, so the plan "
                        "mounts more than the harness")
    if facts.get("docker_socket") != "no":
        problems.append("the docker socket is visible inside the container, so the "
                        "harness can start a sibling container with any mount it likes")
    if facts.get("harness_readable") != "yes":
        problems.append("the harness tree is not mounted, so the harness cannot run")
    if facts.get("harness_writable") != "no":
        problems.append("the harness tree is writable, so tamper detection is void")
    if facts.get("state_writable") != "yes":
        problems.append("the harness's .state/ is not writable: the read-only bind of "
                        "its parent won over the writable bind of the hole")
    if facts.get("task_writable") != "yes":
        problems.append("the task directory is not writable")
    if facts.get("rootfs_writable") != "no":
        problems.append("the container rootfs is writable, so the environment is not "
                        "the image and two runs of one task need not agree")
    if facts.get("tmp_writable") != "yes":
        problems.append("/tmp is not writable, which several agent CLIs require")

    return {"ok": not problems,
            "detail": ("; ".join(problems) if problems
                       else f"image {digest_of(pinned)}: " + ", ".join(
                           f"{k}={v}" for k, v in sorted(facts.items()))),
            "facts": facts, "image_digest": digest_of(pinned)}


# ------------------------------------------------- container-state lifecycle ---
#
# INTERFACE.md §2.5.9. Some tasks put their state in the container's own filesystem
# rather than in a bind mount -- measured on the real Terminal-Bench images: `WORKDIR
# /app` is set by the image, `/app` ships *empty*, and the check reads `/app`. A bind
# mount would put the task somewhere the task itself did not put it.
#
# So the platform creates the container, copies the task in, runs the harness inside it,
# **commits** the result, and verifies against a fresh container from that snapshot.
# Every function here is one step of that, and `eval/run/runner.py` owns the order.

def require_snapshot_identity(image: str, expected: str | None) -> None:
    """Trusted-daemon/caller consistency, not publisher provenance or a byte rehash."""
    if (not expected or not looks_pinned(expected) or not looks_pinned(image)
            or digest_of(image) != digest_of(expected)):
        raise SnapshotUnavailable("snapshot identity mismatch or missing pinned expected identity")


def require_snapshot_container(name: str, expected: str) -> None:
    """Compare the image actually consumed by Docker before any snapshot code runs."""
    try:
        proc = docker("inspect", "--format", "{{.Image}}", name,
                      timeout=SNAPSHOT_INSPECT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _snapshot_failure("identity inspect", exc=exc) from exc
    if proc.returncode:
        raise _snapshot_failure("identity inspect", proc)
    require_snapshot_identity(proc.stdout.strip(), expected)


def _require_static_elf(path: Path) -> None:
    """Reject loaders and dynamic dependencies in the platform's idle executable.

    An absolute image path alone is unsafe: its executable, ELF loader and preloads
    can all belong to the actor. Validate the copied host binary, not image tools.
    """
    raw = path.read_bytes()
    try:
        if raw[:4] != b"\x7fELF" or raw[4] not in (1, 2) or raw[5] not in (1, 2):
            raise ValueError("not ELF")
        endian = "<" if raw[5] == 1 else ">"
        layout = "16sHHIIIIIHHHHHH" if raw[4] == 1 else "16sHHIQQQIHHHHHH"
        header = struct.unpack_from(endian + layout, raw)
        offset, stride, count = header[5], header[9], header[10]
        if (header[1] != 2 or not 0 < count <= 4096
                or stride < (32 if raw[4] == 1 else 56)
                or offset + stride * count > len(raw)):
            raise ValueError("invalid executable headers")
        for i in range(count):
            kind, = struct.unpack_from(endian + "I", raw, offset + i * stride)
            if kind in (2, 3):  # PT_DYNAMIC / PT_INTERP
                raise ValueError("dynamic executable")
    except (IndexError, struct.error, ValueError) as exc:
        raise SnapshotUnavailable("snapshot startup requires a static ELF BusyBox") from exc


@contextmanager
def snapshot_startup():
    """A trusted idle PID 1; no actor entrypoint, PATH, loader or healthcheck.

    Requires a static BusyBox on the trusted host; absence fails closed. The private
    copy remains mounted read-only until verifier removal. Scorer execution itself
    still uses the snapshot runtime and is outside this startup guarantee.
    """
    source = shutil.which("busybox")
    if not source:
        raise SnapshotUnavailable("snapshot startup requires host static BusyBox")
    with tempfile.TemporaryDirectory(prefix="hg-snapshot-idle-") as tmp:
        binary = Path(tmp) / "busybox"
        try:
            shutil.copyfile(source, binary)
            _require_static_elf(binary)
            binary.chmod(0o755)
        except OSError as exc:
            raise SnapshotUnavailable(f"snapshot startup unavailable: {exc}") from exc
        target = "/" + Path(tmp).name
        yield target + "/busybox", ("--mount", f"type=bind,src={tmp},dst={target},readonly")


def create(name: str, *, image: str, limits: dict | None = None,
           network: str = "none", extra_mounts: tuple[str, ...] = (),
           tmpfs: tuple[str, ...] = (), run_id: str = "", task_id: str = "",
           startup_entrypoint: str | None = None) -> str:
    """Create a stopped container that stays behind, so it can be committed.

    `sleep infinity` as the command because the image is the task's, and a task image
    need not have a long-running entrypoint -- measured: the Terminal-Bench images have
    no `CMD` at all, only `FROM`, `WORKDIR` and a `COPY`. The platform brings its own
    process to keep the container alive while it copies the task in.

    **Root, unlike everything else here.** These images and their checks need it: the
    real Terminal-Bench `test.sh` opens with `apt-get update && apt-get install -y curl`.
    The two host-ownership problems that pushed every other container to the invoking uid
    are avoided instead of ignored -- the harness tree is mounted read-only, and its
    scratch space is a tmpfs that never reaches the host.
    """
    limits = limits or {}
    # **Labelled, because a name is not findable.** A hard kill (SIGKILL, or the harness
    # process tree being reaped) skips every `finally`, so these containers survive it --
    # and `services.reap` searches by `label=harnessgrad.run=<id>`, which is how the
    # *service* containers and the task networks were already findable. The harness
    # container had only a name, so the one leak that actually happened was the one a
    # reaper could not see. Measured (2026-10-04): `screen-all-1` was killed at 22:42
    # with five of these still `running`, and `reap` reported only "network ... has
    # active endpoints" because it could not name the containers holding them.
    labels = ["--label", f"harnessgrad.run={run_id}"]
    if task_id:
        labels += ["--label", f"harnessgrad.task={task_id}"]
    cmd = ["create", "--name", name, "--network", network, *labels,
           "--pids-limit", str(limits.get("pids", 1024)),
           "--memory", str(limits.get("memory", "4g")),
           "--cpus", str(limits.get("cpus", "2"))]
    for path in tmpfs:
        cmd += ["--tmpfs", path]
    if startup_entrypoint is not None:
        cmd += ["--entrypoint", startup_entrypoint, "--no-healthcheck", "--workdir", "/"]
    cmd += list(extra_mounts)
    # Numeric duration works with static BusyBox versions that reject "infinity".
    # The verifier's own timeout/teardown bounds its lifetime, not this idle timer.
    cmd += [image, "sleep", "2147483647" if startup_entrypoint else "infinity"]
    proc = docker(*cmd, timeout=180)
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"could not create a container for {image!r}: {proc.stderr.strip()[:300]}")
    return name


def cp_in(name: str, source: Path, dest: str, *, contents: bool = False) -> None:
    """Copy a host file or directory into a container.

    `docker cp` rather than a bind mount because the point of container state is that the
    task lives *in* the container: what is copied in is part of the image the check will
    grade, and what is not copied in is not there at all.

    `contents=True` copies what is *inside* the source rather than the source directory
    itself, which is what copying a task's initial state into an existing `workdir`
    needs. It is a parameter rather than `source / "."` at the call site because
    `pathlib` **normalizes that dot away** — measured: `docker cp <setup> <ctr>:/app`
    put the task at `/app/setup/...` instead of `/app/...`, and the harness found
    nothing, which looked exactly like a harness that could not do the task.
    """
    src = f"{source}/." if contents else str(source)
    proc = docker("cp", src, f"{name}:{dest}", timeout=300)
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"could not copy {source} into {name}:{dest}: {proc.stderr.strip()[:300]}")


def cp_out(name: str, source: str, dest: Path) -> bool:
    """Copy out of a container. Returns False when the source does not exist.

    False rather than an exception because the common call is "read the harness's trace",
    and a harness that wrote no trace is a fact to record, not a failure to raise.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = docker("cp", f"{name}:{source}", str(dest), timeout=300)
    return proc.returncode == 0


def mkdir_in(name: str, path: str, mode: str = "0777") -> None:
    proc = docker("exec", name, "mkdir", "-p", "-m", mode, path, timeout=60)
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"could not create {path} in {name}: {proc.stderr.strip()[:300]}")


def start(name: str) -> None:
    proc = docker("start", name, timeout=180)
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"could not start {name}: {proc.stderr.strip()[:300]}")


def exec_in(name: str, argv: list[str], *, cwd: str | None = None,
            env: dict[str, str] | None = None, timeout_s: int = 3600,
            ) -> subprocess.CompletedProcess:
    """Run a command inside a running container. `subprocess.run` shape."""
    cmd = ["exec"]
    if cwd:
        cmd += ["-w", cwd]
    for key, value in sorted((env or {}).items()):
        cmd += ["-e", f"{key}={value}"]
    cmd += [name, *argv]
    return docker(*cmd, timeout=timeout_s)


def stop(name: str) -> None:
    """Stop the container, which is what guarantees nothing it started survives.

    A whole container stopping is a stronger statement than killing one process tree,
    and it is free here: `docker commit` works on a stopped container, so the snapshot
    is taken from a filesystem on which nothing can still be writing.
    """
    try:
        proc = docker("stop", "--time", "5", name, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _snapshot_failure("stop", exc=exc) from exc
    if proc.returncode != 0:
        raise _snapshot_failure("stop", proc)


def commit(name: str, tag: str) -> str:
    """Snapshot a container into an image, and return its id.

    An **image** rather than a tarball because an image is immutable: the check runs on a
    writable copy of it, so a check that writes into what it is grading cannot alter the
    thing it graded (INTERFACE.md §2.5.9).
    """
    snapshot_admission(name)
    try:
        proc = docker("commit", name, tag, timeout=SNAPSHOT_COMMIT_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _snapshot_failure("commit", exc=exc) from exc
    if proc.returncode != 0:
        raise _snapshot_failure("commit", proc)
    image_id = proc.stdout.strip()
    if not looks_pinned(image_id):
        raise SnapshotUnavailable(
            f"snapshot commit failed kind=docker: `docker commit` reported {image_id!r}, "
            "not a digest")
    return image_id


def remove_container(name: str) -> None:
    """Never raises: it runs in a `finally`."""
    try:
        docker("rm", "--force", name, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        pass


def remove_image(image: str) -> None:
    """Never raises. A leaked snapshot is a disk leak, and there is one per task."""
    try:
        docker("image", "rm", "--force", image, timeout=300)
    except (subprocess.TimeoutExpired, OSError):
        pass


def network_connect(name: str, network: str) -> None:
    """Attach a container to a second network.

    Needed because a task may want both things at once and docker gives one `--network`
    at create time: the **internal** network, on whose gateway the platform publishes the
    agent's model, and the default bridge, which is the internet the task declared. Every
    real Terminal-Bench task declares `allow_internet = true`, so this is the normal case
    for them rather than an exotic one.
    """
    proc = docker("network", "connect", network, name, timeout=120)
    if proc.returncode != 0:
        raise ContainerUnavailable(
            f"could not attach {name} to {network!r}: {proc.stderr.strip()[:300]}")


def limits_from(spec: dict) -> dict:
    """The `docker run/create` resource envelope declared by an environment."""
    return {"cpus": spec.get("cpus", 2), "pids": 1024,
            "memory": f"{int(spec.get('memory_mb', 4096))}m"}


def _number(value) -> str:
    """A CPU count as docker spells it: `2`, not `2.0`.

    `envelope()` normalises to a float so two envelopes compare as numbers; the argv must
    not inherit that, because `--cpus 2.0` and `--cpus 2` are the same request and only one
    of them is the string this module has always passed.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContainerUnavailable(f"unreadable cpu limit {value!r}") from exc
    return str(int(number)) if number.is_integer() else str(number)


def envelope(limits: dict | None = None) -> dict:
    """The resource flags a container **really gets**, in one place and in one shape.

    Three readers have to agree about this and used to have no way to: `command()` writes
    the flags, `eval/service/container_observation.py` measures them back, and the runner
    has to know whether an environment's declared `cpus`/`memory_mb` are what the launcher
    applies. A fourth hand-written copy of "the defaults are 2 / 4g / 1024" is a copy that
    can drift, and the drift would be invisible -- the receipt would describe a container
    nobody runs.

    `limits=None` is not "no limits": it is docker's default CPU, memory and PID envelope,
    which is what `command()` has always applied when a caller passes none. The numbers are
    the same values as `memory_mb`, so a caller that has to compare an envelope against a
    declaration does not have to parse `4g`.

    **The memory spelling is the default's when the value is the default.** `limits_from`
    renders 4096 MB as `4096m`, and `--memory 4096m` is the same request as `--memory 4g`
    -- but the argv is what a run's log and receipts quote, and a dataset whose envelope
    did not change must not read as though it had. Measured against the two datasets on
    this path (`appworld`, `tau_bench`), which both declare 2 cpus / 4096 MB.
    """
    limits = limits or {}
    memory = str(limits.get("memory", DEFAULT_MEMORY))
    memory_mb = _memory_mb(memory)
    if memory_mb == _memory_mb(DEFAULT_MEMORY):
        memory = DEFAULT_MEMORY
    return {"cpus": float(limits.get("cpus", 2)), "pids": int(limits.get("pids", 1024)),
            "memory": memory, "memory_mb": memory_mb}


#: `command()`'s own default memory flag. Named because `envelope()` compares against it:
#: a request for exactly 4096 MB keeps this spelling rather than `4096m`.
DEFAULT_MEMORY = "4g"


def _memory_mb(memory: str) -> int:
    """The MB a docker memory string names. Only the two spellings `command()` emits."""
    suffixes = {"g": 1024, "m": 1}
    text = memory.strip().lower()
    factor = suffixes.get(text[-1:], None)
    if factor is not None:
        text = text[:-1]
    try:
        value = float(text)
    except ValueError as exc:
        raise ContainerUnavailable(
            f"unreadable docker memory limit {memory!r}") from exc
    return int(round(value * (factor or 1)))
