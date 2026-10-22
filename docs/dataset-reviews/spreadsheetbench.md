<!-- Filing split, not a new review. The section below is verbatim from
     docs/dataset-source-reviews-b2.md, recovered from 1825cd0e^
     (`tests/fixtures/hg_migration_site/dataset-source-reviews-b2.md`).
     Only the title line was added, so that the per-dataset citation convention
     already used by docs/dataset-reviews/fever.md, physicsquestions.md and
     reasoning_gym.md applies to this dataset too. No content was rewritten. -->

# spreadsheetbench source-only journal review

## spreadsheetbench source-only review

**Metric identity.** The claimed upstream is
`RUCKBReasoning/SpreadsheetBench`, `evaluation/evaluation.py::compare_workbooks`,
transcribed in `data/datasets/spreadsheetbench/verify/check.py`
(`data/datasets/spreadsheetbench.py:20-48`;
`data/datasets/spreadsheetbench/verify/check.py:50-91,154-201`). Upstream citations:
[evaluator](https://github.com/RUCKBReasoning/SpreadsheetBench/blob/main/evaluation/evaluation.py)
and [recalculation helper](https://github.com/RUCKBReasoning/SpreadsheetBench/blob/main/evaluation/open_spreadsheet.py).
The local implementation loads cached values with `data_only=True`, compares
all specified cells in comma-separated ranges, and returns binary reward.
Numbers/numeric strings round to two decimals, datetimes become rounded Excel
days, times lose seconds, and empty string equals `None`; it ignores cell colours
and accepts but does not use `instruction_type`
(`data/datasets/spreadsheetbench/verify/check.py:55-91,132-201,380-387`).
The adapter explicitly describes one shipped variant per task versus the paper's
three-variant `soft_restriction`/`hard_restriction` aggregates, so this reward
must not be labelled those aggregates (`data/datasets/spreadsheetbench.py:43-48`).
No evaluator commit pin is supplied by the cited transcription. The checked-in
reference-normalizer test and workbook comparisons are local tests, not fresh
execution of a separately authenticated upstream evaluator
(`tests/quality/test_spreadsheetbench_official_scorer.py:69-95,142-180,205-224`).

**Task and split provenance.** The declared release is `verified_400` from
`KAKA22/SpreadsheetBench` at Hub revision
`ab0b742b0fc95b946f212d80ac7771b5531272e4`, tarball
`spreadsheetbench_verified_400.tar.gz`, SHA-256
`10ef893dd29cb13ab97143ea787e68cdc9574a13873ab9a54e50b31dc03fc949`
(`data/datasets/spreadsheetbench.py:7-18,74-87,257-276`). Upstream citation:
[pinned Hub release](https://huggingface.co/datasets/KAKA22/SpreadsheetBench/tree/ab0b742b0fc95b946f212d80ac7771b5531272e4).
`HG_SPREADSHEETBENCH_DATA` can replace the root. `_tasks()` reads the release's
`dataset.json`, requires 400 rows, resolves initial/golden workbooks using ordered
filename/glob fallbacks, and requires a nonempty `answer_position`
(`data/datasets/spreadsheetbench.py:74-75,105-168`). The source declares the
whole pool official-eval, but its default SPLIT is the hash third; setting
`HG_SPREADSHEETBENCH_SPLIT=none` puts all loaded IDs in eval
(`data/datasets/spreadsheetbench.py:64-71,352-372`). These declarations and
filename fallbacks do not authenticate installed metadata/workbooks, upstream
mirror equivalence or eligible/selected/gradable coverage. `provision_status()`
checks file presence and binary lookup, not hashes or successful recalculation
(`data/datasets/spreadsheetbench.py:319-339`). No payload was inspected here.

**Runtime requirements and verification boundary.** The files environment
declares Python, `openpyxl==3.1.2`, initial workbook and QA CLI/helper; VERIFY
declares trusted golden/task/check inputs and a 300-second timeout
(`data/datasets/spreadsheetbench.py:207-254,278-287,357-366`). Formula detection
uses openpyxl; formula-bearing results require `soffice`/`libreoffice` on PATH,
a writable temporary profile/conversion directory and a subprocess with a
180-second timeout (`data/datasets/spreadsheetbench/verify/check.py:207-260,348-365`).
The adapter's assertion that LibreOffice was measured on a host is inherited
documentation, not current installation/version/cache evidence
(`data/datasets/spreadsheetbench.py:32-37`). Import also attempts `_tasks()`, so
even adapter-based synthetic tests would violate this review's no-payload boundary
(`data/datasets/spreadsheetbench.py:298-304`). Source verifies these code paths;
the present host, dependencies, offline execution, pristine runtime and cleanup
remain unverified. Run aggregation declares arithmetic mean over measured tasks
(`data/aggregation_policies.json:26`).

**What repair `8c323e6` establishes.** Its local diff adds `qa/prediction.py`
to SETUP and removes captured output from the oracle submission; both remain
visible in the current source (`data/datasets/spreadsheetbench.py:213-219`;
`data/datasets/spreadsheetbench/control.py:91-96`). This resolves the missing
module needed by the CLI's import (`data/scaffolds/qa/cli/qa:44-45`). The focused
regression constructs synthetic initial/reference workbooks and injects records;
it asserts a local materialized oracle returns measured reward one, and a failed
submission exposes both output streams
(`tests/quality/test_spreadsheetbench_control_submit.py:15-74`). The earlier
journal records a local oracle reward one, 2/2 regressions and 13 combined tests
for that repair (`docs/adapter-integration-journal.md:77-83`); those are inherited
claims, not fresh validation or production receipts. The local control copies
the golden answer before grading (`data/datasets/spreadsheetbench/control.py:91-109,128-137`).
This is submission-path/control evidence only: no model ability, formula/runtime
coverage, source custody or production admission follows. At this checkout the
blocker checker only reads source literals, registry evidence and pinned history;
it checks that locators exist and reports structural counts, without executing
this control (`tools/check_dataset_blockers.py:52-80,99-121,193-218`). A valid
checker result does not certify the repair's execution or close a runtime gate.

**Classification issue still open.** Comments say an unparseable `283-32`
answer range is unmeasured, but `compare_workbooks()` actually returns
`(False, message)` for a parse failure, and `main()` records that result as
`measured=True, reward=0.0` (`data/datasets/spreadsheetbench/verify/check.py:174-188,369-387`).
Workbook-load failures also return `False`; only a raised comparison exception
uses the unmeasured path (`data/datasets/spreadsheetbench/verify/check.py:164-168,373-378`).
The existing named-range regression asserts only the comparator result/message,
not the main evidence classification
(`tests/quality/test_spreadsheetbench_official_scorer.py:227-235`). Missing result
workbooks are deliberately measured zero, despite the broader module docstring's
description (`data/datasets/spreadsheetbench/verify/check.py:29-31,335-344`).
These are source findings, not newly observed task failures; eligibility and
invalid/unmeasured counts need an explicit reviewed policy and evidence.

**Gate disposition.** CLOSED: missing source-only journal review and clarification
of the repaired submission/control path. OPEN: independently pinned scorer
identity/execution, single-variant metric comparability, installed/mirror and
selected-only/privacy provenance, formula/LibreOffice/offline runtime closure,
failure classification and measured eligible/selected/task/trial coverage.
All eight shared requirements remain open, including trusted runtime, quotas,
artifact-only export and owned snapshot/cleanup evidence
(`docs/dataset-blockers.json:18-140`; `AGENTS.md:61-81`). No production/H0 gate
closes. Retain the original blocker as a measurement follow-up; schema statuses
classify required work and contain no `closed` readiness status
(`tools/check_dataset_blockers.py:16,124-140,170-176`).

