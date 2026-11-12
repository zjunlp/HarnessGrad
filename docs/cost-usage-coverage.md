# Reported usage and complete evaluation cost

New evaluation results distinguish complete totals from sums of reported usage.
No provider request, response, finish or usage count is synthesized from another
one of those facts. No provider billing or retry policy is changed.

`tokens.input`, `tokens.output` and `tokens.calls` are canonical totals: each is
`null` if any required task/trial lacks that dimension. The corresponding
`*_reported` fields retain known lower bounds. `tasks_reported` keeps its prior
meaning, tasks with a usage object; it does not mean complete token coverage.
`tasks_total`, `trials_total`, `trials_reported` and each dimension's
`*_tasks_known`/`*_tasks_missing` counters state coverage separately. All retained
trial reports contribute to cost, rather than only the final trial's trace.
OpenCode's `reported_steps < completed_steps` means its available token sum is
partial; a successful complete session export can restore coverage. Completed
turn counts remain valid independently of missing token usage.

Curve cost uses `harness_tokens` only when both token dimensions are complete;
otherwise it is `null`. `harness_tokens_lower_bound` keeps observed partial
spend. Calls have the analogous canonical and lower-bound fields. A provider
explicitly reporting zero input and output remains a measured zero, distinguishable
from a completed response without usage. Trace UI summaries preserve that same
distinction.

Environment model brokers already retain logical requests, individual attempts,
`usage_known` and `usage_complete`. Their reported token sum is a lower bound if
any attempt has unknown usage. The runner now preserves that flag, attempt counts
and lower bounds. Environment service recording aggregation also tracks missing
records and each token dimension independently. `env_generation_tokens` becomes
`null` whenever total coverage is incomplete; its lower bound stays separate.
A failed HTTP response can have known provider usage, or unknown usage; a 200
status alone establishes neither valid generation nor known usage. Actor
`usage.calls` counts completed model turns; environment `calls` retains the
broker's logical-request meaning, while `attempts` counts its attempts. These
numbers can legitimately differ and are never substituted for each other.

Progress records add per-trial `trial_usage` and `trial_env_usage` metadata.
Resume reuses the same coverage aggregation as fresh evaluation. Missing legacy
metadata stays incomplete; it is not restored as a free request. An unmeasured
scorer result can still carry known spend, so its usage is retained separately
from its unchanged scoring outcome. Environment cumulative totals propagate
`null` rather than failing arithmetic or treating an unknown delta as zero.

## Compatibility and historical evidence

Historical files are not rewritten. A legacy raw zero with no coverage evidence
cannot be retroactively certified as a true measured zero or reclassified as
unknown from the number alone. When old aggregate-shaped input is passed to the
cost reader, its numeric values remain compatible and are labelled
`legacy_reported_coverage_unknown`; that label does not certify complete spend.
New writer coverage fields determine whether a canonical total is justified.
Old progress records with no per-trial metadata cannot prove complete resumed
cost, so resumed totals remain unknown rather than inventing missing history.

The console reads canonical cost fields and renders `null` as unknown. Run
summaries do not replace explicitly incomplete new harness costs with legacy
method-only cumulative zero. Legacy cross-round partial sums remain available
with explicit lower-bound notes; they are not complete totals. Method-generation
cost stays a separate budget, including its existing legacy alias; this change
does not merge actor, method and environment budgets or infer provider invoices.

Validation uses authored OpenCode JSON events, the real broker response parser
and `Judge.report`, HTTP framing bytes, and local synthetic recording files.
Only transport/actor execution is supplied as local test input. No model,
external endpoint, benchmark or Docker service is needed. The checks cover
missing/partial/zero usage, mixed tasks, all retained trials, failed responses,
retries, multiple service recordings, progress/resume, and cost rendering. They
do not establish universal accounting completeness for external adapters that
omit both reports and evidence of their requests.

CLI improver audit receipts also include `*-transcript-metadata.log` and
`*-usage-ledger.log` JSON files in the existing method-log collection. The display
transcript still retains at most 200,000 Unicode characters (stdout followed by
stderr); metadata records the original length, retained half-open range and
truncation flag. Usage is parsed from the full in-memory transcript, independently
of that display limit. This change does not imply that previously recorded usage
was lost when display logs were truncated.

The ledger contains only numeric usage, fixed status labels and validated SHA
identities; it excludes tool content, environment values and credentials. Absent
provider reports produce `status: unknown` and `usage: null`. Provider completeness
is unknown; retry counts and provider timing remain null without evidence. Parent,
evidence and patch-base SHA fields remain `unknown` unless explicitly supplied as
validated identities. These receipts do not reconstruct historical missing traces
or establish benchmark success.

The ledger additionally allowlists each `dsh-usage` record with a one-based ordinal,
zero-based transcript line index, input/output/cache counts and per-field coverage.
Missing or invalid fields remain null; malformed records retain only fixed status
and null values. Original lines and extra provider JSON fields are never copied.
The compatible aggregate remains the existing `parse_usage_lines` result under
`canonical_usage_aggregate`; `usage` totals become null for any field missing or
invalid in a recognized usage line, rather than filling gaps with zero.
`evidence_sha` is an alias of `evidence_harness_sha`: the measured harness identity,
not a hash of evidence bytes. Optional selected-parent and evidence round numbers
are retained only as nonnegative integers.

Parent/evidence bindings come from platform-staged round records and state indexes.
`selected_parent_sha` and `patch_base_sha` name the source being edited;
`evidence_harness_sha` names the measured harness described by the evidence.
They may differ for a historical source and current-run evidence. These are declared
harness-tree identities, not Git HEAD guesses or evidence-content digests.
