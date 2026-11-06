# Writing a harness for HarnessGrad

This is what a harness must satisfy to be dropped in and measured. It is written
for someone contributing a harness, not for someone reading the platform's
internals.

**The short version:** a harness is a repository with a `harness.json`, and a
program that turns one task into one answer. Everything else — language, tools,
control flow, memory, how many models it calls — is yours.

---

## 1. What a harness is

A harness is **a directory** containing a `harness.json` and whatever code you
want. It does not have to be the root of a git repository, and the platform never
looks inside it.

Three things are fixed, and they are fixed so that a harness can be measured at
all rather than because the platform has opinions:

| # | Fixed | Why |
| --- | --- | --- |
| F1 | `harness.json` at the harness root | so the platform can identify and invoke it |
| F2 | one command that solves one task | so any harness, in any language, is measurable |
| F3 | one answer file | so scoring does not depend on your output format |

---

## 2. `harness.json`

```json
{
  "name": "my-harness",
  "version": "1.0.0",
  "path": ".",
  "entrypoint": "run.py",
  "backend": "cli",
  "install": "requirements.txt"
}
```

| field | required | meaning |
| --- | --- | --- |
| `name` | **yes** | identity; appears on every curve point |
| `version` | **yes** | identity; bump it when behaviour changes |
| `path` | no (default `"."`) | where the harness lives, relative to the repo root. Use `"."` if the harness *is* the repo. Needed when a harness is a subdirectory of a larger project. |
| `entrypoint` | **yes** | the file the platform runs |
| `backend` | no (default `"cli"`) | how the entrypoint is driven; `cli` is the only backend implemented today |
| `install` | no (default `null`) | dependency file relative to the harness root — a `requirements.txt` (pip) or a `.sh` (shell). **Runs once, at import time**, baked into the environment the task declares: `python tools/import_env.py --image <base> --harness <this dir>`. It never runs during a measurement, because the harness tree is read-only then and the platform does not install into the host's interpreter. See INTERFACE.md §2.5.6. |
| `env_kinds` | no (default `["files"]`) | which environments this harness can operate on. The platform refuses a run whose dataset needs a kind you do not declare, rather than scoring it zero on every task. Declaring `"exec"` means your harness can run inside a container — so it must be **self-contained except for `install`**: the image is not the host, and the host's interpreter, packages and paths are not there |
| `install` (again, for `exec`) | — | if you declare `"exec"`, list your dependencies here. The image will not have them, your tree is read-only during a run, and a missing import fails with a traceback the platform records as an exit code — which reads like a weak harness. Measured: the platform's own base harness scored zero on every container task for `ModuleNotFoundError: No module named 'openai'` |
| `runtime_paths` | no (default `[]`) | absolute files or dedicated directory trees the sandbox must bind read-only because you need them — a CLI installed outside `/usr`, a runtime you were built against. Applies to `files` **and** `exec`: a task image need not have any runtime at all — the real Terminal-Bench images ship no `python3` — so the platform mounts the interpreter it invokes your entrypoint with, and mounts whatever you list here read-only beside it. |
| `runtime` | no | opt into a managed Python environment with the declaration below, or the existing Bun runtime contract. Managed Python replaces legacy `install` overlays for this Harness. |

External CLI templates can require local installation preparation before measurement.
The [Codex preparation procedure](../base_harness/codex/README.md) fixes one CLI and
its runtime bindings in a Harness copy; [DSH preparation](../base_harness/deepseek-harness/README.md)
also validates its plugin link targets. [OpenClaw preparation](../base_harness/openclaw/README.md)
fixes its supported package, Node executable and bindings together.
[OpenCode preparation](../base_harness/opencode/README.md) selects an installed v1
source tree and Bun without copying that tree into checkpoints.
[miniSWE preparation](../base_harness/miniswe/README.md) provisions its declared
dependencies for each executing Python version, including task images. Run preparation
before recording the candidate identity, and repeat it when selecting another installation.

**`name` and `version` are the harness's identity and a method may not change
them.** Everything else in the directory is yours, and a method that improves
your harness is allowed to rewrite any of it — including the entrypoint.

> If you are contributing a *base* harness for others to improve, pick a version
> and keep it stable: every curve point records it, and two runs under different
> versions are not comparable.

