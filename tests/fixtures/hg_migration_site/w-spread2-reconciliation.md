# SpreadsheetBench reconciliation - w-spread2

Base: `83cad84`, registered isolated worktree `/tmp/hg-spread2`, branch `wg-spread2`.
Read `AGENTS.md`, reverted worker commit `5202b62`, its
`reports/w-spread-classification.md`, merge `87b4254`, revert `4b7b1d0`, and the
round-33 parent review at `docs/adapter-integration-journal.md:1839` before editing.

## Decision: option (a), official-equivalent measured zero

A range rejected by the official parser keeps the official evaluator's measured
zero, including released task `283-32`. Local workbook-load, unexpected comparator
and LibreOffice/runtime failures are unmeasured. An absent submitted artifact and
a genuine completed value/result-sheet mismatch remain measured zero. This
preserves the released-task behavior that the reverted patch accidentally changed.
No assertion in `test_the_initial_workbook_does_not_score_one` was changed.

The range-policy zero is explicitly named `official-equivalent measured zero` in
the checker evidence; it does **not** claim that a cell comparison completed or
that the malformed task has become semantically gradable. The official caller's
choice determines this one compatibility rule. Other measurement failures do not
become zeros simply because upstream's broad caller also swallows exceptions.

## Official behavior: raw source and metadata

Canonical GitHub API source response is preserved in
`reports/w-spread2-evidence/official-source-response.json` (lossless base64 bytes).
`reports/w-spread2-evidence/official-evaluation.py.txt` is a readable view with
trailing whitespace trimmed; function line numbers and the parsed AST are
unchanged and checked by the audit. Immutable upstream URL:

https://github.com/RUCKBReasoning/SpreadsheetBench/blob/e2758ac01c6c52efc746bf0d53b2fd01dbc42dd2/evaluation/evaluation.py

Raw decoded-source SHA-256 is
`4ae77cee8df01d1f34684fceab972810d696886533d33be2e89373de6b4d3de3`;
Git blob SHA-1 is `d8109e9509f06fc67e9458fbbd4d494e086e48fc`, recomputed from the
decoded bytes and checked against the API response. The initial raw.githubusercontent.com
request reset its connection; the canonical GitHub contents API supplied the bytes.
This is upstream source evidence, not a claim that a complete official deployed
scorer/runtime closure has been authenticated.

File+line trace in the preserved upstream file:

- `:93-110`: `parse_cell_range('A:G')` collects empty `start_row` and `end_row`,
  then calls `int(start_row)`, raising
  `ValueError: invalid literal for int() with base 10: ''`.
- `:113-120`: `generate_cell_names` invokes that parser for colon ranges; single
  cells bypass it. `:123-129`: a present result sheet reaches range generation.
- `:155-163`: workbook-load exceptions are separately caught and return false.
  `:169-188`: the actual range comparison is outside that loader guard, so a
  parse failure **escapes compare_workbooks** rather than returning false there.
- `:204-220`: `evaluation()` loops over three variants, calls the comparator
  inside `try`, catches the parse exception at `:218-219`, assigns
  `result = False`, and appends `int(result)` at `:220`. Thus the caller records
  zero for each variant. `:221-228` produces soft `0.0` and hard `0`.

The raw installed metadata is
`/mnt/20t/xubuqiang/datasets/spreadsheetbench/spreadsheetbench_verified_400/dataset.json`:
line 156 names `"id": "283-32"`; line 160 is verbatim:

```json
        "answer_position": "Sheet3'!A:G,'Sheet4'!A:G",
```

The full value has **two** column-only ranges, not just the first abbreviated
component. Quote stripping yields sheet `Sheet3` / range `A:G` first. A result
missing that sheet already returns false upstream; with the sheet present, the
parser raises and the caller catches it. Both routes produce zero.

`reports/w-spread2-evidence/audit.py` loads only the relevant unmodified upstream
function definitions via AST, leaving upstream imports/CLI/main unexecuted. It
reproduces the direct comparator exception and runs the unmodified `evaluation()`
body against an in-memory authored workbook and synthetic metadata/open/load
functions. It does not execute a released benchmark task. Raw result at
`reports/w-spread2-evidence/audit-output.txt:22-35` is the direct `ValueError`, then
`test_case_results: [0, 0, 0]`, `soft_restriction: 0.0`, `hard_restriction: 0`.

## Affected-task count and its limits

**One of the 400 installed verified_400 tasks is affected: `283-32`; two range
components fail.** The audit processes all 400 metadata rows and all comma-separated
components, using the actual official parser on all 371 colon-range components.
The 45 single-cell components correctly bypass that parser. This is a parser
failure count, not a substring/no-digit heuristic. Raw method, counts and failures
are at `reports/w-spread2-evidence/audit-output.txt:13-19`.

Installed metadata SHA-256 is
`bcecaa89a005bd4e3bbe98da150a86e8062c27f262e575d5e47bd9861b3525e7`.
Its bytes exactly equal the dataset.json member of the local archive, whose hash
is the adapter's declared
`10ef893dd29cb13ab97143ea787e68cdc9574a13873ab9a54e50b31dc03fc949`
(`audit-output.txt:5-12`). This binds this local count to the declared archive
bytes, without certifying every installed workbook or an independent mirror
custody chain. No count is claimed for upstream's larger 912-task set, and
**399 parseable rows is not 399 authenticated gradable tasks**.

