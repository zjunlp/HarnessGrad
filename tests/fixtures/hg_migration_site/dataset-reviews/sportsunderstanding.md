<!-- Filing split, not a new review. The section below is verbatim from
     docs/dataset-source-reviews-b2.md, recovered from 1825cd0e^
     (`tests/fixtures/hg_migration_site/dataset-source-reviews-b2.md`).
     Only the title line was added, so that the per-dataset citation convention
     already used by docs/dataset-reviews/fever.md, physicsquestions.md and
     reasoning_gym.md applies to this dataset too. No content was rewritten. -->

# sportsunderstanding source-only journal review

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

