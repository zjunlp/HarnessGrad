#!/usr/bin/env python3
"""Adapt DeepSeek Harness (`dsh`) to HarnessGrad's task/answer/trace contract.

DSH is an agent harness, so this file is a **launcher, not a loop**: the platform hands it
one task and one working directory, DSH does the work in that directory with its own tools,
and this file turns what it did into the two things the contract asks for.

    <entrypoint> --task <task.json> --workdir <dir>   ->  exit 0, answer.txt, trace.jsonl

**`cwd` is the whole interface.** Measured 2026-10-06: `dsh --profile headless "<task>"`
takes no `--cwd`/`--dir` flag (`dsh --profile headless --help` lists only `-h`), and it reads
and writes the process's working directory -- verified by running it from a temp directory and
watching the file it was asked for land there and nowhere else. It also picks up whatever
`AGENTS.md` the working directory carries, which for a task directory is the task's own
instructions. So the subprocess's `cwd` is the task, and nothing has to be translated.

**Model selection.** When `HG_AGENT_MODEL` and `HG_AGENT_BASE_URL` are configured, the
adapter writes a per-task patch that selects that route. With neither configured,
DSH keeps its shipped model default. The trace records the selected model and the
patch identity. `--profile headless` selects the one-task profile in both cases.

**DSH needs a profile, a container's `HOME` is empty, and the profile must not travel in this
tree.** Measured 2026-10-06. DSH resolves its plugin tree out of
`$HOME/.dsh/profiles/node_modules`: **277 absolute symlinks** into the DSH checkout, 90 KB, no
files of its own. An empty `HOME` means DSH composes no tree, and the headless runner then
returns *early and silently* -- exit 0, no output, no files
(`packages/bundle/headless/src/index.ts` returns when any of its `agents` /
`agentDefaultModel` / `sessions` services is missing). In a task container the failure is
louder (`Cannot find package '@deepseek-ai/cordis-plugin-timer'`), which is how it was found.

**Those symlinks cannot ship beside this file.** The platform stages a harness with
`shutil.copytree(harness_src, work)` (`ckpt/git_state.py:stage_workspace`) at its default
`symlinks=False`, so a link in this tree is *followed and copied* -- and these point into a 2 GB
dependency graph full of links of its own. Measured: the driver spun inside `shutil.copytree`
for over 800 s per attempt and produced nothing. So they are bound instead
(`harness.json`'s prepared `runtime_paths` names the installation), and `_install_profile`
rebuilds the relative farm under the writable `HOME` the container gave this process.

**Credentials come from the agent's environment.** DSH reads a stored credential when it has
one and `DEEPSEEK_API_KEY` when it does not; measured with an empty `HOME` (which is what a
container gives it): the stored-credential path finds nothing and the environment variable is
what makes it work. So `HG_AGENT_API_KEY` -- the key the platform already gives the harness
for the *agent's* model, which is this harness's own model -- is passed through under DSH's
name. It is not written anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import time

# The shipped harness is portable. Prepare this manifest with
# tools/configure_dsh_harness.py to record the installation the sandbox will bind.
CLI_RELATIVE = Path("apps/cli/lib/bin.js")

#: Where DSH keeps its own state. A container's `HOME` is a writable tmpfs, but it may be
#: unset entirely, and DSH resolves its home from it.
DEFAULT_HOME = "/tmp/dsh-home"

#: The model DSH answers with when the environment names none: the `model` of its shipped
#: `agent-default-model` row (`packages/bundle/base/cordis.patch.yml`).
DEFAULT_MODEL = "deepseek-flash"


# One package name and DSH-root-relative target per line. Text avoids copying the
# installed dependency graph when the platform stages the candidate.
FARM_MANIFEST = Path(__file__).resolve().parent / "dsh_farm.tsv"


def _declared_paths() -> list[Path]:
    manifest = json.loads(Path(__file__).with_name("harness.json").read_text(encoding="utf-8"))
    return [Path(value).expanduser() for value in manifest.get("runtime_paths", [])]


def _executable(value: str, env: dict, *, preserve_symlinks: bool = False) -> str:
    found = shutil.which(value, path=env.get("PATH", ""))
    if not found:
        raise ValueError(f"DSH executable is unavailable: {value}")
    path = Path(found)
    return str(path.absolute() if preserve_symlinks else path.resolve())


def runtime(env: dict | None = None) -> dict:
    """Resolve one installation for execution, configuration facts and plugin links.

    Explicit command wrappers own their runtime. Supply HG_AGENT_DSH or
    HG_AGENT_DSH_ROOT as well when the wrapper needs this adapter's plugin farm.
    """
    env = os.environ if env is None else env
    wrapper = shlex.split(env.get("HG_AGENT_DSH_COMMAND", ""))
    declared = _declared_paths()
    explicit_root = env.get("HG_AGENT_DSH_ROOT")
    cli = env.get("HG_AGENT_DSH")
    wrapper_cli = next((part for part in wrapper[1:]
                        if not part.startswith("-") and part.endswith(CLI_RELATIVE.as_posix())), None)
    if wrapper_cli:
        cli = wrapper_cli
    if not cli and explicit_root:
        cli = str(Path(explicit_root).expanduser() / CLI_RELATIVE)
    if not cli and not wrapper:
        cli = next((str(root / CLI_RELATIVE) for root in declared
                    if (root / CLI_RELATIVE).is_file()), None)
    root = None
    if cli:
        cli = Path(cli).expanduser().resolve()
        if not cli.is_file() or tuple(cli.parts[-4:]) != tuple(CLI_RELATIVE.parts):
            raise ValueError(f"DSH CLI must be an installed {CLI_RELATIVE}: {cli}")
        root = cli.parents[3]
        if explicit_root and root != Path(explicit_root).expanduser().resolve():
            raise ValueError("HG_AGENT_DSH and HG_AGENT_DSH_ROOT name different installations")
    elif not wrapper:
        raise ValueError("DSH is not configured; run tools/configure_dsh_harness.py "
                         "or set HG_AGENT_DSH and declare its runtime_paths")
    if wrapper:
        argv = list(wrapper)
        # A venv's Python is commonly a symlink to the base interpreter. Its
        # spelling selects pyvenv.cfg and site-packages, so keep that spelling
        # while making relative/PATH commands independent of the task cwd.
        argv[0] = _executable(argv[0], env, preserve_symlinks=True)
        # A direct interpreter + bin.js override names the interpreter that
        # actually runs. Prepared bindings or HG_AGENT_NODE must not substitute
        # another one for diagnostics. An opaque wrapper may choose internally;
        # even an explicit DSH root does not reveal its interpreter.
        node = argv[0] if wrapper_cli and wrapper[1] == wrapper_cli else None
        if node is not None:
            argv[1] = str(cli)
    else:
        node = env.get("HG_AGENT_NODE")
        if not node:
            node = next((str(bound) for bound in declared
                         if bound.is_file() and os.access(bound, os.X_OK)), None)
        if not node:
            node = next((str(path) for bound in declared
                         for path in (bound / "node", bound / "bin/node") if path.is_file()), None)
        node = _executable(node or "node", env)
        argv = [node, str(cli)]
    return {"argv": argv, "root": root, "node": node, "cli": str(cli) if cli else None}


def farm_targets(root: Path, manifest: Path | None = None) -> list[tuple[str, Path]]:
    """Validate the complete farm before writing any links or profile directories."""
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"DSH root cannot be resolved: {root}: {exc}") from exc
    if not root.is_dir():
        raise ValueError(f"DSH root is not a directory: {root}")
    entries = []
    seen = set()
    for number, line in enumerate((manifest or FARM_MANIFEST).read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("\t")
        if len(parts) != 2:
            raise ValueError(f"invalid DSH farm row {number}")
        name, relative = parts
        package = PurePosixPath(name)
        target = PurePosixPath(relative)
        valid_name = (len(package.parts) == (2 if name.startswith("@") else 1)
                      and all(part not in {"", ".", ".."} for part in package.parts))
        if (not valid_name or package.is_absolute() or name != package.as_posix()
                or "\\" in name or name in seen or not relative
                or not target.parts or target.is_absolute() or ".." in target.parts or "\\" in relative
                or relative != target.as_posix()):
            raise ValueError(f"unsafe DSH farm row {number}: {line}")
        destination = root / relative
        try:
            resolved = destination.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"DSH dependency is missing: {destination}") from exc
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"DSH dependency cannot be resolved: {destination}: {exc}") from exc
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise ValueError(f"DSH dependency escapes its installation or is not a package: {destination}")
        entries.append((name, destination))
        seen.add(name)
    if not entries:
        raise ValueError("DSH farm manifest is empty")
    return entries


def _install_profile(home: Path, root: Path) -> int:
    """Create the writable farm, or verify an existing one matches this installation."""
    entries = farm_targets(root)
    farm = home / ".dsh" / "profiles" / "node_modules"
    if farm.is_symlink() or (farm.exists() and not farm.is_dir()):
        raise ValueError(f"DSH profile farm must be a real directory: {farm}")
    if farm.exists():
        for name, target in entries:
            link = farm / name
            if not link.is_symlink() or link.resolve() != target.resolve():
                raise ValueError(f"DSH profile contains stale or missing link: {link}; use a fresh HOME")
        return -2
    farm.mkdir(parents=True)
    for name, target in entries:
        link = farm / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    return len(entries)


#: The question a failed DSH boot cannot answer from its own output.
#:
#: DSH resolves a loader entry's bare package name through **Node's internal ESM loader**,
#: reached by the native addon `node-addon-require-builtin`
#: (`vendor/loader/lib/index.js`, `ModuleLoader.fromInternal`). Every failure on that path is
#: swallowed by a bare `catch {}`, and the fallback is a plain `import(name)` issued from
#: `vendor/loader/lib/index.js` -- a file whose ancestors hold no `@deepseek-ai` packages, so
#: the only error a user ever sees is `Cannot find package ... imported from
#: .../vendor/loader/lib/index.js`, which names neither the addon nor the reason it failed.
#: This probe asks the addon directly and reports what it said, plus whether the internal
#: loader will import a plugin from the profile base DSH hands it.
_LOADER_PROBE_JS = """
import { createRequire } from "node:module";
import os from "node:os";
const loaderJs = process.env.HG_PROBE_LOADER, base = process.env.HG_PROBE_BASE;
const out = { node: process.versions.node, exec: process.execPath, tmp: os.tmpdir(),
              base, kind: null, has_import: null, addon: null, resolved: null };