Coverage consequence: option (a) does not exclude `283-32` or shrink the declared
400-row pool. It stays a measured-zero denominator entry whenever selected and
successfully evaluated without a local infrastructure failure. The task's ID hash
starts with byte 191, so it is outside default hash eval, inside hash train, and
included by whole-pool/all-eval choices (`audit-output.txt:20-21`; adapter split
logic at `data/datasets/spreadsheetbench.py:352-372`). No selected manifest or
benchmark coverage measurement was created here. Any future semantic gradability
filter must expose the exclusion and its changed denominator/comparability.

Comparability consequence: this repair preserves upstream's zero for malformed
released ranges and preserves valid named-cell comparison results. It deliberately
separates local infrastructure failures from scores, so those trials require
invalid/unmeasured coverage reporting instead of inclusion as upstream-style zero.
The adapter still compares **one supplied variant**; it does not reproduce the
paper's three-variant soft/hard aggregate metric.

## Implementation and regression evidence

`data/datasets/spreadsheetbench/verify/check.py:142-174` distinguishes invalid
reference sheets and comparison-time file loss/load failure from completed
result-sheet/value mismatches. `:179-195` retains the explicit range-policy zero
for parser `ValueError`; unexpected parser exceptions propagate. `:254-268`
raises on formula-probe load failures. `:354-381` guards formula detection and
the entire LibreOffice lifecycle, including scratch setup, launch, conversion
move and cleanup, and writes phase/type/reason with `measured=false`, exit 3,
no reward file and no score/reward/correct fields. The absent-artifact branch at
`:343-352` stays measured zero; the measured comparison sink is at `:383-391`.

The new `tests/quality/test_spreadsheetbench_classification_w_spread2.py` covers
21 scenarios at two levels: checker evidence/output and the actual adapter
verifier spec through verifier plus runner persistence/cache/aggregation.
Subprocess/runtime calls are intercepted; synthetic files live outside the repo.
Unmeasured cases have no numeric score or trial scores in persisted progress or
aggregates, no complete marker, and no cache admission; completed mismatches and
the range-policy zero persist measured zero normally. The generic verifier's
internal fallback number is never admitted as a measurement: existing guards at
`eval/verification/verifier.py:649-665` and `eval/run/runner.py:1102-1127` retain
the unmeasured reason and exclude it before scoring/cache admission.

The initial regression version exposed **20 failed, 22 passed** against the base.
After strengthening the released-case reason assertion and including the full raw
range, the **final** 42-case file replayed against the base produces **21 failed,
21 passed**. Against the fix it produces **42 passed**. Both baselines and the
post-fix output are retained in `reports/w-spread2-validation.txt`.
The required owning-file-plus-blocker command passes **65 tests**; the owning
file itself passes **11 tests**, including the unchanged initial-workbook pin.

`rg -l -i spreadsheetbench tests --glob 'test_*.py'` identifies the owning file,
new classification file, control-submit, import-direction, data-layout,
registry-discovery-order, dataset-journal-coverage and dataset-controls files.
The other six files pass **34 tests, 3 expected failures, 20 deselected**. The
three strict expected failures are inherited journal-review-location debts for
sportsunderstanding/spreadsheetbench/strategyqa. The 20 explicitly deselected
`test_the_control_reaches_one_and_the_noop_reaches_zero` cases execute benchmark
controls across datasets, which the user's no-benchmark-run rule prohibits.
All remaining cases in those files run; the owning file is fully run, with no
deselections. Exact commands, raw outputs and exits are in the validation log.

## Registry disposition: blocker stays OPEN

CLOSED: the local classification repair and the deliberate official-range-zero
compatibility decision, supported by source evidence and synthetic controls.
The registry observation/next action are made more accurate; its measurement
status, taxonomy, shared references and production refusal remain unchanged.

OPEN: `spreadsheetbench / dataset-journal-gap` still requires:

- Authenticated row-level gradable eligibility and eligible/selected/task/trial,
  invalid/unmeasured coverage, bound to the selected split and exact inputs.
- Complete installed workbook/mirror provenance and scorer custody/execution;
  the metadata-to-local-archive check and upstream source receipt are partial.
- Formula/LibreOffice offline runtime, including transitive dependencies/ABI,
  actual formula/cache behavior and required resource evidence.
- `scorer-offline-closure`: complete pinned scorer closures, official provenance
  and execution, trusted class-specific resources/lifecycle/cleanup and valid
  live controls.
- `artifact-only-state-export`: authoritative per-task export/private-input
  contracts, ownership/quiescence/trial binding, scoring equivalence and live
  lifecycle/cleanup under the declared class.
- Separate diagnostic authorization after all gates; retain single-variant
  metric limitations and never promote synthetic controls into production proof.

No blocker was closed, removed or downgraded. No benchmark task, model, GPU,
endpoint, container or paid service was run. Historical runs, receipts, archives
and `runs/` were not modified. Only this registered worktree is committed; parent
review/merge remains outside this worker's scope.
