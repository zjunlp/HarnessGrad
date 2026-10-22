# Blocker closure assessment — open blockers, clause by clause

Parent assessment, 2026-10-15, opened at registry commit `21d6ebbf` and updated as blockers closed. This document does not change the
registry. Its purpose is to answer one question for every open blocker: **which of its own
`next_action` obligations are already discharged, which are not, and what class the rest belong to** —
so that "14 open" becomes a set of concrete dispositions instead of a number. It currently describes
**11** open blockers; the count and every supersession are recorded in the closure sections below.

The controlling rule is the objective's: a blocker closes only on the kind of evidence its own
`next_action` names. Nothing here is a claim that an obligation is met unless the record already holds
the artefact.

## Obligation classes used below

| class | meaning |
| --- | --- |
| `SATISFIED` | The named artefact exists in the record now (evidence file, bound hash, review document). |
| `WITHDRAWN` | The operator withdrew the requirement; it is recorded in `registry/withdrawn-requirements.md` and no longer exists as a gate. |
| `PRE-DIAGNOSTIC` | A precondition on a *future, separately authorized* diagnostic, not a condition on closing the blocker. Recorded so it is not lost. |
| `PENDING-WORK` | Repo-local analysis or artefact work that is genuinely obtainable and not yet done. |
| `PENDING-UPSTREAM` | Requires an artefact from an authoritative public source; a search is in flight or has returned a bounded negative. |
| `PENDING-RUN` | Requires a live measured execution under the staged pilot policy (authorization plus a run). |
| `PENDING-INPUT` | Requires an artefact only the operator or a third party can supply. |
| `LIMIT-CANDIDATE` | The original artefact no longer exists anywhere the record can reach; a candidate for the operator's proven-unobtainable standing-limit treatment. |
| `REFUSED` | Refusal the operator instructed not to revisit. **No longer permanent for `tau2_bench`:** lifted conditionally on 2026-10-22 (`docs/refusal-lift-decision.md`), while its adapter still refuses in code. |

## Summary

| dataset / blocker | status | disposition | blocking class(es) |
| --- | --- | --- | --- |
| `kumo / kumo-measurement-coverage` | measurement | single-task integration verified; broader evaluation and resource closure remain separate | `PENDING-RUN`, `PRE-DIAGNOSTIC` |
| `api_bank / retain-contract-refusal` | external | permanent refusal | `REFUSED` |
| `nestful / retain-contract-refusal` | external | permanent refusal | `REFUSED` |
| `sotuqa / retain-contract-refusal` | external | permanent refusal | `REFUSED` |
| `tau2_bench / retain-contract-refusal` | code-solvable | exclusion lifted conditionally 2026-10-22 (`docs/refusal-lift-decision.md`); the adapter still refuses in code | `REFUSED` |
| `aime / dataset-journal-gap` | measurement | the operator's bounded first-five custody packet, plus a comparability statement for the Harbor port | `PENDING-INPUT`, `PRE-DIAGNOSTIC` |
| `cooperbench / runtime-group-coverage` | measurement | all 652 pair identities stay explicit; reference controls are not full-benchmark performance | `PRE-DIAGNOSTIC` |
| `locomo / adapter-acceptance-pending` | measurement | accept the Conda receipt and pin scope/model/budget before any model evaluation | `PRE-DIAGNOSTIC` |
| `memoryagentbench / judge-dependent-subset` | measurement | judge provider and resolved model on every point; without one the judge-dependent questions are unmeasured | `PRE-DIAGNOSTIC` |
| `personamem_v2 / excluded-source-question` | measurement | original/excluded/eligible/measured counts under the valid-question metric | `PRE-DIAGNOSTIC` |
| `strongreject / judge-model-metric-identity` | measurement | the resolved judge model *is* the metric identity, and two judge profiles never pool | `PRE-DIAGNOSTIC` |

**Withdrawn 2026-10-16 — `bfcl_v3 / simple-ast-contract`, `triviaqa / hub-export-scorer-linkage`
and `appworld / successful-task-evidence`.** All three rows left this table; **no non-refusal
blocker remained in the registry at that review**, so the class vocabulary below now classifies nothing and is
retained only as the historical scheme. The three went for two different reasons, and the
difference is recorded rather than flattened:

* `bfcl_v3` (`PENDING-WORK`, then `PENDING-RUN`) and `triviaqa` (`PENDING-UPSTREAM`) were withdrawn
  as **scope** limits — "relax these two, there is no need for evidence this rigorous." The missing
  observations are still missing and are **not** claimed unobtainable; a producer-published build
  manifest or upstream execution receipt would supersede each entry.
* `appworld` (`PENDING-RUN`) was withdrawn because the gate's purpose — establishing that the
  appworld measurement path *can* succeed — was answered by measurement: the task-independent
  fabricated controls pass, and the last authorized diagnostic performed, recorded and replayed
  completion while the platform's own checker faithfully reproduced a `0.0` from actor behaviour.
  Because the gate admitted only success, no correctly executed failure could ever close it.

Named limits and the scope note: `registry/withdrawn-requirements.md`. Reasoning:
`docs/evidence-status-decision.md`. `appworld`'s limits are the sharpest — **one** task was ever
attempted (of 732; 147 eligible) and no successful measured actor task exists, so nothing here is
evidence about the dataset's difficulty or headroom, and a usable appworld number needs a fresh,
separately authorized multi-task run.

## The four permanent refusals

`api_bank`, `nestful`, `sotuqa`, `tau2_bench` each carry one 113-character clause: retain the explicit
refusal and exclusion, do not provision or run the dataset, do not substitute its metric. Each is
`SATISFIED` by retention and is `REFUSED` by operator instruction — the objective explicitly says not to
attempt closure. Unchanged.