### 2.1 Managed Python dependencies

A Python Harness can declare its own interpreter and complete dependency lock:

```json
"runtime": {
  "kind": "python",
  "python": "3.12",
  "requirements": "requirements.lock"
}
```

Add this field to `harness.json`. Keep `runtime_paths` empty, and omit `install`
or set it to the same lock path. The lock must contain the complete dependency
closure as unique `package==version` lines, plus optional comments. A Harness
using only the standard library can use an empty lock. Ranges, extras, markers,
URLs, local/editable dependencies, installer options and source distributions
are not supported. Incomplete transitive dependencies are refused before install.
The entrypoint and lock must be owned, normalized relative files. Other source
files must also be regular owned files, without symbolic links.

Every source directory must be the parent of a retained source file. Empty
directories, including directories containing only filtered environments,
bytecode or state, are refused before staging or measurement. If a directory's
presence matters, include an owned marker file such as `.gitkeep`; it enters
the source identity and portable snapshot even if Git ignores it. Platform
state directories and the filtered local environments themselves remain
excluded from source. Invalid input is rejected without deleting its directories.

Install the declared CPython minor, `uv` and bubblewrap before configuration.
From the activated platform environment:

```bash
# Offline: wheels must already be available in the operator's uv cache.
python tools/configure_harness.py --harness /path/to/my-harness
# Explicitly allow downloading wheels when preparing the initial environment.
python tools/configure_harness.py --harness /path/to/my-harness --fetch
# Check the published artifact without installing anything.
python tools/configure_harness.py --harness /path/to/my-harness --list --json
```

Only run the preparation command matching your available wheel cache. The
default store is `~/.cache/harnessgrad/harness-runtimes`; `HG_HARNESS_RUNTIME_ROOT`
or `--runtime-root` selects a dedicated directory outside source/platform/work
trees. `UV_CACHE_DIR` selects a separate installer cache. Python is never
downloaded automatically. `--force` explicitly publishes a fresh instance; keep
old instances while saved runs still reference them.

The driver prepares each frozen candidate once, before cache lookup or
measurement, using offline wheels. Source-only edits reuse the dependency
artifact; lock changes prepare another artifact. Preparation failure records
an unmeasured candidate and a private receipt. It does not become an ordinary
zero score. Preparation time is recorded separately and included in wall time;
it consumes no model tokens or evaluation trials.

The actor starts the bound venv with `-I -B`. A trusted bootstrap adds the
entrypoint's directory first and the Harness source root second, so nested
entrypoints can import both sibling modules and root modules. The artifact and source are
read-only; each task gets private HOME, temporary storage and `.state`. The
platform venv is not projected into this actor. Provider, service and scorer
dependencies keep their separate role bindings. This isolates Python packages;
it does not seal the operating system or hash every system library.

The current route supports **sandboxed native `files` tasks**. Container/`exec`,
observed or cooperative workers, `agent_runtime` projections, external
`runtime_paths` and `--no-sandbox` are refused. Existing Harnesses without
`runtime.kind="python"` keep their legacy runtime behavior.
[MiniAgent](../base_harness/miniagent/README.md) is the first shipped base Harness
using this route, with a complete CPython 3.12 lock and an isolated native worker.
Other base Harnesses retain their declared runtime setup.
`loop` and `miniswe` still support both `files` and `exec`; their migration needs
a contract for both routes before replacing their current dependency setup.
The Codex, DSH and OpenClaw wrappers use their separate external-CLI preparation
procedures, while OpenCode declares Bun. A Python venv alone does not describe
those external runtimes.

Each measured point records `harness_runtime_identity`, including owned source
bytes and its exact dependency artifact. The source fingerprint also covers
gitignored source files; generated environments, bytecode and task state are
excluded and unavailable to the actor. H0, cache and resume validate this
binding before reuse. Training checkpoints may have different locks and each
is validated against its own artifact. A run starting with managed Python
cannot later drop that declaration. Portable snapshots contain source, while
dependency artifacts stay in the external store.

