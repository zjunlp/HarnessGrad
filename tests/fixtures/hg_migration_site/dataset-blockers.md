# Dataset blocker registry

[dataset-blockers.json](dataset-blockers.json) is the source-only review index of
all 28 publishable dataset declarations at this revision. It records task
`demand` and `surface` separately from harness `exercises`, using the literal
declarations in `tools/dataset_classes.py`. An exercise means score sensitivity
to a harness component, not an isolated measurement of that component. For
example, Jericho's `memory` exercise does not make its task demand `memory`.

Each dataset has explicit current `blockers` and references to applicable
`shared_blockers`. Expand those references to obtain its complete work-item list;
retained `done` entries are closed only for their documented scope.
Every blocker has an ID, family, work status, observed finding, evidence paths
with text locators, and a next review or repair action. Multiple statuses can
apply to one dataset; no aggregate category replaces these independent facts.

| Status | Meaning |
| --- | --- |
| `code-solvable` | Repository implementation or documentation work can make progress; success still requires independent review. |
| `external` | Independent provenance, custody, source acquisition or an unavailable benchmark contract prevents closure. |
| `runtime` | Actual backend enforcement or lifecycle observations are missing; synthetic contracts do not close the gate. |
| `measurement` | Coverage, metric comparability, receipt review or current dataset-specific evidence remains unresolved. |
| `done` | The recorded work is closed with checked-in evidence. A source/documentation review closes only that scope; it is not a runtime or measurement pass and grants no execution authorization. Retain the entry and shared references. |

Families cover selected-only privacy/provenance, offline scorer closure, trusted
verification, artifact export, paired filesystem quotas, official scoring,
snapshot/cleanup, measurement and adapter-specific contracts. Refused datasets
remain represented with their refusal evidence. Adapters without a specific
journal entry have an explicit `journal-coverage` gap, rather than an inferred
passing status. AgentDojo's code-solvable selected metadata/privacy and defensive
detection review is closed by the synthetic controls documented in
[the Codex 180 report](../reports/agentdojo-metadata-privacy-codex180-report.md). The active
`production-route-admission` runtime blocker preserves the separate unresolved
production route. No production `run_one` or H0 readiness follows from these
tests, and no adversarial execution is delegated by this index.

Sports Understanding, SpreadsheetBench and StrategyQA now have
[cited source-only reviews](dataset-source-reviews-b2.md) and dataset-specific
journal entries. Their original `dataset-journal-gap` records remain in the
index as `measurement` follow-ups: the missing-review work is closed, while
source custody, scorer/runtime verification, coverage and shared admission
requirements remain open. The SpreadsheetBench review distinguishes the
`8c323e6` submission repair from production evidence and records unresolved
failure classification. Fever, Physics Questions and Reasoning Gym have explicit
pending-review journal pointers for the coverage regression; their substantive
reviews remain open. Review-pointer coverage is not gate closure.

AppWorld's `trusted-trial-evidence-writer` code blocker is closed for saved
verdicts by the synthetic writer/report tests documented in
[the Codex 183 report](../reports/appworld-trusted-trial-writer-codex183-report.md).
Scorer facts and explicit original trial identities survive persistence;
anonymous or compacted cells stay unaligned. Progress/events remain insufficient
measurement inputs. The successful-task and shared production gates remain open.

BFCL's `simple-ast-contract` remains `external` after partial pinned official
source acquisition in [Codex 195](../reports/bfcl-source-provenance-codex195-report.md)
and the [Codex 227 checker retry](../reports/bfcl-blob-retry-codex227-report.md).
`ast_checker.py` and `type_mappings.py` are acquired with Git blob checks;
direct model-config and Java/JS converter helpers, complete dependency/runtime
closure, archive binding and independent custodian approval remain missing.
The [code-only acquisition contract](../tests/fixtures/bfcl_ast_source/README.md)
requires independent archive/tag/commit linkage, original-file custody, an exact
approved receipt pin and a complete declared source projection. Its test-only
gate fails closed on missing approved acquisition. Synthetic integrity tests
establish neither official AST semantics nor release/index custody; function-name scoring
remains diagnostic-only and the separate production gates remain open.

ScienceWorld's platform-integrity prevention review is closed at source/test level,
but trusted effective mount enforcement and shared runtime gates remain blocked;
the historical V4 record remains incomplete with `effect=null`.

GSM8K's `scorer-protocol-comparability` and MuSiQue's
`joint-policy-comparability` documentation actions are closed by the
[B4 scorer/protocol review](../reports/b4-gsm8k-musique-metric-comparability.md).
Their original metric-comparability IDs are retired. GSM8K's decimal-aware numeric
scorer and trial-mean protocol remain separate from historical integer extraction,
upstream string EM and verifier-selected test@N. MuSiQue's platform joint reward,
legacy Answer F1 and official component columns remain separate; explicit run and
corpus denominator declarations do not authenticate historical pilots.
`matched-scorer-protocol-receipts` and `matched-joint-policy-receipts` now record
the independent measurement-coverage review still needed before an actual
comparison. All shared gates, journal claims and historical receipts are preserved;
this closure establishes no matched measured pair or production authorization.

2WikiMultihopQA's independent evaluator source gate is closed by the merged
Codex 197 code-only fixture and the [Codex 206 review](task-206-twowiki-provenance-review.md).
The active `independent-evaluator-source` blocker is retired; the schema has no
closed status. Both original evaluator versions, Apache-2.0 license and
commit/tree/blob linkage pass 38 offline provenance/synthetic tests with real
installed `ujson==5.10.0`. [Codex 226](task-226-ujson-wheel-report.md) acquired
the exact cp312 Linux x86_64 wheel from official PyPI metadata, checked its
RECORD and verified a fresh offline package installation without loading the
extension. Bundled-component notices, supported-image identity, loader/transitive
native closure and fresh image-bound native import/synthetic execution remain
`external` under `ujson-native-offline-closure`. Package installation alone does
not establish deployable native-runtime closure.
`hub-data-provenance` remains `external`, `recovery-comparability` remains
`measurement`, and all eight shared gates remain active. Source acquisition
does not authenticate Hub/data/alias provenance, repair mixed historical metrics
or establish production scorer/runtime readiness. The original partial pilot
and its `effect=null` are preserved.

