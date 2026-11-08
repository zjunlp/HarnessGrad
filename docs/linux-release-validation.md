# Linux delivery validation — 2026-11-08

The selected Linux software checks passed with eight explicit optional skips at
source `ecb004b745fe126e45e9b6730c5286cdc6b3633d`, tree
`53cc90d7fe29a0bbe78d8e5eb48e67ceef842ff6` (identical to integration `4e2856`).
The [sanitized receipt](../.github/assets/linux-final-delivery-20261108.json)
records exact file/node selections, per-file counts and raw evidence SHA-256/bytes.
This docs-only receipt follows the five thematic candidate commits; it preserves
the tested commit rather than amending it. It records no remote release action.

## Source checks and retained attempts

The reconciled 107-file selection has **1721 passed, zero failed, zero errors
and eight skipped**. Both source snapshots have the tested HEAD/tree, clean
status and no tracked drift. This is a selected software gate, not the full suite.

The original complete attempt remains retained: 1720 passed, one setup error
and eight skips. Its acceptance harness omitted a genuine prepared Loop runtime
profile for `test_trials_and_unmeasured.py`. A fresh same-source single-file
rerun with that profile passed all 15 tests, replacing only that file's 14-pass/
one-error counts in reconciliation. Product source and assertions were unchanged;
no download or product fix was used to turn the setup error into a pass.

Seven skips require optional managed Harvey, pinned lab-core and Pandoc inputs.
The eighth is the exact historical legacy-adapter test whose fixture is absent:
`legacy/adapters/harnessgrad_domain/adapter.py`. The receipt lists all eight node
IDs and reasons, with a one-skip allowance for that precise historical case.
These remain unmeasured; no missing fixture is represented as a passing test.

## Installed wheel, native controls and required CI

| Check | Bounded result |
| --- | --- |
| Installed wheel | 16 successful steps after build-source removal; 350 packaged resource files checked with none missing/changed; 44 architecture owners loaded as 43 dotted imports plus one installed file asset. |
| Same-source SWE | Six passed: three actual Docker/official-grader authored cases with rewards 1/0/0 and three separate unit controls. |
| Same-source T2 | Two actual owned-Docker success/failure controls passed for resource/gateway cleanup; these are not dataset or model-quality scores. |
| Independent native cleanup | All 14 exact receipt references inspected absent; cached base preserved. Fourteen references are not fourteen independent resources. |
| Required CI supplement | Six files: 165 passed, no failures/errors/skips. Required documentation-command and import-direction files are also in the 107-file gate. |
| Driver entrypoints | Help and imports exited zero with clean checkout; the supporting receipt records completed commands after execution, not a new invocation. |

The first wheel attempt remains retained as failed: its probe incorrectly treated
a same-stem adapter resource directory as a dotted Python package. The corrected
probe file-loads that installed asset; the subsequent report passed all 16 steps.
The JSON retains both reports' digests. Counts from overlapping source, native
and CI selections are not summed as independent coverage. Raw private logs,
host paths, credentials and task hashes are not published.

## Limits

Windows and macOS runtime acceptance remain deferred. Original task/data,
ROM/corpus, immutable images/toolchains and reviewed native archive supply remain
adapter-specific external prerequisites. These authored/offline checks do not
certify complete released benchmarks, paid endpoints, model quality or every
adapter/toolchain combination, and do not declare the overall Goal complete.
Historical component/delivery receipts retain their original sources and scope.