## Closed under reading A, 2026-10-15 — `physicsquestions` and `sportsunderstanding`

The operator chose reading **(A)**: a `dataset-journal-gap` is discharged when the journal coverage its
own `next_action` names exists, and the diagnostic-time clauses are preconditions on a *future,
separately authorized* diagnostic rather than conditions on closure. Both blockers are therefore
**closed** and removed from the active registry; the pre-closure texts stay readable in this file's
git history and the decision is recorded in the registry's own `review.notes`.

At closure, every clause of each `next_action` was satisfied or withdrawn:

| clause | class | where it stood |
| --- | --- | --- |
| Retain installed-to-ReWOO and ordered pinned-BIG-bench row linkage | `SATISFIED` | `tests/fixtures/hg_wprov/README.md` |
| Retain the bounded first-five selected-only custody record | `SATISFIED` | `tests/fixtures/hg_wsel/physicsquestions.json`, `.../sportsunderstanding.json` |
| Cite the dataset's own migrated review | `SATISFIED` | `docs/dataset-reviews/<dataset>.md`, provisioned as `/site/dataset-reviews/<dataset>.md` |
| Report the local metric separately, with no upstream-equivalence claim | `SATISFIED` | `registry/w-metriccmp-comparability.md` - physicsquestions is asymmetric normalized accepted-spelling exact match, **different** from BIG-bench `exact_str_match`; sportsunderstanding is generated-text ASCII-letter-boundary label accuracy, **different** from candidate-log-probability `multiple_choice_grade` |
| Do not infer a historical converter revision or runtime execution | constraint | recorded in the same document |
| For any separately authorized diagnostic: reconcile the run-specific manifest/export, exact rows and split mode/side; establish offline dependencies and dataset-specific trusted/pristine execution; record eligible/selected/task/trial/invalid/unmeasured coverage | `PRE-DIAGNOSTIC` | statement only; **no diagnostic is authorized** and none of it is waived by this closure |
| Preserve the withdrawn `official-scorer-identity` decision and the shared-requirement withdrawals | `WITHDRAWN` | `registry/withdrawn-requirements.md` |

**This closes no measurement and claims no score.** Neither dataset has ever had a measured run, no
coverage count is asserted anywhere, and the diagnostic-time obligations above remain requirements of
any future diagnostic - which still needs its own authorization, a fresh run ID, a split/side binding
and the staged policy. The closure removes a bookkeeping blocker, not a verification step.

**The rule is now fixed, so it does not need re-deciding.** It does **not** generalise to the other
four journal-coverage blockers, and the registry note says why: `fever` still owes an upstream
producing-execution receipt, `reasoning_gym` an original generation receipt plus behavioural family
eligibility, `strategyqa` the original scorer/split definition, and `spreadsheetbench` measured
coverage plus scorer and formula/LibreOffice runtime evidence. Each of those is a named artefact that
does not exist yet, not a diagnostic-time precondition.

## The ten that need something

**`appworld / successful-task-evidence`** — `PENDING-RUN`. Retained receipts, the incomplete Docker
exit-125 attempt, the raw-versus-staged distinction and the generic protocol review are all
`SATISFIED`. The gate itself is explicit: "only a genuine successful measured actor task under the
staged pilot policy can do so". The review additionally found a **task-independent** completion-guidance
gap (`data/datasets/appworld.py:234-236` versus `data/datasets/appworld/verify/check.py:45-56`) whose
harness-side remedy would change the measurement instrument; that is an operator decision and was not
applied. Two operator decisions are therefore outstanding: whether to authorize a diagnostic, and
whether the actor guidance may be changed.

**`spreadsheetbench / dataset-journal-gap`** — **CLOSED** on 2026-10-15 under reading (A), seventh
closure. The eligibility inventory, the fixed formula/cache runtime, complete workbook/mirror custody,
the one-variant metric statement, the official-range-zero policy and the vacuous-pass fix are all
`SATISFIED`; the only clause left was measured coverage, and the operator read it as a precondition on
a future separately authorized diagnostic rather than a condition on closure. The clause-by-clause
basis and the exact wording of the decision are in the section at the end of this document.

**`bfcl_v3 / simple-ast-contract`** — `PENDING-WORK`, then `PENDING-RUN`. Archive/tag/commit/member
acquisition and the code-only graph review are `SATISFIED`. Still required: exact offline
third-party/native runtime custody, the concrete local-model handler and response grammar, integrity
pins for the code-only projection, containment of expression evaluation, the fabricated-differential
full-call contract, and finally actual official scorer execution. `w-bfclrt` is working the custody
half.

**`fever / dataset-journal-gap`** — **CLOSED** on 2026-10-15 under reading (A), on an operator decision to
record the producing-execution receipt as a **scoped** standing limit; see the closure section below. The
two historical objects the search had left unread are now `SATISFIED` (both read, neither carrying a
producing-execution receipt), the metric choice is `SATISFIED` by naming option (a), and the
producing-execution provenance is `WITHDRAWN` with its bounded scope named.

**`strategyqa / dataset-journal-gap`** — **CLOSED** on 2026-10-15 under reading (A); see the closure
section below. Custody, the local EM statement and the EM-identity limit were already `SATISFIED`; the
original scorer was `FOUND` and the split membership is now `FOUND` and bound, so the obligation the
earlier assessment recorded as **undetermined** - "an original scorer/split definition" - is discharged.

**`reasoning_gym / dataset-journal-gap`** — **CLOSED** on 2026-10-15 under reading (A); see the closure
section below. Distribution, source and installed-member custody, the declared pool hash and the frozen
family/order inventory were `SATISFIED`; the original generation environment/date/order metadata and
exact reproduction from recorded inputs are now `WITHDRAWN` as proven unobtainable, and the independent
behavioural `reference=1 / empty=0` family eligibility determination is now `SATISFIED`.

