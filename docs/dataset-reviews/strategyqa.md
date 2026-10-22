<!-- Filing split, not a new review. The section below is verbatim from
     docs/dataset-source-reviews-b2.md, recovered from 1825cd0e^
     (`tests/fixtures/hg_migration_site/dataset-source-reviews-b2.md`).
     Only the title line was added, so that the per-dataset citation convention
     already used by docs/dataset-reviews/fever.md, physicsquestions.md and
     reasoning_gym.md applies to this dataset too. No content was rewritten. -->

# strategyqa source-only journal review

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
