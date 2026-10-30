"""Calling an external method, and keeping it out of the platform.

A method is code the platform did not write. It runs in the same sandbox a harness does,
with one difference: the platform is hidden except `methods/` and the single public
`ckpt/bun_dependencies.py` helper. No scorer or private task data is exposed.

Two checks live here and they are different in kind. `_check_method_entrypoint` refuses an
unusable entrypoint at the door, before anything has been paid for. `_call_method` hashes
the platform around every call, because detection is what is actually available when the
method runs as the same user -- so the guarantee is "a run whose method changed the referee
is not a measurement", made loudly instead of silently.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

from eval.run.integrity import diff
from eval.run.integrity import snapshot
from harnessgrad import PLATFORM_ROOT
import eval.containers.sandbox as sandbox

def _check_method_entrypoint(argv: list[str]) -> str | None:
    """Reject relative script paths that fail from the harness worktree.

    Methods run with `cwd=work`, so `python methods/x/run.py` resolves to
    `<work>/methods/x/run.py`. A missing script exits with code 2 and looks like
    a missing trajectory, leaving the curve at round 0 without a useful cause.

    This configuration error once escaped 53 passing tests. Reject it with an
    actionable message instead of guessing or silently rewriting the command.

    Return an error message, or None when the command is valid.
    """
    for part in argv:
        # Inspect script-like arguments anywhere: interpreter, -m, or wrapper.
        if not part.endswith(".py"):
            continue
        if Path(part).is_absolute():
            continue
        resolved = Path(part)
        hint = ""
        if resolved.exists():
            # A path present in the current directory may be absent in the worktree.
            hint = f"(it exists relative to the current directory, but the method runs elsewhere: {resolved.resolve()})"
        return (f"method entrypoint names a relative script path: {part!r} {hint}\n"
                f"  The method runs with cwd set to the harness working tree, so a\n"
                f"  relative path resolves there and does not exist. Use an absolute\n"
                f"  path: python {PLATFORM_ROOT}/methods/<name>/run.py")
    return None

def _method_sandbox_plan(work: Path, argv: list[str], scratch: Path,
                         readonly: tuple[Path, ...] = (),
                         runtime: tuple[str, ...] = ()) -> dict:
    """What a method is allowed to see, expressed for `eval/containers/sandbox.py`.

    A method used to run as an ordinary subprocess with the whole platform on disk.
    Measured, with a probe placed where a real method lives (`methods/<name>/run.py`):
    it could read `data/probe_set.py` -- **the expected answer for every task,
    including the eval side this platform goes to some trouble to withhold** -- along
    with `.env` and `eval/run/runner.py`, and it held both API keys. The only check was a
    hash either side of the call, which sees writes and not reads.

    So a method is sandboxed like the harness, with one difference: the platform is
    hidden **except** the directory its own program lives in. It cannot be given less
    than that and still run, and it must not be given more.

    `runtime` is the second thing it cannot be given less of, and it was missing for
    exactly as long as the improver layer has existed. A method whose contribution is an
    external tool -- `methods/cli_improver` runs whatever the platform resolved -- needs that
    tool's trees on its disk. Measured with `--improver codex`: round 1 died three times
    with `exit 1`, once because the method's own resolver lives under the hidden
    platform, and again with `FileNotFoundError: …/codex.js` because the namespace binds
    nothing under `$HOME`. The improver layer was usable only with `--no-sandbox`, so
    nobody had used it. The trees come from `tools/improver.py` (`runtime_trees`), which
    is where the improver was resolved and the only place that knows what it needs.
    """
    # The stdlib-only link helper is shared with trusted preparation; exposing
    # one file avoids duplicating runtime logic or revealing the ckpt directory.
    visible = [PLATFORM_ROOT / "methods", PLATFORM_ROOT / "eval/ckpt/bun_dependencies.py", *readonly]
    # Its own script, wherever it is -- a test fixture's method lives outside the
    # platform, and a method that cannot be read cannot start.
    for arg in argv:
        candidate = Path(arg)
        if candidate.is_absolute() and candidate.is_file():
            visible.append(candidate.parent)
    # **The improver's trees**, bound read-only like the method's own program. Two
    # guards: never a tree the platform lives in (that would undo the hide), and never a
    # tree the namespace already binds (`/usr`, the Python prefix), because a duplicate
    # mount is noise in a plan that is read by people.
    tools = []
    for entry in runtime:
        tree = Path(entry)
        if not tree.is_dir() or tree == Path("/"):
            continue
        if tree == PLATFORM_ROOT or PLATFORM_ROOT in tree.parents:
            continue
        if any(tree == Path(r) or Path(r) in tree.parents for r in sandbox.RUNTIME):
            continue
        tools.append(tree)
    visible.extend(tools)
    return {
        "platform": PLATFORM_ROOT,
        "runtime_paths": tuple(tools),
        "work_root": Path(os.environ.get("HARNESSGRAD_WORK_ROOT")
                          or work.parent.parent),
        # The whole method directory is writable, because the trajectory lands beside
        # the workspace rather than inside it. `readonly` is re-bound *after* this, and
        # a later mount wins -- which is how `base_harness` stays untouchable while its
        # sibling is the method's scratch.
        "task_dir": scratch,
        # **Not optional.** `eval/containers/sandbox.py` reads `repo=None` as "the repo is the
        # platform" and binds it read-only -- so leaving this out handed the platform
        # straight back. Measured: with `repo` omitted, a probe method still read
        # `data/probe_set.py` and `.env`, and the sandbox looked like it was working.
        #
        # **A known footgun, measured, not fixed: a method script that lives in `/tmp`
        # cannot write its candidate.** The scratch is `/tmp/hg-method-<run>-<rand>/`, and
        # a script named by an absolute path has its own directory bound read-only -- so
        # when that directory is `/tmp`, the read-only bind lands on top of the writable
        # scratch (later mounts win) and the method dies with `OSError: [Errno 30]
        # Read-only file system: .../round1_ws/next`. Real methods live in the repository
        # or in the author's project, so this only bit a demo script; the fix, when someone
        # has one, is to bind the scratch *last* rather than to stop binding the ancestor.
        # The first element of `readonly` is the method's own read-only input tree.
        "repo": (readonly[0] if readonly else Path(argv[0]).resolve().parent),
        "visible_paths": tuple(dict.fromkeys(visible)),
    }

def _method_env(work: Path, improver: dict | None = None) -> dict:
    """The environment a method runs in: its own model, and not the agent's.

    `harness_env()` does the mirror of this for the harness. The symmetry matters
    because the two budgets are separate by design (`.env.example`), and separate
    configuration is not separation until something enforces it: a method that can
    read `HG_AGENT_API_KEY` can spend the agent's quota, and -- worse for this
    platform -- can answer the eval tasks itself.

    When the run resolved an improver that *is* an endpoint (`--improver deepseek`),
    the endpoint and the model are written in here. A method reaches a chat model through
    `HG_METHOD_BASE_URL` / `HG_METHOD_MODEL` (`methods/editor.py`), so a named improver
    that only lived in the registry would leave the record saying `deepseek` while the
    method called whatever `.env` happened to hold. That is the same failure as a harness
    misreporting its model, and it is fixed the same way: the platform states the fact
    where the consumer reads it.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("HG_AGENT_")}
    endpoint = (improver or {}).get("base_url")
    if endpoint:
        env["HG_METHOD_BASE_URL"] = str(endpoint)
        if (improver or {}).get("model"):
            env["HG_METHOD_MODEL"] = str(improver["model"])
        # The secret is copied from the variable the registry named, never from the
        # record: the record is written to disk and read by people.
        key_env = (improver or {}).get("api_key_env") or ""
        if key_env and os.environ.get(key_env):
            env["HG_METHOD_API_KEY"] = os.environ[key_env]
    # A CLI improver that builds its own home gets the path from the resolution rather than
    # from an ambient `DSH_HOME`. That ambient variable is the coupling this closes: it is
    # exported by the tool environment, inherited by the driver and by every method, and it
    # decided which settings file the improver read -- measured, the operator's, complete
    # with `llm-pi-ai` providers nobody chose for it.
    if (improver or {}).get("home"):
        env["HG_IMPROVER_DSH_HOME"] = str(improver["home"])
    env["HARNESSGRAD_WORKSPACE"] = str(work.resolve())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{work.resolve()}{os.pathsep}{existing}"
                         if existing else str(work.resolve()))
    return env

