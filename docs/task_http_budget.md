# Optional strict task HTTP admission

Ordinary runs retain their existing opaque model byte pumps. A budget profile can
opt in with `task_http.mode: "strict"`. This uses the production native AF_UNIX
and container model gateway handlers; unsupported task routes refuse admission.
Train and audit use distinct ledger files (`*.train.json`, `*.audit.json`) with
the original arm identity. Task calls use the `task` ledger role. Operator limits
remain in the existing profile; this feature supplies no token or call limits.

Example policy fragment (replace all placeholders with operator-approved values):

```json
{
  "task_http": {
    "mode": "strict",
    "tokenizer_contract": "approved-model-template-v1",
    "tokenizer": {
      "argv": ["/absolute/tokenizer", "--contract", "approved-model-template-v1"],
      "sha256": {"/absolute/tokenizer": "<sha256>"},
      "models": ["<exact-model-identity>"],
      "timeout_s": 30
    }
  }
}
```

The tokenizer is an operator-trusted local program, not an actor tool. Pin its
executable and any script/file arguments in `sha256`; model/template resources
used internally by that program are the operator's contract responsibility.
Its stdin JSON contains `contract_id`, `model`, `operation`, and the complete
request `payload`. Its stdout must be one JSON object containing matching
`contract_id` and `model`, `exact: true`, and nonnegative integer `prompt_tokens`.
The program receives a minimal environment without provider credentials, runs
without a shell, and must count the actual provider's complete input template,
including tools and other serialized fields. Hash and response assertions verify
identity; they do not independently prove tokenizer accuracy. No estimator is
provided. Missing or mismatched contracts refuse before upstream socket creation.
A driver-owned callback with the same counting responsibility is available for
controlled integrations and synthetic tests.

Initial support is HTTP/1 JSON generation requests using one Content-Length,
with explicit `max_tokens`/`max_completion_tokens` for chat/completions or
`max_output_tokens` for responses. Multiple completions, ambiguous lengths,
chunked requests, Expect, and encoded request bodies refuse. Accepted request
bytes and response bytes (including chunk framing and SSE) are forwarded intact;
no output bound, model identity or payload field is rewritten. Every request on
a persistent connection has a separate durable reservation before connection.
Response parsing tees bytes and reconciles authoritative final usage. Cache detail
fields are retained as raw usage and do not reduce inclusive prompt/input totals.
Unsupported usage semantics remain unknown rather than receiving fabricated cost.

Missing/invalid usage, disconnects and provider errors retain unknown reservations.
Unknown usage blocks later reservations, including zero-token call-only requests.
Gateway receipts expose structured `budget_stops` with a reason and optional
request ID. Completed calls keep their measured costs; incomplete evaluations
must remain unmeasured or partial. The gateway test suite uses only synthetic
loopback HTTP fixtures and does not establish compatibility with every provider.

Strict routing currently requires native `sandbox=True` with declared
`env.network="none"`, or a container route to a host-local plain HTTP endpoint.
Unsandboxed, cooperative, container-state and mock-backed strict task runs refuse
before actor execution. These restrictions do not change ordinary runs, including
ScienceWorld runs without the optional strict profile. No default token limits,
call limits, model choices or experiment configuration are introduced.

Strict transport support bounds request start/header lines to 64 KiB, total
request headers to 256 KiB, and JSON request bodies to 16 MiB; oversized requests
are refused intact before upstream construction, never truncated or rewritten.
Usage observation retains at most 1 MiB of a nonstream JSON response or one SSE
line. An observation overflow makes usage unknown while forwarding all response
bytes unchanged. These parser bounds apply only to this optional strict gateway.

Gateway `account()` waits for active reservation settlement rather than waiting
for persistent connection closure. After five seconds it cancels pending upstream
I/O and records an explicit unknown settlement stop, so seeing the final HTTP byte
cannot publish an evaluation before durable accounting. `admission_stop()` is a
read-only check for exhausted call/token counters, unknown usage and elapsed wall
limits; reaching a cap preserves completed measurements while preventing future
planned work from purchasing another meta session.

Production driver bindings also include the split in the durable dispatcher run
identity (`<run_id>::task:<split>::<arm>`); ledger paths alone are not the identity
boundary. Original run and arm metadata remain explicit. Existing operator
request/wall timeout reservations bound connection and streaming duration using
an absolute request deadline. Timeout or incomplete Content-Length responses keep
usage unknown, including when some response bytes were already forwarded.

SSE usage is authoritative only with a terminal `[DONE]` after its final usage
record, or a `response.completed` event carrying a completed response and its own
usage. Usage followed by EOF without a terminal event, usage followed by later
nonusage data, invalid/empty usage, or truncated HTTP framing remains unknown.
All received response bytes are still forwarded unchanged.
