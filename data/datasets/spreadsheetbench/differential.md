# Authored workbook differential controls

`tests/quality/test_spreadsheet_official_differential.py` compares official
source execution with the platform's actual `_verify` command route. It uses
self-authored XLSX files, not released workbooks, dataset scores or a model.

The v1 reference is an independent source-only snapshot from
[`RUCKBReasoning/SpreadsheetBench`](https://github.com/RUCKBReasoning/SpreadsheetBench/blob/49b73a94775fb489063f60ca1865e3a650079a79/evaluation/evaluation.py),
revision `49b73a94775fb489063f60ca1865e3a650079a79`, with evaluator SHA256
`4ae77cee8df01d1f34684fceab972810d696886533d33be2e89373de6b4d3de3`.
The test requires that exact source via an external path and does not redistribute
it. This snapshot does not authenticate the historical transcription's origin or
change the production scorer/source identity. V2 executes the unchanged evaluator
and recalculation source whose hashes are checked against its existing
`PROVENANCE.json` (revision `a7bdc2d358e052d35a94768e1f487c0d9e9c0011`).

Provide existing, separately prepared Python execution artifacts and the selected
native deployment manifest. Nothing in this test installs or updates them:

```sh
export HG_SPREADSHEETBENCH_TEST_RUNTIME_ROOT=/absolute/v1-runtime-store
export HG_SPREADSHEETBENCH2_TEST_RUNTIME_ROOT=/absolute/v2-runtime-store
export HG_SPREADSHEET_NATIVE_MANIFEST=/absolute/native/manifest.json
export HG_SPREADSHEETBENCH_OFFICIAL_EVALUATOR=/absolute/upstream/evaluation/evaluation.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  -p no:cacheprovider --basetemp=/tmp/authored-spreadsheet-differential \
  tests/quality/test_spreadsheet_official_differential.py
```

The reference process also needs genuine openpyxl 3.1.2, tqdm 4.66.4, and v1's
NumPy import. No fake import modules stand in for these dependencies or UNO.
Each reference workbook is independently copied and recalculated using the
existing isolated UNO transport and pinned v2 recalculation source. Its existing
transport adaptation binds the selected engine/profile and suppresses macros and
external document updates; its calculation settings remain unchanged. This is
not execution of v1's upstream recalculation launcher. V1's
platform route uses its separate CLI conversion/cache-freshness path; v2's
platform route uses private UNO and the exact pinned evaluator. Both native
transports own their private process/namespace/profile, not a shared listener.
Original, golden and submitted bytes must remain unchanged after both routes.

The cases cover numeric-string/boolean/date/time normalization, empty strings,
empty-versus-zero, relative numeric tolerance, financial display text and Excel error cells,
a wrong preserved cell, cross-sheet formula references, a formula returning
empty text, and v2 quoted-comma sheet names plus Debugging Embedded formula and
Color font comparisons. Comparison includes v2 regression/modification ratios,
not only overall reward. The existing upstream rule gives accuracy zero for an
all-regression selection with no modification cells, even for an identical
output. The tests preserve that rule. V1 and v2 also intentionally differ on
numeric tolerance, empty-versus-zero and financial display text equivalence.
A literal `#DIV/0!` error cell versus `N/A` is a separate measured-zero case:
this native engine saves the error as a formula, so the official workbook
evaluator takes its raw-formula error fallback. The value helper alone would
accept the two strings, but does not determine the full workbook score.

This is selected-cell metric and native dependency evidence for the authored
cases. It does not establish Excel/LibreOffice equivalence, all functions,
volatile/external formulas, macros, unsupported formula groups, benchmark
coverage, portability or a pristine native closure. The native manifest still
represents a verified host installation with the scope limits described in
`data/scaffolds/spreadsheet_runtime/README.md`. Existing fake/identity-only
controls remain failure/wiring controls; they are not native-engine evidence.

Observed on 2026-11-07: 21 controls passed with LibreOffice 7.3.7.2
30(Build:2), its actual system Python/UNO, and the existing managed Python
execution artifacts. The 312 warnings were openpyxl 3.1.2 UTC timestamp
deprecations. Without explicit runtime configuration, all 21 controls skipped
with a resource requirement; those skips supply no native readiness evidence.