Source bindings use version 3: a canonical list of file paths, executable flags
and content digests, with the file-and-parent-directory policy above. Versions 1
and 2 cannot safely establish this identity: v1 used ambiguous binary framing;
v2 admitted empty directories that snapshots could lose. Both are refused for
admission, cache/H0 reuse and resume. Their historical records are preserved;
remeasure the source to establish a new binding. Dependency artifact manifests
retain their existing format, and valid prepared dependencies can still be reused.
An eval selected with `--from-run` requires the original managed checkpoint's
binding; a missing or invalid identity is refused before preparation, without
selecting the current published dependency instance.

For shared H0, first configure the selected source, then use
`python tools/prepare_h0.py --dataset demo --harness-path /path/to/my-harness ...`.
The helper's discovery and dry-run paths only read prepared artifacts. It
checks source and runtime identity before publishing or reusing the baseline.
Static `validate_harness.py` checks support this declaration. Its CLI and upload
smoke routes explicitly refuse managed Python; use the sandboxed driver with
`--harness-path /path/to/my-harness --dataset demo --rounds 0 --improver none`
and `HG_AGENT_BACKEND=mock` for a controlled interface check. This exercises the
actual actor environment without making a model request.

---

## 3. The task contract

The platform invokes a Python wrapper:

```
<python> <entrypoint> --task <task.json> --workdir <dir>
```

