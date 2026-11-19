# Personal evolution foundation

The personal foundation provides an independent desktop entry point, local records
and shared model access for later harness evolution. The UI saves drafts, submits
explicit feedback and selects excerpts from imported sessions. With explicit
model configuration and safe host reads, the same page generates a read-only
proposal and shows the native Diff. Applying changes, rollback and automatic
daily learning remain later work.

Use the [desktop guide](../desktop/personal/README.md) to run it and the
[validation summary](validation/personal-foundation.md) to assess the tested scope.
It builds on the existing [shared foundation](shared-foundation.md).

## Entry points and ownership

```text
desktop / browser -> personal.api -> PersonalService -> EventStore
personal CLI      -> entrypoints.personal -> service or explicit model fixture
ProposalWorker   -> B5 selected snapshot -> B3 plan (+ optional reply) -> B4 candidate -> B5 native Diff
ModelSession      -> JsonModel -> existing endpoint Client
ModelSession      -> ModelStore -> EventStore transactions and events
```

The native shell controls windows and displays the local page. It does not own
models, evidence or workflow decisions. The HTTP service delegates to a single
owned proposal worker only after an explicit generation request.

| Owner | Responsibility |
| --- | --- |
| [Personal CLI](../harnessgrad/entrypoints/personal.py) | Arguments, owned service lifecycle, configuration preview and explicit fixtures |
| [Service](../harnessgrad/personal/service.py) / [API](../harnessgrad/personal/api.py) | Selected projects, imports, drafts, read-only inspection and authenticated loopback routes |
| [Contracts](../harnessgrad/personal/contracts.py) | Strict JSON, identifiers and bounded synthetic source events |
| [Codex adapter](../harnessgrad/personal/adapters/codex.py) | Explicit executable/version probe and scoped capability results |
| [Event store](../harnessgrad/personal/event_store.py) | SQLite transactions, schema migrations, original evidence, jobs, questions, event cursors and drafts |
| [Feedback input](../harnessgrad/personal/inputs.py) / [feedback store](../harnessgrad/personal/feedback_store.py) | Explicit feedback kind, idempotent local submission and frozen selected excerpts |
| [Workflow](../harnessgrad/personal/workflow.py) / [proposals](../harnessgrad/personal/proposals.py) | Recorded plan and candidate stages, frozen input and shared text proposal adapter |
| [Proposal worker](../harnessgrad/personal/proposal_jobs.py) / [receipts](../harnessgrad/personal/proposal_run_store.py) | Selected read-only host scope, stage sequencing and durable proposal status keyed by feedback |
| [Evidence](../harnessgrad/personal/evidence.py) | Selected-session/window retrieval, optional FTS5 cache and original-source excerpt checks |
| [Profile](../harnessgrad/personal/profile.py) | Selected method configuration, immutable model settings and task limits |
| [Model session](../harnessgrad/personal/model.py) | Execution context, stage callbacks, per-job lock, deadlines and durable reservations |
| [Model store](../harnessgrad/personal/model_store.py) | Budget/call ledger SQL using the event store's transactions |
| [Shared JSON model](../harnessgrad/improvement/model.py) | Strict JSON callback, logical calls and observed transport usage |
| [Personal page](../console/static/personal.js) | Project interaction and API requests; separate controllers handle drafts, window actions and proposal polling |
| [Native shell](../desktop/personal/src/main.rs) | Window commands, with placement and diagnostics in separate leaf modules |

Personal modules do not import experiment drivers, datasets or scorers. The
shared JSON model and transport do not depend on personal storage. Public help
and read-only service operations do not construct a model client. No Python
runtime dependency or third-party agent runtime was added.

## Data and service boundaries

The caller selects a state-root outside the checkout and project. SQLite v5 owns
projects, sessions, original source events, jobs, evolution events, questions,
model budgets/calls, project drafts, feedback, selected excerpts and proposal
receipts. Known v1–v4 schemas migrate transactionally;
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

## Selected Codex surface limits

B5 reads an explicitly selected project instruction and a standalone single-file
Skill through `CodexAdapter.collect_selected`. Filesystem reads require
handle-relative access that refuses links. Windows supports fixed local volumes;
network, mapped and substituted drives are declined. Each child is opened relative
to a verified directory handle and checked for reparse points. The adapter rejects
hard-linked files and checks file identity and metadata again before publishing a
Diff. Platforms missing the required primitives report
`codex.selected_surface_read=unavailable`. Pure candidate rendering remains available
from a valid snapshot.

The controlled Windows Codex loading probe separately verified two fresh threads
against two synthetic revisions of `AGENTS.md` and `SKILL.md`, with no real model
calls. The normal capability probe still reports selected surface loading as
unchecked; it does not infer loading in daily sessions from that experiment.

