# OpenCode Harness installation preparation

The shipped Harness is a portable adapter for an external, prepared OpenCode v1
source tree and Bun executable. It does not vendor the monorepo or install packages
during measurement. Copy the adapter, then prepare that copy:

```sh
cp -a base_harness/opencode /path/to/opencode-harness-copy
python3 tools/configure_opencode_harness.py /path/to/opencode-harness-copy \
  --source /path/to/opencode --bun /path/to/bun
```

The source can also be selected with `HG_AGENT_OPENCODE_SOURCE`. Bun selection is
explicit `--bun`, then `HG_AGENT_OPENCODE_BUN`, `HG_BUN_BINARY`, then discovery on
PATH during preparation. The tool reads the main checkout `.env`, a worktree
`.env` if present, and the process environment in that order; arguments win.
Source and executable aliases select their physical installations once.

Preparation checks the installed v1 package and lockfile, hashes source and all
bound dependency contents, permissions and link layout, and runs version and
projection checks inside a network-isolated native sandbox with private HOME/XDG/configuration. It never
imports candidate Python or executes source code in the host namespace. It
requires bubblewrap and an already installed dependency tree. It does not upgrade
OpenCode, populate caches or run package lifecycle scripts.

`harness.json` atomically records `_opencode_runtime` and `runtime_paths`. The
source directory and exact Bun executable are mounted read-only; the tool never
infers a binding of the executable's bin directory or a shared installation parent.
Use repeatable `--runtime /path/to/dedicated-dependency` for external dependency
links. Installations hidden under `/tmp`, broad HOME/system/shared temporary
directories and overlap with the platform or Harness copy are refused.

Tasks and the platform's Bun admission/cache checks use the same fixed source,
binary and identity. Changing PATH does not select another installation; a
conflicting override or changed prepared source requires preparing again. Native
and container-state execution continue to use the existing Bun runtime contract.
Task HOME/XDG/configuration and session state remain private.

Candidates that carry editable source in their own `opencode/` directory retain
the existing offline preparation route, `tools/configure_bun_harness.py`. Their
workspace links belong to that candidate and dependencies stay out of checkpoints.
The external-installation tool refuses candidate/installation overlap instead of
copying a large external tree into every checkpoint. An unprepared shipped
template cannot run until an installation is selected.

Legacy Harnesses with manually declared external source retain the existing Bun
route. Their dependency contents are unverified, so they neither read nor populate
the measurement cache. Use explicit preparation to obtain a verified installation
identity and cache reuse. Standalone interface diagnostics may select an undeclared external tree with
`HG_AGENT_OPENCODE_EXTERNAL_SOURCE=1`; the trace marks that mode and formal
admission rejects it. This flag does not authorize a measured run.

Schema 2 fingerprints every file, directory, permission mode and symlink in the
bound installation, including `node_modules`, native modules, data and extra
runtime directories. Cache/VCS-named directories are included. Content is read
again during admission and execution; unchanged sizes or timestamps do not bypass
the check. The installation must remain unchanged after preparation. A changed
dependency invalidates the old record before measurement-cache lookup; preparing
again records a new identity and cannot reuse the old score.

Schema 1 metadata-only records are refused. Existing prepared copies need the
updated adapter helper and must be prepared again.

The version probe establishes startup, not model-backed task quality. Selecting
another major OpenCode release needs an adapter compatibility change; the runtime
selection tool does not silently upgrade the v1 adapter.
