# Measurement coverage review — hg-b3 — 2026-10-15

Scope: only ALFWorld, Bird MiniDev, DS1000 and HotpotQA, from repository sources
in `/tmp/hg-b3`. Preserved receipts, historical registry records and their pinned
fixture remain unchanged. External archive paths below are journal citations;
they were not opened. No benchmark run, model call or whole-suite test was made.

The four source reviews are complete. ALFWorld's historical/current identity
review and HotpotQA's schema/timeout source review close their review gates.
Bird's actual task/trial/error reconciliation and DS1000's archive acceptance
cannot close without the missing receipt metadata; their measurement blockers
remain active with the precise missing evidence. No historical score is accepted
as a new effect, and all eight shared gates remain active for all four datasets.

## Original instructions, preserved verbatim

These `observed` and `next_action` fields were read before the evidence review.

### alfworld / current-runtime-review

> observed: The journal reports a complete historical 9-task/3-trial archive; no fresh current-runtime receipt was inspected.
>
> next_action: Retain the completed historical generation; review its identity separately from current runtime readiness.

### bird_minidev / sql-coverage-reconciliation

> observed: V5 is historically labeled complete, but only 28/34 tasks were measured; v4 remains incomplete.
>
> next_action: Reconcile task/trial coverage and SQL engine/schema errors from metadata before comparison; retain both historical labels and limits.

### ds1000 / host-slice-archive-review

> observed: The 61-task pilot covers a 926/1000 host slice; final archive and timeout classification are pending review.
>
> next_action: Review selected-ID/trial/timeout metadata and pool coverage before accepting any archive effect.

### hotpotqa / schema-timeout-measurement

> observed: User-stopped v4 measured only 94/1476 task-trials; timeout and malformed evidence are distinct from ordinary wrong answers.
>
> next_action: Review repaired schema and timeout classification against synthetic evidence, keeping the historical full-size protocol incomplete.

## Evidence boundary

The historical sources are the existing dataset sections of
`docs/adapter-integration-journal.md:284`, `:294`, `:304` and `:336`, and the
unchanged records in `tests/fixtures/dataset_blockers/history-codex178.json`.
Repository file inventory and searches for the run IDs/coverage figures found
no raw receipt, selected manifest, original trial ledger or console timeout log
for these generations. `docs/task-243-diagnostic-readiness.json` repeats the
registry summaries; it is not independent receipt evidence. External references
are not permission to leave this checkout or proof of receipt contents.

For all four generations, repository summaries do not supply a complete binding
of selected IDs, original trial identities, source/split/manifest/model/harness
hashes, endpoint, measured/invalid/unmeasured counts, budget/resource observations
and cleanup receipt. Configured limits and source pins cannot fill those cells.

## ALFWorld: historical identity review closed; current runtime remains open

- **Established history:** `docs/adapter-integration-journal.md:286` retains
  `complete`; `:287` records 9 platform train-side tasks, 3 trials, `jobs=1`;
  `:289` names `alfworld-h0-10pct-v4` and external oracle/noop logs; `:292` cites
  the external archive index. This is a nominal **27-task-trial protocol**, not
  independently verified 27 measured trials. The journal gives no scalar score
  or original trial outcomes. Keep the completed historical generation intact;
  the controls are not a measured model curve.
- **Pool and split:** `data/datasets/alfworld.py:78` describes a platform-imposed
  hash division; `:106` identifies the 134-task `valid_unseen` official evaluation
  pool and says the official 3,553-task training split is not loaded. Historical
  “train-side” therefore names the platform side, not official training data.
- **Metric and limits:** `data/datasets/alfworld.py:69` defines success as any
  replayed `won` within the 30-action budget. `:527` describes host execution
  defaults (2 CPUs, 4 GB, 300-second harness/check limits). Those declarations
  are limits, not observed utilization or verified enforcement. Current source
  is not authenticated as the historical scorer/runtime.
- **Classification:** `data/datasets/alfworld/verify/check.py:139` and `:158`
  return unmeasured on missing game/replay failure. `:178` records measured
  reward, replayed steps and `budget_exhausted` after successful replay. An
  episode losing within its action budget can be measured zero; inability to
  execute the environment is unmeasured. Existing scorer tests explicitly
  distinguish the local empty-episode convention from upstream's undefined
  empty reduction (`tests/quality/test_alfworld_official_scorer.py:172`).
