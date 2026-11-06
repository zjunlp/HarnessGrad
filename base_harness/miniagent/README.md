# MiniAgent for HarnessGrad

This is FlashRSI's own workspace-driven Python MiniAgent, **not** mini-SWE-agent.
The 19 native files and their SHA-256 hashes are pinned in `PROVENANCE.json` to
[RUC-NLPIR/FlashRSI](https://github.com/RUC-NLPIR/FlashRSI/tree/dea1b1d0285e3328234a1e344562bb9e54ce14f7/flashrsi/agents/miniagent).
The native runtime/resources are copied without modifications and remain part
of the candidate tree, so a method can change code, prompts, tools, middleware,
sub-agents and skills. The upstream framework adapter and process entrypoint are
excluded; `agent.py` and `bridge.py` implement the HarnessGrad boundary instead.
The Apache 2.0 upstream license is included as `LICENSE.upstream`.

`agent.py --task task.json --workdir work` starts an independent process with a
fresh HOME/config/cache/temp directory. It calls the candidate's `runtime.agent.run`
and gives its tools the task workdir. The harness source can be read-only.
The native shell and file editing tools still execute, and the platform separately
verifies the resulting files. Changed `answer.txt` wins over final chat text;
declared file submissions must exist and cannot escape the workdir.

All native model calls—including side-model/reader/finalization calls—use
`HG_AGENT_BASE_URL`, `HG_AGENT_MODEL` and `HG_AGENT_API_KEY`. Calls are counted
at the OpenAI SDK transport boundary. Missing provider token usage remains
unknown, rather than being reported as zero or as a partial total.
The native loop selects Responses for its recognized OpenAI model names and
Chat Completions otherwise; the chosen endpoint must support the required wire.
The provider's response body, native events, actual shell return code/output,
submission and final usage are written to the standard `trace.jsonl` dialect.

`HG_AGENT_MAX_STEPS` limits native turns; `HG_AGENT_MAX_OUTPUT_TOKENS`
(`HG_AGENT_MAX_TOKENS` fallback) caps each request. `HG_AGENT_CONTEXT_WINDOW`
also imposes a conservative UTF-8 byte bound on each complete request body;
this is intentionally stricter than a tokenizer and may refuse a prompt the
model could fit. The task deadline is a relative number of seconds. The adapter
stops before it and reaps detached Linux shell descendants at completion.
Native web tools are disabled by the supplied config: a later web integration
must map those services to the platform's selected routes, not inherit provider
credentials or private configuration from the operator.

`HG_AGENT_BACKEND=mock` uses an HTTPX mock transport inside the actual OpenAI
SDK. It executes the real native tool loop, writing `MINIAGENT_MOCK` through a
real shell action and reading its observation before a final model turn.
It opens no socket, uses no network, and performs no benchmark computation.

The manifest declares a **managed CPython 3.12 environment for sandboxed native
`files` tasks**. `requirements.txt` is the direct dependency input;
`requirements.lock` pins the complete wheel dependency closure. Neither the
platform's packages nor a legacy overlay supplies missing dependencies. Both the
adapter and its native worker use the bound Python with isolated startup (`-I`).
The 19 upstream files remain unchanged; the lock and process boundary belong to
the HarnessGrad adapter.

Use an activated platform environment as described in the top-level README.
CPython 3.12, uv and bubblewrap must already be installed. For initial preparation
on a machine without cached wheels, explicitly allow wheel downloads:

```bash
python tools/configure_harness.py --harness base_harness/miniagent --fetch
python tools/configure_harness.py --harness base_harness/miniagent --list --json
python tools/validate_harness.py base_harness/miniagent
HG_AGENT_BACKEND=mock python driver.py --harness miniagent --dataset demo \
  --tasks t01 --rounds 0 --run-id miniagent-smoke --no-summary \
  --runs-root /tmp/hg-miniagent-runs --work-root /tmp/hg-miniagent-work
```

Omit `--fetch` when the wheels are already cached. The driver also prepares frozen
candidates offline before measurement. Prepared dependencies live outside the
candidate in `~/.cache/harnessgrad/harness-runtimes` (override with
`HG_HARNESS_RUNTIME_ROOT`). Source edits reuse dependencies; lock edits prepare
another artifact. Each task shares read-only dependencies and gets private state,
HOME and temporary files. Checkpoints, cache and H0 bind the exact source and
artifact; retain those artifacts while saved runs reference them.

The mock control executes the real native shell loop and writes `MINIAGENT_MOCK`;
the demo's expected answer differs, so its valid score is zero. This verifies
execution and isolation, not task ability. The validator's legacy `--smoke` path
is refused for managed Harnesses; use the sandboxed driver control above.
Container/`exec`, workers, external `runtime_paths` and `--no-sandbox` are
unsupported. See [managed Python dependencies](../../docs/writing_a_harness.md#21-managed-python-dependencies)
for the complete lifecycle and compatibility limits.
