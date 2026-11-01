# OpenClaw harness

This is a wrapper around **OpenClaw 2026.3.13 (`61d171a`)**, not a FlashRSI
OpenClaw port. FlashRSI's inspected `main` registers MiniAgent, Claude Code,
Codex, Pi and Hermes; it does not contain an OpenClaw adapter.

`agent.py --task task.json --workdir <dir>` runs the real installed CLI via
`node openclaw.mjs agent --local --json`. It does not start a gateway daemon,
connect messaging channels or read the operator's OpenClaw state. Each task has
fresh HOME/XDG/config/auth/session directories which are removed on exit.

## Editable surface

The candidate contains the adapter (`agent.py`), `prompt.md`, `config.json` and
its mock fixture (`mock_model.py`). Changes to these files affect the run.
OpenClaw's installed compiled package and Node executable are **external read-only
dependencies**, named by `runtime-identity.json` and `harness.json.runtime_paths`.
The package is 532 MB on this host and is not copied into checkpoints. This
adapter therefore does not expose the upstream agent loop as editable candidate
source, unlike the vendored MiniAgent harness.

`config.json` supplies tool policy and native behavior settings. The adapter
replaces the model/provider/credential/workspace/deadline fields with the
platform's `HG_AGENT_*` contract. Only one custom provider is configured, with
no fallback model. The default tool surface is `read`, `write`, `edit`, `exec`;
remote channels, browser and subagents are absent. OpenClaw's exec approvals are
off **inside the HarnessGrad task sandbox**, which is the filesystem boundary.
The manifest declares **files only**: Node/OpenClaw would need separate
provisioning in a container before declaring `exec` support.

## Model-free validation

From the repository root:

```bash
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/openclaw --smoke
pytest -q tests/quality/test_openclaw_harness.py --basetemp=/tmp/hg-openclaw-tests
```

The mock runs the native CLI and native `exec` tool against an ephemeral loopback
SSE server. It executes `printf 'OPENCLAW_MOCK\n' > answer.txt`, then finishes.
It does not call a model endpoint and its output is deliberately not a task
solution. The trace identifies the backend as `mock`.

## Runtime and record limits

The installed Node must be at least 22.12. This host's runtime is Node 22.16.0.
`runtime-identity.json` pins the package version and absolute dependency paths;
other hosts must provision the same version and update the declared paths.
The identity records hashes of Node and the package metadata/launcher. It does **not** claim a complete
transitive dependency tree digest; hashing the 532 MB installed tree per task would dominate execution.

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