- **Closed:** `current-runtime-review` as a source review of the preserved
  historical identity, without rerunning or independently recertifying it.
  **Open:** replacement `current-runtime-identity` (`runtime`), requiring current
  selected controls and runtime/scorer identity; any historical comparison still
  needs original receipts and trial/provenance binding. Shared gates stay open.

## Bird MiniDev: label/semantics review closed; metadata reconciliation open

- **Established history:** `docs/adapter-integration-journal.md:296` preserves
  v5's historical `complete` label and v4's `incomplete` label. `:297` reports
  34 selected tasks ×3 trials, `jobs=1`, **102 planned task-trials**, **28/34
  measured tasks**, and **90 verify phases**. Six tasks lack certified task-level
  measurements. `:299` reports v5 score `0.3214285714`, without its trial ledger.
  V4's exact run ID remains null; v5 remains `partial-measurement`, `effect=null`.
- **What cannot be reconciled:** 90 verify phases are not 90 verified measured
  trials. Neither 30 fully measured tasks nor 28×3=84 measured trials follows.
  There is no repository task/trial mapping, duplicate/failed-phase account,
  per-error count or denominator reconstruction. Do not attribute all six gaps
  to database/schema failures or convert missing coverage to zeros. The reported
  score is not a certified mean over all 34 selected tasks.
- **Metric and error policy:** `data/datasets/bird_minidev/verify/check.py:39`
  defines SQLite EX by result-set equality; `:20` excludes Soft-F1 and R-VES.
  The implementation executes **gold first**, despite its stale introductory
  ordering description (`:107`, `:119`). Missing database/empty gold/gold-query
  engine, schema or query-timeout errors yield no reward, `measured=false`,
  exit 3 (`:82`, `:99`). With runnable gold, missing prediction or prediction
  SQL/schema/query-timeout errors are measured EX zero (`:114`). The query
  timeout of 30 seconds (`:34`) is a metric limit, not a recorded timeout count.
  Gold versus prediction missing-column cases are exercised by synthetic
  tests (`tests/quality/test_measurement_coverage_hg_b3.py:105`).
- **Split and trial interpretation:** the platform hash split is declared in
  `data/datasets/bird_minidev.py:310`. Current aggregation excludes explicitly
  unmeasured tasks (`eval/run/runner.py:982`) and retains actual trial lists
  (`:1069`); it cannot infer the historical allocation of the 90 phases.
- **Closed:** source review of historical labels, denominator limits and SQL
  failure semantics. **Open:** `sql-coverage-reconciliation` stays `measurement`:
  require the original selected-ID/phase/trial/error ledger, SQLite/schema and
  scorer identity, exact counts and score-denominator reconstruction before
  comparison. Both historical records and all shared gates remain unchanged.

## DS1000: host-pool interpretation closed; archive acceptance open

- **Established history:** `docs/adapter-integration-journal.md:306` says final
  archive review is pending; `:307` records 61 platform train-side tasks ×3
  trials (**183 planned task-trials**), from the **926/1000 host slice**;
  `:309` names `ds1000-h0-10pct-v4` and reported score `0.2076502732`.
  `:311` requires selected-ID/trial/timeout verification; `:312` says archive
  not yet appended. Actual measured tasks/trials and timeout counts are unknown.
- **Pool coverage is not pilot coverage:** `data/datasets/ds1000.py:12` describes
  the documented 926-row host pool and 74 exclusions: 47 TensorFlow-import rows
  plus 27 frozen dependency/library/network failures (`:16`, `:25`, `:90`).
  `:39` places the original oracle sweep outside git. Its reference-code host
  availability claim is separate from this pilot. Nominal 61 selected tasks are
  6.59% of the host pool and 6.1% of the released 1000 rows; neither percentage
  establishes measured coverage or representative sampling. All adapter rows
  originate from the official test pool (`:57`); train/eval is imposed by hash
  (`:160`). The run name `10pct` is a label, not proof of its sampling fraction.
- **Metric and timeout limits:** `data/datasets/ds1000/verify/check.py:29`
  constructs `test_execution` plus conditional `test_string`; `:41` configures
  the 120-second inner timeout and `:54` the 130-second outer timeout. These are
  deadlines, not measured elapsed times. `:55` through `:65` write a zero and
  `measured=true` for a serialized failing result, including a synthetic
  `result="timed out"` (`tests/quality/test_measurement_coverage_hg_b3.py:130`).
  This control supplies no historical timeout count. Inspect the original result
  string and lifecycle/outer-timeout records before calling a zero an ordinary
  wrong answer; `measured=true` alone does not establish validity.
