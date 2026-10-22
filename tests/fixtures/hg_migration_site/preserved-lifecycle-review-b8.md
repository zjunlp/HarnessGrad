# Preserved lifecycle review — B8

Reviewed 2026-10-15 in `/tmp/hg-b8`, base
`906607202fe6d7674695a410a72eb7379b869e75` (registered detached worktree).
Scope is exactly the four named blockers below. All four remain `runtime`.
The metadata/source review is complete within this checkout; no runtime,
measurement, termination or production-readiness gate is closed.

Only repository source, documentation, registry/history metadata and local file
existence were inspected. No adapter was imported, task/gold/attachment payload
was read, endpoint or backend was contacted, or dataset/control/scorer was run.
External audit/receipt paths were not followed because work is confined to this
worktree. Thus external receipt authenticity and their current bytes remain
unverified, even where a journal quotes counts or a digest. No preserved receipt,
run ID, historical record or history fixture was edited, resumed or overwritten.
Historical date strings, including the GAIA IDs containing `20261117`, are retained
as recorded; this review does not reconcile their chronology.

## Original registry instructions, read before evidence review

### Original gaia / frozen-lifecycle-receipt

`observed` (verbatim):

> Compatibility checks do not establish coherent source lock, snapshot bounds, trusted verification or fresh cleanup; V2/V3 remain incomplete.

`next_action` (verbatim):

> Review the metadata-only source/runtime/lifecycle identity requirements and preserve both interrupted run IDs; no aggregate score is valid.

### Original tau_bench / local-state-lifecycle

`observed` (verbatim):

> Returned local pool loadability does not attest service reset, database state or evaluator secrecy.

`next_action` (verbatim):

> Review selected-only state and lifecycle contracts using source metadata; require independent runtime evidence before readiness claims.

### Original chessbench_puzzles / interruption-closure

`observed` (verbatim):

> Both interrupted pilots lack terminal verdict/cleanup closure; termination authority remains undetermined.

`next_action` (verbatim):

> Review the existing audit and receipt identities without resuming or overwriting either run ID.

### Original chessbench_puzzles / trusted-stepwise-transport

`observed` (verbatim):

> A pure public-observation transport contract now exists and is covered by fabricated tests, but it is not wired into the shared runner or an interactive official scorer; successive real observations and actor isolation remain unverified.

`next_action` (verbatim):

> Integrate the public transport with a trusted shared runner and fresh lifecycle receipts, or retain offline-only status; never claim interactive official accuracy from fabricated transport tests.

## GAIA / frozen-lifecycle-receipt

**Established from source metadata:**

- `eval/policy/gaia_snapshot_attestation.py:56` requires a full commit/tree and
  file mode/object-ID manifest; `:31` and `:80` require validation split, dataset
  revision, pool/split/selection/scorer/harness/dependency/web/export/quota digests,
  exact actor and verifier image IDs, container plan, positive snapshot maxima/
  reserves and CPU/memory/PID limits. These are supplied identity declarations.
- `eval/policy/gaia_snapshot_attestation.py:173` explicitly returns
  `receipt_validation=not-performed`, `evidence_reusable=false` and
  `lifecycle_authorized=false`. `eval/policy/gaia_preflight.py:18` lists missing
  gates; `:84` keeps execution blocked and measured/production authorization false.
  GAIA remains policy-refused; this review permits no provisioning or execution.
- `docs/gaia-operator-packet-codex210.md:54` through `:61` specify independent
  source/dependency custody, a selected validation/attachment/gold manifest, pristine
  verifier and actor identities, effective network/resource/paired byte-inode limits,
  bounded artifact-only export, snapshot preflight/completion and observed owned
  cleanup/absence. `:95` binds run/trial/manifest and pre-execution scope to ordered
  admission/execution/quiescence/scoring/sealing/cleanup and outcome/snapshot digests.
- `eval/admission/lifecycle_receipt.py:1` disclaims observation collection and
  authentication. `:127` prohibits stage skipping, repeated stages and scoring
  after failed/unknown work; `:150` retains failure after cleanup. Structural
  publication requires bound scope/outcomes/snapshot/cleanup (`:181` and `:189`)
  and never grants production authorization (`:171`). These are library contracts,
  not observations of either interrupted GAIA run.

**Preserved identities and limits of the record:**

- `gaia-validation-pilot-h0-20261117-v2`: the journal records source `92f6b10`,
  17 tasks × 3 planned, jobs=1 and GPU1/8002 at
  `docs/adapter-integration-journal.md:440`. Its `:442` cites the sealed
  `GAIA_H0_V2_RECEIPT.json` with 9 persisted trials versus 10 done events and unknown
  timeout usage. Coverage is unresolved; neither number becomes a complete curve.
