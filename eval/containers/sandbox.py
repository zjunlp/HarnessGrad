"""Run the subject in its own mount namespace, so the platform is not on its disk.

The threat this closes
----------------------
`eval/run/runner.py` executes a harness the platform did not write. Until this module
existed the harness was an ordinary child process of the driver, which meant -- and
this was measured, not assumed -- that it could `open()` `driver.py`, read `.env`,
and write into `eval/run/metrics.py`. `eval/run/integrity.py` catches all three after the
fact. Catching is not preventing: the run is void, but the credentials have already
been read.

What was measured, before and after
-----------------------------------
A harness whose entrypoint probes its own surroundings, run with and without the
namespace:

                                bare        sandboxed
    HarnessGrad/.env            readable    absent
    HarnessGrad/driver.py       readable    absent
    HarnessGrad/eval/           readable    absent
    HarnessGrad/methods/        readable    absent
    other runs' workspaces      readable    absent
    HG_METHOD_* in the env      leakable    absent

    startup overhead            --          +11.1 ms (median of 15)
    one live model call         ~2100 ms    ~2100 ms
    relative cost               --          +0.53%

Neither a container nor an image is needed, which matters for a reason that is not
about speed: a container fixes the language and runtime, and `docs/writing_a_harness.md`
§4 promises the opposite. `bwrap` runs whatever the manifest names, so that promise
survives.

The rule the mount plan follows
-------------------------------
Paths are bound at **the same absolute path they have on the host**. No translation,
because every translation is a place where a harness's own record of what it did
stops matching the filesystem it did it on. The plan is therefore:

  1. bind the runtime the subject needs (`/usr`, `/bin`, the interpreter's prefix)
     and leave the rest of the host root alone,
  2. hide the tree that contains the platform *and* the work root, using an empty
     tmpfs -- the platform is not "permission denied", it is absent,
  3. bind the work root back, which re-exposes exactly one subtree of the hidden
     tree and no siblings,
  4. bind the task directory, which lives under `/tmp` and would otherwise be lost
     behind a tmpfs.

Step 2 is what makes step 3 safe: binding a directory exposes that directory, not
its siblings, so `harnessgrad_work/` reappears while `HarnessGrad/` stays gone.

What this does not do
---------------------
By default the subject has unrestricted network access and holds the agent's
API key. Native tasks that declare network='none' get a private network namespace.
A subject on the default network can
therefore exfiltrate that key to anywhere it likes. A namespace cannot fix this;
only egress filtering or a scoped key can, and neither is implemented. This is a
recorded gap, not an oversight -- see INTERFACE.md §4.8.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, targets
from eval.containers import model_route, tool_route
from eval.containers.process import run as run_process

from eval.ckpt.git_state import STATE_DIRNAME
from harnessgrad.config import current as current_config

#: The mount plan's version. Bump it whenever what the subject can see changes.
#: A curve made under plan 1 and one made under plan 2 are measurements of a
#: harness in two different worlds, so `run_meta.json` records this number and
#: `tools/plot_curve.py` refuses to put them on the same axis without saying so.
PLAN_VERSION = 5  # 5 adds the read-only host-side tool socket (`eval/containers/tool_route.py`);
                  # 4 was the offline model socket and the trusted namespace-local HTTP bootstrap.

#: Runtime trees a subject may need, bound read-only at their host paths. Kept as
#: a short explicit list rather than "the whole host": the point of the namespace
#: is that what is not named is not there.
RUNTIME = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

#: Sub-paths of `/etc` that carry DNS. `/etc/resolv.conf` is a symlink into
#: `/run/systemd/resolve` on this distribution, and binding only `/etc` leaves it
#: dangling -- which presents as "model call failed", the worst possible shape for
#: a sandbox bug, because it looks like a harness defect. WSL instead points at
#: /mnt/wsl/resolv.conf; bind only that generated file, not the shared WSL tree.
DNS = ("/run/systemd/resolve", "/mnt/wsl/resolv.conf")


class SandboxUnavailable(RuntimeError):
    """bwrap is missing or cannot build a namespace. Never silently fall back."""


def interpreter_prefix() -> Path | None:
    """The tree holding the running interpreter, if it lives outside the host root.

    A conda env or a venv is one directory that must survive step 2. `/usr/bin/python3`
    needs nothing extra (step 1 already bound `/usr`); `/mnt/8t/.../anaconda3/bin/python3`
    needs its prefix, or the subject starts and immediately dies importing `openai`.
    """
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if (parent / "pyvenv.cfg").exists() or (parent / "conda-meta").is_dir():
            return parent
        if parent == Path("/"):
            break
    return None


def hidden_trees(work_root: Path, platform: Path) -> list[Path]:
    """Exactly which directories get covered by an empty tmpfs.

    Two, and the list is this short on purpose. An earlier version hid every
    directory from the root down to the work root, on the theory that wholesale
    erasure was tidier. It erased the interpreter too: this platform's Python lives
    under `/mnt/8t/.../anaconda3`, so covering `/mnt` left the subject unable to
    start and the failure read as a broken harness.

    So:

    * **the platform itself**, so the driver, the scorer and `.env` are not on the
      subject's disk. Its siblings stay visible, deliberately -- hiding a
      neighbouring work tree buys nothing and costs diagnostics.
    * **`/tmp`**, always and unconditionally. The task directory is bound back
      afterwards; without this, two runs on a machine whose work root is elsewhere
      would share a `/tmp` and could read each other's answers.

    The work root's parents are never touched, which is what makes the plan
    independent of where the trees sit: no common ancestor is required, so a work
    root on a different mount from the platform is fine. And since bwrap starts from
    an empty root rather than inheriting the host's, hiding the platform is the only
    hiding that matters -- everything unnamed is already absent.
    """
    work_root, platform = work_root.resolve(), platform.resolve()
    if platform == work_root or platform in work_root.parents:
        raise SandboxUnavailable(
            f"the work root {work_root} is inside the platform {platform}; the "
            f"subject's own tree cannot be hidden from it")
    hidden = [platform]
    if Path("/tmp") != work_root:
        hidden.append(Path("/tmp"))
    return hidden


def _bwrap() -> str:
    """Where bubblewrap is, honouring `HG_BWRAP` if it is set to an executable.

    The override exists because the installer checks the binary it is about to use and
    the platform must check the *same* one: with two candidates in play, `install.sh`
    could report a working `bwrap` from a non-standard path while the driver silently
    used a broken one from `PATH`, and the diagnostic would then be about a different
    program than the one that failed.
    """
    override = current_config().sandbox.bwrap
    if override:
        if os.access(override, os.X_OK):
            return override
        raise SandboxUnavailable(
            f"HG_BWRAP={override!r} is not an executable. Unset it to fall back to "
            f"PATH, or point it at a real bubblewrap.")
    exe = shutil.which("bwrap")
    if not exe:
        raise SandboxUnavailable(
            "bwrap not found on PATH. Run ./install.sh, install bubblewrap yourself "
            "(`apt install bubblewrap`), or pass --no-sandbox to accept that a harness "
            "can reach the platform.")
    return exe


def command(argv: list[str], *, platform: Path, work_root: Path,
            task_dir: Path, env: dict[str, str], repo: Path | None = None,
            state_dirs: tuple[Path, ...] = (),
            task_dir_writable: bool = True,
            runtime_paths: tuple[Path, ...] = (),
            visible_paths: tuple[Path, ...] = (),
            writable_paths: tuple[Path, ...] = (),
            network: str | None = None,
            model_socket: Path | None = None,
            model_path: str = "",
            tool_socket: Path | None = None,
            package_overlays: tuple[tuple[Path, Path], ...] = (),
            immutable_runtimes: tuple[tuple[Path, str], ...] = (),
            private_paths: tuple[Path, ...] = (),
            unsetenv: tuple[str, ...] = ()) -> list[str]:
    """Wrap `argv` so it runs with the platform off its disk.

    Raises rather than degrading. A sandbox that quietly did not apply would make
    every subsequent number mean something different from what it says -- the one
    failure mode this whole module exists to prevent.
    """
    bwrap = _bwrap()
    platform, work_root = platform.resolve(), work_root.resolve()
    repo = platform if repo is None else repo.resolve()
    repo_in_work_root = repo == work_root or work_root in repo.parents
    private_tmp = Path("/tmp") != work_root

    plan: list[str] = ["--die-with-parent", "--unshare-pid", "--proc", "/proc",
                       "--dev", "/dev"]
    if network not in (None, "none"):
        raise ValueError(f"unsupported native sandbox network policy: {network!r}")
    if network == "none":
        plan += ["--unshare-net"]

    for path in RUNTIME:
        if Path(path).exists():
            plan += ["--ro-bind", path, path]
    for path in DNS:
        if Path(path).exists():
            plan += ["--ro-bind", path, path]

    prefix = interpreter_prefix()
    if prefix is not None:
        plan += ["--ro-bind", str(prefix), str(prefix)]

    # Trees the harness declares it needs, bound read-only.
    #
    # The interpreter prefix above is the same idea hard-coded for Python. It stopped
    # being enough the moment a harness could be a *CLI*: Claude Code installed into a
    # venv or a local build lives outside `/usr`, and an agent CLI that cannot be
    # executed is indistinguishable from a broken harness. A harness that needs a tree
    # says so in its manifest rather than the platform guessing.
    #
    # Bound **before** the hides, so a declaration cannot re-materialise something the
    # hides remove: the platform stays hidden even if a harness declares its path.
    for path in runtime_paths:
        target = Path(path)
        if target.exists():
            plan += ["--ro-bind", str(target), str(target)]

    # Paths that must survive the hides below. Bound twice, exactly like `repo`: once
    # here so the mount target exists, and again after the hides, because **a tmpfs
    # over a parent covers what is mounted beneath it**. That second bind is what makes
    # a path inside the hidden tree visible again -- which is how a method is given its
    # own program (under the platform) without being given the platform.
    for path in visible_paths:
        target = Path(path)
        if target.exists():
            plan += ["--ro-bind", str(target), str(target)]

    # --- What the subject must be able to read ---------------------------------
    #
    # Bound before the hides, because these are the mounts the subject's whole job
    # depends on. All of them are re-asserted at the very end; this pass is what makes
    # the *hides* possible, that one is what makes them true.
    plan += ["--ro-bind", str(work_root), str(work_root)]
    if repo_in_work_root:
        pass  # already covered by the work root bind above
    else:
        # Bound explicitly rather than assumed to be inside the work root. `run_one`
        # normally passes `<work_root>/<run>/workspace`, but "normally" is how an
        # earlier version shipped a sandbox in which the subject could rewrite itself:
        # a harness outside the bound tree fell through to bwrap's auto-created parents
        # and ended up writable. The tree the harness must read is a stated
        # requirement now, not an inference.
        plan += ["--ro-bind", str(repo), str(repo)]

    # The task directory. Bound here and not after the hides, so it cannot re-create a
    # parent that the work root bind above needs to have made first.
    plan += ["--bind", str(task_dir), str(task_dir)]

    # --- What the subject must not see -----------------------------------------
    #
    # A tmpfs on the platform's own path. Its contents go; the directory name survives
    # as an empty mount point, which is the deliberate stopping point -- nothing
    # readable remains, and erasing the name too would mean covering the whole branch
    # the work root lives on.
    for tree in hidden_trees(work_root, platform):
        plan += ["--tmpfs", str(tree)]
    if private_tmp:
        # A private /tmp, so two runs cannot read each other's task directories.
        plan += ["--tmpfs", "/tmp"]

    # --- Re-assert everything, in order ----------------------------------------
    #
    # This pass exists because bwrap creates missing mount targets: a writable `--bind`
    # of a directory that *contains* the harness re-materialises it, and the later
    # mount wins. Measured: with the task directory one level above the harness, the
    # final `--bind /tmp/xxx /tmp/xxx` made the harness's own source writable and the
    # self-check reported `source_writable='ok'` -- a sandbox that silently did not
    # apply, which is the one failure this module exists to prevent.
    #
    # So: mounts that must exist come first (parents before children), then the
    # read-only trees, then the writable hole on top of the read-only tree it lives in.
    #
    # **The order inside this pass is load-bearing and was wrong.** The read-only
    # re-asserts used to come *after* the writable hole, which is fine while they do
    # not nest -- the harness's work directory is under `/tmp` and the work root is
    # elsewhere. A *method's* scratch directory is under the work root, and there the
    # read-only bind of the parent won: the method got `Read-only file system` writing
    # its own trajectory. Read-only first, writable hole last, which is what the
    # sentence above always said.
    # The writable hole first, then everything that must stay read-only **even where
    # it sits inside the hole**, then the enclosing tree.
    #
    # This order is the whole of it, and it took three attempts to get right:
    #
    #   * the harness's source lives inside the hole's parent, so it must be re-bound
    #     read-only *after* the hole or the harness can rewrite itself (the self-check
    #     catches exactly this, and did);
    #   * a method's `base_harness` is a sibling of its scratch directory, so it too
    #     must come after the hole;
    #   * `work_root` must come last, and must not enclose the hole -- a read-only bind
    #     of a parent wins over a writable bind of its child, which is how a method's
    #     own trajectory file came back `Read-only file system`.
    plan += ["--bind", str(task_dir), str(task_dir)]
    if not (immutable_runtimes and repo == task_dir.resolve()):
        plan += ["--ro-bind", str(repo), str(repo)]
    for path in visible_paths:
        target = Path(path)
        if target.exists():
            plan += ["--ro-bind", str(target), str(target)]
    if not (immutable_runtimes and work_root == task_dir.resolve()):
        plan += ["--ro-bind", str(work_root), str(work_root)]
    if not task_dir_writable and not immutable_runtimes:
        # Verification runs with the harness's artifacts frozen. Said *last*, so it is
        # the final word about this path.
        plan += ["--ro-bind", str(task_dir), str(task_dir)]

    # The check's own verdict directory, when it declares one. **After** the read-only
    # re-assert of the task dir, so it is the last word about that subtree: a host check
    # that must write a `reward_file` can write exactly there and nowhere else, and the
    # harness's artifacts stay frozen. This exists so the reward-file contract does not
    # require turning the verification sandbox off -- an unsandboxed check would be one
    # whose import path (`sys.path[0]`) is the harness's workdir, which is a route from
    # the harness into a process that can read the platform.
    for path in writable_paths:
        target = Path(path)
        if target.exists():
            plan += ["--bind", str(target), str(target)]

    # The writable hole: a tmpfs over the harness's own scratch directory, so a harness
    # can write caches, session state or intermediate files where it expects to find
    # them -- inside its tree -- while the code beside them stays untouchable.
    # Measured: with a read-only parent and a tmpfs child, rewriting `agent.py` raises
    # `OSError`, writing `.state/cache.json` succeeds, and the host tree is not touched.
    #
    # A tmpfs rather than a bind, so the contents are per-task and evaporate at the end
    # of it. A directory shared across the task set would make a score depend on what an
    # earlier task left behind, which is what the task boundary exists to prevent;
    # persistent cross-session memory belongs in a task that contains the sessions, not
    # in storage the platform supplies.
    for state_dir in state_dirs:
        state = Path(state_dir)
        if not state.is_dir():
            raise SandboxUnavailable(
                f"the harness has no {state.name}/ directory at {state}, so the "
                f"sandbox cannot give it a writable scratch space. It is created when "
                f"the workspace is staged (ckpt/git_state.py); a workspace built by "
                f"hand from an older commit will not have it.")
        plan += ["--tmpfs", str(state)]

    # Last mounts win: runtime_paths or a parent bind must never uncover the
    # installed reference-bearing package after its public projection is mounted.
    for source, target in package_overlays:
        source, target = Path(source).resolve(), Path(target).resolve()
        if not source.is_dir() or not target.is_dir():
            raise SandboxUnavailable("agent package overlay is missing")
        for writable in (task_dir, *writable_paths, *state_dirs):
            writable = Path(writable).resolve()
            if source == writable or writable in source.parents:
                raise SandboxUnavailable("agent package projection is in writable storage")
        plan += ["--ro-bind", str(source), str(target)]

    # Only in the surviving re-assert pass, after the writable task --bind and all
    # parent mounts: bwrap is last-one-wins. These exact shared sources are read-only
    # runtime projections, never host copies; no later mount may reopen their trees.
    for source, target in targets(task_dir, immutable_runtimes,
                                  writable_paths=(*writable_paths, *state_dirs)):
        plan += ["--ro-bind", str(source), str(target)]
    if immutable_runtimes and not task_dir_writable:
        # New verifier-only mount points must be made while the task parent is
        # writable. Freeze that parent without recursively replacing child mounts:
        # runtime binds stay read-only, and the verdict directory stays writable.
        plan += ["--remount-ro", str(task_dir)]

    if model_socket is not None:
        # After every parent bind/hide and immutable projection: no later mount
        # may cover or reopen the infrastructure socket or trusted bootstrap.
        # Fixed destinations cannot alias task state or immutable-runtime targets.
        plan += model_route.mounts(
            model_socket, network=network,
            exposed_paths=(platform, work_root, repo, task_dir, *runtime_paths,
                           *visible_paths, *writable_paths, *state_dirs,
                           *(target for _, target in package_overlays),
                           *(task_dir / rel for _, rel in immutable_runtimes)),
            writable_paths=(task_dir, *writable_paths, *state_dirs))
        argv = model_route.bootstrap(argv, model_path)

    if tool_socket is not None:
        # The interaction environment, served from the host: the same placement as the
        # model socket and for the same reason -- after every parent bind and every
        # hide, so no later mount may cover or reopen it. The tool route has its own
        # fixed destination (`/run/harnessgrad-tool`) and its own rules, and it is
        # told about the model route's directory so a task cannot point one route's
        # mount at the other's. Unlike the model route it does not require
        # `network == "none"`: `{"kind": "files"}` datasets may leave the policy unset,
        # and a UNIX socket is not reachable over a network (`eval/containers/
        # tool_route.py` says why).
        #
        # Nothing is inserted into `argv` here, unlike the model bootstrap: the agent
        # imports the published transport module itself. A dataset that passes
        # `tool_socket` has to bump PLAN_VERSION, because from that commit on its
        # subjects see a socket they did not see before; no dataset passes it yet.
        plan += tool_route.mounts(
            tool_socket, network=network,
            exposed_paths=(platform, work_root, repo, task_dir, *runtime_paths,
                           *visible_paths, *writable_paths, *state_dirs,
                           *(target for _, target in package_overlays),
                           *(task_dir / rel for _, rel in immutable_runtimes),
                           model_route.ROOT),
            writable_paths=(task_dir, *writable_paths, *state_dirs))

    if private_paths:
        from eval.containers.private_mounts import reject_visible
        reject_visible(plan, private_paths)

    #: Variables the platform itself sets to a filesystem path. Only these are
    #: checked against the hidden tree, and the list is explicit because guessing
    #: is what went wrong twice: `HG_AGENT_MODEL=deepseek-flash` is not a path, but
    #: `Path("deepseek-flash").resolve()` is relative to the platform's own cwd and
    #: lands inside the hidden tree -- so a value-shaped guess deleted the agent's
    #: API key out of the sandbox, and the failure would have surfaced as "the
    #: harness cannot reach a model". A model name, a URL and a secret are not
    #: paths, and the platform does not get to pretend otherwise.
    path_bearing = ("HARNESSGRAD_WORKSPACE", "HARNESSGRAD_WORK_ROOT", "PYTHONPATH")

    #: Directories the plan re-asserts **after** the hides. A value pointing into one of
    #: them does survive the plan, so dropping it would be a lie in the other direction.
    #: Resolved once: `visible` is asked about every variable in the environment, and a
    #: symlinked `visible_path` would otherwise not match its own resolved targets.
    survivals = tuple(Path(p).resolve()
                      for p in (*visible_paths, *writable_paths, *state_dirs))

    def visible(name: str, value: str) -> bool:
        """Does this platform-set path still exist once the plan is applied?"""
        if name not in path_bearing:
            return True
        try:
            target = Path(value).resolve()
        except (OSError, ValueError):
            return True
        # Measured: a dependency overlay under `/tmp` was mounted and its `PYTHONPATH` was
        # still dropped, so the import answered from the host's site-packages while the
        # point recorded `source: overlay`. A directory the harness can read must be
        # allowed to name itself.
        for anchor in (work_root, task_dir, *survivals):
            if target == anchor or anchor in target.parents:
                return True
        for tree in hidden_trees(work_root, platform):
            if target == tree or tree in target.parents:
                print(f"  sandbox: dropping {name}={value} -- it points under the "
                      f"hidden tree {tree} and is not the work root. The subject "
                      f"will not see it.", file=sys.stderr)
                return False
        return True

    for name, value in sorted(env.items()):
        if visible(name, value):
            plan += ["--setenv", name, value]

    # Last, so it wins over anything above. This exists for variables the *launcher* needs
    # and the subject must not see: `systemd-run --user` cannot reach the user manager
    # without `XDG_RUNTIME_DIR`, and handing that (or the session bus address) to the
    # harness would be a way out of the sandbox that the plan otherwise never opened. The
    # caller only lists variables it added itself, so a subject's view is unchanged.
    for name in unsetenv:
        plan += ["--unsetenv", name]

    return [bwrap] + plan + ["--"] + argv


# --- The resource envelope ------------------------------------------------------
#
# The namespace bounds what a subject can *see*. It did not bound what it could
# **consume**, and that asymmetry is measurable: an `exec` task gets
# `--memory 4g --cpus 2 --pids-limit 1024` from Docker, while a `files` task ran with no
# limit at all -- one CLI that allocates without end takes the machine, the model server
# and every concurrent run with it, and the run that did it is not even the one that
# reports the damage.
#
# A user scope gives the same three limits without root and without a daemon:
# `systemd-run --user --scope` is unprivileged, synchronous, and lands the child under
# `memory.max` -- measured here: `MemoryMax=1G` produced `memory.max=1073741824` inside
# the scope, and `MemoryMax=64M` killed a 400 MB allocation with exit 137.
#
# `--collect` matters: a scope that fails is garbage-collected instead of being left
# behind as a failed unit, so a hundred tasks cannot fill the user's unit list.

#: What makes `systemd-run` a synchronous, self-cleaning wrapper around one command.
SCOPE_FLAGS = ("--user", "--scope", "--quiet", "--collect")

#: Variables the scope *launcher* needs in order to find the user manager. They are
#: handled apart from the subject's environment on purpose: `DBUS_SESSION_BUS_ADDRESS` is
#: a way to talk to everything the operator's session can, so a harness that is given one
#: has a door the mount plan never opened.
MANAGER_ENV = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")

#: One probe per distinct envelope, per process. The probe starts a scope, so it is the
#: kind of thing that must not happen once per task.
_ENVELOPE_CACHE: dict[tuple, dict] = {}


def configured_limits() -> dict:
    """The envelope a `files` subject gets by default: the container chain's numbers."""
    cfg = current_config().sandbox
    return {"memory": f"{int(cfg.memory_mb)}M",
            "cpus": float(cfg.cpus),
            "pids": int(cfg.pids)}


