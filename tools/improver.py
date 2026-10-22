#!/usr/bin/env python3
"""Find the improver, prove it works, and say which one it is.

An *improver* is the agent that composes the edit sequence a method applies to a
harness. Nothing in the platform's contract requires it to be a `codex`; the contract
is "an external command that takes a prompt and works in a directory". Codex is the
first one we ship because the measured bottleneck was exactly its job -- the 9B model
that methods used to carry could not make an architectural change to a harness, and on
`loop-terminal_bench-41133` it could not even notice that the harness had died at step 0.

Three things this file exists to get right, in order of how much they cost to get wrong:

**1. There can be several codex binaries on one machine, and only some work.** Measured
on the box this was written on:

    /usr/local/bin/codex                0.125.0   BROKEN (no native binary)
    ~/.nvm/.../node/v22.16.0/.../codex  0.156.1   works
    /opt/node-v22.22.1/.../codex        0.139.0   works

`codex --version` on the broken one does not print a version, it crashes. So the
resolver does not trust `PATH`: it enumerates candidates and *verifies each one by
running it*, and reports what it found including what it rejected. A run that silently
used a different codex than the operator expected is a run whose identity is wrong.

**2. `latest` and reproducibility pull against each other, so record which one you got.**
"Every experiment should name its improver" and "people should be able to use a current
codex" cannot both be satisfied by a version string. The resolution is that **whatever
we resolve is reported as an immutable fact** -- `codex-cli 0.156.1` plus the sha256 of
the binary -- and that fact is what goes on the curve point. `latest` then means "do not
pin", not "do not record". `doctor --json` is the machine-readable half of that.

**3. A modified codex is out of scope to govern and in scope to name.** If somebody
patched their codex, we cannot obtain their binary and we must not pretend to. What the
platform can do is refuse to let such a thing be anonymous: an improver whose provenance
is not a published release must be declared with a path *and* a note saying it was
modified, and then the curve says so. That is the same stance the platform takes on
harnesses -- it cannot inspect what a harness gets right, but it can say which bytes ran.

Usage:

    python3 tools/improver.py --list              every candidate, verified, with reasons
    python3 tools/improver.py --doctor            resolve the configured one and prove it
    python3 tools/improver.py --doctor --json     the same, for a script
    python3 tools/improver.py --which             just the resolved path
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "improvers" / "improvers.json"

#: `harnessgrad.config` lives at the repository root, which is on `sys.path` when this
#: module is imported as `tools.improver` (the driver and the UI do that) but *not*
#: when it is run directly (`python3 tools/improver.py --list`). One insert makes the
#: declared configuration reachable in both cases; it is a no-op for the former.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from harnessgrad.config import current as current_config      # noqa: E402

#: Where a native codex binary may live if the npm wrapper is unusable. Ordered so the
#: common global install is tried before the version-manager trees; order only decides
#: what `--list` reports first, never what is chosen (that is the config's job).
#: (root, relative pattern) pairs. `Path.glob` does not expand `~`, so the home
#: directory is resolved once here rather than being smuggled through a pattern.
_SEARCH_GLOBS = (
    (Path("/"), "usr/local/lib/node_modules/@openai/codex/bin/codex.js"),
    (Path("/"), "opt/node-*/lib/node_modules/@openai/codex/bin/codex.js"),
    (Path.home(), ".nvm/versions/node/*/lib/node_modules/@openai/codex/bin/codex.js"),
)

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def declared_model(config: dict | None = None, source: Path | None = None,
                   entry: dict | None = None) -> str:
    """The model an improver will actually use, read from *its* config, not ours.

    A fact about the experiment that the platform cannot see any other way: codex reads
    its model from `~/.codex/config.toml`, so `HG_METHOD_MODEL` says nothing about what
    the improver will call. Recording it is the same discipline as recording the harness
    sha -- "which improver" is not answered by a name and a version.
    """
    config = config if config is not None else load_config()
    # **Which entry we are asking about.** Passing the *chosen* one is not a convenience:
    # without it this function answered for the config's `default`, so
    # `resolve(requested="codex")` reported `model: deepseek-flash` -- the endpoint
    # improver's model -- while codex was about to call whatever its own
    # `~/.codex/config.toml` names. A curve point that says the improver's model was
    # deepseek-flash when codex never touched deepseek is the same class of lie as
    # `agent_model: mock` on a run a live model answered. Measured (2026-10-04), and found
    # only because writing the unification plan needed to know *which* model has to emit
    # standard tool calls.
    if entry is None:
        entry = (config.get("improvers") or {}).get(config.get("default", "")) or {}
    if entry.get("model"):
        return str(entry["model"])
    home = Path(os.environ.get("CODEX_HOME") or (source or Path.home() / ".codex"))
    cfg = home / "config.toml"
    if not cfg.is_file():
        return ""
    for line in cfg.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("model") and "=" in stripped:
            key, _, value = stripped.partition("=")
            if key.strip() == "model":
                return value.strip().strip('"').strip("'")
    return ""


def forbidden_reason(config: dict | None, model: str) -> str:
    """Why this model may not be used, or `""`.

    A project rule kept as data (`improvers.json:_forbidden_models`) and enforced in the
    one place that resolves a model, so it cannot be forgotten in a launch script. The
    platform's own position is that a refusal with a reason beats a convention: a run that
    quietly used a different model than the team agreed on puts a number on a curve that
    cannot be compared with anything, and nothing in the record would say so.
    """
    config = config if config is not None else load_config()
    banned = config.get("_forbidden_models") or {}
    for name, reason in banned.items():
        if model == name or (model or "").startswith(f"{name}:"):
            return f"{model!r} is not allowed here: {reason}"
    return ""


def config_home() -> Path | None:
    """The improver's own state directory: its model, and its credentials.

    An improver is a tool with a config, and a method that cannot read the config cannot
    call a model. Measured with the CLI improver under the method sandbox: the method
    copies `config.toml` and `auth.json` out of here into the session's own `CODEX_HOME`,
    and inside its namespace there was nothing to copy -- so the session would have had
    no provider and no key, and the failure would have looked like a method bug.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return home if home.is_dir() else None