- `gaia-validation-pilot-h0-20261117-v3`: `:450` records source `cde983ff` and
  output cap 2048; `:451` reports 11 normal trials and exit 137 before verification
  of the next attempt. `:452` reports removal of an interrupted resource; that
  statement is not an independently observed, complete cleanup/absence receipt.
  `:454` forbids resuming the ID. No aggregate score is valid.
- Both complete IDs and the original evidence pointers are separately pinned in
  `tests/fixtures/dataset_blockers/history-codex178.json:140` and `:162` (records
  `gaia-v2-incomplete` and `gaia-v3-incomplete`). They retain `status=incomplete`
  and `effect=null`; neither historical record was changed.

**Unknown / disposition:** coherent source lock through execution/sealing;
independent source/scorer/dependency and selected-only custody; actual actor/
verifier identity and enforcement; bounded snapshot/export and fresh complete
cleanup; V2 coverage and timed-out usage; V3 exit-137 cause and unverified trial.
No new authentic lifecycle evidence was found in the reviewed repository
metadata. The older source review also explicitly reports no GAIA lifecycle
receipt (`reports/GAIA_GATE.md:22`); that is an attributed review, not a global
proof of absence. Requirements review is recorded; `frozen-lifecycle-receipt`
stays **open, runtime, policy-refused, unmeasured**. No aggregate or readiness claim.

## tau_bench / local-state-lifecycle

**Established from source metadata:**

- `data/datasets/tau_bench.py:178` declares container state, network none, two
  CPUs, 4096 MiB, no services and finite actor/verifier timeouts. This describes
  intended local state, not effective backend enforcement or a service-reset receipt.
- `data/datasets/tau_bench.py:322` stages the selected task's domain clean database
  as `/app/db.json`. `:328` and `:351` declare task-specific gold, clean database,
  checker and trusted library inputs for verification after actor execution.
  `data/datasets/tau_bench/Dockerfile:18` and `:35`, together with
  `data/datasets/tau_bench/.dockerignore:1`, exclude `gt/` and `verify/` from
  intended actor image construction. This establishes the source separation plan,
  not secrecy of an observed image or a pristine production verifier.
- Selected-task setup does not establish a selected-only reader: `_read_gt()`
  loops over all ground-truth JSON files (`data/datasets/tau_bench.py:198`), is
  called at import (`:217`), and constructs pool-wide maps (`:374`). No adapter
  import or full-pool read was used in this review. Source metadata therefore
  cannot close selected-only custody/privacy.
- The CLI loads and atomically writes `/app/db.json`
  (`data/datasets/tau_bench/image/tau_cli.py:83` and `:95`); `:193` mutates it
  through domain tools. This is a database-state design contract, not proof of
  isolation, clean restoration or no contamination between trials.
- `docs/tau-bench-vendoring.md:347` records absent clean databases. Metadata-only
  `Path.exists()/is_file()` checks in this worktree confirmed both
  `data/datasets/tau_bench/db/airline.json` and `retail.json` absent. They were
  not regenerated. A provenance inventory does not supply those bytes or a runtime.

**Unknown / disposition:** no independent selected runtime/control/lifecycle
receipt was located in reviewed repository docs/reports/receipt metadata. The
journal explicitly requires actual reset/database/secrecy evidence
(`docs/adapter-integration-journal.md:429` through `:434`), as does the later
readiness review (`docs/task-243-diagnostic-readiness.md:99`). The historical
image inventory names `beda1b01f43c` (`reports/docker-resource-cleanup-20261014.md:51`)
but labels ownership unclear and preserves it (`:165`); this is not a fresh,
run-bound image-content/reset/verifier/cleanup receipt. Runtime evidence elsewhere
is unknown, not asserted nonexistent.

Before any readiness conclusion, independent evidence must bind selected-only
scope/custody, actual clean database/image/toolchain identities, trial reset and
isolation, protected evaluator inputs, effective resource/network/quota limits,
quiescence, bounded artifact export/snapshot, official scorer execution and
observed owned cleanup to a fresh run/trial/manifest. The shared pure lifecycle
library supplies no such observations (`eval/admission/lifecycle_receipt.py:1`).
Review is recorded; `local-state-lifecycle` stays **open, runtime, unmeasured**.
No control or pilot is authorized by this review; no historical run was invented.

## chessbench_puzzles / interruption-closure

**Established as preserved journal/audit citations:**

- `docs/adapter-integration-journal.md:588` names
  `chess-pilot-h0-20261013`, 19 completed task-trials and 0 failed envelopes,
  stopping during test/1270 trial 2 around 70 seconds with no terminal verdict
  or aggregate. These are reported partial counts, not a measured full result.
- `:589` names `chess-pilot-h0-20261013-recovery-01`, 149/150 completed task-trials,
  0 failed envelopes and a last test/9967 trial past 620 seconds. It reports a
  parent driver stop, but provides no terminal verdict/curve or cleanup closure.
  A human-stop narrative does not settle termination authority or missing outcomes.
