# Decision: all three evidence gates withdrawn; four permanent refusals remain

> **Superseded 2026-10-22, on the refusal half only:** the four refusals are no longer permanent
> exclusions, and `tau2_bench`'s was lifted conditionally. The three gate withdrawals stand.
> See `docs/refusal-policy.md`.

**Operator decision, 2026-10-16.** `bfcl_v3 / simple-ast-contract` and
`triviaqa / hub-export-scorer-linkage` were withdrawn as **scope** limits — "relax these two,
there is no need for evidence this rigorous" — and `appworld / successful-task-evidence` was
withdrawn after the protocol question it existed to answer had been answered by measurement.
Applied to `registry/dataset-blockers.json`; the named limits are in
`registry/withdrawn-requirements.md`.

| Dataset | Blocker | State | Why |
| --- | --- | --- | --- |
| `bfcl_v3` | `simple-ast-contract` | **withdrawn** | scope: producer-held build-time facts a rebuild cannot supply |
| `triviaqa` | `hub-export-scorer-linkage` | **withdrawn** | scope: the upstream execution record was never published |
| `appworld` | `successful-task-evidence` | **withdrawn** | the measurement path is proven sound by controls; a success-only gate can never be closed by a faithful failure |

Checker: `{"valid": true, "datasets": 28, "shared_blockers": 0, "production_authorized": false}`.
**No non-refusal blocker remains**; the registry now carries only the four contract refusals
(`api_bank`, `nestful`, `sotuqa`, `tau2_bench`).

## The two scope withdrawals are not impossibility findings

`registry/withdrawn-requirements.md` defines its entries as requirements "that no amount of further
work in this repository can satisfy — not one that is merely difficult". **`bfcl_v3` and `triviaqa`
do not meet that bar and are not claimed to.** They are withdrawn because the operator judged the
evidence standard higher than the project needs. A producer-published build manifest or an upstream
execution receipt would supersede each entry at its actual scope. A scope note ahead of that file's
closing section says exactly this, so they cannot be misread as findings that the missing evidence
does not exist.

`appworld` is a third kind again: not unobtainable evidence and not a relaxed standard, but a gate
whose **purpose had already been served**. It asked for "a genuine successful measured actor task",
because a success was the only thing that could show the appworld measurement path works. That is
now shown by other means — see below — and because the gate admitted *only* success, no correctly
executed failure could ever close it.

## `appworld`: what was actually measured

Three authorized diagnostics ran, all selecting **the same** study task `cf6abd2_2` out of **147
eligible** (the pool holds **732**):

- `w-appworld-20261014T225023Z-f02d7cd5` — `success=false`, `score=0.0` over 8 official tests;
- `w-appworld2-20261015T050036Z-a6d55a63` — one trial, `jobs=1`, `rounds=0`, unchanged
  32-step/2048-output-token ceiling;
