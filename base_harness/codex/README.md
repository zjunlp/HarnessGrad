# Codex Harness installation preparation

The checked-in Harness is a portable adapter template. Codex itself is an external,
read-only installation.

## Install a dedicated copy

On Linux x86-64 with Bubblewrap and network access, use the HarnessGrad installer.
It uses Node.js 24.22.2 and npm 11.19.1 to install the npm launcher and its Linux
x64 native package into a dedicated runtime store. It checks both archive
digests against the release recipe and installs from the npm lockfile with
lifecycle scripts disabled.

```sh
harnessgrad harness install codex --version 0.167.2
HARNESS_DIR=$(harnessgrad harness install codex --version 0.169.0)
```

Use the printed directory with `harnessgrad run --harness-path`. Omitting
`--version` selects the latest verified Codex release listed by `harnessgrad
harness list`, currently `0.169.0`, and reports the choice on stderr. Use
`--install-root` to choose a dedicated store outside the platform checkout and
shared temporary directories. By default the store is `harnessgrad/base-harnesses`
under the user data directory. Each release has its own package lock and binding.

An explicit unverified `X.Y.Z` release is attempted with a warning and a
network-isolated version probe. Its npm launcher and Linux x64 payload must match
the exact version and digests in the lockfile. Running a task also requires the
release to work with the Codex adapter.

## Bind an existing installation

Copy the Harness, then prepare that copy before measurement.

```sh
cp -a base_harness/codex /path/to/codex-harness-copy
python3 tools/configure_codex_harness.py /path/to/codex-harness-copy
```

With no override, preparation discovers `codex` on `PATH` once. To choose a particular
installation, pass `--codex /path/to/codex`. The precedence is explicit `--command`
or `--codex`, then `HG_AGENT_CODEX_COMMAND`, then `HG_AGENT_CODEX`, then `PATH`.
The tool reads the shared main checkout `.env`, a worktree `.env` if present, and
the process environment in that order. Explicit command-line choices win.

Preparation resolves the command, derives the installation bindings, and runs
`--version` with a fresh HOME on the host and in a network-isolated native sandbox.
It requires bubblewrap and compatible host runtime dependencies. The probe environment
excludes model credentials and personal Codex configuration. Both version probes must agree
before one complete `harness.json` is atomically replaced. Preparation does not
install or upgrade Codex and makes no model calls.

The manifest's `runtime_paths` and adapter-owned `_codex_runtime` record are written
together. The record contains the selected argv, version, file hashes and executable
search directories. It is part of the candidate's source identity. Tasks use this
same argv even if PATH changes, verify the recorded files, and report the runtime
record in `trace.jsonl`. An unprepared template, missing executable, changed file,
different override or inconsistent binding declaration fails with a preparation
diagnostic; it does not select a different installation.

Native Codex aliases resolve to their physical executable. Standalone releases with
`codex-exec-server` / `codex-code-mode-host` bind their dedicated `bin/` directory;
single executables bind only their files, including when placed directly in HOME.
No entire user HOME or `.codex` configuration directory is inferred as a runtime.
The adapter still creates a private per-task `CODEX_HOME` and obtains its model and
credentials from `HG_AGENT_*`. Daily `OPENAI_*` / `CODEX_*` settings and credentials
for other model roles are excluded from the CLI environment.

An npm launcher is expanded into an explicit Node-and-script command. Its Codex
package and any installed `@openai/codex-*` native packages are included, covering
both nested and hoisted npm layouts. Select a dedicated Node distribution with
`--node /path/to/node-prefix/bin/node` if the launcher's Node is not already on PATH.
System Node prefixes are refused because binding `/usr` or `/usr/local` would cover
the task image's own tools. Complete runtime installations must be outside `/tmp`,
which the native sandbox hides, and must not overlap the platform or Harness copy.
Shared temporary roots and broad system/user directory bindings are also refused.

For a custom wrapper, use an argv-style command and declare additional dependencies:

```sh
python3 tools/configure_codex_harness.py /path/to/codex-harness-copy \
  --command '/path/to/venv/bin/python /path/to/wrapper.py' \
  --runtime /path/to/wrapper-dependencies
```

Arguments naming existing files are made absolute. Python virtualenv executable
symlink spelling is preserved; the venv and its base interpreter are included.
The wrapper must forward `--version` and the adapter's `exec` arguments. Matching
`HG_AGENT_CODEX_COMMAND` / `HG_AGENT_CODEX` values can remain set, including relative
or bare choices used during preparation. Changing the choice requires preparing
matching bindings again. Additional `--runtime` entries are read-only; use them only
for runtime dependencies, not personal credentials.

Supported Linux ELF native and exact Node-and-script npm installations now record
the bytes and modes of their declared runtime contents, including physical link
targets. Git metadata is excluded. A companion link redirected to equal bytes at
another physical path requires preparation again. Unknown, script and parameter
wrappers retain their selected-file checks and are explicitly noncacheable.

Preparation and admission require `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD`,
`LD_AUDIT` and `LD_LIBRARY_PATH` to be unset or empty. Task CLI children remove
these ambient loader inputs. Configure dependency bindings with `--runtime`
instead of relying on undeclared startup modules or library search paths.

Cache lookup, H0, retained checkpoints and progress recovery re-admit the current
installation and require matching recorded evidence. Old partial records remain
readable but cannot authorize score reuse. Prepare a new Harness copy and use a
fresh run ID to measure it; do not rewrite historical manifests or receipts.

The content scope covers declared application/runtime files. Host OS libraries,
shell tools and compatibility with a task image remain platform capabilities,
not a hermetic machine image or proof that a model-backed task will succeed.

The task defines its own deliverable; the adapter does not require every task to
modify a file. Native delegation depends on the installed CLI and its configuration.
When a child session is detected but complete child usage is unavailable, the trace
retains observed parent usage and marks the total unknown. Use a fresh baseline
after this adapter change.
