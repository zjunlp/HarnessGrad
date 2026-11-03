# DSH Harness runtime preparation

The shipped manifest contains no host installation paths. Prepare the Harness
copy that will be measured with an existing, built DSH checkout and compatible
Node executable:

```sh
python3 tools/configure_dsh_harness.py /path/to/harness-copy \
  --dsh-root /path/to/deepseek-harness --node /path/to/node
```

Preparation validates `apps/cli/lib/bin.js`, Node, and every package target in
`dsh_farm.tsv`, then records the canonical installation, exact Node executable and Node prefix in
`harness.json.runtime_paths`. The platform binds those directories read-only in
both native and container sandboxes. The exact executable binding keeps symlinked or
nonstandard Node names from falling back to another binary on PATH. Preparation changes the manifest and its
candidate identity; perform it before creating an experiment record. It does not
install dependencies or prove that the CLI can boot a profile.

The farm stores package names and targets relative to the selected DSH root.
Each task gets real writable profile directories and links into that installation.
Missing dependencies, targets escaping the checkout, or an existing farm from a
different installation cause an error rather than reusing another installation.

`HG_AGENT_DSH` and `HG_AGENT_NODE` still override the selected CLI and Node.
`HG_AGENT_DSH_ROOT` can select the standard CLI under another root; specifying both
root and CLI requires them to agree. Changes to an installation also require
preparing matching `runtime_paths` before sandbox execution.

`HG_AGENT_DSH_COMMAND` remains an argv-style wrapper override for invocation and
configuration inspection. An opaque wrapper owns its dependencies and profile
setup; the adapter skips its plugin farm and loader probe unless a DSH CLI/root is
also supplied explicitly. Wrapper dependencies must be declared in `runtime_paths`
when they live outside the sandbox's normal system directories.

The selected argv is shared by task execution and configuration inspection. The
loader probe and plugin farm use that same selected installation. A scratch HOME
is still required; the existing operator-home refusal and model patch remain in
effect.