try {
  const req = createRequire(loaderJs);
  const m = req("node-addon-require-builtin");
  out.addon_from_profile = (() => {
    try { createRequire(base)("node-addon-require-builtin"); return true; }
    catch (e) { return String(e && e.message || e).split("\\n")[0]; }
  })();
  const L = m.requireBuiltin("internal/modules/esm/loader");
  const raw = L && L.getOrInitializeCascadedLoader && L.getOrInitializeCascadedLoader();
  if (!raw) { out.addon = "getOrInitializeCascadedLoader returned nothing"; }
  else {
    out.kind = typeof raw.getOrCreateModuleJob === "function" ? "v2"
             : typeof raw.getModuleJobForImport === "function" ? "v1" : "none";
    out.has_import = typeof raw.import;
    if (typeof raw.import === "function") {
      try { await raw.import("@deepseek-ai/cordis-plugin-timer", base, {}); out.resolved = true; }
      catch (e) { out.resolved = String(e && e.message || e).split("\\n")[0]; }
    }
  }
} catch (e) { out.addon = String(e && e.message || e).split("\\n")[0]; }
console.log(JSON.stringify(out));
"""


def _loader_probe(home: Path, env: dict, selected: dict | None = None) -> dict:
    """Ask the container what DSH's own silent resolution path does there.

    Never raises and never blocks the task: a probe that fails is itself the answer, and it
    is recorded as text rather than dropped.
    """
    try:
        selected = selected or runtime(env)
        if selected["root"] is None or selected["node"] is None:
            return {"probe_skipped": "custom command owns its runtime"}
        node = selected["node"]
        loader_js = str(selected["root"] / "vendor/loader/lib/index.js")
        base = (home / ".dsh" / "profiles" / "headless").as_uri() + "/"
        probe_env = dict(env, HG_PROBE_LOADER=loader_js, HG_PROBE_BASE=base)
        done = subprocess.run([node, "--input-type=module", "-e", _LOADER_PROBE_JS],
                              env=probe_env, capture_output=True, text=True, errors="replace", timeout=60)
        line = (done.stdout or "").strip().splitlines()
        if not line:
            return {"probe_error": (done.stderr or "no output").strip()[-400:]}
        return json.loads(line[-1])
    except Exception as exc:                                  # noqa: BLE001 - diagnostic
        return {"probe_error": f"{type(exc).__name__}: {exc}"}


#: The patch that puts the platform's model in front of DSH.
#:
#: DSH ships one default model (`agent-default-model`: `deepseek-official` /
#: `deepseek-flash`) and a live user document, `$DSH_HOME/settings.yaml`, that **overrides
#: that row**. Left alone, DSH therefore answers every task with its own model no matter
#: what `HG_AGENT_MODEL` says -- and a harness that ignores the platform's model cannot be
#: placed on the model axis of any comparison at all.
#:
#: Two rows do it, and both are needed. `llm-pi-ai` gains a hand-declared route: the
#: platform's endpoint, spoken over `openai-completions`, with the platform's model id in
#: its catalog. `agent-default-model` then names that route. A route another adapter already
#: owns cannot be registered twice, so the name `harnessgrad` is the adapter's own and
#: collides with nothing DSH ships.
#:
#: The key is named by environment variable, never written here: `apiKeyEnv:
#: DEEPSEEK_API_KEY` is a reference the credential seam resolves per request, and the
#: adapter has already copied `HG_AGENT_API_KEY` into that variable. Measured 2026-10-06 on
#: the host: with an isolated `DSH_HOME` this answers through `qwen3.5-9b-local`, and with
#: the developer's own `settings.yaml` present the same patch is silently beaten by it --
#: which is why the container, whose `HOME` starts empty, is the case that matters.
_MODEL_PATCH = """\
# Written by the harnessgrad `deepseek-harness` adapter. Do not edit: it is rebuilt per task from
# HG_AGENT_MODEL and HG_AGENT_BASE_URL so the recorded model is the model that ran.
- id: llm-pi-ai
  config:
    providers:
      harnessgrad:
        displayName: HarnessGrad agent model
        apiKeyEnv: DEEPSEEK_API_KEY
        api: openai-completions
        baseURL: {base_url}
        models:
          - id: {model}
            name: {model}
            contextWindow: {context_window}
            maxTokens: {max_tokens}