**`triviaqa / hub-export-scorer-linkage`** — `PENDING-UPSTREAM` plus `PENDING-WORK`. The bound first-five
eval-side manifest/export, the v2 identity and the retained Hub/scorer bytes are `SATISFIED`. Still
required: the metadata-only HumanAnswers / membership / duplicate / scorer-key / denominator review, and
an enumerated re-inspection of the authoritative record for an immutable build/export execution receipt.
Both are `w-trivia2`'s task; the withdrawn-gate boundary does not condition closure.

**`hotpotqa / historical-comparison-receipts`** — `LIMIT-RECORDED` (2026-10-15). The candidate v4 ID
`hotpotqa-h0-10pct-v4` is recovered, and the historical `ls` listing proves the v4 split, console log and
budget ledger **existed**; the original per-trial ledger is absent in the documented local scope and the
binding to the reported 94/1382/1476 coverage is unresolved. Timeout classification is `SATISFIED` and
the frozen record keeps `run_id=null` / `effect=null`. Unlike `bird_minidev` and `ds1000`, the files are
not "not present in this repository and not published upstream" — they existed on this machine and are
gone — so the existing withdrawal wording would overstate it and `w-hotpotrec` correctly refused to
propose one. This was the clearest candidate for the operator's standing-limit rule, and it is now
**recorded** under it in `registry/withdrawn-requirements.md` as
`hotpotqa / historical-comparison-receipts / original-v4-receipts`, with wording that does not
overstate the case: the receipts existed and were lost, the run was a local pilot so no upstream
source can hold them, the enumerated scope is named, and the deletion cause is left unattributed.
The candidate ID, the filenames and the enumerated absence stay recorded; the blocker was later
**closed** on the same reading - see the closure section at the end of this document.

## What is not in doubt

Two blockers were closed under reading (A) on 2026-10-15 and both are recorded above with their
clause-by-clause basis; that closure waived no verification step. Five more - `reasoning_gym`,
`hotpotqa`, `strategyqa`, `fever` and `spreadsheetbench` - were closed under the same reading the same
day, each with its own clause-by-clause section below, and that closure waived no verification step
either. For every blocker that remains open,
nothing here is discharged by a review that has not been done: every `SATISFIED` entry names an artefact
that exists, every `PRE-DIAGNOSTIC` entry is labelled as such rather than counted as closure, and no measured score is asserted anywhere. The four permanent refusals are untouched.

## Reverse audit — is every cleared dataset cleared for a recorded reason?

A forward assessment of the 7 open blockers says nothing about the 21 datasets that currently carry
none. If a blocker had ever been dropped without a disposition, the registry would show a dataset as
clear for no recorded reason, which is a worse defect than an open blocker. The reverse audit compared
the blocker set per dataset at `db32421c` (before the withdrawal wave) and at `1825cd0e^` (before the
bulk `reports/`/`docs/` deletion) against the commit the audit was taken at:

* `1825cd0e^` and `db32421c`: 28 datasets, **22** blockers. At the reverse audit's own commit: 28
  datasets, **14** blockers (8 at the reverse audit, **7** now; the seven reading-(A) closures below
  are documented steps, not unrecorded drops).
  Eight blockers were removed and eight withdrawal entries exist in
  `registry/withdrawn-requirements.md`, one per removal: `livecodebench/row-attestation`,
  `twowikimultihopqa/hub-data-provenance`, `twowikimultihopqa/ujson-native-offline-closure`,
  `bird_minidev/sql-coverage-reconciliation`, `ds1000/host-slice-archive-review`,
  `gaia/frozen-lifecycle-receipt`, `tau_bench/local-state-lifecycle` and
  `chessbench_puzzles/interruption-closure`.
* A mechanical check found **zero** removals not explained by either a withdrawal entry or the
  separately recorded `twowikimultihopqa/recovery-comparability` review closure.
* The remaining seven cleared datasets - `agentdojo`, `alfworld`, `gsm8k`, `jericho`, `musique`,
  `scienceworld`, `terminal_bench` - carried no blocker at either baseline, so their clearance predates
  this phase's registry and is not a result of anything removed here.

Conclusion: no dataset is currently clear because a blocker was silently dropped. `22 - 8 = 14`
accounts for every one of those removals, and the seven closures under reading (A) on 2026-10-15
(`physicsquestions`, `sportsunderstanding`, `reasoning_gym`, `hotpotqa`, `strategyqa`, `fever` and
`spreadsheetbench`) take the active set from **14** to **7**. Each is recorded above and in the registry's `review.notes`, so the count is
accounted for in documented steps rather than one unexplained drop.

## Closed under reading A, 2026-10-15 - `reasoning_gym`

The registry's note records the decision; this section is the clause-by-clause basis, and it is the one
the earlier assessment named as the reason `reasoning_gym` stayed open: *"an original generation receipt
plus behavioural eligibility"*. Both halves are now discharged, and every other clause is diagnostic-time.

