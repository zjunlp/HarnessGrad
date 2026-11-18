# Method stops, attempt accounting, and private improver evidence

These are platform correctness features. Apply the same platform and launcher versions
to every experimental arm. They do not change a Task Harness or a method's parent,
acceptance, rollback, or candidate selection rule. Existing experiment snapshots are
not upgraded in place.

## Structured terminal status

A method response, its `method_reported` mapping, or a failed step can carry:

```json
{"terminal_stop":{"schema":"harnessgrad.terminal-stop.v1",
  "code":"budget_exhausted","scope":"meta_round","retryable":false}}
```

Supported codes are `budget_exhausted`, `budget_usage_unknown`,
`artifact_persistence_failed`, `terminal_status_invalid`, and
`task_budget_stopped`; scopes are `meta_session`, `meta_round`, `meta_run`, and
`task_run`. A malformed explicit terminal
receipt fails closed. Error messages containing words such as “budget” are not
control signals. Ordinary transient failures retain the existing retry limit.
Nonzero method exits can retain a valid JSON control envelope.

CLI launchers receive `HG_IMPROVER_TERMINAL_STATUS_PATH`, a new per-call path.
A launcher may write the same JSON envelope there before exiting. This is an
interface for an explicitly instrumented launcher, not a claim that every existing
CLI emits it. The editor propagates a terminal status and refuses further dispatch
inside the method process. Mode A records the stop without inventing a candidate
measurement or rerunning the whole session.

## Optional between-session token admission

`HG_META_ROUND_TOKEN_LIMIT` and `HG_META_RUN_TOKEN_LIMIT` are optional non-negative
integer limits for new method-session admission. Neither has a default. They do
not replace or silently change an existing per-session/provider budget.

The driver writes `method_attempts.json` before invoking the method. Every attempt
has a unique ID, round, reconciliation status, reported usage and terminal reason.
A method can explicitly report `method_usage.total_tokens` as an observed total;
missing, malformed, incomplete, or uncertain-transport usage remains unknown.
The driver does not reconstruct a provider total by adding normalized cache fields
or estimates. Repeated observations with the same recorded session/attempt identity
are retained individually but not blindly added; ambiguous totals remain unknown,
while per-field maxima are recorded only as numerical lower bounds. Reported input/output/call observations remain separately available.

With a configured limit, prior unknown or admitted-but-unreconciled attempts refuse
the next invocation. The remaining allowance also gates each CLI session and endpoint
parse retry within one method invocation, including across `reset_spent()` phases. Limits and run identity cannot change on reopening the ledger.
A legacy resume without an attempt ledger is not assumed to have spent zero.
Without configured token limits, ordinary-error retry behavior remains unchanged.
An explicit terminal stop always prevents another session, even without a token cap.

**This boundary gates method invocations, not individual HTTP provider requests.**
Already admitted requests/sessions can overshoot. Known overshoot and its lower
bound are recorded; unknown overshoot remains null. A hard per-request reservation
and bounded parallel in-flight overshoot require a production gateway using the
trusted budget dispatcher. Merely initializing `--budget-profile` does not prove
that every task/meta provider request crosses that gateway. Model service
integration and provider raw-usage coverage must be verified separately.

## Complete observed output versus display tails

The editor supplies a host-bound `archive_directory` to the CLI bridge. Observed
stdout/stderr are streamed to private files before the CLI finishes. Full output,
a bounded display tail and a receipt have separate unique `.log` names; each session
retains its own files. The existing method-log collection copies them into
`method_logs/round-N` and refuses symlinks, hard links and nonregular files.

The receipt records original byte/Unicode-character lengths and SHA-256, persisted
length/hash, ordering/representation, capture outcome and completeness. Streaming capture keeps stdout and stderr in separate private partial files, then
combines them in the existing stdout-then-stderr order. Legacy callers retain
the same ordering with the decoded-text representation. If the process is
interrupted before finalization, partial streams remain evidence and are not
claimed to be a complete combined transcript. The legacy
human-readable editor log still keeps its last 200,000 Unicode characters. This is
not a 200 KB claim.

Known credential values are redacted before persistence. Raw and redacted hashes
are distinct; the persisted file is not described as original bytes after redaction.
No complete environment or authentication arguments are archived. Private logs still
need a separate review before publication: exact-value redaction is not a proof that
arbitrary tool output contains no sensitive information.

Complete CLI output is not complete native API/tool-body evidence. Native bodies
are `not_exported` unless the producer explicitly declares them using the
[artifact manifest contract](improver-artifact-manifest.md). Upstream CLI truncation
cannot be reconstructed from a complete capture of what it printed. Missing
coverage stays unknown. A persistence failure is terminal; it cannot buy a second
model session. Historical lost bodies and truncated prefixes are not reconstructed.

## Offline verification scope

Focused tests run real driver subprocesses with a mock Task Harness and a fake method:
ordinary failures retry, terminal signals do not, cumulative known totals survive
reopening, unknown/in-flight usage fails closed, and archive-copy failure does not
start another fake provider session. Separate fake CLI tests exercise long Unicode
output, head usage retention, timeout/exception capture, permissions, link rejection
and credential redaction. None of these tests calls a real provider or establishes
production DSH/provider billing completeness.