- id: agent-default-model
  config:
    provider: harnessgrad
    model: {model}
"""


def _model_patch(env: dict, home: Path) -> "Path | None":
    """Write the DSH patch that selects the platform's model, or `None` when there is none.

    `None` is the honest answer for an environment that names no model: DSH then runs its
    own default, and the trace says so rather than claiming a model nobody asked for.
    """
    model = env.get("HG_AGENT_MODEL")
    base_url = env.get("HG_AGENT_BASE_URL")
    if not model or not base_url:
        return None
    path = home / ".dsh" / "harnessgrad-model.patch.yml"
    path.parent.mkdir(parents=True, exist_ok=True)

    def quoted(value: str) -> str:
        """A YAML scalar, single-quoted, so a model id with a colon cannot change meaning."""
        return "'" + str(value).replace("'", "''") + "'"

    path.write_text(_MODEL_PATCH.format(
        base_url=quoted(base_url), model=quoted(model),
        context_window=env.get("HG_AGENT_CONTEXT_WINDOW", "32768"),
        max_tokens=env.get("HG_AGENT_MAX_TOKENS", "8192")), encoding="utf-8")
    return path


def command(prompt_path: Path, patch: "Path | None" = None, *,
            selected: dict | None = None) -> list[str]:
    """The argv that answers one task.

    The task text is passed as a single argument rather than on stdin: the headless app's
    own help says multiple words are joined by spaces, so an argv element is the shape it
    documents. It comes from a file because a task statement can be long enough to run into
    a command-line limit, and a truncated task is a task the agent was never given.
    """
    argv = list((selected or runtime())["argv"])
    if patch is not None:
        argv = argv + ["--patch", str(patch)]
    return argv + ["--profile", "headless", prompt_path.read_text(encoding="utf-8")]


def _deadline() -> float:
    """Seconds DSH is given: the environment's budget, less a margin, or a fallback.

    The margin matters here for the same reason it did for the codex adapter: the platform
    kills the container at the environment's own `agent_timeout_s` and does not tell the
    harness that number, so `HG_AGENT_DEADLINE_S` is exported for exactly this
    (`eval/container.py:container_env`). Stopping 90 s short lets DSH be reaped and the trace
    be written, so a run that ran out of time is recorded as a harness that stopped rather
    than as a container that was killed -- which reads like a harness that never started.
    """
    deadline = os.environ.get("HG_AGENT_DEADLINE_S")
    if deadline:
        return max(60.0, float(deadline) - float(os.environ.get("HG_AGENT_STOP_MARGIN_S", "90")))
    return float(os.environ.get("HG_AGENT_DSH_TIMEOUT_S", "780"))


#: The behavioural knobs, as data. A method can edit `knobs.json` (it is in `surface.json`'s
#: `allow` list) instead of the launcher, so an edit's diff is a value change rather than a code
#: rewrite -- and the record can say *which knob* moved. A missing file means the defaults below,
#: so the harness still runs when the file is absent or a key is dropped.
KNOBS_FILE = Path(__file__).resolve().parent / "knobs.json"

DEFAULT_PREAMBLE = ("Complete the following task. Everything you need is in the current working "
                    "directory, and the work is the files you leave there rather than a "
                    "description of them.\n\n")
DEFAULT_INPUTS_HEADER = "\n\nTask inputs:\n"


def knobs() -> dict:
    try:
        return json.loads(KNOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sha(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest() if text is not None else None


_FACTS: dict | None = None
_FACTS_KEY: tuple | None = None


def facts(env: dict, home: Path, prompt: str, patch: "Path | None",
          selected: dict | None = None) -> dict:
    """What this run's configuration actually is, as facts on the trace.

    **Why the trace and not a file.** The improver reads the previous round's traces; writing a
    `dsh_facts.md` into the task workdir would put the harness's own configuration in front of
    the *task* agent, which is a different party and would change what is being measured. So the
    facts travel in `trace.jsonl`, where only the method side reads them next round.

    **What is hashed and why three hashes.** `prompt_sha` is the per-task prompt this launcher
    built; `config_sha` is the composed plugin tree `--dump-config` prints (stable across tasks
    within a round, so two rounds with the same value had the same effective configuration);
    `profile_sha` is the profile patch file itself. `tools_sha` is deliberately absent: the tool
    schemas live inside DSH and `--dump-config` does not print them without booting, so a hash
    claiming to cover tools would be a number nothing verifies (findings.md §186).
    """
    global _FACTS, _FACTS_KEY
    selected = selected or runtime(env)
    key = (tuple(selected["argv"]), selected["root"], selected["node"])
    if _FACTS is not None and _FACTS_KEY == key:
        out = dict(_FACTS)
    else:
        dump = None
        argv = selected["argv"] + ["--profile", "headless", "--dump-config"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", env=env,
                                  timeout=int(knobs().get("limits", {}).get("dump_config_timeout_s", 20)))
            if proc.returncode == 0:
                dump = proc.stdout
        except (OSError, subprocess.SubprocessError):
            dump = None
        rows = [line.strip() for line in (dump or "").splitlines()
                if line.strip().startswith("- id:")]
        out = {"config_sha": _sha(dump), "rows": len(rows),
               "row_ids": [r.split(":", 1)[1].strip() for r in rows][:48],
               "dump_ok": dump is not None}
        # Wrappers may carry arbitrary arguments, including credentials. Keep
        # command identity without publishing their raw argv in the trace.
        out["runtime_command_sha"] = _sha(json.dumps(selected["argv"]))
        out["runtime_node"] = selected["node"]
        out["runtime_cli"] = selected["cli"]
        _FACTS = dict(out)
        _FACTS_KEY = key
    out["prompt_sha"] = _sha(prompt)
    out["profile_sha"] = _sha(patch.read_text(encoding="utf-8")) if patch and patch.is_file() else None
    out["knobs_sha"] = _sha(KNOBS_FILE.read_text(encoding="utf-8")) if KNOBS_FILE.is_file() else None
    out["knobs"] = knobs()
    return out


def _answer_stamp(path: Path):
    """`answer.txt` as it was before the CLI ran, so a task-written file is attributable."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def run(task: dict, workdir: Path) -> int:
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    answer = workdir / "answer.txt"
    # **`answer.txt` belongs to the task first.** This adapter used to delete it here and
    # write the CLI's final message at the end, which is the same defect `codex` had: a
    # dataset whose goal asks the agent to write `answer.txt` (gsm8k's does) would have had
    # that file replaced by whatever the model said last -- and this model narrates before
    # it answers. A file the agent wrote wins; the final message is the fallback.
    before = _answer_stamp(answer)

    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal")
    tun = knobs()
    prompt_cfg = tun.get("prompt") or {}
    prompt = str(prompt_cfg.get("preamble") or DEFAULT_PREAMBLE) + goal
    if task.get("inputs") and prompt_cfg.get("append_inputs", True):
        prompt += str(prompt_cfg.get("inputs_header") or DEFAULT_INPUTS_HEADER) + json.dumps(
            task["inputs"], ensure_ascii=False)
    prompt_path = workdir / ".dsh-task.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith("HG_METHOD_")}
    # **The one environment fact this harness has to add, and it is not optional.**
    #
    # DSH resolves loader-entry package names through Node's internal ESM loader, reached by
    # the native addon `node-addon-require-builtin`. The addon first *copies its own binary*
    # to `os.tmpdir()` and loads it from there (`node-addon-native-custom-loader`:
    # `materializedNativeBinaryPath`). In a task container `TMPDIR` is the harness's own
    # home, which the platform mounts as a **plain `--tmpfs`** -- and Docker's `--tmpfs`
    # defaults to `rw,nosuid,nodev,noexec`. A `.node` file on a `noexec` filesystem cannot be
    # `dlopen`ed, the addon gives up, and it reports `No usable native binding found for
    # node-addon-require-builtin-linux-x64-gnu (auto)`. Every one of those failures is
    # swallowed by a bare `catch {}` inside DSH, whose fallback is a plain `import(name)`
    # issued from `vendor/loader/lib/index.js` -- a file with no `@deepseek-ai` packages
    # anywhere above it. The whole tree then fails to compose, with the only visible error
    # being `Cannot find package '@deepseek-ai/cordis-plugin-timer'`, which names neither the
    # addon nor the tmpfs. Measured 2026-10-06: identical image and mounts pass on the host
    # (`TMPDIR=/tmp`, exec) and fail in the container (`TMPDIR=<home>/tmp`, noexec); setting
    # this one variable fixes it, because the addon then loads its binary from the read-only
    # DSH checkout instead of a copy. Caching is a convenience for `pkg`-packaged runtimes,
    # not a correctness requirement, so declining it costs one `dlopen` from a path that is
    # already there.
    env["NARB_DISABLE_NATIVE_CACHE"] = "1"
    env.setdefault("HOME", DEFAULT_HOME)
    home = Path(env["HOME"])
    # **An operator's own home is refused, not used.** `DSH_HOME` below is named from
    # `HOME`, so on this host (where `HOME` is a real account) DSH reads
    # `~/.dsh/settings.yaml`, whose *saved* `agent-default-model` selection beats the
    # `agent-default-model` row in the `--patch` generated two hundred lines down. Measured
    # 2026-11-01: a host-side smoke with `HOME=/home/xubuqiang` loaded that document and
    # answered through a **real DeepSeek endpoint** with the local key -- authentication
    # failed, and the trace still said `agent_model: qwen3.5-9b-local`, `agent_base_url:
    # http://127.0.0.1:8001/v1`. A record that names a model which did not run is worse than
    # a failed run, so this is a refusal with the fix in the message. A task container's
    # `HOME` starts empty and never reaches this branch.
    inherited_settings = home / ".dsh" / "settings.yaml"
    if inherited_settings.is_file() and env.get("HG_AGENT_DSH_KEEP_HOME") != "1":
        raise ValueError(
            f"refusing to run DSH against an operator home: {inherited_settings} holds a "
            "saved model selection that overrides the model this adapter declares; point "
            "HOME at a scratch directory (a container's HOME is empty, which is the case "
            "this adapter is built for), or set HG_AGENT_DSH_KEEP_HOME=1 to run with that "
            "configuration and accept that the recorded model may not be the model that ran")
    # **DSH's home, named rather than inferred.** `resolveDshHome` walks `$DSH_HOME` and
    # otherwise falls back to `os.homedir()` (`packages/util/home-paths/src/index.ts`), and
    # `$HOME` is not the only thing that answers that: an operator's own
    # `~/.dsh/settings.yaml` carries an `agent-default-model` *saved selection* that wins over
    # the `agent-default-model` config row at agent-creation time. Measured 2026-10-06 on this
    # host: with the model patch applied and `HOME` pointed at a fresh directory, DSH still
    # answered through `deepseek-official` -- the patch was composed correctly (`--dump-config`
    # agreed) and then beaten by a settings document the harness never asked for. Naming the
    # home makes DSH read the home this adapter built the farm in and nothing else, which is
    # the only behaviour that survives being run next to somebody's desktop configuration.
    env["DSH_HOME"] = str(home / ".dsh")
    home.mkdir(parents=True, exist_ok=True)
    # **`TMPDIR` names a directory, not a promise that one exists.** The platform sets
    # `TMPDIR=<home>/tmp` inside the container and mounts `<home>` as a tmpfs, but nothing
    # ever creates `<home>/tmp`: a `--tmpfs` creates its own mount point and none of its
    # children. DSH's spill store is the first thing to want that directory and it fails the
    # whole boot when it is absent -- `ENOENT: no such file or directory, mkdtemp
    # '<home>/tmp/dsh-spill-XXXXXX'`, raised from
    # `packages/spill/spill-local/lib/index.js:privateRoot` and surfacing as `dsh: plugin
    # tree failed to load: ... failed to apply loader entry spill-local`. A scratch directory
    # the environment promises and does not provide is worth one `mkdir`, and this harness
    # cannot run without it.
    Path(env.get("TMPDIR") or "/tmp").mkdir(parents=True, exist_ok=True)
    selected = runtime(env)
    farm_links = _install_profile(home, selected["root"]) if selected["root"] is not None else None
    # DSH's own name for the key the platform gives the agent. Never logged, never written.
    if env.get("HG_AGENT_API_KEY") and not env.get("DEEPSEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = env["HG_AGENT_API_KEY"]
    # A container has no `PATH` of its own for the bound node, and DSH's launcher is a node
    # script: `node` is named by absolute path in `command`, so nothing here needs `PATH`.

    timeout = _deadline()
    patch = _model_patch(env, home)
    # `HG_AGENT_DSH_MODEL` used to be the whole story, and it was only ever a *label*: the
    # record named a model (or the shipped default) while DSH answered with its own. Now the
    # patch decides, so the record is taken from the patch.
    model_ran = env.get("HG_AGENT_MODEL") or env.get("HG_AGENT_DSH_MODEL") or DEFAULT_MODEL
    with (workdir / "trace.jsonl").open("w", buffering=1) as trace:
        def record(value: dict) -> None:
            trace.write(json.dumps(value, ensure_ascii=False) + "\n")

        measured = facts(env, home, prompt, patch, selected)
        record({"harness_identity": {"harness": "deepseek-harness",
                                     "harness_version": "0.2.0",
                                     "agent_backend": "dsh-headless",
                                     "agent_model": model_ran,
                                     "agent_base_url": env.get("HG_AGENT_BASE_URL"),
                                     "model_patch": str(patch) if patch else None,
                                     "prompt_sha": measured["prompt_sha"],
                                     "config_sha": measured["config_sha"],
                                     "profile_sha": measured["profile_sha"],
                                     "knobs_sha": measured["knobs_sha"]},
                "task_id": task.get("task_id")})
        record({"harness_facts": measured})
        # A custom wrapper with no installation declaration owns its profile;
        # normal launches report the validated farm and selected runtime root.
        probe = (home / ".dsh" / "profiles" / "node_modules" / "@deepseek-ai"
                 / "cordis-plugin-timer")
        profile_info = {"home": str(home), "manifest": FARM_MANIFEST.is_file(),
                        "links_created": farm_links,
                        "probe_resolves": probe.exists()}
        profile_info["runtime_root"] = str(selected["root"]) if selected["root"] else None
        profile_info.update(_loader_probe(home, env, selected))
        record({"profile": profile_info})
        launched_at = time.time()
        proc = subprocess.Popen(command(prompt_path, patch, selected=selected), cwd=workdir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors="replace")
        def translate(out: str, err: str) -> None:
            """The session log when there is one, the two text streams when there is not.

            DSH publishes per-step structure only in the log it flushes on exit
            (`sessions.flush`), so a killed run may leave nothing -- and then the text
            translation is what a reader gets, which is why it is still here.
            """
            log = find_session_log(home, launched_at)
            if log is not None:
                events, how = read_session_log(log)
                summary = _record_session(events, record) if events else {}
                if summary.get("turns") or summary.get("actions"):
                    _record_streams(out, err, record, turns=False)
                    record({"session_log": {"path": str(log), "compression": how, **summary}})
                    return
                record({"session_log": {"path": str(log), "compression": how,
                                        "events": len(events), "translated": False}})
            _record_streams(out, err, record)

        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            translate(out, err)
            record({"error": "dsh timed out", "timeout_s": timeout,
                    "deadline_s": float(os.environ.get("HG_AGENT_DEADLINE_S") or 0) or None})
            return 1
        translate(out, err)
        final = (out or "").strip()
        if proc.returncode:
            record({"error": "dsh failed", "exit_code": proc.returncode})
            return proc.returncode
        try:
            record(submitted(answer, before, final))
        except ValueError as exc:
            record({"error": str(exc), "exit_code": 0})
            return 1
    return 0


def submitted(answer: Path, before, final: str) -> dict:
    """The `submitted` record: the task's file if the agent wrote one, else the message.

    Separated from `run` so the precedence is testable without the CLI: the rule is the
    whole content, and getting it wrong is invisible in a score (a task graded on an exact
    file would be graded on the model's prose instead).
    """
    current = _answer_stamp(answer)
    if current is not None and current != before and current.strip():
        # The agent wrote the file the task asked for -- keep it, do not overwrite it with
        # the final message.
        return {"submitted": True, "answer": current.decode("utf-8", "replace")[:4000],
                "answer_source": "artifact"}
    if not final:
        raise ValueError("dsh printed no final message and the task wrote no answer.txt")
    # The contract's answer file. For a task graded by its own tests this is a record of
    # what the agent said it did; for an answer-graded task it is the answer.
    answer.write_text(final + "\n", encoding="utf-8")
    return {"submitted": True, "answer": final[:4000], "answer_source": "final_message"}


#: The session events DSH flushes, translated. `sessions.flush` (headless runner, before it
#: exits) writes `<DSH_HOME>/sessions/<cwd-key>/<session-id>/session.v<N>.jsonl[.zstd]`, and
#: that log is the only place this CLI publishes per-step structure: the process itself
#: streams human text (reasoning to stderr, the final message to stdout).
#:
#: Measured on a real log on this host (330 events): 98 `tool/call`, 98 `tool/result`,
#: 29 `assistant/message` each carrying `usage`, plus `turn/*` and `step/*` boundaries. So
#: "DSH has no structured output" was wrong -- the adapter was reading the wrong stream.

#: How the tool name maps onto the contract's `observation.kind`. Same vocabulary as the
#: codex and opencode adapters, so a method can read one field across all three.
_OBSERVATION_KINDS = {
    "shell": "shell", "bash": "shell", "terminal": "shell",
    "read": "filesystem", "write": "filesystem", "edit": "filesystem",
    "patch": "filesystem", "glob": "filesystem", "grep": "filesystem",
    "web_fetch": "api", "web_search": "api", "fetch": "api", "search": "api",
}

#: Bound on what one record may put in the trace. A patch can be megabytes, and the trace is
#: read by a model: the full string stays available in `tool_input.chars`-style summaries and
#: in the session log itself, which is kept beside the run.
MAX_COMMAND_CHARS = 500
MAX_OUTPUT_CHARS = 2000


def observation_kind(tool: str) -> str:
    return _OBSERVATION_KINDS.get((tool or "").lower(), "other")


def _text_of(message: dict | None, depth: int = 0) -> str:
    """The text parts of a DSH message, joined, at any nesting depth.

    A tool result nests one level deeper than an assistant message -- measured on a real log:

        tool/result  message.content[0] = {type: "tool-result", content: [{type: "text", ...}]}

    so a one-level reader finds tool results with **zero** characters of output, which reads
    as an environment that said nothing back.
    """
    if depth > 3:
        return ""
    content = (message or {}).get("content") or []
    if isinstance(content, str):
        return content
    out = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append(str(part.get("text") or ""))
        elif part.get("content") is not None:
            out.append(_text_of(part, depth + 1))
    return "".join(out)


def find_session_log(home: Path, since: float) -> Path | None:
    """The session log this run flushed, or `None`.

    Searched by modification time under the whole sessions root rather than by rebuilding the
    path: the directory is bucketed by a hash of the cwd (`session-persistence-jsonl`'s
    `projectKey`), so a change in that spelling would silently produce an empty trace, and an
    empty trace reads as a harness that did nothing.
    """
    root = Path(home) / ".dsh" / "sessions"
    try:
        candidates = [path for path in root.rglob("session.*.jsonl*")
                      if path.stat().st_mtime >= since - 5]
    except OSError:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime, default=None)


