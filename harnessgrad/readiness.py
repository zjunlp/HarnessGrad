"""Is this machine ready for the run described on the command line?

**The defect this module exists for: a new user could only find out by paying.** Every
prerequisite below -- an unprovisioned dataset, a `bwrap` that cannot build a namespace,
a harness whose `harness.json` names an entrypoint that is not on disk -- used to be
discovered *inside* the run, after round 0 had already spent model calls. `driver.py
--check-only` asks this module the same question the run will ask, from the same parsed
argv, and prints the answer before anything is created, staged or called.

Three rules the checks keep, each learned from the checks that came before them:

* **A check reports a fact; it does not act.** It runs no model, starts no harness and
  writes nothing. The only subprocesses are `git --version` and the sandbox's own
  namespace probe, which is `true` under `bwrap` and nothing else, so `--check-only` is
  safe on a machine where nothing has been configured yet -- which is exactly when it is
  worth running.
* **`required` is a property of the selected run, not of the check.** `bwrap` is
  required by every run that does not pass `--no-sandbox` and optional for one that does.
  Disk headroom is never a reason to refuse a run (a small task set fits in little space)
  but it is exactly the reason a run dies at task 37, so it warns instead.
* **A check never raises.** `plan_checks` turns an unexpected exception into a failed row
  naming the exception: a preflight that tracebacks tells the user nothing about what to
  fix, and this module is read by people whose platform is not working yet.

`render()` is a fixed-width table on purpose: the point of the feature is that one screen
answers whether to start. `all_required_ok()` is the driver's exit code, so the same table
is usable from a script -- `driver.py --check-only && driver.py ...`.

The console has its own launch-time preflight (`console/server.py:guard_launch`), and a
copy of that logic is not imported here: `data/`, `eval/`, `harnessgrad/` and `tools/`
never import `console/`, and the check that matters *before a run* belongs where the run
is launched, not in one front end's form validation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import data.scaffolds.registry as datasets
from harnessgrad import PLATFORM_ROOT
from harnessgrad.config import current

#: The interpreter floor the platform declares (README, `pyproject`-free by design).
MIN_PYTHON = (3, 10)
#: A task set materialises working trees and, for some datasets, a runtime venv; under
#: this much free space a run is more likely to die at task N than to finish. Optional
#: because "how much is enough" is a property of the dataset, not of the platform.
FREE_DISK_BYTES = 5 * 1024 ** 3
#: A namespace probe that has not answered in five seconds is a broken bwrap, not a slow
#: one: the real probe inside `sandbox.self_check` uses the same order of magnitude.
PROBE_TIMEOUT = 5
#: The endpoint check must not make `--check-only` feel like a hang. `--check-net` is
#: opt-in for that reason: an unreachable endpoint is a fact worth having, but the probe
#: leaves the machine, so it never runs unless asked for.
ENDPOINT_TIMEOUT = 3
#: One line per check, whatever the probe found. A detail that wraps destroys the columns
#: that let a reader scan the table, and a stack trace pasted into a cell is worse.
DETAIL_WIDTH = 96


@dataclass(frozen=True)
class Check:
    """One prerequisite, as a fact rather than as a plan.

    `required` is part of the row because it is not a constant of the check: the selected
    run decides. `ok` for a skipped optional check is `True` -- nothing failed, there was
    simply nothing to do (`bwrap` under `--no-sandbox`) -- so a reader can tell "not
    needed" from "not working" by the detail.
    """

    name: str
    required: bool
    ok: bool
    detail: str


def plan_checks(args) -> list[Check]:
    """Every prerequisite of the run `args` describes, one row each, in reading order.

    The order is the order a run meets them: the interpreter, the tool the round lineage
    is committed with, the sandbox boundary, the data, the harness that runs inside that
    boundary, the two writable roots, then disk -- which is long-lived rather than fatal.
    `endpoint` is appended last and only on request because it is the one check that leaves
    the machine (`--check-net`).

    `args` is an argparse namespace from the same parser a real run uses, so the answer
    is about *that* run: `--dataset`, `--harness`/`--harness-path`, `--runs-root`,
    `--work-root` and `--no-sandbox` all change what is checked, not just what is
    printed. A caller that has already resolved the harness directory (driver.py) puts it
    on `args.harness_dir`; see `_harness_dir`.
    """
    guards = [
        ("python", True, _python_check),
        ("git", True, _git_check),
        ("bwrap", bool(getattr(args, "sandbox", True)), _bwrap_check),
        ("dataset", True, _dataset_check),
        ("harness", True, _harness_check),
        ("writable", True, _writable_check),
        ("disk", False, _disk_check),
    ]
    checks: list[Check] = []
    for name, required, probe in guards:
        try:
            checks.append(probe(args))
        except Exception as exc:                              # noqa: BLE001 - see module doc
            checks.append(Check(name, required, False,
                                f"probe raised {type(exc).__name__}: {exc}"))
    if getattr(args, "check_net", False):
        try:
            checks.append(_endpoint_check())
        except Exception as exc:                              # noqa: BLE001
            checks.append(Check("endpoint", False, False,
                                f"probe raised {type(exc).__name__}: {exc}"))
    return checks


def render(checks) -> str:
    """The table a human reads: a header and one aligned line per check.

    Every column but the detail is sized to its content, and the detail is collapsed to
    one line and cut at `DETAIL_WIDTH`, so the shape of the output does not depend on
    what the probes found. A probe that puts a kernel error or a task list in `detail`
    must not be able to make the table unreadable.
    """
    rows = [("check", "req", "result", "detail")]
    for check in checks:
        rows.append((check.name, "yes" if check.required else "no",
                     "ok" if check.ok else "FAIL", _one_line(check.detail)))
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    return "\n".join(
        f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}"
        for row in rows)


def all_required_ok(checks) -> bool:
    """True when nothing a run depends on failed. A failing optional row is a warning."""
    return all(check.ok for check in checks if check.required)


# ---------------------------------------------------------------- the checks ---

def _fixture_kind(name: str) -> bool:
    """Is `name` the platform's own scaffolding rather than a published dataset?

    Asked through the registry so this module still never imports an adapter itself. A
    module that cannot be imported is not a fixture: it is a name nothing answers to, and
    the caller reports it as one.
    """
    try:
        return datasets.kind_of(name) == "fixture"
    except Exception:                                         # noqa: BLE001
        return False


def _python_check(_args=None) -> Check:
    """The floor, checked against the interpreter that would actually run the platform.

    Not `python3 --version` from PATH: in a sandboxed or venv deployment the driver can
    run under an interpreter PATH does not name, and the version that matters is the one
    already executing.
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    detail = f"python {version}"
    if not ok:
        detail += (f" — the platform needs >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
    return Check("python", True, ok, detail)


def _git_check(_args=None) -> Check:
    """Round lineage is committed with git (`eval/ckpt/git_state.py`), so it is required.

    A run without it stages no checkpoints: the method's edits exist only in a directory
    the next round overwrites, and an eval side finds no `state_after_round<N>` to
    measure. That is discovered today at the end of round 0.
    """
    ok, text = _run(["git", "--version"])
    if not ok:
        return Check("git", True, False,
                     f"git --version failed: {text or 'no output'}")
    return Check("git", True, True, text)


def _bwrap_check(args) -> Check:
    """The binary exists **and** a real namespace builds -- the second half is the point.

    `shutil.which("bwrap")` proves nothing: a container host with user namespaces disabled
    has the binary and fails every mount. So the probe is the invocation the platform's
    own plan is built from (`eval/containers/sandbox.py:command`): the whole host bound
    read-only, `/dev` and `/proc` fresh, one `true` to run. `sandbox._bwrap()` supplies
    the path so `HG_BWRAP` is honoured -- the override exists so the installer and the
    platform check the same binary.

    Not `sandbox.self_check()` here even though it is the stronger probe: it needs a work
    root outside the platform and writes two throwaway trees, and `--check-only` must be
    safe to run before those roots exist. It remains the door check a real run performs,
    so a green row here is a necessary, not a sufficient, condition.
    """
    required = bool(getattr(args, "sandbox", True))
    if not required:
        return Check("bwrap", False, True, "not needed: --no-sandbox")
    try:
        from eval.containers.sandbox import SandboxUnavailable, _bwrap
    except Exception as exc:                                  # noqa: BLE001
        return Check("bwrap", True, False, f"cannot load the sandbox module: {exc}")
    try:
        exe = _bwrap()
    except SandboxUnavailable as exc:
        return Check("bwrap", True, False, str(exc))
    argv = [exe, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc", "--", "true"]
    ok, text = _run(argv, timeout=PROBE_TIMEOUT)
    if not ok:
        return Check("bwrap", True, False, f"{exe}: a namespace did not build: {text}")
    version = _run([exe, "--version"])[1] or "version unknown"
    return Check("bwrap", True, True, f"{exe}: namespace builds ({version})")


def _dataset_check(args) -> Check:
    """Published, provisioned, and loadable -- three separate ways to fail, named apart.

    They are checked in that order because the fix differs: a name that is not published
    is a typo or a fixture (`data.demo`), a published adapter whose bytes are absent is a
    `tools/datasets.py --fetch`, and a `load()` that raises is the adapter's own refusal
    (a blocker it declares, a judge it cannot configure). Collapsing them into one
    "dataset unavailable" would send the user to the wrong fix.

    `provision_status()` is consulted only when the adapter has one: a module without it
    is assumed provisioned, which is the documented rule (`tools/dataset_lint.py`) and
    what `load()` decides anyway.
    """
    name = getattr(args, "dataset", None)
    if not name:
        return Check("dataset", True, False, "no --dataset selected")
    published = datasets.published()
    if name not in published:
        # A fixture is not a typo. `demo` is the dataset every zero-cost smoke in
        # `README.md` step 2 and 21 test modules use, and the driver runs it; refusing it
        # here made the preflight disagree with the run it is supposed to certify
        # (measured 2026-11-02: `driver.py --dataset demo ... --check-only` printed
        # "'demo' is not a published dataset" for a run that then scored 0.000 on 8 tasks).
        # The distinction that matters is "the driver will run this" versus "nothing does".
        if _fixture_kind(name):
            try:
                tasks, _scorable = datasets.load(name)
            except Exception as exc:                          # noqa: BLE001
                return Check("dataset", True, False,
                             f"{name}: fixture, load() raised {type(exc).__name__}: {exc}")
            if not tasks:
                return Check("dataset", True, False, f"{name}: fixture, load() returned no tasks")
            return Check("dataset", True, True,
                         f"{name}: {len(tasks)} tasks (test fixture, not a published dataset)")
        return Check("dataset", True, False,
                     f"{name!r} is not a published dataset; "
                     f"`python3 tools/datasets.py --list` prints the {len(published)} "
                     f"that are")
    # Through the registry, never by importing the adapter here: `harnessgrad/` reaches a
    # dataset only through `data/scaffolds/registry.py` (the import-boundary rule this
    # table exists to serve). `provision_status` is `None` when the adapter makes no claim,
    # which is different from a claim that the bytes are absent.
    try:
        answer = datasets.provision_status(name)
    except Exception as exc:                                  # noqa: BLE001
        return Check("dataset", True, False,
                     f"{name}: the adapter's provision_status() raised "
                     f"{type(exc).__name__}: {exc}")
    if isinstance(answer, dict) and not answer.get("present"):
        where = answer.get("split_dir") or answer.get("root") or "its data directory"
        return Check("dataset", True, False,
                     f"{name}: not provisioned — no data at {where}")
    try:
        tasks, _scorable = datasets.load(name)
    except Exception as exc:                                  # noqa: BLE001
        return Check("dataset", True, False,
                     f"{name}: load() raised {type(exc).__name__}: {exc}")
    if not tasks:
        return Check("dataset", True, False, f"{name}: load() returned no tasks")
    return Check("dataset", True, True, f"{name}: {len(tasks)} tasks")


def _harness_check(args) -> Check:
    """The tree that runs, and the four facts about it the rest of the platform assumes.

    `harness.json` is the contract (`tools/validate_harness.py` holds the full schema).
    The four checked here are the ones a run cannot proceed without: `entrypoint` names
    the file the sandbox executes, `backend` decides how the model is reached, and
    `env_kinds` is what the driver compares against the dataset's environments before it
    stages anything. The deeper checks (naming, declared files, path safety) stay in the
    validator a run still runs; duplicating them here would be a second schema.
    """
    path, problem = _harness_dir(args)
    if problem:
        return Check("harness", True, False, problem)
    manifest_path = path / "harness.json"
    if not manifest_path.is_file():
        return Check("harness", True, False, f"{path}: no harness.json")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return Check("harness", True, False, f"{manifest_path}: {exc}")
    if not isinstance(manifest, dict):
        return Check("harness", True, False, f"{manifest_path}: not a JSON object")
    missing = [key for key in ("entrypoint", "backend", "env_kinds")
               if not manifest.get(key)]
    if missing:
        return Check("harness", True, False,
                     f"{manifest_path}: missing {', '.join(missing)}")
    entrypoint = path / str(manifest["entrypoint"])
    if not entrypoint.is_file():
        return Check("harness", True, False,
                     f"{manifest_path}: entrypoint {manifest['entrypoint']!r} "
                     f"is not a file")
    kinds = manifest["env_kinds"]
    shown = ", ".join(str(kind) for kind in kinds) if isinstance(kinds, list) else str(kinds)
    return Check("harness", True, True,
                 f"{path} ({manifest['backend']}, entrypoint {manifest['entrypoint']}, "
                 f"env_kinds {shown})")


def _writable_check(args) -> Check:
    """Both roots must be usable, and the work root must be outside the platform.

    The second half is not a nicety: the workspace is where the harness runs, and a work
    root inside the platform is a harness that can read `driver.py` and `.env` and write
    the scorer (`driver.py` refuses it at the door, `workspace_is_outside_platform`).
    Reporting it here means `--check-only` cannot say "ready" for a run the driver will
    refuse one line later.

    Writability is probed without creating anything -- the nearest existing ancestor is
    the one that has to be writable -- because `--check-only` must not leave a directory
    behind.
    """
    runs_root = Path(getattr(args, "runs_root", "runs"))
    work_root = Path(getattr(args, "work_root", "../harnessgrad_work"))
    # The refusal a run meets first goes first: the detail is cut at `DETAIL_WIDTH`, and a
    # truncated tail must never hide the one line that says what to fix.
    details: list[str] = []
    failed: list[str] = []
    resolved = work_root.expanduser().resolve()
    platform = PLATFORM_ROOT.resolve()
    if resolved == platform or platform in resolved.parents:
        details.append(f"work root {resolved} is inside the platform {platform}, so a "
                       f"harness there could read the driver and .env")
        failed.append("work root")
    for label, path in (("runs root", runs_root), ("work root", work_root)):
        ok, detail = _can_write(path)
        details.append(f"{label}: {detail}")
        if not ok and label not in failed:
            failed.append(label)
    return Check("writable", True, not failed, "; ".join(details))


def _disk_check(args) -> Check:
    """Free space on the device the run record lands on, as a warning rather than a stop.

    The runs root's own device, not `/`: a run record on an NFS mount or a dedicated
    volume has a capacity `/` says nothing about. The threshold is a warning because the
    honest answer depends on the dataset -- an AgentDojo task materialises a 217 MB
    runtime venv, `gsm8k` materialises nothing -- and refusing a run that would fit is
    worse than starting one that may not.
    """
    target = Path(getattr(args, "runs_root", "runs")).expanduser()
    probe, problem = _existing_ancestor(target)
    if problem:
        return Check("disk", False, False, problem)
    try:
        free = shutil.disk_usage(probe).free
    except OSError as exc:
        return Check("disk", False, False, f"{probe}: {exc}")
    return Check("disk", False, free >= FREE_DISK_BYTES,
                 f"{probe}: {free / 2 ** 30:.1f} GB free "
                 f"(a run needs about {FREE_DISK_BYTES / 2 ** 30:.0f} GB)")


def _endpoint_check() -> Check:
    """`GET {HG_AGENT_BASE_URL}/models` in three seconds, and never a credential by value.

    `--check-net` is opt-in because this is the one probe that leaves the machine: a
    `--check-only` on an air-gapped host must still answer the local questions. The key is
    sent (an endpoint that requires it would answer 401 otherwise) and scrubbed from
    every string that reaches the table, because `run_meta.json` and this table are both
    written to disk and read by people.
    """
    agent = current().agent
    base = (agent.base_url or "").strip().rstrip("/")
    if not base:
        return Check("endpoint", False, False,
                     "HG_AGENT_BASE_URL is unset, so there is no endpoint to probe")
    url = f"{base}/models"
    key = agent.api_key or ""
    request = urllib.request.Request(url, method="GET",
                                     headers={"Accept": "application/json"})
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=ENDPOINT_TIMEOUT) as response:
            body = response.read(1 << 20)
            status = getattr(response, "status", 0)
    except Exception as exc:                                  # noqa: BLE001
        return Check("endpoint", False, False,
                     _without(f"GET {url}: {type(exc).__name__}: {exc}", key))
    elapsed = time.monotonic() - started
    listed = ""
    try:
        payload = json.loads(body or b"{}")
        count = len(payload.get("data") or [])
        listed = f", {count} models listed"
    except (ValueError, AttributeError):
        pass
    return Check("endpoint", False, True,
                 _without(f"GET {url} -> {status} in {elapsed:.2f}s{listed}", key))


# ------------------------------------------------------------------ helpers ---

def _run(argv: list[str], *, timeout: int = PROBE_TIMEOUT) -> tuple[bool, str]:
    """(ok, last line of output) for one short probe. Never raises.

    `stdin=DEVNULL` because a probe that reads a terminal would hang `--check-only` in a
    CI job, and `errors="replace"` because a locale-mangled message is still a message.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{' '.join(argv)}: {exc}"
    lines = [line for line in (proc.stdout or proc.stderr or "").splitlines()
             if line.strip()]
    last = lines[-1].strip() if lines else ""
    return proc.returncode == 0, last


def _one_line(detail: str) -> str:
    """Collapse whitespace and cut at a fixed width, so the table keeps its shape."""
    text = " ".join((detail or "").split())
    if len(text) <= DETAIL_WIDTH:
        return text
    return text[:DETAIL_WIDTH - 3].rstrip() + "..."


def _without(text: str, secret: str) -> str:
    """A credential never reaches a rendered row, even if an exception quotes it."""
    return text.replace(secret, "***") if secret else text


def _existing_ancestor(path: Path) -> tuple[Path, str]:
    """The nearest path that exists, or a problem naming the one that never will."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return probe, f"{path} has no existing ancestor to create it under"
        probe = probe.parent
    return probe, ""


def _can_write(path: Path) -> tuple[bool, str]:
    """Can this directory be created/used, judged without creating anything.

    `os.access()` on the nearest existing ancestor is the honest test available without a
    write: it follows the same permission bits the `mkdir` would, and it leaves the tree
    exactly as it was found.
    """
    probe, problem = _existing_ancestor(path)
    if problem:
        return False, problem
    if not probe.is_dir():
        return False, f"{probe} exists and is not a directory"
    if not os.access(probe, os.W_OK | os.X_OK):
        return False, f"{probe} is not writable by this user"
    if probe == path:
        return True, f"{path} exists and is writable"
    return True, f"{path} can be created"


def _harness_roots() -> tuple[Path, ...]:
    """Where a harness may live, in the order the driver looks.

    The same tuple as `driver.py:_harness_roots`. It is repeated here rather than
    imported because `driver.py` is the entrypoint and importing it from the layer below
    would invert the dependency; `plan_checks` prefers `args.harness_dir`, which the
    driver fills with the directory it resolved, so the repeat is only the fallback a
    direct caller (a test, a tool) gets.
    """
    return (PLATFORM_ROOT / "base_harness",
            PLATFORM_ROOT / "tests" / "fixtures" / "base_harness")


def _harness_dir(args) -> tuple[Path, str]:
    """(directory, "") or (placeholder, problem) for the harness the run would stage.

    `harness_dir` set to `None` by the caller means "this run resolves to no harness" --
    an eval run whose `--from-run` record is not there, say -- and must not fall back to
    a name search: that would report a *different* harness as ready.
    """
    name = getattr(args, "harness", None) or "loop"
    explicit = (getattr(args, "harness_path", None)
                or getattr(args, "harness_dir", None))
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            return path, f"{path}: no such directory"
        return path, ""
    if hasattr(args, "harness_dir"):
        problem = getattr(args, "harness_problem", "") or (
            f"{name!r}: no harness resolved (searched "
            + ", ".join(str(base) for base in _harness_roots()) + ")")
        return PLATFORM_ROOT, problem
    for base in _harness_roots():
        if (base / name).is_dir():
            return base / name, ""
    searched = ", ".join(str(base) for base in _harness_roots())
    return PLATFORM_ROOT, f"{name!r}: no such harness (searched {searched})"


__all__ = ["Check", "plan_checks", "render", "all_required_ok"]