- **Closed:** source review of host-pool versus selection scope, scorer
  construction and configured timeout limits. **Open:** `host-slice-archive-review`
  stays `measurement`: the original selected manifest and original task/trial,
  timeout/error and provenance metadata are missing. Archive acceptance/append
  remains pending; `ds1000-v4-pending-review`, `effect=null`, stays unchanged.
  All shared gates remain open.

## HotpotQA: schema/timeout review closed; timeout aggregation still blocked

- **Established history:** `docs/adapter-integration-journal.md:338` retains
  user-stopped `incomplete`; `:339` reports 492 platform train-side tasks ×3 =
  **1476 planned task-trials**, with **94 completed**. The original registry
  calls those 94 measured, but the repository has no trial ledger to validate
  that attribution. There are **1382 uncertified planned task-trials**; their
  attempted/unattempted/timeout/schema/invalid breakdown and unique task coverage
  are unknown. Ninety-four trials does not mean 94 complete three-trial tasks.
  The exact run ID remains null, `effect=null`. `:340` reports that a task can
  spend 60 minutes; this is not a reconstructed timeout count or score.
- **Repaired schema:** `data/scaffolds/qa/prediction.py:12` validates the closed
  versioned object and optional nonempty-title/nonnegative-integer evidence pairs,
  rejecting boolean indices. `data/scaffolds/qa/verify/check.py:185` checks JSON
  syntax/schema before importing the scorer; `:104` returns exit 3 with
  `measured=false` and no new reward. Fabricated malformed JSON, wrong evidence
  types, boolean and negative indices exercise that path in
  `tests/quality/test_measurement_coverage_hg_b3.py:51`.
- **Metric:** `data/datasets/hotpotqa.py:37` reports answer EM/F1, support EM/F1
  and joint EM/F1, with **reward=joint EM**. Missing/empty evidence is valid
  and yields joint zero; partial valid support can also yield joint EM zero
  despite a correct answer. Complete correct support yields one. These measured
  cases are tested through the real QA check boundary with fabricated gold,
  while pool loading is stubbed (`tests/quality/test_measurement_coverage_hg_b3.py:23`,
  `:65`). Malformed support is unmeasured, not that valid zero. This establishes
  current source behavior, not which scorer bytes the historical run used.
- **Timeout classification limit:** `data/scaffolds/qa/base.py:386` configures
  a 3600-second verifier limit; it is not an outcome. Native verifier expiration
  returns `passed=false` and timeout detail, **without `score` or `unmeasured`**
  (`eval/verification/verifier.py:578`). Aggregation checks `unmeasured`
  (`eval/run/runner.py:982`) and otherwise defaults a failing verdict to zero
  (`:1061`). A synthetic expiration plus the real aggregation path reproduces
  `per_task={"synthetic-timeout":0.0}`, `invalid={}`
  (`tests/quality/test_measurement_coverage_hg_b3.py:82`). That is a demonstrated
  classification gap, not a valid benchmark zero. Agent timeout is also recorded
  separately before artifact verification (`eval/run/runner.py:436`); its detail
  alone cannot prove an ordinary wrong answer or a verifier timeout.
- **Closed:** `schema-timeout-measurement` as the requested synthetic source
  review, with the repaired schema established and timeout limitation exposed.
  **Open:** replacement `timeout-aggregation-admission` (`code-solvable`): timeout
  needs an explicit outcome preserved through persistence/aggregation rather
  than fallback zero, plus current selected scorer/control identity and all
  shared gates. Historical full-size protocol remains **incomplete**. Do not
  resume or repeat it; any separately authorized future run must use a fresh ID
  and current staged policy after gates pass.

## Validation

Only the registry checker and focused tests are used:

```
python3 tools/check_dataset_blockers.py
python3 -m pytest -q tests/quality/test_measurement_coverage_hg_b3.py tests/quality/test_dataset_blockers.py
```

Final results: **68 passed in 2.89s**, no failures or skips. Registry checker:
`valid=true`, 28 datasets, 8 shared blockers, 11 historical records,
`production_authorized=false`. `git diff --check` passed. The checker and focused
history tests confirm that the pinned incomplete/partial/pending records retain
their exact content. Only the four owned dataset blocker objects change in the
registry; no shared entry, taxonomy or historical record changes.

The synthetic controls exercise current source with fabricated inputs. They do
not fill missing historical coverage, certify external receipts, alter scorer
policy or authorize model/H0 execution. No adapter/runtime implementation changed.