def read_session_log(path: Path) -> tuple[list[dict], str]:
    """`(events, how)`. DSH writes JSONL, optionally zstd-compressed (`.jsonl.zstd`)."""
    data: bytes
    if str(path).endswith(".zstd"):
        try:
            import zstandard                                    # noqa: PLC0415
            with open(path, "rb") as fh:
                data = zstandard.ZstdDecompressor().stream_reader(fh).read()
            how = "zstandard"
        except ImportError:
            proc = subprocess.run(["zstd", "-dc", str(path)], capture_output=True)
            if proc.returncode != 0:
                return [], "zstd-missing"
            data, how = proc.stdout, "zstd-cli"
    else:
        data, how = path.read_bytes(), "plain"
    events: list[dict] = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events, how


def _record_session(events: list[dict], record) -> dict:
    """Translate DSH's session events into the adapter contract's records.

    Returns what the translation saw, for the trace: a run whose log is present but whose
    shape changed (the format is versioned, `session.v3`) must be *visible* as a translation
    that found nothing, not as a silent success.
    """
    calls: dict[str, dict] = {}
    turns = actions = 0
    tokens = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    seen_usage = False
    turn_end = None
    for event in events:
        kind, data = event.get("type"), event.get("data") or {}
        if kind == "assistant/message":
            turns += 1
            text = _text_of(data.get("message"))
            record({"step": turns, "reply": text})
            usage = data.get("usage") or {}
            for source, target in (("inputTokens", "input_tokens"),
                                   ("outputTokens", "output_tokens"),
                                   ("cacheReadTokens", "cached_input_tokens")):
                value = usage.get(source)
                if isinstance(value, int):
                    tokens[target] += value
                    seen_usage = True
        elif kind == "tool/call":
            calls[str(data.get("callId"))] = {
                "name": str(data.get("name") or ""),
                "arguments": str(data.get("arguments") or ""),
                "turn": data.get("turn"), "step": data.get("step")}
        elif kind == "tool/result":
            call = calls.pop(str((data.get("message") or {}).get("source", {}).get("callId")), {})
            actions += 1
            name = call.get("name") or ""
            arguments = call.get("arguments") or ""
            message = data.get("message") or {}
            output = _text_of(message)
            meta = data.get("meta") or {}
            code = meta.get("exitCode") if isinstance(meta, dict) else None
            record({
                "step": turns,
                # The action is the tool plus what it was asked to do: the platform counts
                # `command` records and treats two equal strings as a re-issue, so the name
                # alone would make every `web_fetch` look like a repeat of the last one.
                "command": f"{name} {arguments}".strip()[:MAX_COMMAND_CHARS],
                "tool": name,
                "tool_input_chars": len(arguments),
                "observation": {"kind": observation_kind(name),
                                "returncode": code if isinstance(code, int) else None,
                                "output_chars": len(output),
                                "truncated": len(output) > MAX_OUTPUT_CHARS},
                **({"error": json.dumps(data["error"], ensure_ascii=False)[:MAX_OUTPUT_CHARS]}
                   if data.get("error") else {})})
        elif kind == "turn/end":
            turn_end = (data.get("reason") or {}).get("kind") if isinstance(
                data.get("reason"), dict) else data.get("reason")
        elif kind == "session":
            event_version = event.get("version")
    if turns or seen_usage:
        usage = dict(tokens) if seen_usage else {}
        usage["calls"] = turns
        record({"usage": usage})
    return {"events": len(events), "turns": turns, "actions": actions,
            "unmatched_calls": len(calls), "turn_end": turn_end}


