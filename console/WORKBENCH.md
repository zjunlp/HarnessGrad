# Workbench editor selection

The six existing assistant names remain visible on `/harness.html`. Only **Codex**
and the **DSH headless editor** have workbench editor drivers. OpenCode, mini-SWE,
MiniAgent and OpenClaw task wrappers are not interchangeable editor protocols;
their choices are marked unavailable. Direct API requests for these four names
return `503 assistant-not-configured` before copying a candidate or starting a
process. They never select Codex as a fallback.

`GET /api/workbench/capabilities` supplies the UI's supported/configured state.
It reads declarations and existing runtime files only. It does not run a CLI,
build, download packages, contact a model or create candidate/session state.
Configured means these checks passed, not that remote authentication or a model
request has been tested. POST performs admission again before copying.

## Codex compatibility

The existing `HG_ASSISTANT_CODEX` path remains accepted. Otherwise the canonical
improver registry's `codex` path, or its PATH selection, is used. This read-only
selection does not run the experimental resolver's verified-version search.
Existing Codex login/provider configuration remains owned by the chosen CLI and
`CODEX_HOME`; this change does not rewrite it or add a universal provider client.

`HG_CONSOLE_ASSISTANT_CMD` retains its old Codex command-prefix behavior, including
Codex JSON input/output and `-C <candidate> -` framing. Without an explicit
`HG_CONSOLE_ASSISTANT_DRIVER`, this legacy override belongs to **codex**, not every
name in the selector. `HG_CONSOLE_ASSISTANT_DRIVER=codex|dsh` declares the override's
owner. A different selected driver does not consume that override. DSH overrides
must name the declared DSH registry launcher; configure a custom installation in
the registry rather than attaching Codex flags to another executable.

Complete native/npm Codex content bindings support installation-scoped resume.
Opaque command prefixes can still run the declared Codex protocol, but their
external runtime cannot be completely identified: the response and UI explicitly
say that saved sessions are kept but not resumed. Successful commands must emit
the declared Codex JSON event protocol; plain text is not silently accepted as a
Codex session.

## DSH configuration and execution

Use the canonical `harnessgrad/improvers/improvers.json` declaration or the
existing `HG_IMPROVER_CONFIG` override. The `dsh` entry must declare CLI kind,
`argv_style=dsh`, its existing launcher, prepared DSH root, Node executable,
role-specific home, `headless` profile, provider/model and key-environment name.
The declaration reader reuses the canonical path/configuration rules without
executing `build_command` or `--version`. Missing files, package-farm targets,
unsupported wire/profile declarations and missing selected credentials are
reported before copying the candidate.

The selected credential comes from assistant settings or the entry's named key
environment. Operator credential bags, saved model settings, unrelated profiles
and sessions are not copied. DSH runs with an owned private home and pin outside
the downloadable candidate root. Its package farm comes from the canonical DSH
runtime declaration. Only the selected provider/model configuration and key
reference enter the pin; the key stays in the child's environment.

The argv uses DSH's native `--profile`/`--patch` and positional-task contract,
with the candidate as cwd. The bundled launcher's shared parent-directory pin is
not used. A declared custom endpoint uses the existing openai-completions patch
vocabulary; a generic assistant URL must match that declaration. This driver does
not infer another wire protocol or import a solver wrapper to impersonate an
editor. Temporary home/pin state is removed on success, timeout and exception.

DSH returns a bounded plain transcript and is explicitly **one-shot** here;
`session` is null. It never consumes or fabricates a Codex thread ID. The existing
single follow-up when no file changed can run in the same owned request home,
without claiming cross-request resume.

## Saved sessions and failures

The thread file retains legacy candidate/string entries and adds versioned
binding scopes. A legacy session can be adopted once, only by the first complete
Codex binding for that candidate. New sessions are keyed by candidate, driver and
actual binding digest. Changing CLI path/content, runtime closure or selected
configuration does not reuse another binding's thread. Old scopes remain stored;
they are not deleted when another editor is selected. File locking and atomic
replacement protect scope/legacy-owner updates. Existing legacy helper reads and
writes remain format-compatible; bound drivers use the versioned methods.

`new_thread` remains request-local and applies to a follow-up as well: absence of
a new thread ID never revives an old candidate-only session. Process/protocol
failure is explicit. Failed or timed-out edit copies remain available under the
returned candidate name and are not silently reused. This is still a host editor
integration, not a new assistant sandbox or a complete implementation of all six
CLI choices.
