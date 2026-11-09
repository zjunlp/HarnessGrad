# OpenClaw harness

Harness adapter `0.6.0` was verified with **OpenClaw 2026.11.2 (`b556fdf`)** and
**2026.3.13 (`61d171a`)**. The checked-in Harness is a portable adapter template. OpenClaw
and Node are external read-only dependencies. Each reviewed release selects an
adapter profile for its configuration and session format.

`agent.py --task task.json --workdir <dir>` runs the real installed CLI via
`node openclaw.mjs agent --json`, with a private task-local loopback Gateway
for delegation. The older profile uses `--local`; the newer profile uses its
private Gateway. It does not import the operator's Gateway or state. Each task has fresh HOME/XDG/config/auth/session directories
which are removed on exit. Gateway startup and child work count against the task deadline.

## Prepare a local installation

On Linux x86-64 with Bubblewrap and network access, install a dedicated copy
with the bundled HarnessGrad command. Both supported releases use Node.js
24.22.2 and npm 11.19.1.

```sh
harnessgrad harness list
harnessgrad harness install openclaw
```

The installer defaults to the latest verified release in `harnessgrad harness list`,
currently `2026.11.2`. It reports the selected version on stderr. Use `--version`
to select an exact `YYYY.M.D` release. An unlisted release produces a warning
on stderr and is tried with the latest verified release's adapter profile.
The binding records the exact release and its unverified status. Its config
and session format must work with that profile for evaluation to succeed.

The command prints the prepared directory for `harnessgrad run --harness-path`.
Use `--install-root` to choose a dedicated runtime store outside the platform
checkout and `/tmp`. The default is `harnessgrad/base-harnesses` under the user
data directory. The installer retains the npm lockfile and rechecks the binding
when reusing an installation. It uses `npm ci --ignore-scripts` to install from
the lockfile. For 2026.11.2 and unverified releases, it then targets the locked
OpenClaw package directory with `npm rebuild` to run that package's lifecycle
scripts in the installation sandbox. Verified releases use a pinned package
digest. For other releases, npm checks the archive against the digest recorded
in the lockfile for the exact registry tarball.

The checked-in template carries the portable identity of 2026.3.13. To bind
an existing installation of that release, copy and prepare the template:

```sh
cp -a base_harness/openclaw /path/to/openclaw-harness-copy
python3 tools/configure_openclaw_harness.py /path/to/openclaw-harness-copy
```

Preparation discovers `openclaw` and `node` on PATH once. To choose an installation:

```sh
python3 tools/configure_openclaw_harness.py /path/to/openclaw-harness-copy \
  --openclaw /path/to/openclaw --node /path/to/node
```

To bind a different installed release, add `--version` with its exact version.
Preparation warns for unverified releases and checks the package metadata and
CLI output against that selection.

The corresponding optional settings are `HG_AGENT_OPENCLAW` and `HG_OPENCLAW_NODE`.
The tool reads the main checkout `.env`, a worktree `.env` if present, then the
process environment; explicit command-line choices take precedence. An invalid
explicit choice is an error. Preparation does not install or upgrade software.

The selected launcher must resolve to the package's own `openclaw.mjs`. Package-root
and launcher aliases are supported, and resolve to the real installation. Node is
bound as an exact executable file, including system Node; its bin directory or NVM
prefix is not mounted. The package gets its own directory binding. Preparation
recursively collects installed `dependencies`, installed `optionalDependencies`
and installed peers using each package's nearest ancestor `node_modules`. This
covers nested and hoisted npm layouts, scoped packages and npm aliases. Optional
declarations override required declarations of the same name; missing optional
packages/peers are allowed, while missing required dependencies fail preparation.
Each selected package's metadata is pinned, including nested versions already
covered by a parent package binding. Shared `node_modules` roots and unrelated
packages are not automatically mounted.

For nonstandard or undeclared dependencies, add explicit dedicated bindings:

```sh
python3 tools/configure_openclaw_harness.py /path/to/openclaw-harness-copy \
  --runtime /path/to/dedicated-dependency
```

The preparation-only setting `HG_OPENCLAW_RUNTIME_PATHS` accepts a JSON array of
path strings, for example `'["/path/to/dedicated-dependency"]'`. Repeatable
`--runtime` arguments replace that list. The same setting is accepted by the
real-CLI mock test fixture, which takes `HG_OPENCLAW_TEST_VERSION` to select a
reviewed release. Additional aliases select physical dependencies once.
Linked package targets can be collected automatically when their lookup links
are inside an existing binding. npm-link/pnpm layouts with lookup links outside
those bindings require an explicit directory containing the links and must pass
the native projection check. A launcher or `package.json` symlink escaping its
package is refused.

The tool checks package and CLI versions, and runs Node/OpenClaw version probes
in a network-isolated native sandbox. Verified releases also get a matching
host probe. The package and CLI versions must match the exact selection or the
copy's portable identity when no selection is supplied. The probes use fresh HOME/XDG/
OpenClaw state directories and an environment without personal settings or model
credentials. Bubblewrap is required. Hidden `/tmp` installations, overlap with the
platform or Harness copy, shared temporary roots and broad HOME/system/configuration
directory bindings are refused before writing the manifest.

One atomic `harness.json` replacement records both `runtime_paths` and the
adapter-owned `_openclaw_runtime` identity. `runtime-identity.json` holds the
portable version/source identity and the profile for 2026.11.2. Tasks use the
recorded Node and launcher, verify file hashes, the npm dependency graph,
binding types and symlink targets, and report them in `trace.jsonl`.
Changing PATH does not select a new installation. An override must match the
prepared choice; an unprepared template, changed installation or inconsistent
record produces a preparation diagnostic. Prepare again to select another installation.