Standalone Skills reject declared supporting files and explicit local Markdown
links, reference definitions, images and `file:` destinations. Remote URLs and
same-document anchors remain allowed. These checks never read linked targets.

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
| Candidate revision reservations | 2 |
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

## Feedback and selected evidence

Saving a draft remains separate from **提交反馈**. A submission records the user's
verbatim text and explicitly selected kind: `explicit_request`, `correction`,
`success_observation` or `failure_observation`. It does not create a model job or
convert a request into failure feedback. Empty history is supported; context gaps
remain explicit. Assistant output stays unverified assistant output, distinct
from user quotes and tool observations. Imported source text remains synthetic.

Search requires both a selected source instance and session in the selected
project. `GET /api/personal/projects/<id>/evidence` accepts `source_instance`,
`session_id`, literal `q` (at most 256 UTF-8 bytes), exclusive `after`/`before`
sequence bounds and `limit` (1–50). It returns the latest matching window in
chronological order, `has_more` and `next_before` for older windows. The optional
FTS5 trigram index is a derived cache over imported text only. Missing FTS5/trigram
support and queries shorter than three characters use exact substring matching
within the same selected session; the response reports the actual mode.

`POST /api/personal/feedback` accepts exactly `project_id`, `kind`, `text`,
`evidence`, `idempotency_key` and nullable `draft_revision`. Each evidence item
contains `source_instance`, `session_id`, `event_id`, `sha256`, `start` and `end`.
Offsets count Unicode characters, not UTF-16 units; the end is exclusive. The
service reconstructs excerpts from original events and checks ownership/digests,
rather than accepting replacement evidence text. Limits are 16 KiB feedback,
32 excerpts and 128 KiB combined excerpt text. Truncation, partial selection,
source identity, timestamp and role remain attached.

The same project/request key and payload return the original receipt. Reusing a
key with another payload or submitting an outdated draft returns HTTP 409 with
`code=feedback_conflict`; no partial record is committed. Distinct keys represent
distinct requests. The frontend preserves an in-memory key after an uncertain
response for retry; reloading the page ends that in-memory retry context, so
inspect feedback history before explicitly submitting again. Submission does not
clear the draft or claim planning, application or successful behavior.

`GET /api/personal/projects/<id>/feedback` lists recent records (default 20,
maximum 50); append `/<feedback-id>` for its recorded evidence. Read-only service
status refers to harness files, not local feedback persistence. B5's adapter,
shared contracts and model configuration/call implementation are unchanged.

## B3 planning

`personal.workflow.plan_feedback` consumes a saved B2 feedback ID, its project,
the existing job's `ModelSession`, and caller-selected virtual text surfaces.
It accepts at most one instruction and one `skills/*.md` file. The caller supplies
these from an adapter snapshot or an explicit fixture. It performs no project
discovery or filesystem read. Unknown/unsafe target paths and cross-project
bindings fail before model dispatch.

The validated result is `plan_ready`, `needs_input`, or `no_change`. A ready plan
contains an objective, selected targets, observable criteria, fixed check IDs,
and a future scenario/input/expected comparison. Explicit requests and success
observations cannot become historical failures. Incomplete evidence cannot
support a ready behavior-fix plan. Source excerpts retain their role, synthetic
provenance and completeness flags. None of these records establish reproduction,
candidate generation, verification, or adoption.

Before the first model call, a `plan.requested` event freezes the selected-input
digest and planner policy on the existing job. A rejected or interrupted attempt
retains this binding and its model-budget charges. Older attempts can be matched
against the existing model-call ledger.

The plan and its selected-input snapshot are saved under the owned state root's
`plans/<job-sha256>/<record-sha256>.json` with exclusive atomic publication.
SQLite retains the record ID, decision and request digest. A `needs_input` result
also creates an open question on the same model job. If publication completes
before the database transaction fails, retry validates the one published output
and completes the event/question transaction without another model call.
Conflicting outputs are refused. Recovery removes only a temporary alias sharing
the published file's inode; other incomplete temporary files are retained.

Reading an existing result checks its full selected input, job/project binding,
decision, allowed targets and fixed checks. It does not start model execution,
including when the job is completed or its execution budget is exhausted.
Previously referenced flat `plans/<sha256>.json` records remain readable.
Native record access rejects links/junctions and external hardlinks; Windows
record paths longer than 260 characters have explicit regression coverage.

The existing task's remaining model budget is shared with subsequent stages.
Question-answer continuation and further planning revisions, worker events,
API and UI handling are delivered with B6.

Use an external state directory and an explicit existing synthetic project for
the offline CLI exercise.