- `:590` expressly leaves termination authority undetermined and timeout/failure/
  cleanup/snapshot closure absent. It identifies native bwrap/files execution,
  not Docker snapshot execution. Its audit pointer is
  `/mnt/20t/xubuqiang/Study/HarnessGrad-evidence-archive/chess/interruption-audit.md`,
  with cited SHA-256
  `db357459d4160a5058b022ef5483227f8e01629e24cdf528017db5e7d418de4f`.
  This digest identifies the cited audit, not either run receipt. It was not
  recomputed here; individual external run-receipt hashes are not authenticated
  by this checkout review. Preserve the originals, not reconstructed receipts.
- `reports/chess-recovery-id-conflict-codex182-report.md:11` closed only reused-ID
  documentation guidance; `:16` preserves both incomplete histories and null
  effects. The two records (`chess-original-incomplete`,
  `chess-recovery-incomplete`) remain equal to the pinned history baseline at
  `tests/fixtures/dataset_blockers/history-codex178.json:74` and `:96`.

**Unknown / disposition:** independent audit/receipt authentication, precise
termination cause/authority, the missing final outcomes, resource quiescence and
complete owned cleanup. Do not infer timeout, scorer zero or complete coverage
from missing events or 0 failed envelopes. Repository audit/identity review is
recorded; `interruption-closure` stays **open, runtime, incomplete/unmeasured**.
Both used IDs are immutable: never resume, re-run or overwrite either, even if a
later audit explains the cause. This review launches no replacement attempt.

## chessbench_puzzles / trusted-stepwise-transport

**Established from source metadata:**

- `data/datasets/chessbench_puzzles.py:39` pins upstream revision
  `90ae0e6b121673fc3079aaeffa047580bb600c0a`; `:41` names
  **ChessBench strict legal puzzle replay v1** and the separate diagnostic
  **ChessBench upstream puzzle replay (unchecked push)**. `:309` persists both
  while explicitly identifying the offline sequence protocol. Neither is Elo or
  measured interactive official accuracy.
- `data/datasets/chessbench_puzzles.py:201` requires a trusted controller isolated
  from the actor. `:250` exposes only contract/FEN/solver_turn/status/verdict;
  `:259` accepts exactly one UCI field and applies the private opponent response
  before returning the next public observation. This is a pure session contract.
- `docs/chessbench-puzzles-evaluation.md:78` requires controller-owned ordering,
  gold isolation and termination records; actor-authored transcripts cannot attest
  observation delivery. `:93` explicitly states the shared QA interface remains
  offline. A source search of `eval/` and `data/scaffolds/` found no
  `StepwisePuzzle`, `STEPWISE_CONTRACT` or `chessbench_puzzles.stepwise` integration.
  `docs/adapter-integration-journal.md:579` through `:585` limits the prior test
  findings to fabricated boards/contract semantics, with interactive accuracy
  unmeasured.

**Unknown / disposition:** successive real observations before actor decisions,
production actor/controller isolation, trusted submissions/termination ordering,
independent official execution and fresh run-bound lifecycle/cleanup receipts.
The explicitly permitted offline-only option is retained; no transport integration
or model run was attempted. Review is recorded; `trusted-stepwise-transport`
stays **open, runtime, offline-only; interactive official execution unmeasured**.
Any future claim needs separately reviewed trusted shared-runner integration and
fresh identity-bound lifecycle evidence, without reusing either interrupted ID.
Synthetic success is insufficient.

## Registry scope and validation

Only `observed`, `next_action` and repository evidence anchors of the four owned
blockers change. Their IDs, families and `runtime` statuses, every shared blocker,
all other dataset entries, taxonomy, production authorization, historical records
and the independently pinned history fixture are unchanged. The registry has no
closed status; completing a metadata review does not remove the runtime blocker.

Validation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-hg-b8-blockers \
  tests/quality/test_dataset_blockers.py
git diff --check
```

- Checker: `valid=true`, 28 datasets, 8 shared blockers, 11 historical records,
  `production_authorized=false`.
- Focused source-only registry tests: **52 passed in 2.95s**; no failures/skips.
  The first invocation incorrectly supplied an in-tree basetemp and was rejected
  by the repository guard before collection (exit 4); the command above corrected
  it to the required external directory. No whole-suite test run occurred.
- A structural comparison against the base commit confirmed only the four owned
  blocker observations/actions/evidence changed in the registry; every other field,
  all historical records and the pinned history fixture are identical. Original
  `observed`/`next_action` wording above is retained verbatim from that base.
- Full diff review and whitespace check passed. Only the registry, this unique
  review report and an appended journal entry are part of this change. No runtime
  or receipt artifact was changed; these checks establish no dataset readiness.