def _shebang_tool(script: Path) -> Path | None:
    """The program a launcher script runs, from its first line.

    An npm-installed CLI is a small JS file whose `#!` names an interpreter, and the
    interpreter is not under `/usr` on this machine -- it is a versioned tree under
    `$HOME`. The platform cannot guess that from the improver's name, and the script is
    where the answer is written down.
    """
    try:
        with script.open("rb") as fh:
            first = fh.readline(256).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    if not parts:
        return None
    if Path(parts[0]).name == "env" and len(parts) > 1:
        found = shutil.which(parts[1])
    else:
        found = parts[0] if Path(parts[0]).is_file() else shutil.which(parts[0])
    return Path(found) if found else None


def _interpreter_prefix(exe: Path) -> Path | None:
    """The installation a `bin/`-style executable belongs to.

    `…/.nvm/versions/node/v22.16.0/bin/node` -> `…/.nvm/versions/node/v22.16.0`. The
    sandbox already binds `/usr`, `/bin`, `/lib` and the running *Python* prefix; every
    other runtime a tool needs has to be named, and this is the first ancestor that is a
    self-contained install rather than a directory inside one.
    """
    try:
        exe = exe.resolve()
    except OSError:
        return None
    for parent in exe.parents:
        if parent == Path("/"):
            break
        if (parent / "bin").is_dir():
            return parent
    return None


