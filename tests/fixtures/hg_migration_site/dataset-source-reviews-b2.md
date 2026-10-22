# Dataset source reviews — b2 — 2026-10-15

Scope: repository source at baseline `9066072`, plus the local diff of
`8c323e6`; no adapter imports, task payload reads, controls, scorer execution,
model calls, network access or host provisioning inspection. The only executable
validation for this change is the blocker checker and source-only focused tests.
Line citations below refer to the checked-in source, not an inspected upstream
checkout. An upstream URL is a citation recorded by source, not fresh custody
or a verified equivalence claim.

## sportsunderstanding source-only review

**Metric identity.** The adapter calls its metric
`multiple_choice_grade (the correct label named)`, returning binary reward.
The implementation is a case-insensitive label match bounded by positions without
adjacent ASCII letters, with answers naming both labels rejected. Thus `implausible` cannot
accidentally match `plausible`, but this is a local generative-answer rule, not an
execution of BIG-bench's multiple-choice evaluator
(`data/datasets/sportsunderstanding.py:63-95`). The adapter attributes the metric
to `google/BIG-bench`, `sports_understanding/task.json`, and describes ReWOO's
flattening of `target_scores` to the winning label
(`data/datasets/sportsunderstanding.py:16-32,178-191`). Upstream citation:
[BIG-bench task definition](https://github.com/google/BIG-bench/blob/main/bigbench/benchmark_tasks/sports_understanding/task.json).
No upstream scorer revision is pinned by those declarations; the existing test
checks the local word/dual-label rules rather than invoking an independent
BIG-bench scorer (`tests/quality/test_bigbench_official_scorers.py:114-142`).
Official equivalence and cross-paper comparability remain unestablished.

**Task and split provenance.** The declared source is
`billxbf/ReWOO@9cd0283043ff4be0c9d614fda2789d143ca6ffd1`, member
`data/bigbench/sports_understanding.csv`; the download declares tarball SHA-256
`7fe653d18faad40bf10ddcc7d85415782af975d80792bb26401393203b9ce136`
(`data/datasets/sportsunderstanding.py:52-56,147-163,189-191`). Upstream citation:
[pinned ReWOO table](https://github.com/billxbf/ReWOO/blob/9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/bigbench/sports_understanding.csv).
The loader reads the entire CSV with `csv.DictReader`, requires 1,000 rows and
validates targets as `plausible`/`implausible`; `input` becomes the question and
the table is named `test` (`data/datasets/sportsunderstanding.py:101-116,166-173,188`).
The scaffold constructs `sportsunderstanding/test/<row-index>` IDs and imposes
the SHA-256 first-byte modulo-three eval partition because no study split is
supplied; `OFFICIAL_EVAL=None` represents the whole loaded pool, not independent
proof of completeness (`data/scaffolds/qa/base.py:123-131,147-161,241-258,290-312`;
`data/datasets/sportsunderstanding.py:200-212`). Row count, correct labels, pins
and comments do not authenticate installed bytes, upstream derivation, a
selected-task manifest, official split custody or absence of overlap. The
loader's checks do not hash the extracted CSV
(`data/datasets/sportsunderstanding.py:101-116`). None was measured here.

**Runtime requirements and verification boundary.** Source inspection establishes
a Python CSV/regex scorer and a files environment; `pyarrow==23.0.0` is declared
for consistency and explicitly unused for this CSV
(`data/datasets/sportsunderstanding.py:38-41,183-186`;
`data/scaffolds/qa/base.py:438-446`). SETUP stages the QA CLI, prediction helper
and schema; VERIFY declares `python3`, a 3,600-second timeout, inline per-task
gold, the check and copied scaffold/adapter code
(`data/scaffolds/qa/base.py:314-386`). The QA check imports the local adapter and
calls its scorer; an unmeasured path writes no reward, while successful scoring
writes reward/evidence (`data/scaffolds/qa/verify/check.py:104-109,208-242,249-269`).
Importing this adapter itself calls `build()` and `ensure()`, which can attempt
the CSV read; this review therefore never imports it
(`data/datasets/sportsunderstanding.py:194`;
`data/scaffolds/qa/base.py:195-213,438-440`). These are verified source contracts,
not verified package installations, offline transitive closure, trusted runtime
identity or a successful production scoring receipt. The declared run aggregation
is arithmetic mean over measured tasks, so missing measurements cannot stand in
for full task/trial coverage (`data/aggregation_policies.json:25`).

**Gate disposition.** CLOSED: the missing source-only journal review, including
identification of the implemented metric, declared task/split origin and runtime
requirements above. OPEN: independent upstream scorer identity/equivalence,
installed-source and selected-only/privacy provenance, measured coverage and
comparability; all eight referenced shared requirements remain open. Their
definitions explicitly distinguish source calls/rematerialization from official
execution, offline closure and pristine runtime, and require filesystem quotas,
artifact-only export and owned snapshot/cleanup observations
(`docs/dataset-blockers.json:18-140`; `AGENTS.md:61-81`). No production/H0 gate
closes. Retain `dataset-journal-gap`, `journal-coverage`, `measurement` as the
review/receipt follow-up record; its observation must describe this closure
without representing readiness (`tools/check_dataset_blockers.py:16,124-140,170-176`).

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

## strategyqa source-only review

**Metric identity.** The adapter implements whole-string exact match after
lowercasing, removing ASCII punctuation, removing English articles and collapsing
whitespace, yielding binary reward. Its metric label is
`exact_match after ReWOO's normalize_answer`
(`data/datasets/strategyqa.py:66-101`). The declared upstream is
`billxbf/ReWOO`, `utils/Evaluator.py::normalize_answer` and `em`; F1 and an
unpinned LLM judge's `acc` are explicitly excluded
(`data/datasets/strategyqa.py:20-37`). Upstream citation:
[ReWOO evaluator at the declared table commit](https://github.com/billxbf/ReWOO/blob/9cd0283043ff4be0c9d614fda2789d143ca6ffd1/utils/Evaluator.py).
The local test transcribes the same normalizer and checks whole-string examples;
it does not run an independent upstream evaluator
(`tests/quality/test_bigbench_official_scorers.py:37-52,86-103`). Neither a
canonical StrategyQA leaderboard identity nor fresh scorer-source custody follows
from this transcription. The statement that BIG-bench main removed the task is
an inherited adapter assertion, not a network observation by this review
(`data/datasets/strategyqa.py:14-18,188-191`).

**Task and split provenance.** Source declares ReWOO commit
`9cd0283043ff4be0c9d614fda2789d143ca6ffd1`, member
`data/bigbench/strategy_qa.csv`, and tarball SHA-256
`7fe653d18faad40bf10ddcc7d85415782af975d80792bb26401393203b9ce136`
(`data/datasets/strategyqa.py:56-60,157-173,198-199`). Upstream citation:
[pinned ReWOO table](https://github.com/billxbf/ReWOO/blob/9cd0283043ff4be0c9d614fda2789d143ca6ffd1/data/bigbench/strategy_qa.csv).
The CSV loader reads all rows and checks 2,290 entries; targets must be exactly
`Yes` or `No`, and `input` supplies the question in a locally named `test` pool
(`data/datasets/strategyqa.py:107-122,176-183,197`). The scaffold assigns
`strategyqa/test/<row-index>` IDs and the hash eval third, with no supplied study
split (`data/scaffolds/qa/base.py:123-131,147-161,241-258,290-312`). The adapter
describes whole-pool official eval, but that is a declaration about this pool,
not independently established correspondence to original StrategyQA splits
(`data/datasets/strategyqa.py:208-220`). Installed CSV digest, upstream derivation,
official split/label custody, overlap, selected-only provenance and coverage
are not established by row-count/label checks
(`data/datasets/strategyqa.py:107-122`). No payload was inspected here.

**Runtime requirements and verification boundary.** Scoring uses Python's
`re`/`string` and the QA scaffold; declared `pyarrow==23.0.0` is explicitly
unneeded by the CSV reader (`data/datasets/strategyqa.py:41-45,192-195`). SETUP
stages the CLI/helper/schema; VERIFY declares `python3`, inline gold and copied
scaffold/adapter/check inputs with a 3,600-second timeout; the environment is
`files` (`data/scaffolds/qa/base.py:314-386,438-446`). The check invokes the
local adapter's score and distinguishes its unmeasured path from reward output
(`data/scaffolds/qa/verify/check.py:104-109,208-242,249-269`). Module build attempts
table loading, so no adapter was imported here
(`data/datasets/strategyqa.py:202`; `data/scaffolds/qa/base.py:195-213,438-440`).
These verified source contracts do not establish installed packages, offline
transitive closure, trusted verifier identity, execution or cleanup. The declared
aggregation is arithmetic mean over measured tasks, not evidence of complete
task/trial coverage (`data/aggregation_policies.json:27`).

**Gate disposition.** CLOSED: missing source-only journal review of the ReWOO EM
metric, declared table/split and runtime requirements. OPEN: independent scorer
and original-benchmark comparability, installed/selected-only/privacy and split
provenance, measured coverage, and all eight referenced shared requirements.
Source-only rematerialization declarations cannot close official execution,
offline scorer closure, pristine runtime, quotas, artifact export or owned
snapshot/cleanup gates (`docs/dataset-blockers.json:18-140`; `AGENTS.md:61-81`).
No production/H0 gate closes. Preserve the original blocker as the measurement
follow-up rather than deleting its record or adding an unsupported passing
status (`tools/check_dataset_blockers.py:16,124-140,170-176`).
