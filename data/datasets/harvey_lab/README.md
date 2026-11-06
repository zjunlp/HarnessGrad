# Harvey LAB: native file deliverables and official dual judges

This adapter transports the complete native Harvey LAB file/rubric protocol from
upstream `e9c7667945f959d62da27204649a82f94d1b6705`; its official evaluator is
`lab-core==1.2.0`, wheel SHA-256
`5bfd70664a99c16a97a35bf61a3845a9890477168d11978d58864f986c9f53ba`, from Harvey
LAB revision `f93ae2ac2831e65892fea83094cfb50149bc2e59`.

Tasks give the actor only native instructions and the selected public `documents/`
files. It must produce deliverable files in `output/`; a final answer/chat message
is not the scored artifact. Native criteria, `task.json`, both provider credentials
and grading reports remain private. The platform exports regular single-link
output files into an isolated grading root after actor exit. Symlinks, hardlinks,
devices, changed-while-read artifacts, excessive counts and bytes are rejected.
The trusted scorer executes `evaluate_run_dual`, not a single-model substitute.

Native per-judge all-pass flags are averaged, so `all_pass_rate` is exactly
0, 0.5 or 1. `criterion_pass_rate` and the conjunction `all_pass` are retained.
Both judges must complete every criterion; provider failures, parse errors,
truncation or missing dependencies are unmeasured, never ordinary score zero.
The SDK boundary rejects Anthropic final responses without `end_turn` and OpenAI
Responses without `completed`, preserving native prompts and request parameters.
This is a stricter HG completion guard; it does not replace the official rubric
or synthesize model verdicts.
No actor-supplied `correct`/rubric/verifier event is accepted.

## Explicit prerequisites

Prepare native data through `tools/prepare_native_research.py`. Preparation
copies only public documents separately from private cases and records each file
digest. The original task/documents layout is preserved. A local data override is
recorded as local custody, not authenticated acquisition of the upstream release.

Provision an independent Python 3.12/3.13 runtime containing the pinned official
wheel, `anthropic==0.88.0`, OpenAI SDK, Google SDK and native document dependencies.
A startup probe checks the evaluator/provider-helper bytes against upstream's
pinned wheel inventory. Provision `pandoc>=3.5`; DOCX conversion stays native.
Neither imports nor actor runs install dependencies or download models.

All settings are trusted host environment variables. Their `HG_JUDGE_` prefix is
removed by the existing HarnessGrad actor environment filter:

```text
HG_JUDGE_HARVEY_PYTHON=/absolute/runtime/bin/python
HG_JUDGE_HARVEY_PANDOC=/absolute/pandoc
HG_JUDGE_HARVEY_ANTHROPIC_MODEL=claude-<local-provider-model-id>
HG_JUDGE_HARVEY_ANTHROPIC_BASE_URL=http://127.0.0.1:<port>
HG_JUDGE_HARVEY_ANTHROPIC_API_KEY=<local-provider-key>
HG_JUDGE_HARVEY_OPENAI_MODEL=gpt-<local-provider-model-id>
HG_JUDGE_HARVEY_OPENAI_BASE_URL=http://127.0.0.1:<port>/v1
HG_JUDGE_HARVEY_OPENAI_API_KEY=<local-provider-key>
HG_JUDGE_HARVEY_PARALLEL=6
HG_JUDGE_HARVEY_TIMEOUT_S=1800
```

Both endpoints must be explicitly configured at loopback/private addresses.
Official public paid endpoints and implicit SDK endpoint defaults are disabled.
Model names retain provider identity because the official evaluator uses Anthropic
Messages streaming for Claude and OpenAI Responses for GPT. The standard upstream
pair is `claude-sonnet-4-6` and `gpt-5.5`; any other pair is recorded as `custom-dual`
and must not be presented as the standard LAB leaderboard metric. A local endpoint
is not proof that the underlying model is identical to a named hosted model.

## Validation limits

Earlier documentation described `tests/quality/test_upstream_harvey.py` as an
authored control using the pinned official evaluator, native provider SDKs and
local scripted HTTP/SSE responses. It reported pass/pass -> 1, pass/fail -> 0.5,
fail/fail -> 0, and checks of actor-file export and post-exit verification. The test
file is no longer present in this checkout, and its receipt has not been independently
verified here. These descriptions are not measured legal-task capability or production
H0 authorization.