def runtime_trees(path: Path) -> list[str]:
    """What a method must be able to read in order to *run* this improver.

    Resolution answers "which improver is this"; this answers "what does it need on
    disk". Both are the platform's to answer, because a method cannot: it runs in its own
    mount namespace with the platform hidden, so it can neither look the improver up nor
    guess where its runtime lives.

    Measured, before this existed: a run with `--improver codex` reached round 1 and died
    three times with `exit 1` and the stderr line `codex method: no usable improver` --
    the method's own resolver script lives under the hidden platform. With the path
    supplied by hand it then died with
    `FileNotFoundError: …/codex.js`, because the namespace binds `/usr`, `/bin`, the
    interpreter prefix, the work root and the method's own directory, and nothing under
    `$HOME`. Two different messages, one cause: the improver layer was usable only with
    `--no-sandbox`.

    Three trees, each because a real improver needs it:

      * **the package** -- an npm CLI is a wrapper that re-execs the native binary shipped
        beside it, so binding the launcher file alone is not enough;
      * **the interpreter** -- `#!/usr/bin/env node` is an install under `$HOME` here;
      * **the config home** -- `config.toml` and `auth.json`, without which the session
        has no model and no credentials.

    Bound read-only by the caller: a tool the method runs is not state it may write.
    """
    path = Path(path).expanduser()
    trees: list[Path] = []
    for parent in path.parents:
        if parent.name == "node_modules":
            trees.append(parent)
            break
    else:
        trees.append(path.parent)
    tool = _shebang_tool(path)
    if tool is not None and tool.name not in ("sh", "bash", "env"):
        prefix = _interpreter_prefix(tool)
        if prefix is not None:
            trees.append(prefix)
    home = config_home()
    if home is not None:
        trees.append(home)
    out: list[str] = []
    for tree in trees:
        if tree.is_dir() and str(tree) not in out:
            out.append(str(tree))
    return out


class Candidate:
    """One thing that might be the improver, and what running it actually did."""

    def __init__(self, path: Path):
        self.path = path
        self.ok = False
        self.version = ""
        self.reason = ""

    def as_dict(self) -> dict:
        return {"path": str(self.path), "ok": self.ok, "version": self.version,
                "reason": self.reason, "sha256": self.sha256() if self.ok else None}

    def sha256(self) -> str:
        """The bytes that ran. For the npm wrapper this is the wrapper; the native
        binary beside it is what `--version` talks to, so the wrapper's hash alone is
        not the whole identity -- which is why the version string is recorded too."""
        h = hashlib.sha256()
        try:
            with self.path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()