| clause from the pre-closure `next_action` | class | where it stands |
| --- | --- | --- |
| Retain distribution/source/installed-member custody and exact frozen-pool hash/family/order evidence, the original pool bytes, and the preserved 84-row/six-family failed-regeneration evidence | `SATISFIED` | `registry/w-journalcov-review.md`, `registry/w-recgym-receipts.md`; pool 3315981 bytes, SHA-256 `c0ff4eae...` |
| Record the original launch/environment/clock/hash-order metadata and exact historical seed-only reproduction **as unavailable limits** | `WITHDRAWN` | `registry/withdrawn-requirements.md` -> `reasoning_gym / original-generation-process-receipt`, after the enumerated 733-session / 23773-object / 395197-file census in `registry/w-recgym-receipts.md` and `registry/w-recgym-validation.txt` |
| Retain the independent host-package two-point behavioural eligibility record | `SATISFIED` | `registry/w-gymelig-eligibility.md`, `tests/fixtures/hg_gymelig/`: 89/89 families, 20 indices each, 1780 rows, 3560 direct `score_answer` calls, reference=1 and empty=0, zero exceptions/refusals, pool digest unchanged |
| Retain the bounded operator first-five selected manifest/export | `SATISFIED` | `tests/fixtures/hg_wsel/reasoning_gym.json`; mixed-side, not a single-side diagnostic specification |
| Cite the dataset's own migrated review | `SATISFIED` | `/site/dataset-reviews/reasoning_gym.md`, `# Reasoning Gym source-only journal review` |
| Do not require manufacturing absent original records or substituting a new seed/family/order/current environment | constraint | recorded in the withdrawal entry |
| For any separately authorized diagnostic: resolve applicable split/side and run binding, establish deterministic transitive dependencies without scoring extras and metric identity, obtain actual selected scorer/runtime execution evidence under the declared runtime | `PRE-DIAGNOSTIC` | statement only; **no diagnostic is authorized** and none of it is waived by this closure |
| The `official-scorer-identity` proof obligation | `WITHDRAWN` | `registry/withdrawn-requirements.md` |

**This closes no measurement and claims no score.** `reasoning_gym` has never had a measured run, no
coverage count is asserted anywhere, and the diagnostic-time obligations above remain requirements of any
future diagnostic - which still needs its own authorization, a fresh run ID, a split/side binding and the
staged pilot policy. One judgement is worth stating plainly because it is the whole difference between
closing and staying open: the generation-receipt clause is discharged by a **recorded limit**, not by
finding the artefact. What the census proved is that the retained readable corpus does not contain it - the
earlier bounded regeneration differed in 84 rows across six families, and the process fields were never
recovered. The withdrawal entry says exactly that, and does not claim a historical or unrestricted-machine
absence.

## Correction and update, 2026-10-15 — a family-level contract the forward assessment missed

The forward assessment above read each blocker's `next_action` and stopped there. That was
incomplete: the `journal-coverage` **family** carries a contract enforced by the test suite rather
than by the prose, and for three datasets it was not met.

* `tests/quality/test_dataset_journal_coverage.py:43-62` requires every `journal-coverage` blocker's
  `evidence` to contain a **per-dataset** reference `/site/dataset-reviews/<dataset>.md` with the
  locator `# <dataset> source-only journal review`, and pinned `sportsunderstanding`,
  `spreadsheetbench` and `strategyqa` as `xfail(strict=True)` in a `_KNOWN_JOURNAL_GAPS` set whose
  comment reads "Strict xfail makes a resolved gap fail until its stale exception is removed".
  Those three pointed only at the combined `/site/dataset-source-reviews-b2.md`, so they carried a
  real, recorded journal-coverage debt that no `next_action` mentioned.
* The two test files also encode two filing conventions, which is why this was invisible from the
  registry alone: `tests/quality/test_dataset_blockers.py:132-138` maps the b2 three to the combined
  document and asserts that entry is present (it was), while the family guard above demands a
  per-dataset one.

**Resolved in this commit.** The b2 document's three sections
(`## sportsunderstanding source-only review`, `## spreadsheetbench source-only review`,
`## strategyqa source-only review`) were extracted **verbatim** into
`docs/dataset-reviews/{sportsunderstanding,spreadsheetbench,strategyqa}.md`, each with a provenance
comment stating that this is a filing split and not a new review, plus the title line the per-dataset
convention requires. The carriers were provisioned at the conftest resolver path with
`PROVENANCE.json` entries recording `source_path: docs/dataset-source-reviews-b2.md`,
`source_commit: 1825cd0e^` and an explicit `transform` note, and the exact evidence entry was added to
each of the three blockers. `_KNOWN_JOURNAL_GAPS` is now empty, with its purpose and its "never add a
dataset here just to make an omitted review pass" warning retained.

**Effect on the dispositions above.** `spreadsheetbench` and `strategyqa` stay open on their own
`next_action` items (measured coverage plus scorer/formula runtime; the original scorer and split).
`sportsunderstanding` is now a closure candidate on exactly the same footing as `physicsquestions`:
its `next_action` obligations were already satisfied, and the family-level debt that silently sat
beside them is now discharged. The closure-semantics question at the end of the "two closure
candidates" section is unchanged and still belongs to the operator; what changed is that
`sportsunderstanding` no longer has a second, hidden reason to stay open.

**Method note.** A blocker can be gated by its family's contract as well as by its own `next_action`.
Any future pass over this registry must read the family guards in `tests/quality/` alongside the
registry text, and a strict `xfail` in those guards is debt, not a passing state.

## Closed under reading A, 2026-10-15 - `hotpotqa / historical-comparison-receipts`

The parent filed this as a **closure candidate** first, because it is the first time reading (A) is
applied to a `measurement-coverage` blocker rather than to a `dataset-journal-gap`; the operator then
confirmed the closure. The basis is unchanged by the confirmation: every clause of the blocker's current
`next_action` is satisfied, recorded as a limit, or a precondition on a future diagnostic — and the
operator's standing rule is that a requirement which is **proven unobtainable is withdrawn and recorded
as a limit rather than left open**. Leaving it open would reimpose, as an open blocker, a requirement
this registry has already withdrawn, which is the failure mode that rule exists to prevent.

