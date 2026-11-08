# Console ownership and boundaries

`server.py` selects configured paths, binds public compatibility delegates to
explicit service capabilities, and owns the loopback listener and app lifetime.
Package/module startup, direct-file CLI and arbitrary-name spec loading are
supported. No service imports the server to recover configuration or state.

| Owner | Responsibility |
| --- | --- |
| `record_reader` | Historical JSON/curve parsing, numeric conversion and run-row projection. Missing measurements remain distinct from recorded zero. |
| `run_query`, `comparison` | Run/round/trace/history queries, training-batch joins and coverage projection. Roots, authorization lookup and live observations are inputs. |
| `path_policy`, `serialization` | Resolved-path containment and JSON/display primitives. |
| `settings`, `catalogue`, `launch` | Endpoint persistence/redaction, declared launch choices, ordered preflight, invocation construction and one-shot detached start. |
| `candidates`, `round_recovery`, `round_snapshot`, `round_publish`, `round_ledger` | Candidate fd custody, byte bounds, source recovery, staged publication and provenance. |
| `candidate_trial` | Dedicated trial-root validation, paired run/work reservations and blocking driver handoff. |
| `workbench`, `assistant_drivers` | Explicit edit requests, admitted Codex/DSH editor protocols, scoped sessions, owned runtime state and edit-copy results. |
| `explanation` | Bounded evidence inputs, prompts, credential-safe chat transport, reply validation and explanation caching. |
| `submission_controller`, `submission_http` | Submission HTTP admission, scoped environment resolution, backend initialization/close synchronization and upload-token release. |
| `submissions.SubmissionService` | Canonical durable admission, queue, quota, retention, recovery and driver mechanics. |
| `artifact_catalogue` | Read-only inspection of method, baseline and dataset source declarations without importing adapters or executing methods. |
| `author_bundles` | Active bundle validation/gates, archive import and builder draft requests. This is separate from read-only artifact inspection. |
| `health`, `snapshots`, `snapshot_restore` | Health projection, configured snapshot-API loading and the legacy checkout adapter. Current candidate recovery uses the fd-based owners. |
| `http_transport`, `http_routes` | Headers, origin/framing guards, JSON/static transport and small adapters for named endpoint capabilities. |

## State and process ownership

`ListCache` alone owns cached rows, timestamp and lock. Root/live changes become
visible at the existing force/TTL refresh boundary. `ExplainCache` alone owns its
memory, recency order, lock and optional disk cache. Each server module creates
one instance of each; separately spec-loaded apps have independent caches.

`SettingsStore` keeps the existing merge precedence, legacy aliases, JSON format
and direct write followed by chmod. It is not an atomic-write protocol. UI
serialization uses its redacted projection. Endpoint probing is explicit and is
not part of launch preview or validation.

Launch preflight only inspects shared capacity; it does not reserve a run
namespace or create a lease. Detached launch invokes its supplied spawn port once
with the owned log handle and `start_new_session=True`. An HTTP response failure
does not retry the launch or kill the detached child. Trials instead reserve both
namespaces in an administrator-provided existing root; failed handoff or timeout
retains those identities. Driver namespace authority remains with the driver.

Candidate operations retain nofollow/descriptor-relative checks and their
trusted-root/cooperative-writer assumptions. Save publishes the complete manifest
last. Round recovery publishes a new candidate and never replaces an existing
edit. Source fingerprints establish content equality, not source authenticity.
The archive traversal deliberately keeps its fd stack and resource accounting
together.

`WorkbenchRequest.new_thread` is request-local. `ThreadStore` alone handles the
thread file and its versioned driver/binding session scopes, using file locking
and atomic replacement. The workbench owns its edit copy/helper invocation;
Codex and DSH have distinct admitted argv/response protocols. DSH has private
request-only home/pin state and no cross-request session. Unavailable drivers
are refused before copying. This does not add an assistant sandbox. See
[editor configuration and session migration](WORKBENCH.md).

Each submission backend belongs to one listener. Initialization and close are
serialized, creation after close is refused, and listener cleanup covers startup
and serving exceptions as well as interruption. Queue/retention/execution policy
stays in the canonical backend; HTTP adapters release acquired upload tokens in
`finally`.

## Compatibility and remaining responsibilities

Public server functions remain narrow, request-time bindings. Internal controls
should patch the canonical implementation owner; composition controls can patch
the public lookup, settings, chat or process capability they exercise. Prompt
text, route/header/body schemas, scorer evidence and unknown-cost semantics are
not documentation-edit surfaces.

The root still intentionally contains path selection, installed-distribution
state policy, run lookup authorization, endpoint binding and actual service
startup/shutdown. Removing those delegates would change the compatibility seam,
not complete another business extraction.

The substantial remaining owners are durable submission execution/recovery and
candidate fd publication/archive traversal. Their resource and failure protocols
matter more than splitting them to reach a line target. Workbench host isolation,
new editor-protocol implementations and active builder/gates remain explicit
boundaries; the module layout does not establish stronger guarantees for them.