For a Bash, Node or other CLI harness, `entrypoint` names a Python wrapper that
forwards these arguments to your program. Copy `examples/bash-harness/run.py` for
that wrapper, or start with `examples/python-react-harness` for Python. The
[five-minute entry](bring-your-own-harness.md#five-minute-entry) shows validation
and a sandboxed run before you configure any model.

### 3.1 What you are given

`task.json`:

```json
{
  "task_id": "t01",
  "goal": "Answer with the word alpha.",
  "inputs": {}
}
```

| field | meaning |
| --- | --- |
| `task_id` | opaque identifier. **Do not parse it** and do not depend on its format. |
| `goal` | the task, in natural language. This is the only field a harness must understand. |
| `inputs` | optional map of name → content for tasks that need fixtures. Absent for most tasks. |

**`goal` is the contract; `inputs` is a convenience.** A harness that handles
`goal` alone is valid. A harness may ignore `inputs` entirely.

An adapter may declare public tool instructions in `SETUP.runtime_prompt`. The
runner appends them to the actor's `goal`, so the same discovery and invocation
contract reaches any harness without an adapter-specific tool parser. The harness
still controls its own model reply format and tool dispatch.

### 3.2 What you produce

Write the answer to `<workdir>/answer.txt`. The platform compares its stripped
contents, case-insensitively, against the task's expected answer.

**Scoring is deliberately dumb** — exact match on a normalized string. It is not
a judgement of quality, and it is not extensible by the harness, because a
harness that can influence its own scoring cannot be compared with another one.

The exit code must be `0` on completion. A non-zero exit is recorded as a harness
failure and scores zero for that task; it does not abort the run.

### 3.3 What you may do

Write files, spawn subprocesses, and make network calls. Two places are yours, and the
difference between them is only where your harness expects to find things:

* **`--workdir`** — the task's working directory, and the process's cwd. Create, read
  and delete whatever you like. Fresh for every task, thrown away afterwards.
* **`.state/`** — inside your own tree, beside your code. For a harness that writes a
  cache or a session file relative to itself, this is the one that works without your
  having to know where `--workdir` is. `HARNESSGRAD_STATE` holds its absolute path if
  you would rather not assume the layout.

**Nothing persists between tasks, and that is a design decision rather than a
limitation.** Each task gets fresh directories and the next task gets different ones.
If you want to measure cross-session memory, wrap the sessions into a single task: then
remembering is part of what your harness does, and its score depends on its own
behaviour. The alternative -- storage the platform preserves across the task set --
would make a harness's score depend on what the platform supplied rather than on the
harness, and no curve could tell the two apart.

**The code in your tree is read-only, and this is not a request.** That is measurement,
not distrust: one copy of your harness scores *every task in the round*, so a harness
that rewrote its own `agent.py` while answering task 1 would be a different program by
task 2, and the curve would report one harness where two ran. `.state/` is gitignored,
so what you write there never changes your sha.

Your harness runs in its own mount namespace. Measured from inside it:

| | |
| --- | --- |
| `HarnessGrad/driver.py`, `harnessgrad/`, `eval/`, `methods/`, `.env` | **not readable** |
| every other run's workspace | **absent** |
| `HG_METHOD_*` (the method's model and budget) | **not in the environment** |
| your own harness tree, at its host path | readable |
| — the code in it | **not writable** |
| — `.state/` inside it | **writable**, private to the task |
| `--workdir` | present, writable |
| `/tmp` | private, writable |
| `$HOME` | not present |
| `HG_AGENT_*` (your model) | present |
| `HARNESSGRAD_STATE` | absolute path to your `.state/` |
| the network | unrestricted |

The first two rows are absence, not permission. The platform's directory survives
as an empty mount point -- the name is visible, the contents are not -- and an empty
directory is a very different thing from a readable one. There is no `EACCES` to work
around because there is nothing there to be denied.

Paths are the same inside and outside. A harness that records `/…/workspace/foo`
in its trace is naming a path that exists on the host too, so your evidence survives
the sandbox. The namespace costs about 11 ms per task.

If you write no further than `--workdir`, none of this concerns you. A harness that
wants a cache should put it in `--workdir` or `/tmp`, both of which are writable and
private to the task. `$HOME` is not present (the variable is set, the directory is
not), so anything that lazily creates `~/.cache/...` raises -- loudly and
immediately, which is the intended trade. The alternative is a harness whose score
depends on what a previous run left behind.

---

## 4. What is deliberately not specified

Stated so that their absence is not read as an oversight:

* **Language and runtime.** Any language, one invocation. The platform runs
  `<python> <entrypoint> --task … --workdir …` (`eval/run/runner.py`), so Python is
  started directly and anything else — a shell script, a Node program, a compiled
  binary — needs a Python shim as its `entrypoint` that re-executes the real thing.
  Measured 2026-11-02: a manifest naming `run.sh` produced `SyntaxError: unmatched ')'`
  from the smoke, because the interpreter was handed the shell script.
  `examples/bash-harness/run.py` is the worked shim, and
  [`docs/bring-your-own-harness.md`](bring-your-own-harness.md) §1.4 is the measurement.
* **Tools, prompts, control flow, memory, number of model calls.** This is the
  substance of a harness and precisely what a method is supposed to improve.
* **Whether you call a model at all.** A harness with no model is legal and
  useful as a floor.
* **Environment.** Each task gets a fresh working directory, a **writable `$HOME`
  and `XDG_*`**, and a writable `.state/` inside your tree. Nothing else on the host is
  visible or writable: the platform's own tree is hidden, and `/tmp` is private to the
  task. Anything you need outside `/usr` you must name in `runtime_paths`.
* **Your model.** You may read `HG_AGENT_*`. If you got your model from somewhere else,
  write a `harness_identity` event into your trace — otherwise the curve point records
  a model that did not run. See INTERFACE.md §3.

---

## 5. Checking your harness before contributing

```
python tools/validate_harness.py path/to/your/harness
```

It checks the required fields, that the entrypoint exists, and — with `--smoke` —
that the harness runs a trivial task and writes an answer. Getting this to pass is
the whole contribution requirement.

The whole path, from a directory that already has an entrypoint to a point on a
measured curve, with every command and its real output, is
[`docs/bring-your-own-harness.md`](bring-your-own-harness.md).

---

## 6. The reference harness

`base_harness/loop/` is the minimal example: a ReAct loop, one `bash` tool, eight
steps, no middleware, no memory, no sub-agents. Read it as the smallest thing
that satisfies this document, not as a good harness.

**Known limitation, and it matters when choosing a baseline:** on a benchmark
where the model is the bottleneck rather than the harness, a harness this thin
leaves evolution nothing to win. Two published results say the same thing from
opposite ends — an evolution run starting from a single-tool harness scored
*below* simply sampling the same model more times (67.4 vs 68.2), while gains on
strong harnesses were largest for the weakest models (+44.0% vs +11.2%). The
first pair is measured by **Rethinking the Evaluation of Harness Evolution for
Agents** ([arXiv:2607.12227](https://arxiv.org/abs/2607.12227)) Table 1, whose
own conclusion is sharper than the sentence above: on held-out tasks the same
harness-evolution arm lands within noise of direct sampling. The second pair's
source is still not named here, and that is a gap in this page. **A base
harness should be thin enough that improving it is legitimate, and thick enough
that there is something to improve.**
