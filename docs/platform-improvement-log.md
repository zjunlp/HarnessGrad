# Platform improvement log

## KUMO private tool relay — 2026-10-17 (historical; superseded below)

KUMO needs a budget shared across CLI invocations and a trusted query record. The
existing native relay now supports an opt-in post-actor receipt input for command
verification, with payload hashes and cleanup on startup/runtime failure. Stop is
idempotent; early process exits and missing/non-object receipts are faults. Actor
files cannot substitute for the host receipt. SETUP file hashes bind public books.

The classifier recognizes the `act` verb and resolves undeclared splits using the
existing platform default for display. DOWNLOADS rendering summarizes large file
inventories; the full per-file checksums remain available in `downloads()` and JSON.

KUMO control/relay programs have four explicit, file-specific import-boundary
exceptions, matching the existing ALFWorld/AgentDojo program pattern; the adapter
module imports no execution engine. Tests exercise the real transport, sandbox,
verifier, digest mismatch, forged receipts and lifecycle cleanup. No production
admission, egress control or filesystem quota is introduced.

## Minimal KUMO integration — 2026-10-18

The added shared receipt/hash/result interfaces and global sandbox/early-exit
restrictions are withdrawn. File materialization, interface documentation, classifier
behavior and download rendering are restored to the pre-KUMO implementation.
KUMO supplies verified public content and a private case descriptor through existing
setup fields; its own relay and verifier validate the private source. Its CLI uses
`do`, and its adapter explicitly declares the existing hash split as its own policy.
Control orchestration moved to tools, removing three import-boundary exceptions.

Only lifecycle repair remains in the shared runner/relay implementation: stop and
remove the selected relay on normal and exceptional exits, reap failed startups,
and propagate cleanup errors. No new setup keys, return fields or method signatures
remain. Query receipts are no longer scorer inputs. The official scoring predicate
is unchanged; prior API evidence is retained as historical, not rerun evidence.


## KUMO: restore shared runtime — 2026-10-18

Supersedes the lifecycle repair retained in the previous entry. Both shared runner
and tool-relay modules are restored byte-for-byte to pre-KUMO commit `89626402`.
No cleanup exception policy or other shared execution behavior changes remain.
The existing staging leaks and ignored deletion failures remain platform limitations;
KUMO tests clean their own temporary state and do not claim the runner does so.
ALFWorld/AgentDojo runtime validation is incomplete due to missing provisioned data.
Compatibility here is established by restoring the original shared code, not by
claiming those runtime tests passed.


## KUMO controls follow dataset asset layout — 2026-10-18

Moved the control entry point from `tools/kumo_control.py` to
`data/datasets/kumo/control.py`, matching the existing dataset-local controls.
The local arm still reuses command-control helpers. Platform controls now stage
`data/datasets/kumo/oracle_harness/` directly, querying and submitting through the
CLI instead of rewriting a shared oracle template with string replacement.
Removed the unnecessary `tools/kumo_diagnostic.py`; historical API artifacts and
receipts are retained. Tests and current documentation use the new control path.
Only file-specific dependency guard entries were added; shared execution code
and interfaces remain identical to the pre-KUMO baseline.

## Opt-in dataset runtime interfaces — 2026-10-18

Added native relay sealing into verifier-only immutable inputs, lifecycle receipts and
cleanup, isolated judge/environment model brokers, strict optional result handling,
complete-manifest repeated-trial reporting, and fixed native peer execution of the
candidate harness. Existing declarations retain their score, transport, model routing
and aggregation semantics; `ToolRelay.cleanup()` still returns `None`.

See [the dataset runtime interfaces](dataset-runtime-interfaces.md) for adapter
handoffs and supported boundaries. Dataset-specific benchmark validation remains
with each adapter.

## Observed Docker peers and combined verification — 2026-10-18

Added opt-in `env.backend=observed-v1` workers with pinned images, kernel-observed
runtime admission, private model/mailbox channels, bounded artifact export and
container cleanup. `VERIFY.container_runtime` runs the combined check in a fresh
observed container with immutable worker evidence. Failed groups retain successful
peer submissions; existing single-actor exec admission remains unchanged.

Validation: 58 shared/KUMO/Docker controls passed. Expanded regression had 578
passes, 22 failures reproduced on baseline `7dd15360`, and 45 skips. One-off logs
and validation records are archived outside the repository; automated tests remain
under `tests/quality/`.


## CostBench static reuses trusted interaction records — 2026-10-21

CostBench static uses the existing native relay, sealed episode projection and
command verifier to distinguish actual tool execution from actor-written output.
No new shared runtime interface is required. Dataset-owned scoring preserves
cost-metric eligibility separately from task failure, and registration distinguishes
simulated tool-cost optimization from actual model spend or hard-budget management.
See the [integration journal](adapter-integration-journal.md#costbench-static-protocol--2026-10-21)
and [dataset contract](../data/datasets/costbench/README.md). This entry describes
interface reuse and makes no standalone claim about validation or model performance.
