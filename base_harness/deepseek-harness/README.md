# DSH Harness runtime preparation

The shipped manifest contains no host installation paths. Prepare the Harness
copy that will be measured with an existing, built DSH checkout and an isolated
Node installation compatible with the task images:

```sh
python3 tools/configure_dsh_harness.py /path/to/harness-copy \
  --dsh-root /path/to/deepseek-harness --node /path/to/node-prefix/bin/node
```

Preparation validates `apps/cli/lib/bin.js`, Node, and every package target in
`dsh_farm.tsv`, then records the canonical installation, exact Node executable and Node prefix in
`harness.json.runtime_paths`. The platform binds those directories read-only in
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

`HG_AGENT_DSH` and `HG_AGENT_NODE` still override the selected CLI and Node.
`HG_AGENT_DSH_ROOT` can select the standard CLI under another root; specifying both
root and CLI requires them to agree. Changes to an installation also require
preparing matching `runtime_paths` before sandbox execution.

`HG_AGENT_DSH_COMMAND` remains an argv-style wrapper override for invocation and
configuration inspection. Its executable is fixed to an absolute path while
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
loader probe and plugin farm use that same selected installation. A scratch HOME
is still required; the existing operator-home refusal and model patch remain in
effect.
