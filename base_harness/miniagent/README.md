# MiniAgent for HarnessGrad

This is upstream's own workspace-driven Python MiniAgent, **not** mini-SWE-agent.
The 19 native files and their SHA-256 hashes are pinned in `PROVENANCE.json` to
[RUC-NLPIR/upstream](PROVENANCE.json).
The native runtime/resources are copied without modifications and remain part
of the candidate tree, so a method can change code, prompts, tools, middleware,
sub-agents and skills. The upstream framework adapter and process entrypoint are
excluded; `agent.py` and `bridge.py` implement the HarnessGrad boundary instead.
The Apache 2.0 upstream license is included as `UPSTREAM-LICENSE.txt`.

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

Initial declared environments are **files only**. `requirements.txt` pins the
Python dependencies; use the existing HarnessGrad dependency-overlay workflow
before sandbox execution. Container/`exec` support needs a separately prepared
runtime with matching Python ABI and a reviewed environment declaration.

```bash
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/miniagent --smoke
```