def _record_streams(out: str, err: str, record, *, turns: bool = True) -> None:
    """DSH's two streams, as trace records.

    It streams its reasoning to **stderr** and prints the final assistant message to
    **stdout** (`dsh --profile headless --help`). Nothing here is machine-readable the way
    codex's `--json` events are, so the trace keeps the lines in order under two record
    kinds rather than pretending to a structure DSH does not emit. The distinction is the
    one that matters to a reader: `reasoning` is what it was thinking, `reply` is what it
    concluded.
    """
    for line in (err or "").splitlines():
        if line.strip():
            record({"reasoning": line.rstrip()})
    # **One record for the whole final message.** A record per line made a five-line answer
    # look like five model turns to `loop_facts`, which counts `reply` records as turns --
    # the same double-count the miniswe and codex adapters had. Headless DSH is one shot,
    # so its final message is one turn.
    if not turns:
        # The session log carries the turns and the usage; stderr reasoning is kept because
        # it is the only human-readable record of what the agent was thinking.
        return
    answer = "\n".join(line for line in (out or "").splitlines() if line.strip())
    if answer:
        record({"reply": answer})
    # DSH reports no token counts, and `_ended` reads a missing `usage` as a crash (a
    # harness killed before its own end). Recording the turn it *did* make at this boundary
    # keeps a successful run from being reported as a crashed one, and the note says what
    # the number is: one invocation observed here, not the model calls inside it.
    record({"usage": {"calls": 1,
                      "note": "dsh reports no token counts; one invocation observed at the "
                              "harness boundary"}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text()), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
