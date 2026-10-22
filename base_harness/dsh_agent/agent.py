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

**`agent_model` is DSH's own default, not the platform's `HG_AGENT_MODEL`.** This harness
does not read `HG_AGENT_MODEL`: DSH takes its model from its profile, whose
`agent-default-model` names `deepseek-flash`. Recording the platform's variable here would
put a model on the curve point that this harness never asked for -- the same "the record
names a party that did no work" defect the improver field exists to prevent.

**`--profile headless`.** DSH ships named profiles; the headless one is "answer one task,
stream reasoning to stderr, print the final assistant message, and exit" -- a one-shot agent
rather than a session, which is what a harness needs. Its `agent-default-model` names
`provider: deepseek-official, model: deepseek-flash` out of the box, so this harness measures
DSH with the model it ships with rather than with one this platform chose for it.

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
(`harness.json`'s `runtime_paths` names `~/.dsh/profiles`), and `_install_profile` creates the
one link that joins them to the writable `HOME` the container gave this process.

**Credentials come from the agent's environment.** DSH reads a stored credential when it has
one and `DEEPSEEK_API_KEY` when it does not; measured with an empty `HOME` (which is what a
container gives it): the stored-credential path finds nothing and the environment variable is
what makes it work. So `HG_AGENT_API_KEY` -- the key the platform already gives the harness
for the *agent's* model, which is this harness's own model -- is passed through under DSH's
name. It is not written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

#: Where DSH lives and how to start it. Both are bound into the task container read-only by
#: `harness.json`'s `runtime_paths`, so these are the paths *inside* it.
#: **`node` version matters, and the failure is silent.** Measured 2026-10-06: the built
#: `apps/cli/lib/bin.js` runs under node **v22.19.0** and produces *nothing* under
#: **v22.16.0** -- exit code 0, no stdout, no stderr, no files, for every argv including
#: `--help`. The silence has a cause in DSH's own source: the headless runner returns early
#: when any of its `agents` / `agentDefaultModel` / `sessions` services is missing
#: (`packages/bundle/headless/src/index.ts`), so a tree that fails to compose exits as if it
#: had answered nothing to say. Both node binaries are on this host; this is the one that
#: works with the checkout as built.
DEFAULT_NODE = "/home/xubuqiang/.nvm/versions/node/v22.19.0/bin/node"
DEFAULT_DSH = "/mnt/20t/xubuqiang/deepseek-harness/apps/cli/lib/bin.js"

#: Where DSH keeps its own state. A container's `HOME` is a writable tmpfs, but it may be
#: unset entirely, and DSH resolves its home from it.
DEFAULT_HOME = "/tmp/dsh-home"

#: The model DSH answers with when the environment names none: the `model` of its shipped
#: `agent-default-model` row (`packages/bundle/base/cordis.patch.yml`).
DEFAULT_MODEL = "deepseek-flash"


#: The plugin farm DSH's loader resolves its entries through, as **data**: one
#: `relative/name<TAB>absolute target` per line, 277 lines, 42 KB.
#:
#: It travels as text rather than as links because the platform stages a harness with
#: `shutil.copytree` (`ckpt/git_state.py:stage_workspace`), and a link in this tree is a
#: link the staging step has to decide about. Measured before it was fixed: 277 links into a
#: 2 GB DSH checkout made the driver recurse inside `copytree` for over 800 s per attempt and
#: produce nothing. Reproducing them here costs one `os.symlink` call each, into the writable
#: `HOME` the container gave this process, and leaves the harness tree nothing but text.
FARM_MANIFEST = Path(__file__).resolve().parent / "dsh_farm.tsv"


def _install_profile(home: Path) -> None:
    """Give DSH the two things an empty `HOME` is missing, and nothing else.

    **A directory it can write, and the module farm inside it.** Measured 2026-10-06, in a
    task container: DSH's `prepareProfile` writes `$HOME/.dsh/profiles/headless/cordis.yml`
    on every boot, so `profiles/` has to be a real writable directory -- pointing the whole
    of it at a read-only tree fails with `EROFS` on exactly that write. What DSH does *not*
    create for itself is `profiles/node_modules`, the farm its loader resolves loader-entry
    package names through; a container's `HOME` starts empty, the tree fails to compose, and
    the headless runner then returns *early and silently* (exit 0, no output, no files --
    `packages/bundle/headless/src/index.ts` returns when any of its `agents` /
    `agentDefaultModel` / `sessions` services is missing). In a container the boot failure is
    loud (`Cannot find package '@deepseek-ai/cordis-plugin-timer'`), which is how it was
    found.

    The farm is rebuilt from `dsh_farm.tsv` rather than copied, and the links point at
    absolute paths inside the DSH checkout -- which the container binds at the same path
    read-only (`harness.json`'s `runtime_paths`), so they resolve there.

    Idempotent, and it leaves an existing profile alone: an operator running this by hand
    keeps their own. Returns how many links it created, so the trace can say what happened
    rather than leaving a silent `except` to be guessed at.
    """
    if not FARM_MANIFEST.is_file():
        return -1
    dsh = home / ".dsh"
    dsh.mkdir(parents=True, exist_ok=True)
    profiles = dsh / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    farm = profiles / "node_modules"
    if farm.exists():
        return -2
    farm.mkdir(parents=True, exist_ok=True)
    made = 0
    for line in FARM_MANIFEST.read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        name, _, target = line.partition("\t")
        link = farm / name
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            made += 1
        except OSError:
            # A filesystem that will not take one link is not a reason to fail the task:
            # DSH will try to materialise its own profile and the trace will say what
            # happened.
            continue
    return made


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


def _loader_probe(home: Path, env: dict) -> dict:
    """Ask the container what DSH's own silent resolution path does there.

    Never raises and never blocks the task: a probe that fails is itself the answer, and it
    is recorded as text rather than dropped.
    """
    node = os.environ.get("HG_AGENT_NODE", DEFAULT_NODE)
    dsh = os.environ.get("HG_AGENT_DSH", DEFAULT_DSH)
    loader_js = str(Path(dsh).resolve().parents[3] / "vendor" / "loader" / "lib"
                    / "index.js")
    base = (home / ".dsh" / "profiles" / "headless").as_uri() + "/"
    try:
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
# Written by the harnessgrad `dsh_agent` adapter. Do not edit: it is rebuilt per task from
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


def command(prompt_path: Path, patch: "Path | None" = None) -> list[str]:
    """The argv that answers one task.

    The task text is passed as a single argument rather than on stdin: the headless app's
    own help says multiple words are joined by spaces, so an argv element is the shape it
    documents. It comes from a file because a task statement can be long enough to run into
    a command-line limit, and a truncated task is a task the agent was never given.
    """
    argv = shlex.split(os.environ.get("HG_AGENT_DSH_COMMAND", ""))
    if not argv:
        argv = [os.environ.get("HG_AGENT_NODE", DEFAULT_NODE),
                os.environ.get("HG_AGENT_DSH", DEFAULT_DSH)]
    if not argv:
        raise ValueError("HG_AGENT_DSH_COMMAND is empty")
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


def run(task: dict, workdir: Path) -> int:
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    answer = workdir / "answer.txt"
    answer.unlink(missing_ok=True)

    goal = task.get("goal") or task.get("instruction")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task requires a non-empty goal")
    prompt = ("Complete the following task. Everything you need is in the current working "
              "directory, and the work is the files you leave there rather than a "
              "description of them.\n\n" + goal)
    if task.get("inputs"):
        prompt += "\n\nTask inputs:\n" + json.dumps(task["inputs"], ensure_ascii=False)
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
    farm_links = _install_profile(home)
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

        record({"harness_identity": {"harness": "dsh-agent",
                                     "agent_backend": "dsh-headless",
                                     "agent_model": model_ran,
                                     "agent_base_url": env.get("HG_AGENT_BASE_URL"),
                                     "model_patch": str(patch) if patch else None},
                "task_id": task.get("task_id")})
        # What the launcher did before it launched anything. A harness whose setup fails
        # silently fails in a way nobody can read afterwards: `-1` is "the manifest is not
        # beside me", `-2` is "the farm was already there", `>= 0` is how many links this
        # process made.
        # What the launcher did before it launched anything. A harness whose setup fails
        # silently fails in a way nobody can read afterwards: `-1` is "the manifest is not
        # beside me", `-2` is "the farm was already there", `>= 0` is how many links this
        # process made, and `probe_resolves` says whether the farm's links reach their
        # targets *here* -- which is the difference between "the links were not made" and
        # "they were made somewhere this process cannot see".
        probe = (home / ".dsh" / "profiles" / "node_modules" / "@deepseek-ai"
                 / "cordis-plugin-timer")
        profile_info = {"home": str(home), "manifest": FARM_MANIFEST.is_file(),
                        "links_created": farm_links,
                        "probe_resolves": probe.exists()}
        profile_info.update(_loader_probe(home, env))
        record({"profile": profile_info})
        proc = subprocess.Popen(command(prompt_path, patch), cwd=workdir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors="replace")
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            _record_streams(out, err, record)
            record({"error": "dsh timed out", "timeout_s": timeout,
                    "deadline_s": float(os.environ.get("HG_AGENT_DEADLINE_S") or 0) or None})
            return 1
        _record_streams(out, err, record)
        final = (out or "").strip()
        if proc.returncode or not final:
            record({"error": "dsh failed or printed no final message",
                    "exit_code": proc.returncode})
            return proc.returncode or 1
        # The contract's answer file. For a task graded by its own tests this is a record of
        # what the agent said it did; for an answer-graded task it is the answer.
        answer.write_text(final + "\n", encoding="utf-8")
        record({"submitted": True, "answer": final[:4000]})
    return 0


def _record_streams(out: str, err: str, record) -> None:
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
    for line in (out or "").splitlines():
        if line.strip():
            record({"reply": line.rstrip()})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    return run(json.loads(args.task.read_text()), args.workdir)


if __name__ == "__main__":
    raise SystemExit(main())