def _run_version(path: Path, timeout: int = 60) -> tuple[bool, str, str]:
    """Run `<path> --version`. Returns (ok, version, reason).

    This is the whole verification: a codex that cannot print its version cannot run a
    session, and the broken install's failure mode is exactly this command.
    """
    try:
        proc = subprocess.run([str(path), "--version"], capture_output=True, text=True, errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", f"`--version` timed out after {timeout}s"
    except OSError as exc:
        return False, "", f"could not execute: {exc}"
    if proc.returncode != 0:
        first = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "", first[0][:160] if first else f"exit {proc.returncode}"
    found = _VERSION_RE.search(proc.stdout)
    return True, found.group(1) if found else proc.stdout.strip()[:40], ""


def candidates(extra: str | None = None) -> list[Candidate]:
    """Every codex binary we can find, verified. `extra` is an explicit path first."""
    paths: list[Path] = []
    if extra:
        paths.append(Path(extra).expanduser())
    which = shutil.which("codex")
    if which:
        paths.append(Path(which))
    for root, pattern in _SEARCH_GLOBS:
        for match in sorted(root.glob(pattern)):
            if match.is_file():
                paths.append(match)
    # A native binary beside a working wrapper: `codex.js` re-execs it, and pointing at
    # it directly is what makes a broken wrapper usable.
    for wrapper in list(paths):
        vendor = wrapper.parent.parent / "node_modules" / "@openai" / "codex-linux-x64"
        for native in vendor.glob("vendor/*/bin/codex"):
            paths.append(native)

    out: list[Candidate] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        cand = Candidate(path)
        cand.ok, cand.version, cand.reason = _run_version(path)
        out.append(cand)
    return out


def _dotenv(path: Path) -> dict[str, str]:
    """Read minimal `KEY=value` entries; see the same function in `tools/serve_ui.py`."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def requested_note(entry: dict) -> str:
    """What the config *asked* for, for a non-binary improver."""
    return entry.get("version", "") or "configured endpoint"


def load_config(path: Path | None = None) -> dict:
    selected = path or current_config().improver.registry
    path = Path(selected).expanduser() if selected else DEFAULT_CONFIG
    if not path.exists():
        if selected:
            raise FileNotFoundError(f"configured improver registry does not exist: {path}")
        return {}
    return json.loads(path.read_text())


def resolve(config: dict | None = None, *, version: str = "", requested: str = "",
            model_override: str = "") -> dict:
    """Pick the improver to run. Returns {ok, path, version, sha256, reason, requested}.

    `version` is the operator's request: a version like `0.156.1`, the word `latest`, or
    empty for "whatever the config says". It never silently falls back -- a request that
    cannot be honoured is a refusal with the candidates listed, because "you asked for
    0.156.1 and got 0.125.0" is the kind of difference that changes a measurement.
    """
    config = config if config is not None else load_config()

    #: `requested` is what the operator asked for **by name**: a key in the config, or an
    #: absolute path. `version` is the finer request inside that entry. When neither is
    #: given, the config's `default` decides. A name that is not in the config is a
    #: refusal rather than a silent fallback -- "I asked for the local improver and got
    #: codex" is exactly the substitution that makes two experiments incomparable.
    key = config.get("default", "")
    if requested and not requested.startswith("/"):
        if requested in (config.get("improvers") or {}):
            key = requested
        else:
            return {"ok": False, "requested": requested, "candidates": [],
                    "reason": f"no improver named {requested!r} in the config "
                              f"(has: {', '.join(sorted(config.get('improvers') or {})) or 'none'})"}
    entry = (config.get("improvers") or {}).get(key) or {}

    #: A non-`cli` improver is not a binary. `local` is a model endpoint reached with the
    #: platform's own client (`methods/editor.py`), so resolving it by searching for codex
    #: would report a codex version under the name "local" -- which is exactly what the
    #: first version of `improver_options` did in the UI. What "resolved" means for it is
    #: that the endpoint is configured; whether the *model* is good enough is a question
    #: for the smoke test, not for a version string.
    if entry.get("kind") and entry.get("kind") != "cli":
        # Read `.env` too: the panel and driver are separate processes, and
        # `HG_METHOD_*` often exists only there. Reading only `os.environ` made
        # the panel report a missing endpoint while the driver was using it.
        env = _dotenv(ROOT / ".env")
        # **A named entry's endpoint wins over `.env`, and that ordering was measured.**
        #
        # The obvious rule -- "a real environment variable beats a file" -- cannot be
        # implemented here, because `driver.py:load_env` copies `.env` into the process
        # environment before the improver is resolved (`os.environ.setdefault`), so by the
        # time this runs the two are indistinguishable. Measured on the first
        # `--improver deepseek` run: `.env`'s `HG_METHOD_MODEL=qwen3.5-9b-local` was
        # already in `os.environ`, the entry's `deepseek-v4-pro` lost to it, and the run
        # recorded `improver: deepseek` while the method called the local vLLM. A record
        # that names one endpoint and a method that calls another is the same defect as a
        # harness misreporting its model.
        #
        # So the specific statement wins over the generic one: the entry names *this*
        # improver's endpoint, `.env` only names a default for improvers that declare
        # nothing (which is how `local` still works). An operator who wants a different
        # model for a named improver says so with `--improver-model`, which is recorded.
        base = (entry.get("base_url") or current_config().method.base_url
                or env.get("HG_METHOD_BASE_URL", ""))
        model = (model_override or entry.get("model")
                 or current_config().method.model
                 or env.get("HG_METHOD_MODEL", ""))
        key_env = entry.get("api_key_env") or ""
        # The key is not resolved into the record -- it is a secret, and the record is
        # read by people and written to disk. What is checked is that it *exists*: a run
        # whose improver has no credential fails at the first call, after the round has
        # been paid for, and the failure looks like a broken method.
        forbidden = forbidden_reason(config, model)
        if forbidden:
            return {"ok": False, "name": entry.get("name") or key, "kind": entry.get("kind"),
                    "model": model, "path": base, "runtime": [], "reason": forbidden}
        missing = [name for name, ok in (("base_url", base), ("model", model),
                                         (key_env, os.environ.get(key_env)) if key_env
                                         else ("", True)) if not ok]
        return {"ok": bool(base and model and (not key_env or os.environ.get(key_env))),
                "name": entry.get("name") or key,
                "kind": entry.get("kind"), "model": model, "path": base,
                # What the method's own client must be told, so the endpoint it calls is
                # the endpoint this record names (`harnessgrad/methods.py:_method_env`).
                "base_url": base, "api_key_env": key_env,
                # A model endpoint is reached over the network, so it needs no trees
                # bound; kept explicit so every path through here has the same keys.
                "runtime": [],
                "version": entry.get("version", "") or "", "requested": requested_note(entry),
                "sha256": "", "modified": bool(entry.get("modified")),
                "reason": "" if not missing else
                          f"the {key!r} improver needs {', '.join(missing)}"}

    # **A CLI this repository drives itself, resolved from its declaration.** Everything
    # below this line is the codex search: three install globs, a native binary beside a
    # wrapper, and a `--version` probe. That is the right shape for a tool that could be
    # anywhere and whose version is the only thing that identifies it, and the wrong shape
    # for one that lives in a known checkout: the search would have to choose between a
    # source tree and a build output, and a guess here would be worse than the globs.
    #
    # An entry that declares `launcher` is therefore resolved from the declaration and never
    # goes looking. It declares three more things the path cannot imply:
    #
    #   * `argv_style` -- the CLI's own vocabulary, which `methods/cli_improver.py` needs and
    #     cannot infer (codex wants `exec --sandbox ... -C`, dsh wants `--profile`);
    #   * `runtime` -- the trees the method sandbox must bind. `runtime_trees(path)` cannot
    #     work this out from a launcher script: it would bind `improvers/` (inside the
    #     platform, so dropped by the plan's own guard) and `~/.codex`, and the run would die
    #     in round 1 exactly as the docstring at `harnessgrad/methods.py:74` records;
    #   * `build_command` -- what must not drift. codex's `sha256` is its binary and the
    #     binary is the tool; here the tool is a *checkout*, the launcher is a stable 2 KB
    #     script, and `apps/cli/lib/bin.js` underneath it is rebuilt whenever someone works
    #     in that repository. Without this the curve would record an identity that cannot
    #     change while the improver behind it does.
    launcher = str(entry.get("launcher") or "")
    if launcher:
        path = (ROOT / launcher).expanduser()
        if not path.is_file():
            return {"ok": False, "name": entry.get("name") or key, "kind": entry.get("kind"),
                    "requested": requested or key,
                    "reason": f"the {key!r} improver's launcher is missing: {path}"}
        ok, ver, reason = _run_version(path)
        version = ver
        command = entry.get("build_command") or []
        if command:
            try:
                built = subprocess.run([str(part) for part in command], capture_output=True,
                                       text=True, timeout=60)
                if built.returncode == 0 and built.stdout.strip():
                    version = f"{ver}+{built.stdout.strip().splitlines()[0][:40]}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                # Recorded as a version that says it could not check, rather than silently
                # dropping the field: a point whose improver identity is `0.1.5+unknown` is
                # still comparable to another one that says the same thing.
                version = f"{ver}+unidentified({type(exc).__name__})"
        trees: list[str] = []
        for declared in (entry.get("runtime") or []):
            tree = Path(str(declared)).expanduser()
            if tree.is_dir() and str(tree) not in trees:
                trees.append(str(tree))
        # The tool's **own home**, when the entry builds one for this role rather than
        # borrowing the operator's. Two jobs: the sandbox has to bind it, because
        # `methods/cli_improver.py` copies it into the round's workspace, and the method has
        # to be told the path -- an ambient `DSH_HOME` is exactly the coupling this replaces.
        tool_home = str(entry.get("home") or "")
        if tool_home:
            home_path = Path(tool_home).expanduser()
            if home_path.is_dir() and str(home_path) not in trees:
                trees.append(str(home_path))
        # **A bash launcher does not reveal where its interpreter is.** `runtime_trees`
        # follows the shebang, so codex's `codex.js` hands back node's installation for
        # free -- but this launcher's shebang is `bash`, and `sh`/`bash`/`env` are
        # deliberately skipped as interpreters because they live in `/usr`, which the
        # namespace already binds. Node here is an nvm install under `$HOME`: without this
        # tree the method sandbox would hold the CLI's files and no program able to run
        # them, and the failure would read as a broken improver rather than a missing bind.
        interpreter = str(entry.get("interpreter") or "")
        if interpreter:
            found = shutil.which(interpreter)
            if found:
                prefix = _interpreter_prefix(Path(found))
                if prefix is not None and str(prefix) not in trees:
                    trees.append(str(prefix))
        for implied in runtime_trees(path):
            if implied not in trees:
                trees.append(implied)
        return {"ok": bool(ok), "path": str(path), "version": version,
                "sha256": Candidate(path).sha256(), "requested": requested or key,
                "modified": bool(entry.get("modified")),
                "kind": str(entry.get("kind") or ""),
                "name": entry.get("name") or key,
                "argv_style": str(entry.get("argv_style") or ""),
                # The home this CLI should run against, and the one credential it may hold.
                # Not identity: both are environment facts, and neither belongs on a curve
                # point (`_improver_identity` filters them out) -- but the method needs both,
                # and the resolution is the only place that read them off the entry.
                "home": tool_home,
                "api_key_env": str(entry.get("api_key_env") or ""),
                **{key: str(entry[key]) for key in ("base_url", "provider", "profile", "wire")
                   if entry.get(key)},
                "model": (model_override
                          or declared_model(config, source=config_home(), entry=entry)),
                "runtime": trees,
                "reason": "" if ok else reason}

    # Two different things, and conflating them was a bug: `requested` is what the
    # operator asked for (a name, a path, or a version string), `version_request` is what
    # the chosen entry pins. Overwriting the first with the second made
    # `--improver /opt/codex` fall through to "latest" and report a version request it
    # was never given.
    version_request = version or entry.get("version") or ""
    explicit = entry.get("path") or ""
    if (requested or "").startswith("/") and not explicit:
        explicit = requested
        version_request = ""

    found = candidates(explicit or None)
    usable = [c for c in found if c.ok]
    if (requested or "").startswith("/"):
        # A path request: verify exactly that path, do not go looking.
        cand = Candidate(Path(requested).expanduser())
        cand.ok, cand.version, cand.reason = _run_version(cand.path)
        if not cand.ok:
            return {"ok": False, "requested": requested, "reason": cand.reason,
                    "candidates": [c.as_dict() for c in found]}
        chosen = cand
    elif version_request and version_request != "latest":
        match = [c for c in usable if c.version == version_request]
        if not match:
            return {"ok": False, "requested": requested,
                    "reason": f"no working codex reports version {version_request}",
                    "candidates": [c.as_dict() for c in found]}
        chosen = match[0]
    else:
        # `latest`: the highest version we verified. Highest rather than first on PATH:
        # PATH here contains a broken install, and "latest" cannot mean "first found".
        if not usable:
            return {"ok": False, "requested": requested or "latest",
                    "reason": "no working codex on this machine",
                    "candidates": [c.as_dict() for c in found]}
        chosen = max(usable, key=lambda c: tuple(int(x) for x in c.version.split(".")))

    # A path request names *that binary*, not the config's default entry -- otherwise a
    # custom codex would be recorded under the default improver's name, which is the one
    # thing this field exists to prevent.
    name = chosen.path.name if (requested or "").startswith("/") \
        else (entry.get("name") or chosen.path.name)
    return {"ok": True, "path": str(chosen.path), "version": chosen.version,
            "sha256": chosen.sha256(), "requested": requested or "latest",
            "modified": bool(entry.get("modified")),
            # **Which layer this is**, from the config entry rather than guessed from the
            # path: a `cli` improver edits files through its own tools and therefore needs
            # a model that emits standard tool_calls, while an `openai-compatible` one is
            # driven by `methods/editor.py` and needs nothing of the sort. The driver's gate
            # read `improver["kind"]` and this dict had no such key, so the gate silently did
            # nothing -- found by a test that expected a refusal and got a completed run.
            "kind": str(entry.get("kind") or ""),
            "name": name,
            # The chosen entry's model, or -- for a CLI, which declares none -- the model
            # that CLI's own config names. `source=config_home()` is what makes a custom
            # codex report itself rather than the default improver.
            "model": (model_override
                      or declared_model(config, source=config_home(), entry=entry)),
            # Not identity: an environment fact. It travels with the resolution because
            # the method sandbox has to bind these trees, and the resolution is the only
            # place that knows which improver was picked (`runtime_trees`).
            "runtime": runtime_trees(chosen.path),
            "reason": ""}


def _seed_codex_home(home: Path, source: Path) -> list[str]:
    """Give the smoke session a codex home, borrowing credentials from the real one.

    A session needs *authentication* and *a model*, and both live in `CODEX_HOME`
    (`auth.json` and `config.toml`). The first smoke run pointed `CODEX_HOME` at an
    empty directory, so the session had no credentials and no provider and retried
    until it timed out -- measured: `ERROR: Reconnecting... 5/5`,
    `Falling back from WebSockets to HTTPS transport. request timed out`, 300s, and
    `note.txt` untouched. That is a check that cannot pass, which is worse than no check.

    Copied rather than pointed at, because the copy is what the smoke is allowed to
    touch: a session writes `history.jsonl`, session rollouts and caches into its home,
    and a check must not add rows to the operator's own codex history.
    """
    home.mkdir(parents=True, exist_ok=True)
    copied = []
    if source.is_dir():
        for name in ("config.toml", "auth.json"):
            src = source / name
            if src.is_file():
                shutil.copy2(src, home / name)
                copied.append(name)
    # Transport, not preference: a proxy that does not carry WebSockets makes codex
    # retry, warn, and time out. HTTPS is the transport that works wherever the
    # endpoint does.
    cfg = home / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    if "responses_experimental_transport" not in text:
        cfg.write_text(text + '\nresponses_experimental_transport = "https"\n')
        copied.append("responses_experimental_transport")
    return copied


def _smoke(resolved: dict, workdir: Path, timeout: int) -> dict:
    """Prove it can actually do the job: change one file in a throwaway directory.

    A resolved binary that prints a version and then cannot run a session is a failure
    we would rather find here than in the middle of a paid round. The prompt asks for a
    single deterministic edit so the check has a yes/no answer.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "note.txt").write_text("hello\n")
    prompt = ("Edit note.txt so that it contains exactly the line: smoke ok\n"
              "Do not create or modify anything else.")
    home = workdir / ".codex-home"
    # `opt_raw` in the declaration: unset is the `~/.codex` default, but a set value is
    # passed to `Path` unchanged -- the same shape the old `os.environ.get(...)` had.
    source = current_config().improver.codex_home_source
    borrowed = _seed_codex_home(home, Path(source) if source is not None
                                else Path.home() / ".codex")
    argv = [resolved["path"], "exec", "--skip-git-repo-check",
            "--sandbox", "danger-full-access", "-C", str(workdir), prompt]
    env = {**os.environ, "CODEX_HOME": str(home)}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                              env=env, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"the smoke session did not finish in {timeout}s"}
    except OSError as exc:
        return {"ok": False, "reason": f"could not start a session: {exc}"}
    body = (workdir / "note.txt").read_text(errors="replace").strip()
    if body == "smoke ok":
        return {"ok": True, "reason": "", "home_borrowed": borrowed,
                "model": _model_from_output(proc.stdout + proc.stderr)}
    tail = [l for l in (proc.stdout + proc.stderr).strip().splitlines() if l.strip()][-3:]
    return {"ok": False, "reason": f"it ran but note.txt holds {body[:60]!r}",
            "home_borrowed": borrowed, "output": " | ".join(tail)[:400]}


