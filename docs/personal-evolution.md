# Personal evolution foundation

The personal foundation provides an independent desktop entry point, local records
and shared model access for later harness evolution. This implementation covers
stage A and B1: the UI saves drafts and previews labelled fixtures; the Python
model interface supports bounded JSON calls. Feedback planning, candidate edits,
application, rollback and automatic daily learning are not connected yet.

Use the [desktop guide](../desktop/personal/README.md) to run it and the
[validation summary](validation/personal-foundation.md) to assess the tested scope.
It builds on the existing [shared foundation](shared-foundation.md).

## Entry points and ownership

```text
desktop / browser -> personal.api -> PersonalService -> EventStore
personal CLI      -> entrypoints.personal -> service or explicit model fixture
ModelSession      -> JsonModel -> existing endpoint Client
ModelSession      -> ModelStore -> EventStore transactions and events
```

The native shell controls windows and displays the local page. It does not own
models, evidence or workflow decisions. The HTTP service currently has no model
worker; the model session is an explicit Python interface used by the offline demo.

| Owner | Responsibility |
| --- | --- |
| [Personal CLI](../harnessgrad/entrypoints/personal.py) | Arguments, owned service lifecycle, configuration preview and explicit fixtures |
| [Service](../harnessgrad/personal/service.py) / [API](../harnessgrad/personal/api.py) | Selected projects, imports, drafts, read-only inspection and authenticated loopback routes |
| [Contracts](../harnessgrad/personal/contracts.py) | Strict JSON, identifiers and bounded synthetic source events |
| [Codex adapter](../harnessgrad/personal/adapters/codex.py) | Explicit executable/version probe and scoped capability results |
| [Event store](../harnessgrad/personal/event_store.py) | SQLite transactions, schema migrations, original evidence, jobs, questions, event cursors and drafts |
| [Profile](../harnessgrad/personal/profile.py) | Selected method configuration, immutable model settings and task limits |
| [Model session](../harnessgrad/personal/model.py) | Execution context, stage callbacks, per-job lock, deadlines and durable reservations |
| [Model store](../harnessgrad/personal/model_store.py) | Budget/call ledger SQL using the event store's transactions |
| [Shared JSON model](../harnessgrad/improvement/model.py) | Strict JSON callback, logical calls and observed transport usage |
| [Personal page](../console/static/personal.js) | Project interaction and API requests; separate draft and window controllers handle their queues |
| [Native shell](../desktop/personal/src/main.rs) | Window commands, with placement and diagnostics in separate leaf modules |

Personal modules do not import experiment drivers, datasets or scorers. The
shared JSON model and transport do not depend on personal storage. Public help
and read-only service operations do not construct a model client. No Python
runtime dependency or third-party agent runtime was added.

## Data and service boundaries

The caller selects a state-root outside the checkout and project. SQLite v3 owns
projects, sessions, original source events, jobs, evolution events, questions,
model budgets/calls and project drafts. Known v1/v2 schemas migrate transactionally;
unknown structures are refused. Original rows, evidence digests and cursors survive
migration. A source ID collision with changed content rolls back the import.

Imports accept only explicitly synthetic sessions: up to 200 events, 16 KiB of
text per event and 1 MiB per document. Project/session identity and truncation
markers remain attached. There is no default history scan or hook installation.

Drafts are mutable local editing state, not learning evidence or model jobs.
Each project draft has a revision; a stale save returns HTTP 409. The client
serializes saves and waits for acknowledgement before reporting success. An
existing stable draft, including an empty one, takes precedence over legacy
browser text. Unacknowledged changes can be lost on forced exit.

The service accepts eight simultaneous handlers, uses five-second socket timeouts
and limits request bodies to 1 MiB. Draft text has a separate 16 KiB UTF-8 limit.
SQLite uses a five-second busy timeout. Host, Origin, bearer token and JSON shape
checks precede mutations. Text is rendered as text rather than evaluated as HTML.

The desktop reads one explicit connection file and limits navigation to that
service origin. Custom native commands are restricted to window controls. A
per-state lock prevents a second shell. Allowlisted window metadata is bounded to
256 KiB; logs omit draft
text, source bodies and connection tokens. Closing a window and stopping its
service are separate actions.

## Reusing model configuration and calls

`ModelSettings` reuses `MethodConfig`, `load(explicit_mapping).method` and
`read_env_file`. Only `HG_METHOD_BASE_URL`, `HG_METHOD_MODEL`, `HG_METHOD_API_KEY`
and `HG_METHOD_TIMEOUT_S` are selected. Process values override an explicitly
selected file. There is no implicit repository dotenv load, global run snapshot
or experiment default model. Configuration preview validates without constructing
a client. Persistent identity contains the endpoint/model and a credential
reference/presence flag; keys stay in memory.

`improvement.model.JsonModel` extracts the original customization callback and
parameterizes its system prompt. `customization.model.ProposalModel` preserves its
import path, constructor, default prompt, JSON/error behavior and usage shape.
HTTP, retries and response bounds reuse `methods.endpoint_client.Client`.

| Per-job default | Value |
| --- | --- |
| Logical model calls | 12 |
| Candidate revision reservations | 2; candidate generation is still pending |
| Active execution time | 300 seconds |
| Combined system/user input | 1 MiB UTF-8 |
| Per-attempt request timeout | `HG_METHOD_TIMEOUT_S`, default 180 seconds |

One `ModelSession` job owns the quota across `for_stage(...)` callbacks and resumed
contexts. Before dispatch, a transaction reserves the call. Failure or an unknown
outcome does not refund it. The model identity, project, profile and limits are
frozen; rotating a runtime credential does not reset the job.

An OS file lock prevents a live executor from being replaced. A context charges
active time; callers exit it while waiting for a user. Recovery conservatively
charges abandoned active time and marks unfinished reservations unknown. The
ledger stores input hashes/sizes and usage, not prompts, replies or credentials.
Missing provider tokens and unknown retry consumption remain unknown.

The Client's optional monotonic `deadline` bounds later retries and connection/read
waits; existing callers omit it and retain their behavior. This is not a worker
process kill or a guarantee of interrupting DNS. Cross-job quotas and background
cancellation are not implemented.

## Next integration points

Feedback and evidence selection should call the same service/store interfaces.
Planning and proposal stages receive `ask(prompt) -> dict` from the same job's
model session, rather than creating separate clients or budgets. Codex file/Skill
mapping belongs in the adapter boundary; it is separate from model generation.
Future adoption and observation must record writing, loading and behavior
individually. The current probe or a fixture's completion does not establish those
capabilities.