def _properties(limits: dict) -> list[str]:
    """The cgroup properties, in `systemd-run` spelling.

    `MemorySwapMax=0` is not decoration: a cap that can be satisfied from swap is not a
    memory cap, it is a slower way to run out of memory. Docker's `--memory` refuses swap
    by default for the same reason.
    """
    props: list[str] = []
    if limits.get("memory"):
        props += ["-p", f"MemoryMax={limits['memory']}", "-p", "MemorySwapMax=0"]
    if limits.get("cpus"):
        # `CPUQuota` is a percentage of **one** CPU, so two cores is `200%`.
        props += ["-p", f"CPUQuota={int(round(float(limits['cpus']) * 100))}%"]
    if limits.get("pids"):
        props += ["-p", f"TasksMax={int(limits['pids'])}"]
    return props


def _drop(props: list[str], name: str) -> list[str]:
    """The same property list without one property. `_properties` always returns pairs."""
    kept: list[str] = []
    for i in range(0, len(props), 2):
        if not props[i + 1].startswith(f"{name}="):
            kept += props[i:i + 2]
    return kept


def _probe_envelope(limits: dict) -> dict:
    """Whether this machine can bound a subject, proved by bounding one.

    Asking `systemctl --user is-system-running` would be cheaper and wrong: it is
    `degraded` on this host while a scope works perfectly. The only answer that means
    anything is a scope that ran.

    Two attempts, not one. `MemorySwapMax` needs systemd 243 and swap accounting, and a
    host without either would otherwise lose the **memory** cap -- the one that matters --
    because of a refinement. A cap that can be satisfied from swap is a slower way to run
    out of memory, so the first attempt asks for both; if the properties themselves are
    refused, the second keeps the cap and gives up the refinement, and says so.
    """
    tool = shutil.which("systemd-run")
    if tool is None:
        return {"applied": False,
                "reason": "systemd-run is not installed, so a `files` subject runs with "
                          "no memory, CPU or task cap"}
    wanted = _properties(limits)
    attempts = [wanted]
    if any(prop.startswith("MemorySwapMax=") for prop in wanted):
        attempts.append(_drop(wanted, "MemorySwapMax"))
    failures: list[str] = []
    for props in attempts:
        try:
            proc = subprocess.run([tool, *SCOPE_FLAGS, *props, "--", "/bin/true"],
                                  capture_output=True, text=True, errors="replace",
                                  timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            continue
        if proc.returncode == 0:
            state = {"applied": True, "tool": tool, "properties": props}
            if props != wanted:
                state["degraded"] = ("this systemd refuses MemorySwapMax, so the cap can "
                                     "be satisfied from swap")
            return state
        failures.append(((proc.stderr or "") + (proc.stdout or "")).strip()[-300:]
                        or f"systemd-run exited {proc.returncode}")
    return {"applied": False, "reason": " | ".join(failures)}


def envelope(*, limits: dict | None = None) -> dict:
    """The envelope in force, and whether it actually applied.

    `applied: false` is not an error the run should die of -- a bounded run and an
    unbounded one measure the same thing until the subject misbehaves -- but it is a
    fact a reader needs, so it goes on the record and into the run's console header.
    """
    limits = dict(limits if limits is not None else configured_limits())
    key = (limits.get("memory"), limits.get("cpus"), limits.get("pids"))
    if key not in _ENVELOPE_CACHE:
        _ENVELOPE_CACHE[key] = _probe_envelope(limits)
    return {"limits": limits, **_ENVELOPE_CACHE[key]}


def run(argv: list[str], *, platform: Path, work_root: Path, task_dir: Path,
        env: dict[str, str], timeout_s: int, cwd: Path | None = None,
        repo: Path | None = None, state_dirs: tuple[Path, ...] = (),
        task_dir_writable: bool = True,
        runtime_paths: tuple[Path, ...] = (),
        visible_paths: tuple[Path, ...] = (),
        writable_paths: tuple[Path, ...] = (),
        input_text: str | None = None,
        network: str | None = None,
        model_socket: Path | None = None,
        model_path: str = "",
        tool_socket: Path | None = None,
        package_overlays: tuple[tuple[Path, Path], ...] = (),
        immutable_runtimes: tuple[tuple[Path, str], ...] = (),
        private_paths: tuple[Path, ...] = (),
        limits: dict | None = None):
    """`subprocess.run` with the namespace in place. Same return type.

    `input_text` exists because a *method* is driven over stdin (it reads one JSON
    request and writes one JSON reply), while a harness is driven with arguments. Both
    need the same namespace, so the namespace takes the input as a parameter rather
    than the caller reaching for `subprocess` and losing the sandbox by accident.

    `limits` is the resource envelope. `None` means **uncapped**, which is the default
    because the caller knows whether it is launching a subject or a check: the harness
    and the method opt in, the task's own verifier does not. Capping a verifier would
    turn "this check needs 6 GB" into a failed check, which is a wrong verdict rather
    than a bounded one.
    """
    # The scope, if one applies, is decided **before** the plan is built: `systemd-run
    # --user` cannot reach the user manager without `XDG_RUNTIME_DIR`, and the plan may not
    # carry it. So whatever the launcher needs, the launcher gets, and the subject is told
    # to drop it again (`--unsetenv`) so that its own view is exactly what it was.
    state = envelope(limits=limits) if limits is not None else None
    applied = bool(state and state["applied"])
    launch_env, unsetenv = env, ()
    if applied:
        added = {name: os.environ[name] for name in MANAGER_ENV
                 if name in os.environ and name not in env}
        if added:
            launch_env, unsetenv = {**env, **added}, tuple(added)
    try:
        wrapped = command(argv, platform=platform, work_root=work_root,
                          task_dir=task_dir, env=env, repo=repo,
                          state_dirs=state_dirs,
                          task_dir_writable=task_dir_writable,
                          runtime_paths=runtime_paths,
                          visible_paths=visible_paths,
                          writable_paths=writable_paths,
                          network=network, package_overlays=package_overlays,
                          model_socket=model_socket, model_path=model_path,
                          tool_socket=tool_socket,
                          immutable_runtimes=immutable_runtimes,
                          **({'private_paths': private_paths} if private_paths else {}),
                          **({'unsetenv': unsetenv} if unsetenv else {}))
    except SandboxUnavailable as exc:
        if immutable_runtimes:
            raise ImmutableRuntimeUnavailable(str(exc)) from exc
        raise
    if applied:
        # Outside the namespace and around it: the scope has to contain the bwrap process,
        # or a subject that escapes the namespace is also outside the cap. The **applied**
        # properties, not the wanted ones -- a degraded probe must not put back the
        # property this host refused.
        wrapped = [state["tool"], *SCOPE_FLAGS, *state["properties"], "--"] + wrapped
    try:
        proc = run_process(wrapped, input_text=input_text,
                           timeout_s=timeout_s, cwd=cwd, env=launch_env)
    except OSError as exc:
        if immutable_runtimes:
            raise ImmutableRuntimeUnavailable(str(exc)) from exc
        raise
    if immutable_runtimes and proc.returncode and "bwrap:" in proc.stderr:
        raise ImmutableRuntimeUnavailable(f"immutable runtime sandbox failed: {proc.stderr.strip()}")
    return proc


def self_check(platform: Path, work_root: Path) -> dict:
    """Prove the namespace applies before a paid run depends on it.

    Returns {"ok": bool, "detail": str}. Called by the driver at startup: a sandbox
    that cannot be built must stop the run at the door, not once per task, and
    never by falling back to no sandbox.
    """
    try:
        bwrap = _bwrap()
    except SandboxUnavailable as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        hidden = hidden_trees(work_root, platform)
    except SandboxUnavailable as exc:
        return {"ok": False, "detail": str(exc)}

    with tempfile.TemporaryDirectory(prefix="hg-sandbox-check-") as tmp:
        tmp_path = Path(tmp)
        # A throwaway harness, so the probe can check both halves of the plan: the
        # platform is gone, and the writable hole is genuinely writable. Checking
        # only the first would pass on a sandbox that also broke the harness.
        fake_repo = tmp_path / "harness"
        state_dir = fake_repo / STATE_DIRNAME
        state_dir.mkdir(parents=True)
        (fake_repo / "agent.py").write_text("# a harness\n")

        probe = tmp_path / "probe.py"
        probe.write_text(
            "import json, pathlib\n"
            f"platform = pathlib.Path({str(platform)!r})\n"
            f"repo = pathlib.Path({str(fake_repo)!r})\n"
            "def attempt(fn):\n"
            "    try:\n"
            "        fn(); return 'ok'\n"
            "    except OSError as exc:\n"
            "        return type(exc).__name__\n"
            "print(json.dumps({\n"
            "  'platform_visible': (platform / 'driver.py').exists(),\n"
            "  'env_visible': (platform / '.env').exists(),\n"
            "  'source_writable': attempt(\n"
            "      lambda: (repo / 'agent.py').write_text('hacked')),\n"
            "  'state_writable': attempt(\n"
            "      lambda: (repo / '.state' / 'cache.json').write_text('{}')),\n"
            "}))\n"
        )
        try:
            proc = run([sys.executable, str(probe)], platform=platform,
                       work_root=work_root, task_dir=tmp_path,
                       env={}, timeout_s=60, cwd=tmp_path,
                       repo=fake_repo, state_dirs=(state_dir,))
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "detail": f"sandbox probe did not run: {exc}"}

    if proc.returncode != 0:
        return {"ok": False,
                "detail": f"sandbox probe exited {proc.returncode}: "
                          f"{proc.stderr.strip()[-400:]}"}
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "detail": f"unreadable probe output: {proc.stdout[:200]}"}
    if result.get("platform_visible") or result.get("env_visible"):
        return {"ok": False,
                "detail": f"the platform is still visible inside the sandbox "
                          f"({result}); hiding {[str(h) for h in hidden]} did not "
                          f"take effect"}
    if result.get("source_writable") != "OSError":
        return {"ok": False,
                "detail": f"the harness could rewrite its own source "
                          f"(source_writable={result.get('source_writable')!r}); one "
                          f"copy of it scores every task, so two tasks could run two "
                          f"different programs"}
    if result.get("state_writable") != "ok":
        return {"ok": False,
                "detail": f"the harness has no writable scratch space "
                          f"(state_writable={result.get('state_writable')!r}); a "
                          f"harness that cannot write while working cannot do most "
                          f"tasks"}

    # The verification pass runs with the harness's artifacts frozen, and that is the
    # whole reason the platform's grade can be trusted: a check the subject can edit
    # is not a check. Probed separately rather than assumed, because this mode is
    # only taken on runs that declare a `command` verifier -- so a broken read-only
    # mount would stay invisible until the first dataset that needed it, and would
    # look like a wrong score rather than a broken sandbox.
    with tempfile.TemporaryDirectory(prefix="hg-verify-check-") as tmp2:
        verify_dir = Path(tmp2) / "artifacts"
        verify_dir.mkdir()
        (verify_dir / "answer.txt").write_text("the harness's work\n")
        probe2 = Path(tmp2) / "probe2.py"
        probe2.write_text(
            "import json, pathlib\n"
            f"platform = pathlib.Path({str(platform)!r})\n"
            f"artifacts = pathlib.Path({str(verify_dir)!r})\n"
            "def attempt(fn):\n"
            "    try:\n"
            "        fn(); return 'ok'\n"
            "    except OSError as exc:\n"
            "        return type(exc).__name__\n"
            "print(json.dumps({\n"
            "  'platform_visible': (platform / 'driver.py').exists(),\n"
            "  'artifacts_writable': attempt(\n"
            "      lambda: (artifacts / 'answer.txt').write_text('edited')),\n"
            "  'artifacts_readable': (artifacts / 'answer.txt').read_text().strip(),\n"
            "}))\n"
        )
        try:
            # `task_dir` is the whole probe directory, not just the artifacts: the
            # probe script itself has to be readable from inside, and the point of the
            # check is that *everything* there is read-only.
            proc2 = run([sys.executable, str(probe2)], platform=platform,
                        work_root=work_root, task_dir=Path(tmp2),
                        env={}, timeout_s=60, cwd=Path(tmp2),
                        repo=Path(tmp2), task_dir_writable=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "detail": f"verification probe did not run: {exc}"}

    if proc2.returncode != 0:
        return {"ok": False,
                "detail": f"verification probe exited {proc2.returncode}: "
                          f"{proc2.stderr.strip()[-400:]}"}
    try:
        vresult = json.loads(proc2.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False,
                "detail": f"unreadable verification probe output: {proc2.stdout[:200]}"}
    if vresult.get("platform_visible"):
        return {"ok": False,
                "detail": f"the platform is visible to the verification pass "
                          f"({vresult}); the verifier would run with the driver on disk"}
    if vresult.get("artifacts_writable") != "OSError":
        return {"ok": False,
                "detail": f"the verification pass can edit the harness's artifacts "
                          f"(artifacts_writable={vresult.get('artifacts_writable')!r}); "
                          f"a check the subject can rewrite is not a check"}
    if vresult.get("artifacts_readable") != "the harness's work":
        return {"ok": False,
                "detail": f"the verification pass cannot read the artifacts "
                          f"(saw {vresult.get('artifacts_readable')!r})"}

    return {"ok": True,
            "detail": f"bwrap active; {len(hidden)} trees hidden; source read-only; "
                      f"{STATE_DIRNAME}/ writable; artifacts frozen for verification; "
                      f"plan v{PLAN_VERSION}; {bwrap}"}