def _model_from_output(text: str) -> str:
    for line in text.splitlines():
        if "model:" in line.lower():
            return line.strip()[:80]
    return ""


def _print_list(found: list[Candidate]) -> int:
    if not found:
        print("no codex binary found at all")
        return 2
    print(f"{'ok':<4} {'version':<10} path")
    for c in found:
        mark = "yes" if c.ok else "NO"
        note = "" if c.ok else f"   ({c.reason})"
        print(f"{mark:<4} {c.version or '-':<10} {c.path}{note}")
    return 0 if any(c.ok for c in found) else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="every candidate, verified")
    ap.add_argument("--doctor", action="store_true", help="resolve + prove it works")
    ap.add_argument("--which", action="store_true", help="print the resolved path only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", default="", help="a version, `latest`, or a path")
    ap.add_argument("--config", default=None, help="improvers.json to use")
    ap.add_argument("--smoke", action="store_true",
                    help="with --doctor: also run a real one-file session")
    ap.add_argument("--smoke-timeout", type=int, default=300)
    ap.add_argument("--smoke-dir", default=None)
    args = ap.parse_args()

    if args.list:
        found = candidates()
        if args.json:
            print(json.dumps([c.as_dict() for c in found], indent=1, ensure_ascii=False))
            return 0 if any(c.ok for c in found) else 2
        return _print_list(found)

    config = load_config(Path(args.config) if args.config else None)
    resolved = resolve(config, version=args.version)
    if args.smoke and resolved.get("ok"):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="hg-improver-smoke-") as tmp:
            resolved["smoke"] = _smoke(resolved, Path(args.smoke_dir or tmp),
                                       args.smoke_timeout)
            resolved["ok"] = resolved["ok"] and resolved["smoke"]["ok"]

    if args.which:
        print(resolved.get("path", ""))
        return 0 if resolved.get("ok") else 2
    if args.json:
        print(json.dumps(resolved, indent=1, ensure_ascii=False))
        return 0 if resolved.get("ok") else 2

    if not resolved.get("ok"):
        print(f"improver: REFUSED — {resolved.get('reason')}", file=sys.stderr)
        for c in resolved.get("candidates", []):
            mark = "ok " if c["ok"] else "NO "
            print(f"  {mark}{c['version'] or '-':<10} {c['path']} {c['reason']}",
                  file=sys.stderr)
        return 2
    print(f"improver   {resolved['name']} {resolved['version']}")
    print(f"  path     {resolved['path']}")
    print(f"  sha256   {resolved['sha256'][:16]}…")
    if resolved.get("model"):
        print(f"  model    {resolved['model']}  (from its own config, not HG_METHOD_*)")
    print(f"  asked    {resolved['requested']}"
          + ("  (path, not a release)" if resolved["requested"].startswith("/") else ""))
    if resolved.get("modified"):
        print("  ! this improver is declared as MODIFIED from its release: the curve "
              "will say so, and two runs using it are not two runs of the same thing")
    smoke = resolved.get("smoke")
    if smoke is not None:
        print(f"  smoke    {'ok' if smoke['ok'] else 'FAILED'} — {smoke['reason'] or 'edited one file'}")
    return 0 if resolved.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