| clause from the current `next_action` | class | where it stands |
| --- | --- | --- |
| The original v4 selected-ID/trial/error/timeout receipts, and its source/split/manifest/model/harness provenance receipts | `WITHDRAWN` | `registry/withdrawn-requirements.md` -> `hotpotqa / historical-comparison-receipts / original-v4-receipts`; enumerated scope: 722 `~/.codex/sessions` transcripts, the full object store/history/reflog/stash, `runs/`, `runs_discarded_*`, `.hashstate`, `tests/fixtures/`, the named archives and `/mnt/20t/xubuqiang/hg-census/` |
| Historical measured/invalid/unmeasured counts and lifecycle/cleanup reconciliation for that run | `WITHDRAWN` | the same entry: they "go with" the receipts |
| The candidate ID `hotpotqa-h0-10pct-v4` and its unresolved binding to the reported 94/1382/1476 coverage stay recorded as an explicit comparability limit | `LIMIT-RECORDED` | `registry/w-hotpotrec-receipts.md`, `registry/w-hotpotrec-evidence.json`; `historical_runs` keeps `run_id=null` and `effect=null` |
| Preserve the old full-size protocol as incomplete; never resume, rerun or overwrite it | `SATISFIED` | pinned by `tests/quality/test_measurement_coverage_hg_b3.py:155-215` |
| Timeout classification, persistence and aggregation | `SATISFIED` | synthetic closure; `/site/w-hotpot-timeout-closure.md`, cited in the blocker's evidence |
| Retain the bounded first-five selected manifest/export and declared per-task sides | `SATISFIED` | `tests/fixtures/hg_wsel/hotpotqa.json`; mixed-side, not a diagnostic specification |
| Current selected scorer/control admission for any separately authorized future diagnostic, with a fresh ID and the current staged policy | `PRE-DIAGNOSTIC` | statement only; **no diagnostic is authorized**, and nothing above waives it |
| Withdrawn shared gates do not condition closure or admission | `WITHDRAWN` | `registry/withdrawn-requirements.md` |

**This closes no measurement and asserts no score.** The v4 run stays incomplete with `effect=null`, the
94/1382/1476 coverage stays unverified and unbound, unknown outcomes stay unmeasured and never become
zeros, and every diagnostic-time clause remains a requirement of any future diagnostic. What the closure
removes is a blocker whose only remaining content is a limit this registry already records.

**The guard change is a move, not a deletion.** `tests/quality/test_measurement_coverage_hg_b3.py:155-215`
had parametrized hotpotqa with `active_id="historical-comparison-receipts"` and asserted that blocker
**exists** with status `measurement`, that its evidence cites the timeout-closure record, that
`schema-timeout-measurement` is absent, and that its `shared_blockers` set tracks the live shared set.
hotpotqa now takes the `withdrawn` branch beside `bird_minidev` and `ds1000`, with assertions over its own
withdrawal entry (`**Searched:**`, the enumerated on-machine scope, the "existed and were lost" wording
that distinguishes it from the never-captured pair, and the retained `run_id=null`/`effect=null`). The
`historical_runs` comparison, the live-`shared_blockers` comparison and the timeout-closure citation all
still run against the cleared dataset - the citation is now read from this closure record, which carries
the exact `path`/`locator` pair verbatim below - so nothing was deleted to make the closure pass.

Timeout-closure citation retained verbatim:
`{"kind": "external-reference", "path": "/site/w-hotpot-timeout-closure.md", "locator":
"# HotpotQA timeout outcome/persistence/aggregation closure - w-hotpot"}`.

The reverse audit above takes the active set from 11 to **10** with this closure as its fourth documented
step. The summary table's `LIMIT-RECORDED` row for `hotpotqa` is gone because the blocker is gone; the
limit itself is unchanged in `registry/withdrawn-requirements.md` and
`registry/w-hotpotrec-receipts.md`.

## Closed under reading A, 2026-10-15 - `strategyqa`

The earlier assessment left this blocker open for exactly one non-diagnostic-time obligation: *"the
original scorer and split definition remain **undetermined**"*. Both halves are now determined, so the
blocker closes on the same reading as the other four.

| clause from the pre-closure `next_action` | class | where it stands |
| --- | --- | --- |
| Bind the original answer Accuracy separately to `allenai/strategyqa-evaluator@49b88ed2...`, `evaluator/eval.py` SHA-256 `68c0edcd...` | `SATISFIED` | `registry/w-stratqa-receipts.md`, `tests/fixtures/hg_stratqa/`; raw Python equality over every supplied gold instance, **different** from the local normalized whole-answer EM |
| Obtain authoritative membership IDs or a complete author-endorsed generator and bind their scope and local task mapping | `SATISFIED` | `registry/w-stratsplit-membership.md`, `tests/fixtures/hg_stratsplit/binding-summary.json`: the ZIP Train multiset equals the installed 2290 rows and the author repository's unofficial train/dev union (2061/229); the ZIP Test membership equals the official evaluator's published 490-ID key set; 0 installed rows unaccounted for |
| Do not report local EM as original Accuracy; exclude F1 and judge accuracy | constraint | recorded in `registry/w-metriccmp-comparability.md` and the closure note |
| Retain authenticated installed CSV/member/revision and ReWOO evaluator identity and the bounded first-five selected-only custody record | `SATISFIED` | `tests/fixtures/hg_wsel/strategyqa.json`; mixed-side, not a diagnostic specification |
| Cite the dataset's own migrated review | `SATISFIED` | `/site/dataset-source-reviews-b2.md` -> `## strategyqa source-only review`, and `docs/dataset-reviews/strategyqa.md` |
| For any separately authorized diagnostic: reconcile the run-specific manifest/export and split mode/side, establish deterministic offline dependencies and dataset-specific trusted verifier/runtime execution, and report eligible/selected/task/trial/invalid/unmeasured coverage | `PRE-DIAGNOSTIC` | statement only; **no diagnostic is authorized** and none of it is waived by this closure |
| Preserve the shared-requirement withdrawals | `WITHDRAWN` | `registry/withdrawn-requirements.md` |

