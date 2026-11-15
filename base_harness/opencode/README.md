# OpenCode Harness

The adapter runs the released OpenCode CLI from a prepared Harness copy.
On Linux x86-64 with Bubblewrap, install the verified npm releases with:

```sh
harnessgrad harness install opencode --version 1.19.5
HARNESS_DIR=$(harnessgrad harness install opencode --version 2.3.0)
```

Use the printed directory with `harnessgrad run --harness-path`. Omitting
`--version` selects the latest verified release, currently `2.3.0`.
`--install-root` selects the dedicated store, which defaults to
`harnessgrad/base-harnesses` under the user data directory.

The installer uses Node.js 24.22.2 and npm 11.19.1 to install the exact native
package. It checks the npm archive digest and retains the lockfile.
`1.19.5` uses `opencode-linux-x64`. The verified 2.x releases use
`@opencode/cli-linux-x64`.
Tasks invoke the native executable directly. The prepared manifest records
the executable and its package metadata as read-only bindings, together with
their hashes and modes. Preparation checks the version in a network-isolated
sandbox. Explicit unverified v1 or v2 releases are attempted with a warning.

To bind an existing npm native installation, copy the Harness and run
`python3 tools/configure_opencode_harness.py "$HARNESS_DIR" --opencode "$OPENCODE_BIN"`.
`OPENCODE_BIN` points to the Linux x86-64 ELF executable at `bin/opencode` inside
its native npm package, with `package.json` at the package root. Prepare a fresh
copy for a different installation to keep the original binding available for
earlier experiments.

Both release series use `run --format json` for tool events. The adapter uses
each series' provider configuration and session export to record the final
answer and usage. It keeps OpenCode's configuration and state local to the
task and passes the task's model endpoint and credential. Set
`HG_AGENT_OPENCODE_WIRE` to `responses` for a Responses endpoint. The default
transport is Chat Completions (`chat`). The platform records the effective
transport in the measurement's runtime identity. Cache reuse, H0 reuse, and
resume require a matching transport. If the recorded runtime identity omits
the transport, start a new run and measure again.

To resume an experiment with a schema 2 OpenCode source binding, use the
original platform revision and source binding. Start a new run when moving
that experiment to an npm release.

The task prompt does not prohibit native subagents. Parent and child sessions are
recorded separately; child text cannot replace the parent answer. For 2.x, token
usage is combined only when the complete session tree is available. The 1.x CLI
can omit auxiliary requests from exports, so delegated total usage is marked
unknown even when partial usage is available. Delegation can increase time and
cost. Use a fresh baseline after this adapter change.