Repository evidence locators are validated against local source text. Absolute
external paths are citations from the journal and are never opened by the
checker. Historical scores and receipt outcomes remain reported claims; this
review did not authenticate the external artifacts or measure any benchmark.
The registry never authorizes production execution.

The [hg-b3 four-dataset coverage review](../reports/measurement-coverage-hg-b3-review.md)
preserves ALFWorld's journal-complete generation and closes its historical identity
review. The subsequent [w-alf receipts](../reports/w-alf-receipts.md) close
`current-runtime-identity` with a fresh selected noop control, executed scorer/limit
identity and bounded cleanup observations; diagnostic score publication remains
withheld and both shared gates remain open. Bird's 28/34 measured-task claim
and 90 verify phases do not reconcile its planned 102 trials; its
`sql-coverage-reconciliation` gate remains open for original metadata. DS1000's
926/1000 host pool is distinct from 61 selected tasks and 183 planned trials;
`host-slice-archive-review` remains open and its effect unaccepted. HotpotQA's
synthetic schema review closes, but `timeout-aggregation-admission` records a
reproduced timeout-to-zero aggregation gap. Its stopped 94/1476 historical
protocol stays incomplete. These source-review closures do not certify missing
receipts, and all shared gates remain active.

TriviaQA's original evaluator acquisition gate is closed at source/test level by
the pinned code-only fixture and the [Codex 215 review](../reports/triviaqa-review-codex215-report.md).
The active `upstream-evaluator-acquisition` entry is retired. Normalization and
all-alias EM corrections are already merged; fabricated text, structured,
empty and absent answers retain the v2 scorer identity and upstream reference
in evidence written by the materialized verifier. No further adapter EM drift
was reproduced. The remaining `hub-export-scorer-linkage` blocker is `external`:
independently pinned export-to-loader/scorer code and metadata are still needed.
The [Codex 225 metadata review](../reports/triviaqa-hub-linkage-codex225-report.md)
pins an Apache-2.0 historical loader and manifest at `413205c1`, with Git
commit/tree/blob checks. That source trims normalized aliases but omits human
answers and domain/verified flags; no-context removes document lists. Exact Hub
revision metadata could not be reached, so this candidate does not attest the
export's lineage or answer-set preservation. The retained request specifies the
missing immutable conversion association and scorer-key/selection mapping.
Full grouping/denominator comparability, actual deployed scorer/harness hashes
and all eight shared gates remain unresolved. Historical identities and
acquisition-time findings remain unchanged; these checks authorize no diagnostic.

`historical_runs` preserves 11 incomplete, partially measured or pending-review
records, including both chess interruptions, both GAIA failures, Bird v4 and
v5's coverage limit, ScienceWorld's integrity failure, HotpotQA's stopped run,
the original invalid 2Wiki pilot, the initial AppWorld infrastructure smoke and
DS1000's pending review. Unknown run IDs stay `null`. All these registry effects
stay `null`; a later repair, recovery or measured zero does not rewrite them.
The committed [review baseline](../tests/fixtures/dataset_blockers/history-codex178.json)
is independently SHA-256 pinned by the checker. Existing records must remain
byte-equivalent in content; new historical records may be appended. Changing
the baseline pin is an explicit historical migration requiring review, not a
routine blocker-status update.

The review originally flagged two documentation conflicts: historical 50-task
pilot guidance in the platform log, and a chess audit sentence recommending an
already used recovery ID. The [B9 review](../reports/b9-appworld-protocol-and-pilot-guidance-review.md)
closes `pilot-guidance-conflict` as `done` for documentation only: the current
reuse table and onboarding guidance follow AGENTS.md; original platform contracts
and journal launch advice remain preserved with explicit supersession notices.
The shared entry and all dataset references remain in the registry. AppWorld's
task-facing protocol source review is complete, but `successful-task-evidence`
remains `measurement`; its 16/32-step measured zeros and unresolved successful-task
gate are retained. External smoke receipts were not opened or reverified.
The Chess future-launch guidance was already corrected,
so `recovery-id-conflict` is removed from the current blocker list. Every future
attempt must use a genuinely new unique run ID; never resume or overwrite any
used run ID, including `chess-pilot-h0-20261013` and
`chess-pilot-h0-20261013-recovery-01`. Both historical IDs, receipts and incomplete
statuses remain unchanged; termination and runtime gates remain unresolved.
Current planning follows `AGENTS.md`: a one-task/one-trial diagnostic after all
gates, separate receipt review, then at most `min(30, ceil(10% * eligible))` tasks
with one trial and one job for temporary reruns. A registry entry closes none of
those gates.

Validate without importing adapters or loading a task pool:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_dataset_blockers.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-blocker-registry-codex178-pytest \
  tests/quality/test_dataset_blockers.py
```

Coverage uses an AST read of module-level literal `KIND = "dataset"` declarations,
not `data.scaffolds.registry.published()`, whose imports can read mixed pools or hide
unprovisioned modules. This is a conservative source inventory of everything
the chooser may publish. New datasets, missing taxonomy/exercise assignments,
flat categories, unknown statuses, stale evidence and history changes fail
validation. Tests deny adapter imports, external reads, sockets and subprocesses.