**Retained as explicit limits, in the blocker's own words before closure.** Direct landing-page
endorsement of the generation-pinned ZIP is **unobserved** (the request returned HTTP 403), so the
binding rests on the documented official-evaluator/author-content authority bridge rather than on a
landing-page attestation. Two duplicate-content installed rows keep **ambiguous exact qid candidates**,
with no invented occurrence-order tie-break; there is no partition ambiguity. And the imposed 1520/770
hash split and the author repository's *unofficial* train/dev division are **not** the original
Train/Test evaluation - the original Test membership (490 IDs) is entirely absent from the installed
pool, so the installed pool is an original-Train pool and nothing here licenses an original-benchmark
comparison.

**This closes no measurement and claims no score.** `strategyqa` has never had a measured run here, no
coverage count is promoted, F1 and judge accuracy stay excluded, and local normalized whole-answer EM
stays reported separately rather than as original Accuracy. Every diagnostic-time clause above remains a
requirement of any future diagnostic, which still needs its own authorization, a fresh run ID, a
split/side binding and the staged pilot policy.

## Closed under reading A, 2026-10-15 - `fever`

This is the first closure where the deciding clause is a **standing limit** rather than a satisfied
artefact, and the operator made the call explicitly after being shown that the limit is *bounded*, not
categorical. The distinction is the whole point of the entry, so it is restated here: the producing
execution is not proven unobtainable; it is absent over every source this platform can reach, and the
producer is a public third party, so a later maintainer-published binding would supersede the limit at its
actual scope.

| clause from the pre-closure `next_action` | class | where it stands |
| --- | --- | --- |
| Recover the two historical objects the search left unread (loader `1776db7e...`, generated tree `ad832160...`) | `SATISFIED` | both FOUND, raw bodies and digests in `tests/fixtures/hg_fevobj/`, enumerated in `registry/w-fevobj-receipts.md`; the loader is 7,897 bytes and `git hash-object` reproduces the recorded tree entry |
| Choose and name the metric explicitly | `SATISFIED` | `fever_exported_row_conjunctive_strict_at_5` - option (a), one complete exported group per row, the pinned five-pair bound, equal measured exported-row weights, no original-claim weighting; **definition only** |
| Bind the producing conversion revision/execution, ID assignment, split/filter/omission and NEI inputs | `WITHDRAWN` | `registry/withdrawn-requirements.md` -> `fever / producing-conversion-execution-receipt`, with the enumerated reachable scope, the two recovered objects, and the explicit statement that the absence is bounded rather than categorical |
| Retain the 1400/9525 exposed non-NEI rows (1369 at 2-5 pairs, 31 at 6-25), the 51 omitted alternative groups across 42 IDs, and the unauthenticated NEI/omission decisions | `SATISFIED` | recorded in the withdrawal entry and in `registry/w-fevconv-receipts.md` |
| Retain the authenticated Hub/export/installed/original-group and bounded first-five custody | `SATISFIED` | `registry/w-journalcov-review.md`, `registry/w-jc2-review.md`, `tests/fixtures/hg_wsel/fever.json` |
| Report the metric without any original-FEVER aggregate claim; do not invent a release | constraint | in the closure note and the named metric definition |
| For any separately authorized diagnostic: resolve split/side and run binding, implement a conjunction-preserving projection, obtain actual selected scorer/runtime execution evidence, and record coverage | `PRE-DIAGNOSTIC` | statement only; **no diagnostic is authorized** and none of it is waived |
| Preserve the shared-requirement withdrawals and the `official-scorer-identity` decision | `WITHDRAWN` | `registry/withdrawn-requirements.md` |

**This closes no measurement and claims no score.** `fever` has never had a measured run here, no coverage
count is promoted, no local conversion is treated as authenticating the historical run, and the
metric is a definition that is **not implemented** in the current local flat branch. Every diagnostic-time
clause above remains a requirement of any future diagnostic, which still needs its own authorization, a
fresh run ID, a split/side binding and the staged pilot policy.

## Closed under reading A, 2026-10-15 - `spreadsheetbench / dataset-journal-gap`, and the soundness hole found with it