```bash
python -X utf8 -B -m harnessgrad.cli personal plan-demo \
  --state-root /path/to/owned-b3-state --project /path/to/synthetic-project
```

The demo uses one scripted JSON reply and an in-memory instruction fixture.
It records the user's expectation and future comparison with `fixture=true`,
`verified=false`, `applied=false`, and `real_model_calls=0`.

## B4 logical candidates

`personal.workflow.propose_candidate(model, project_id, feedback_id, plan_id,
skills=..., instruction=...)` consumes a recorded `plan_ready` result and the
same virtual surface snapshot used by `plan_feedback`. A different job, feedback,
plan or snapshot is refused. `needs_input` and `no_change` end the stage before
any candidate reservation.

`improvement.text_edit_proposal` uses the shared `Proposal` and unified Diff
format. It asks for one to four exact edits with `path`, `old` and `new`. An
existing block must occur exactly once. Empty `old` appends a new block. Old
and new text are limited to 16 KiB each, and a changed file to 256 KiB.
Edits preserve consistent LF or CRLF endings and reject anchors that split a
CRLF pair. The prompt prefers revising an existing rule. Only planned files
are supplied as editable context. Original feedback labels and provenance
remain intact.

One accepted candidate is frozen per plan at
`candidates/<job-sha256>/<record-sha256>.json` in the external state directory.
`candidate.requested` binds the plan and model input digest before dispatch.
`candidate.prepared` binds the validated record digest to the exact proposal
call and revision before filesystem publication. `candidate.recorded` confirms
publication. Recovery follows the prepared digest and leaves unrelated files
in place. Previously committed candidate references remain readable.
The stage shares the B3 model session's calls, revision and time limits. Invalid
replies consume their attempt. Reading a finished candidate or recovering a
published candidate after an interrupted event commit consumes no model calls
or revisions. If the prepared file was never published, a retry needs remaining
budget. The resulting record is subject to the existing 1 MiB limit.

The record retains `plan_id`, `feedback_id`, `fixture`, the logical `diff` and
the complete `files` mapping, including unchanged selected files. An adapter can
render that mapping against its original snapshot to apply host-specific checks
and produce a native Diff. The logical candidate is `verified=false` and
`applied=false`. B4 reads no project files and runs no scenario. Native target
binding, behavior verification, adoption and version recovery retain their
separate ownership in B5 and C.

## Proposal tasks: feedback to a native Diff

Opt in by starting the owned service with `--proposal-env-file`. Only the
explicit model environment file is used for this service option. The UI identifies
the destination and explains which text can be sent before generating. This flow
reads only the selected project's root `AGENTS.md`. It does not discover Skills,
run commands or change the project. B5 can render a selected single-file Skill
independently; the first desktop flow does not expose Skill selection.

Feedback submission still only saves a receipt. With proposal generation enabled,
the UI requests work for that saved receipt via authenticated `POST` with `{}`
to `/api/personal/projects/{project}/feedback/{feedback}/proposal`. `GET` on
the same route returns `{"proposal": null}` before work starts and a durable
status afterward. Repeating the POST returns the original run. A different
receipt is refused while the worker is busy and can be started later from its
history entry. The worker shares one budget across the initial plan, at most one
clarification and candidate generation (three logical calls, one revision and 90
active seconds). A plan that needs clarification pauses before creating a
candidate. An authenticated `POST /reply` under the proposal route supplies
`{question_id, answer}`. Repeating the same answer returns the existing receipt;
a different answer to the same question conflicts. The worker checks the original
snapshot before resuming the same model job and checks it again before publishing.
The candidate receives the frozen question and answer separately from the original
feedback. If the clarified plan still asks for information, the task ends as
`failed` with `clarification_exhausted` and no further reply action or candidate.
The result carries plan and candidate IDs plus the snapshot and prepared Diff digests.

Authenticated `GET /events` under the proposal route provides SSE status events with
monotonic IDs. The client sends `Last-Event-ID` when reconnecting and reads the
proposal receipt for details. The server holds at most two streams at once; each
response is a bounded batch or a heartbeat after a short wait. Reloading the page
can reopen a saved Diff or follow an active run. After service restart an abandoned
active run is shown as interrupted and is never replayed automatically. SQLite v6
preserves prior receipts and seeds one event for each v5 receipt's last saved state.

`POST /cancel` with `{}` cancels an active run owned by this service or a run
waiting for a reply. Cancellation and model-call reservation use the same SQLite
writer lock: if cancellation commits first, no new call is reserved or sent. A
call already admitted for dispatch may finish within its existing timeout;
cancellation prevents publishing a Diff.
Unconfigured services and platforms without safe reads can still record feedback
and reopen saved results. This flow leaves proposals unapplied and unverified.