With `harnessgrad run --harness-path`, the evaluation entrypoint checks the
supplied copy's prepared binding, including its version identity and file
hashes. It does not consult the installer's release list. A custom copy bound
to another version can pass runtime admission if its binding validates. Its
adapter must also support that version's configuration and session format for
the task to complete.

The 2026.11.2 profile writes the keyed agent configuration and reads transcript
messages through `sessions export-trajectory`. The export stays in the task's
private OpenClaw state. The 2026.3.13 profile reads the native JSONL session.
Both profiles translate replies, completed tool calls and usage into the same
HarnessGrad trace format.

## Editable surface

The candidate contains the adapter (`agent.py`), its `openclaw_runtime.py` and
`task_gateway.py` helpers,
`prompt.md`, `config.json`, portable identity and mock fixture (`mock_model.py`).
Changes to these files affect the run.
OpenClaw's installed compiled package and Node executable are **external read-only
dependencies**, fixed by the prepared `harness.json`. The public template is
unconfigured; prepare a separate candidate and validate its installation and sandbox
on the target deployment before execution. The installed package is not copied into
checkpoints. This
adapter therefore does not expose the upstream agent loop as editable candidate
source, unlike the vendored MiniAgent harness.

`config.json` supplies tool policy and native behavior settings. The adapter
replaces the model/provider/credential/workspace/deadline fields with the
platform's `HG_AGENT_*` contract. Only one custom provider is configured, with
no fallback model. The template uses the native `full` tool profile, with no adapter allowlist;
browser, messaging, plugins, bundled skills and native delegation are not
disabled by the adapter. The task-local Gateway preserves channel, plugin, browser
and service settings from the candidate configuration. Availability still depends
on the installed release, task sandbox networking, compatible browser/plugin
dependencies and explicitly provisioned task-local channel credentials. Operator
state and credentials are not imported automatically. Enabling an external channel
may send task data outside the sandbox; provision only endpoints intended for the
experiment. OpenClaw's exec approvals are
off **inside the HarnessGrad task sandbox**, which is the filesystem boundary.
The manifest declares **files only**: Node/OpenClaw would need separate
provisioning in a container before declaring `exec` support.

Task configuration/model credentials come from `HG_AGENT_*`. Personal
`OPENCLAW_*`, `CLAWDBOT_*`, `PI_*`, `OPENAI_*`, `CODEX_*`, `NODE_OPTIONS` and
`NODE_PATH` settings and other model roles' credentials are excluded from the
CLI environment. Daily use can share the same program installation while each
HarnessGrad task gets its own configuration and state.

## Model-free validation

After preparing a separate candidate, run its smoke check with a mock backend.
`base_harness/openclaw` is a template and refuses execution until configured.
From the repository root:

```bash
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py /path/to/openclaw-harness-copy --smoke
pytest -q tests/quality/test_openclaw_runtime_paths.py tests/quality/test_openclaw_harness.py --basetemp=/tmp/hg-openclaw-tests
```

The mock runs the native CLI and native `exec` tool against an ephemeral loopback
SSE server. It executes `printf 'OPENCLAW_MOCK\n' > answer.txt`, then finishes.
It does not call a model endpoint and its output is deliberately not a task
solution. The trace identifies the backend as `mock`.

Two real-CLI mock checks require the supported installation on PATH or selected
with `HG_AGENT_OPENCLAW` / `HG_OPENCLAW_NODE`; they skip if no installation is
available. The separate runtime tests use synthetic packages with real Node and
bubblewrap to exercise relocation, identity refusal, isolation, answer and trace
behavior. They make no model calls and require Node >=22.12.0 and bubblewrap for
the native controls.

## Runtime and record limits

The prepared identity covers Node, all selected packages' metadata, the launcher and additional
file bindings, plus binding and package-symlink destinations. It does **not** cover
every transitive file in directory bindings. A successful version probe establishes
startup for that installation, not model-backed task quality. Preparation imports
the trusted shipped helper; candidate Python code runs only in the task sandbox.

The platform also validates prepared bindings, recorded files and the npm graph
before evaluating a task set or starting a task, using its own trusted helper.
Changed recorded files are refused before any measurement-cache lookup. Because
the prepared identity does not cover every dependency byte, prepared OpenClaw measurements
neither read nor populate the scoring cache: each evaluation takes a fresh
measurement. A compact verified installation digest is retained in the runtime
receipt, including when execution uses cooperative workers.

The per-task deadline uses the smaller of `HG_AGENT_OPENCLAW_TIMEOUT_S` (default
300 seconds) and the platform's `HG_AGENT_DEADLINE_S`, with a stop margin. Native
shells have that timeout too. `HG_AGENT_TIMEOUT_S` is a per-model-request setting
and is not treated as the task's total deadline. The native CLI currently has no
adapter-enforced count of model turns; task timeout and output/context limits
are the bounds this adapter supplies.

Trace translation uses OpenClaw's native session JSONL, retaining assistant turns,
executed tool arguments, tool output/exit codes, completion and token usage. A
requested tool without a result is recorded as pending, not as an executed action.
The CLI can replace missing provider usage with zeros; real all-zero usage is
therefore conservatively recorded as unknown. Partial missing per-turn usage also
stays unknown. Mock zero usage is explicit and remains zero. Task artifacts take
precedence over final text, and a missing declared file is an adapter failure.

No real-model benchmark quality or container support is claimed by these checks.

Enabling these capabilities changes the harness baseline and may add background
network activity or resource usage.

Native child sessions are retained in the trace separately from the parent answer.
Usage is combined only when the session tree and its usage are available and
complete; otherwise total usage is unknown. Delegation may increase model calls,
time and cost. These adapter changes require a fresh baseline measurement.