- `appworld-diag-20261015T094842Z-3550c7c4` (commit `a861ca67`, guidance names the in-world
  completion boundary) — **completion was performed, recorded and replayed**: 15 API calls ending
  in `POST /supervisor/message`, which **is** `complete_task`. Re-running the platform's **own**
  checker on the frozen log reproduces `reward 0.0`, `success False`, `num_tests 8, passes 6,
  failures 2` — `assert answers match.` (a prose sentence where this state-change task expects
  `None`, which AppWorld's own API doc requires to be left at the default) and a note-content
  assertion. **No platform defect, no replay defect.**

The task-independent fabricated-control set (`tests/quality/test_appworld_completion_control.py`)
separates an actor/guidance boundary from a recording/verifier wiring fault from a task-state
failure, and it passes — so the faithful failure is attributable to the actor, not the instrument.

**The limit a reader must carry.** One task, attempted once per diagnostic, and the same task every
time. **No successful measured actor task exists.** Nothing here is evidence about appworld's
difficulty, about whether this harness fails appworld generally, or about headroom, and this must
not be reported as an appworld score. A usable appworld measurement needs a **fresh, separately
authorized multi-task run** under the staged pilot policy.

## What each withdrawal keeps

**`bfcl_v3`.** Keeps the function-name diagnostic (identifier-filtered bare-name multiset equality,
arguments ignored) and all four source-only qualifications; every frozen receipt and inventory; the
deliberately wired-off preflight; `production_authorized: false`. **The retained decoder containment
boundary revives with the work it guards** — any future official-scorer work reopens the original
technical gate and must bound or bypass `eval(ast.unparse(value))` at
`tests/fixtures/bfcl_ast_source/.../model_handler/utils.py:302`, using `guarded_decode` or recording
why not. No official AST/full-call score is claimed.

**`triviaqa`.** Keeps the DECIDED twelve-declarations/eight-export-routes mismatch with its four
declaration-only names; the limited 17944 stored-row observation with **no invented 725-member
explanation**; the bound first-five custody in `tests/fixtures/hg_wsel/triviaqa.json`; v2 and all
historical identities. The reported figure stays the **local alias-max normalized row-EM**
instrument — no official key-level EM/F1, no denominator, no official comparability.

**`appworld`.** Keeps every measured-zero, staged-null and incomplete exit-125 receipt with its
raw/staged identity; the raw-TGC-loss vs staged-`score=null` distinction; the completed control set;
the parent correction and historical W1/W2 scope; the relative `MARKER` reset hazard with occurrence
still UNKNOWN; the guidance requirement at `data/datasets/appworld.py:239-243`; the source-only
disclosures. **No selected-task tuning, rerun or budget increase is authorized**, and reopening
requires fresh staged-policy evidence and separate authorization. Nothing is authorized by this
decision and `production_authorized` stays `false`.

## Why the guard tests had to change

All three closures were blocked by tests encoding the *previous* disposition. Switching them is
legitimate — a test asserting a decision the operator has reversed is stale, not a guard — but the
changes are enumerated so the reviewer can check them. Measured at each stage: closing all three
first gave **18 failed + 13 errors**; after two of them, **19 failed**; the final state is green.

1. `test_bfcl_ast_work_remains_blocked_on_independent_source_and_index_custody` asserted
   `simple-ast-contract` **stays**. Replaced by
   `test_bfcl_ast_contract_withdrawal_is_recorded_and_the_containment_boundary_survives`, which
   asserts the gate is gone, that the record carries its named limits and the scope note, that
   `release-index-custody` stays absent, and that production stays unauthorized.
2. `test_registry_refuses_incomplete_or_misleading_entries` mutated `blockers[0]` of a pinned
   dataset — stale twice already (`jericho` → `bfcl_v3`). It now attaches a **synthetic** entry to
   a published dataset inside a private copy (`SYNTHETIC_BLOCKER` in `tests/quality/conftest.py`),
   removing the dependency on the live population entirely. It cannot live on a synthetic *dataset*:
   `checker.validate` requires every registry dataset to be a published declaration matching
   `tools/dataset_classes.py`.
3. `test_refuses_a_list_record_with_no_matching_dataset` and `test_apply_blocker_proposal.py`'s
   `registry` fixture read the live registry for their blocker pair. They now use the same synthetic
   entry, so the controls no longer break each time a blocker closes — which happened four times on
   2026-10-15 and again here.

**Final: 156 passed / 17 skipped / 0 failed** across all twelve test files that read the live
registry.

**One pre-existing red test was found and fixed**, unrelated to this decision:
`test_test_scope.py::test_no_new_dataset_arrives_without_a_dedicated_test` was already failing on
`main` because the fidelity phase added `test_fever_w_fever.py` and
`test_tau_bench_recording_w_taubench.py` without deleting `fever` and `tau_bench` from
`NO_DEDICATED_TEST`. Verified pre-existing by running it against the restored registry.

## What is still open

- **The four refusals** (`api_bank`, `nestful`, `sotuqa`, `tau2_bench`). **Superseded 2026-10-22:**
  none of the four is a permanent exclusion any more. A refusal is a statement about what this
  platform's contract cannot carry, and the operator took all four names out of the *Prohibited
  shortcuts* list — `tau2_bench`'s exclusion is lifted conditionally (its two facilities now ship in
  the repository; a pin, a split spec and an adapter remain) and its status moved from `external` to
  `code-solvable`, while `api_bank`, `nestful` and `sotuqa` stay refused for want of an artefact this
  repository cannot produce. All four adapters still return `REFUSED`. See `docs/refusal-policy.md`.
- **`hotpotqa` and the `journal-coverage` family** — still open elsewhere in the record; what they
  need is an operator-supplied selected manifest, an input rather than an impossibility.
- **No dataset in the registry now carries a non-refusal blocker.** That is a state the checker
  supports (an empty `blockers` list is a statement, per `docs/enforcement-proof-decision.md`), but
  no dataset had reached it before, and the ten datasets with no measured run remain the larger gap.

## Provenance

Reviewed-but-not-applied alternatives: `registry/proposals/limit-{appworld,bfcl_v3,triviaqa}.json`
and `registry/proposals/record-bfcl_v3.json`; the four-option analysis is
`registry/w-decisionpack.md`. `tools/apply_blocker_proposal.py` was used for the RECORD edits and
refuses silently-dropping replacements. Pre-removal blocker text remains readable in
`git show <commit-before-this-decision>:registry/dataset-blockers.json`.