def _call_entrypoint(work: Path, argv: list[str], request: dict,
                     timeout_s: int = 3600, *, sandboxed: bool = True,
                     scratch: Path | None = None, cwd: Path | None = None,
                     readonly: tuple[Path, ...] = (),
                     runtime: tuple[str, ...] = (),
                     improver: dict | None = None,
                     rejected_candidate: bool = False) -> dict:
    """Invoke a method entrypoint as a SUBPROCESS and parse its one JSON object.

    Never an import. A method that rewrites its own source must still be
    reachable on the next round, and an imported module is frozen at load time --
    importing would make genuine self-modification unrepresentable.

    A failure here is recorded, not raised: a method that breaks its own
    interface is an observation about that method, and losing a paid run to hide
    it would be the wrong trade.
    """
    env = _method_env(work, improver)
    where = cwd or scratch or work
    try:
        # A method copies source without installations. Supply the prepared,
        # read-only dependency store so its candidate can self-test before editing.
        # This is task-harness setup, independent of the improver's configuration.
        from eval.containers.bun_runtime import prepare, RuntimeUnavailable
        base = request.get("base_harness")
        # A rejected tree is input for repair, not an admitted runtime. Let the
        # external method inspect it without preparing or executing that harness.
        prepared = prepare(Path(base)) if base and not rejected_candidate else None
        if prepared:
            request = {**request, "harness_runtime": prepared}
            runtime = tuple(dict.fromkeys((*runtime, *prepared["runtime_paths"], sys.prefix)))
        payload = json.dumps(request)
        if sandboxed:
            plan = _method_sandbox_plan(work, argv, scratch or where, readonly,
                                        runtime)
            proc = sandbox.run(
                argv, input_text=payload, timeout_s=timeout_s, cwd=where, env=env,
                # The method is code the platform did not write either, and it runs once
                # per round with a whole CLI behind it: the same envelope as the harness,
                # for the same reason (`eval/containers/sandbox.py`).
                limits=sandbox.configured_limits(),
                **plan)
        else:
            proc = subprocess.run(
                argv, cwd=where, input=payload, capture_output=True,
                text=True, errors="replace", timeout=timeout_s, env=env,
            )
    except subprocess.TimeoutExpired:
        return {"_error": f"entrypoint timed out after {timeout_s}s"}
    except RuntimeUnavailable as exc:
        return {"_error": f"method candidate runtime unavailable: {exc}"}
    except OSError as exc:
        return {"_error": f"entrypoint could not start: {exc}"}
    except sandbox.SandboxUnavailable as exc:
        return {"_error": f"refusing to run the method: sandbox unavailable: {exc}"}

    if proc.returncode != 0:
        return {"_error": f"exit {proc.returncode}",
                "_stderr": proc.stderr[-1500:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"_error": f"non-JSON stdout: {exc}",
                "_stdout": proc.stdout[:500], "_stderr": proc.stderr[-500:]}

def _call_method(work: Path, argv: list[str], request: dict, timeout_s: int,
                 *, sandboxed: bool = True, scratch: Path | None = None,
                 cwd: Path | None = None, readonly: tuple[Path, ...] = (),
                 runtime: tuple[str, ...] = (),
                 improver: dict | None = None,
                 rejected_candidate: bool = False) -> dict:
    """Invoke a method with the platform's own files checked before and after.

    Detection, not prevention: a method runs as an ordinary subprocess with the
    platform on disk, and nothing here stops it from appending to `eval/run/runner.py`.
    What this does is refuse a content mismatch. The snapshots do not identify
    its writer: concurrent user edits and untracked artifacts can also cause it.
    """
    before = snapshot(PLATFORM_ROOT)
    reply = _call_entrypoint(work, argv, request, timeout_s=timeout_s,
                             sandboxed=sandboxed, scratch=scratch, cwd=cwd,
                             readonly=readonly, runtime=runtime, improver=improver,
                             rejected_candidate=rejected_candidate)
    changes = diff(before, snapshot(PLATFORM_ROOT))
    touched = changes["added"] + changes["removed"] + changes["modified"]
    if touched:
        shown = ", ".join(sorted(set(touched))[:8])
        print(f"\nFAILING RUN: protected files changed during method call: {shown}\n"
              f"  Content snapshots do not identify the writer or source tracking.\n"
              f"  Run integrity is unverified; inspect the changed paths and their\n"
              f"  provenance before a new run.", file=sys.stderr)
        raise PlatformTampered(changes)
    return reply

def _touched_paths(work: Path, before: str, after: str) -> list[str]:
    """Which files a step actually moved.

    This is how the platform observes the editable surface without asking the
    method to declare anything: `method/accept.py` in this list means the method
    rewrote its own acceptance rule, which is the line most systems say they
    cannot cross. The platform never writes these files itself.
    """
    if before == after:
        return []
    out = subprocess.run(
        ["git", "-C", str(work), "diff", "--name-only", before, after],
        capture_output=True, text=True, errors="replace",
    ).stdout
    return sorted(p for p in out.splitlines() if p.strip())

class PlatformTampered(RuntimeError):
    """Protected-file mismatch; legacy name, unknown writer, run must stop."""
