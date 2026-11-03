# SpreadsheetBench-2

This adapter loads the pinned `KAKA22/SpreadsheetBench-v2` workbook collection
(`5a2215ed4121945ab09d8723df2995602090b042`) and the native evaluator at
`RUCKBReasoning/SpreadsheetBench-2` commit `a7bdc2d358e052d35a94768e1f487c0d9e9c0011`.
This is a distinct dataset from this repository's SpreadsheetBench v1 verified_400.

Supported categories are Debugging, Financial_Model and Template. Visualization
is excluded explicitly; its different runtime/judge is not approximated.
The public collection declares no training split: `SPLIT=None`, `OFFICIAL_EVAL=None`.
Use an explicit experiment split if needed; no source test tasks become training
material implicitly.

```sh
python3 data/datasets/spreadsheetbench2/prepare.py --root /path/to/data \
  --archive /path/to/spreadsheetbench-v2-09-10.zip
HG_DATA_SPREADSHEETBENCH2=/path/to/data python3 tools/dataset_lint.py --dataset spreadsheetbench2
```

Without `--archive`, preparation downloads the exact pinned HF archive, using
`HG_HF_ENDPOINT` (default hf-mirror.com). Preparation records an archive digest
and every extracted file digest. Importing the adapter does not download data.
`HG_SPREADSHEETBENCH2_CATEGORY` selects `all` (default) or one supported category.

Actors get only `input.xlsx` and the native instruction plus a public output
filename. Golden workbooks, comparison ranges and the original filename needed
by the native Debugging Color/Embedded dispatch remain private verifier inputs.
The checker copies inputs/output to private scratch, recalculates them through
the native UNO helper (iterative100 / epsilon0.0001), and calls unchanged
`process_single_item`. This preserves the upstream regression and modification
scores and their binary conjunction; it does not substitute cell-match fraction.
The upstream helper's fixed UNO port/profile is isolated per check for concurrency.

Runtime pins openpyxl==3.1.2 and tqdm==4.66.4 in requirements.txt, matching the tested host. It also requires LibreOffice and its native `uno` Python binding.
A completed native mismatch/missing output is zero. Runtime/source-binding errors
write `measured:false`, omit reward and exit nonzero. Private cell-value diagnostics
are not leaked to method feedback.

`PROVENANCE.json` pins exact evaluator/helper bytes. The upstream root tree has no
root LICENSE file at this revision; `SWE-agent/LICENSE` covers that vendored project,
not a claimed license for evaluation.py. No upstream license is invented here.
No full-data H0 or production authorization is implied by synthetic controls.
