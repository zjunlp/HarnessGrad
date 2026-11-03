# OpenClaw / MiniAgent HarnessGrad acceptance

This worktree adds two launchable task harnesses:

- `base_harness/miniagent`: the native Python MiniAgent workspace from upstream, pinned to commit `dea1b1d0285e3328234a1e344562bb9e54ce14f7`, with the HarnessGrad task/answer/trace bridge.
- `base_harness/openclaw`: a HarnessGrad wrapper around the installed OpenClaw CLI `2026.3.13 (61d171a)`, upstream build commit `61d171a`. upstream main was checked and does not contain an OpenClaw adapter; its registered harnesses include MiniAgent, Claude Code, Codex, Pi and Hermes.

Both manifests declare `files` only. MiniAgent's runtime is candidate-owned and editable. OpenClaw's Node executable and installed package are read-only `runtime_paths`; the candidate-owned editable surface is the wrapper, prompt, config and mock fixture. Neither manifest claims container/`exec` support without separately prepared dependencies.

## Checks

Run from `/tmp/hg-openclaw-miniagent`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/validate_harness.py base_harness/miniagent --smoke
PYTHONDONTWRITEBYTECODE=1 HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/openclaw --smoke
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_openclaw_harness.py \
  tests/quality/test_miniagent_harness.py \
  tests/quality/test_new_harness_platform.py \
  tests/quality/test_trace_contract.py \
  --basetemp=/tmp/hg-two-harness-final
```

Observed result: both smoke commands passed and the focused suite passed **48 tests in 25.11 seconds**.

The platform acceptance test runs both harnesses through `stage_workspace`, Git checkpointing and `run_one(sandbox=True)`. It verifies that candidate edits execute, the candidate is read-only during a task, platform files and verifier-only inputs remain hidden from the actor, the verifier scores the produced artifact, and the unified trace contains identity, turns, actions, observations, usage and terminal state without leaking the test secret.

Both harnesses also completed a one-task `demo-smoke` driver run under bubblewrap. The resulting mock markers (`MINIAGENT_MOCK` and `OPENCLAW_MOCK`) intentionally do not match the fixture's `alpha` answer, so both platform scores were **0.000**; `tools/validate_run.py --certified` certified both records. This confirms the full launch, trace and record path, and is not evidence of task-solving quality.

The smoke provider is model-free. MiniAgent uses the native OpenAI SDK loop with an HTTPX mock transport; OpenClaw uses the installed native CLI with a local SSE provider. Both execute a real shell tool and write a deterministic mock artifact. Mock output is an interface check, not a benchmark result.

The existing `tests/quality/test_harness_decoding.py` inventory was already stale on the parent commit: it observed 12/15/27/6/30 subprocess sites for `base_harness`/`eval`/`data`/`harnessgrad`/`tools`, while the checked-in expectation was 8/10/20/6/22. This branch adds two UTF-8-safe text subprocess sites under `base_harness`, so current counts are 14/15/27/6/30; its expected counts remain stale and that test still fails. The two added sites specify `errors="replace"`. The inventory cleanup remains separate from this harness integration.