The operator decided the one question this document filed, and decided it explicitly: **reading (A) covers
this blocker's remaining `PENDING-RUN` clause**, so `eligible/selected/task/trial/invalid/unmeasured
coverage` is a precondition on a future, separately authorized diagnostic rather than a condition on
closure - the same reading that closed `reasoning_gym` with no measured run at all. `spreadsheetbench` is
therefore the **seventh** closure under reading (A), the second applied to a measurement-family blocker,
and the first for which the parent also had to *fix* a clause rather than only record it.

Every clause of the `next_action` at closure:

| clause from the `next_action` at closure | class | where it stood |
| --- | --- | --- |
| Establish authenticated row-level gradable eligibility | `SATISFIED` | `registry/w-spreadelig-eligibility.md`: 394 of 400 rows are structurally gradable without a run, 1 is retained only by the explicit official-range-zero policy (`283-32`), 5 are not gradable as installed with a per-row reason; 794/800 workbooks open in both openpyxl modes and all 800 ZIP/XML structures are readable |
| Review/fix the synthetic stale-cache formula-conversion failure | `SATISFIED` | `w-spreadcache`: the cause was LibreOffice's own load policy (default `OOXMLRecalcMode=1` at `/usr/lib/libreoffice/share/registry/main.xcd:724`); the check now forces mode 0 in a private profile, strips scalar formula caches from a private ZIP copy, and requires regenerated caches before replacing the candidate; the opt-in real-engine control preserves the authored `[999,888] -> [5,10]` bar |
| Complete installed workbook/mirror authentication and bounded first-five custody | `SATISFIED` | `registry/w-jc2-review.md`, `registry/w-spreadrt-runtime.md`, `tests/fixtures/hg_wsel/spreadsheetbench.json` |
| The one-released-variant named-cell metric statement and the official-range-zero compatibility policy | `SATISFIED` | `registry/w-metriccmp-comparability.md`, `registry/w-spread2-reconciliation.md` |
| Preserve failed receipts, missing engine archives and conditional runtime/replay observations as bounded limits | `SATISFIED` | `registry/w-spreadcache` / `registry/w-spreadrt-runtime.md` |
| A reference that expands to zero cells publishes a vacuous pass | `SATISFIED` | `w-spreadvacuous`, merged as `803ef598`: `cell_level_compare` iterated `generate_cell_names(cell_range)`, which is **empty** for `'Sheet1'!BD2:308` (`parse_cell_range` gives an end column of 0), so the loop body never ran and the function returned `True` |
| eligible/selected/task/trial/invalid/unmeasured coverage | `PRE-DIAGNOSTIC` | the operator read this as a precondition on a future, separately authorized diagnostic; **no diagnostic is authorized** and nothing here is measured |

**The vacuous-pass hole was a soundness defect, not a bookkeeping gap.** The parent reproduced it with two
authored workbooks whose only real cell differs (`golden A1=5`, `result A1=999`):

```
generate_cell_names('BD2:308') -> []
compare_workbooks(..., "'Sheet1'!BD2:308") -> ok=True  msg=''
compare_workbooks(..., "'Sheet1'!A1")      -> ok=False msg='value difference at A1: golden has 5, result has 999'
```

So a released row whose `answer_position` had a range end without column letters scored **1.0 for any
submission**, recorded as a measured pass. `w-spreadelig` had already classified released row `73-45` as
`not_gradable_as_installed` for exactly this reason, and flagged `130-9` as the quoted-sheet-name-with-commas
variant. The fix makes an empty expansion **unmeasured** (exit 3, no reward file, a named reason) and names
an unaddressable component instead of skipping it; the released colon-range `ValueError` stays a measured
zero exactly as before, and the divergence from the upstream transcription is recorded with its per-row
denominator effect.

**Scorer custody moved with the fix, and the binding moved with it.** The shipped checker is now SHA-256
`98dd625481f091a8684dd4d7a66c17f07cf7a36c8bba160e27353e06988b1af7` - the stale-cache fix and the
empty-expansion refusal in one file. It was bound through `tools/apply_blocker_proposal.py` from
`registry/proposals/w-spreadvacuous-merged.json`; the superseded clause that bound custody to the cache-only
digest `5c247413...` and kept `e7131a26...` as the dated observation was acknowledged as dropped and
replaced (the tool refuses an unacknowledged drop, and it refused this one until both sentences were named).
`registry/w-spreadrt-runtime.md` is retained **unedited** as the record of what was observed then.

**What this closure does not do.** THIS CLOSES NO MEASUREMENT AND CLAIMS NO SCORE. `spreadsheetbench` has
never had a measured run here, no coverage count is promoted, `measured`/`selected`/`trial`/`invalid`/
`unmeasured` and `run_id` stay null, and no diagnostic or pilot is authorized. The retained coverage
obligation, the fresh bounded no-model regressions with real engine invocation, correct fresh cached values
and explicit network isolation, and the reconciliation of any diagnostic manifest with the bounded
first-five packet (`tests/fixtures/hg_wsel/spreadsheetbench.json`) all remain preconditions on a future,
separately authorized diagnostic, which must use its own fresh run ID and the current staged policy.

## Metric-identity qualification, 2026-10-16 - 20 datasets, no closure reopened

`registry/w-evidence-qualification.md` and `registry/w-evidence-disclosure.md` (both landed this phase)
record, per dataset, **what our number actually is** and where it differs from the official metric or the
paper protocol. This section attaches that qualification to the closure record, because the registry has
no closed-blocker record to attach it to: `registry/dataset-blockers.json` carries exactly 7
`datasets[*].blockers` entries, all open, plus `historical_runs` and `review.notes`; `agentdojo`,
`alfworld`, `gsm8k` and `musique` each carry `blockers: []`. **No status changes here and no closure is
reopened.** The obligations each closure withdrew are unchanged; what changes is the *identity of the
number*, which several records described incompletely or wrongly.

**Scope.** 20 of 28 datasets carry a finding. 4 are `no new finding in the named scope` (`gaia`,
`jericho`, `spreadsheetbench` before its locator drift, `terminal_bench`) and 4 are the permanent
refusals (`api_bank`, `nestful`, `sotuqa`, `tau2_bench`), which are excluded and were not investigated.

**The six the source review marked `must-fix`.** Of the 20, six carry the strongest inherited severity -
`hotpotqa`, `sportsunderstanding`, `physicsquestions`, `alfworld`, `agentdojo` and `scienceworld`. That
severity is a **judgment inherited from the fidelity handoff**, not a fresh measurement here: what is
measured in this phase is the metric identity and whether the code changed (`registry/fidelity-acceptance.md`
carries the fail-before/pass-after for each). All six appear in the table below with their effect class, and
five of them are `NUMBER` changes that have **already landed on `main`**, so their archived figures predate
the instrument now in the code.

**What the operator needs from this table.** Of the 20, **13 lead with `NUMBER`** - the change alters the
number, so an earlier figure must not be reported under the new metric name - **6 lead with `LABEL`**
(label-only, though some carry a conditional NUMBER) and **1 leads with `CONDITION`** (appworld, where a
guidance or aggregation change would move the number while the current scalar can be stated honestly
now). Because this phase runs **no re-runs**, no archived figure has been re-measured: for every
`NUMBER` row, the archived value was produced by the **pre-fix instrument** and is not comparable to what
the code computes today. That sentence is the deliverable; the rest is provenance.

**Measured addendum, 2026-10-16: for four of these the `NUMBER` change alters no surviving score.**
Replaying the pre-fix and post-fix scorers over the submission and gold recorded in every archived verdict
reproduces the archive exactly in **both** trees - **4,578 of 4,578** archived
verdicts across 10 runs and six datasets, zero disagreements in either tree - so the fixes change none of
them and are **latent** on the runs we
still hold (0 of 3,957 GSM8K trial-predictions carry a `####`; 0 of 53 PhysicsQuestions predictions end in
`.`; 0 of 12 2Wiki verdicts used the structured channel). The labelling obligation below therefore stands as
a *labelling* obligation, not as an observed numeric change. Method, harness validation and coverage gaps:
`registry/rescore-measurement.md`. `gsm8k` is a full eval run; the others are probes.

