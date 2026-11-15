# DeepSeek Harness installation

On Linux x86-64, this command installs the verified npm release and binds it to
an experiment Harness copy.

```sh
HARNESS_DIR=$(harnessgrad harness install deepseek-harness --version 0.2.2-rc.3)
harnessgrad run --harness-path "$HARNESS_DIR" ...
```

Omit `--version` to select the latest verified release, currently the
`0.2.2-rc.3` prerelease. The installer prints its selection on stderr. Explicit
versions outside the verified list emit a warning and go through the same
installation and sandbox probes.

The installer uses Node.js `24.22.2` and npm `11.19.1` in the experiment store.
Use `--install-root` to choose a store. It locks and installs `@deepseek-ai/dsh`
with npm lifecycle scripts disabled, checks the CLI version, and boots the
headless profile in a sandbox with network disabled. The prepared copy binds
the installed npm tree and exact Node. The npm release resolves its plugins
from that tree and uses a task-local DSH home. The bound Node also decodes
compressed session logs. In this release the Bash session event omits the exit
code, so the trace records `returncode: null` for that event.

## Preparing a source checkout

The shipped manifest contains no host installation paths. Prepare the Harness
copy that will be measured with an existing, built DSH checkout and an isolated
Node installation compatible with the task images:

```sh
python3 tools/configure_dsh_harness.py /path/to/harness-copy \
  --dsh-root /path/to/deepseek-harness --node /path/to/node-prefix/bin/node
```

Preparation validates `apps/cli/lib/bin.js`, Node, and every package target in
`dsh_farm.tsv`, then records the canonical installation, exact Node executable and Node prefix in
`harness.json.runtime_paths` and an exact `_dsh_runtime` record. That record binds
source and Node contents, modes, link targets and the plugin farm declaration.
The platform binds those directories read-only in
both native and container sandboxes. The exact executable binding keeps symlinked or
nonstandard Node names from falling back to another binary on PATH. Preparation changes the manifest and its
candidate identity; perform it before creating an experiment record. It does not
install dependencies or prove that the CLI can boot a profile.

Both runtime installations must be outside `/tmp` and the platform checkout, and
must not contain the platform. Native sandboxes hide those trees. System locations
such as `/usr/bin/node` and `/usr/local/bin/node` are refused: binding their inferred
prefix would replace the task image's own Python, libraries and tools. Use dedicated
directories outside system trees, for example under `~/.local/opt/` or `/opt/node/`.
The check follows symlinks and rejects unsupported paths before changing the manifest.
If `--node` is omitted, the `node` found on `PATH` must satisfy the same requirements.
Use a complete Node distribution with its dependencies; copying a system executable
alone does not establish compatibility with a container's libraries. These restrictions
apply to this task Harness preparation tool; the DSH improver has a separate runtime
binding contract.

The farm stores package names and targets relative to the selected DSH root.
Each task gets real writable profile directories and links into that installation.
Missing dependencies, targets escaping the checkout, or an existing farm from a
different installation cause an error rather than reusing another installation.

For prepared copies, `HG_AGENT_DSH`, `HG_AGENT_NODE`, `HG_AGENT_DSH_ROOT` and
`HG_AGENT_DSH_COMMAND` must resolve to the recorded command. A different CLI,
Node, source file or link target requires preparation again. Add dedicated
dependencies outside those installations with repeatable `--runtime` options.

Unprepared diagnostic copies retain the argv-style `HG_AGENT_DSH_COMMAND`
wrapper route for invocation and configuration inspection. Its executable is fixed to an absolute path while
preserving symlinks, so `venv/bin/python` keeps that virtual environment's packages.
A direct `NODE bin.js` override uses that exact Node
for loader diagnostics, even if the prepared manifest names another interpreter.
An opaque wrapper owns its interpreter; Node-specific diagnostics are skipped.
Supplying a DSH CLI/root explicitly still enables the adapter's validated plugin
farm; without one, the wrapper owns profile setup as well. Wrapper dependencies must be declared in `runtime_paths`
when they live outside the sandbox's normal system directories.
For a Python wrapper, this includes the venv and any base interpreter installation
not already provided by the sandbox.

The selected argv is shared by task execution and configuration inspection. The
source loader probe and plugin farm use that same selected installation. A scratch HOME
is still required; the existing operator-home refusal and model patch remain in
effect.

Prepared copies reject nonempty `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD`,
`LD_AUDIT` and `LD_LIBRARY_PATH`; CLI children remove all five. Linux ELF Node
installations with complete declared contents can authorize score reuse after
fresh admission. Script/unknown interpreters, unprepared diagnostics and old
records without a cacheability declaration require fresh measurements.

The same identity is checked before task-cache, whole-point and H0 reuse, and
before restoring completed progress or checkpoints. Existing historical records
remain readable; prepare a new Harness copy and run ID when evidence is missing.
This binds declared application dependencies, not a hermetic host OS or shell.

The task defines its own deliverable; the adapter does not require every task to
modify a file. Native delegation depends on the installed CLI and its configuration.
When a child session is detected but complete child usage is unavailable, the trace
retains observed parent usage and marks the total unknown. Use a fresh baseline
after this adapter change.