| dataset | blocker | effect class | what the fix changes |
| --- | --- | --- | --- |
| `agentdojo` | closed | NUMBER/CONDITION | NUMBER/CONDITION: warning removal and registered-attack wiring have landed. |
| `alfworld` | closed | NUMBER/CONDITION | NUMBER/CONDITION: the budget has changed 30→50, so earlier 30-action figures must not be reused as 50-action success (`data/datasets/alfworld/lib/gym_env.py:41-44`). |
| `appworld` | measurement | CONDITION/NUMBER for guidance or scenario aggregation changes, LABEL for stating single-task success honestly. Earlier single-task figures must not be reused as a new grouped or test@N figure. The successful-measured-task blocker remains open; no new success is asserted. | CONDITION/NUMBER for guidance or scenario aggregation changes, LABEL for stating single-task success honestly. |
| `bfcl_v3` | external | NUMBER | NUMBER: the irrelevance filter has changed. |
| `bird_minidev` | closed | LABEL/DENOMINATOR DISCLOSURE; NUMBER if coverage/failure classification changes. Earlier measured-subset EX figures must not be reused as full-sample EX, Soft-F1 or R-VES. | LABEL/DENOMINATOR DISCLOSURE; NUMBER if coverage/failure classification changes. |
| `chessbench_puzzles` | closed | LABEL ONLY while keeping offline replay; NUMBER/CONDITION if an interactive controller is used. Earlier offline figures must not be reused as interactive official accuracy. | LABEL ONLY while keeping offline replay; NUMBER/CONDITION if an interactive controller is used. |
| `ds1000` | closed | NUMBER/CONDITION | NUMBER/CONDITION: private tests have been removed from actor SETUP. |
| `fever` | closed | NUMBER | NUMBER: conjunction-preserving projection has landed. |
| `gsm8k` | closed | NUMBER | NUMBER: first-marker precedence has landed. |
| `hotpotqa` | closed | NUMBER | NUMBER: joint-EM reward has changed to answer F1 (`data/datasets/hotpotqa.py:52-58`). |
| `livecodebench` | closed | NUMBER/CONDITION | NUMBER/CONDITION: the inner 600-second clamp has been removed. |
| `musique` | closed | LABEL ONLY for the handed-off 0.3064 | LABEL ONLY for the handed-off 0.3064: call it local joint F1, never Answer F1. |
| `physicsquestions` | closed | NUMBER | NUMBER: prediction-side normalization changed. |
| `reasoning_gym` | closed | LABEL/DISCLOSURE pending verified aggregation evidence; NUMBER if an aggregation definition changes. Earlier pooled means must not be reused as an unverified official max+mean figure. | LABEL/DISCLOSURE pending verified aggregation evidence; NUMBER if an aggregation definition changes. |
| `scienceworld` | closed | NUMBER/CONDITION | NUMBER/CONDITION: the 1000→100 budget change has landed and a separate observed-type macro now exists (`data/datasets/scienceworld.py:239-296`), while the default run scalar remains micro. |
| `sportsunderstanding` | closed | NUMBER | NUMBER: immediate-negation interpretation changed. |
| `strategyqa` | closed | LABEL/DISCLOSURE now; NUMBER if answer semantics or the evaluation split changes. Earlier local string EM figures must not be reused as original Accuracy on Test. | LABEL/DISCLOSURE now; NUMBER if answer semantics or the evaluation split changes. |
| `tau_bench` | closed | NUMBER/CONDITION | NUMBER/CONDITION: the service-recording change has landed (`14866a0c`, which took the checker from 347 to 297 lines). |
| `triviaqa` | external | LABEL/PROVENANCE now; NUMBER potentially if alias preservation, key grouping or denominator changes. Earlier row-EM figures must not be reused as official key-level EM/F1 after such a fix. No numerical effect is observed. | LABEL/PROVENANCE now; NUMBER potentially if alias preservation, key grouping or denominator changes. |
| `twowikimultihopqa` | closed | NUMBER/CHANNEL | NUMBER/CHANNEL: the routing change has landed. |

**Ten mechanisms were described against the pre-fix tree in the first draft of these documents** and are
corrected here: alfworld's 30-action budget, hotpotqa's joint-EM reward, the LiveCodeBench 600 s clamp,
AgentDojo's custom fixed attack, FEVER's flat singleton evidence, GSM8K's last-number rule, BFCL's
non-call lines, DS-1000's actor-visible tests, ScienceWorld's 1000-move budget, and 2Wiki's undeclared
`EVIDENCE_AWARE` - plus `tau_bench`, where the cited line range no longer resolved because the fix made
the checker shorter (347 -> 297 lines, `14866a0c`). Nine of these are `NUMBER` changes on `main` today.

**What this section does not do.** It closes no measurement, claims no score, authorizes no run, and
changes no blocker status. It does not substitute for the three open blockers' own decisions, which are
set out with their exact registry text in `registry/w-decisionpack.md`. The archived figures themselves
are separately constrained: this phase measured that the four enumerated archive trees contain **zero**
verdict files and that the census originals are only a partial substitute
(`registry/w-evidence-archive.md`, parent-reproduced).

