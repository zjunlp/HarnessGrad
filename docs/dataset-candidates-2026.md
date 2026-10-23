# Dataset candidates for harness learning — checked 2026-10-23

Read this before onboarding a new benchmark. Everything here was verified by fetching the source
that is named; anything not fetched is marked **unverified** rather than assumed.

The protocol this repository measures is **fit on a train side, select on a dev side, report once on
an exam side**, with one goal and one artifact per task, a deterministic scorer, and no second
party. A candidate is usable only if it can be run that way. "Has a test set" is necessary and not
sufficient — a public test set is still usable as an exam (we never train on it) as long as the
revision is pinned and the model-side contamination risk is declared.

## What is new, and what it is good for

| candidate | released | splits, as published | licence | our verdict |
| --- | --- | --- | --- | --- |
| **VibeTraining-Bench** ([arXiv 2610.26342](https://arxiv.org/abs/2610.26342), [repo](https://github.com/D2I-ai/VibeTraining-Bench)) | 2026-10-19, repo pushed 2026-10-22 | **validation feedback → freeze → test, by protocol**; test isolated in a separate grading container | MIT | **take it** — see the handoff section |
| **HarnessDev / ASPIRE / S³Gym** ([09](https://arxiv.org/abs/2609.01437), [11](https://arxiv.org/abs/2608.31111), [00](https://arxiv.org/abs/2608.31100)) | 2026-08-31 / 09-01 | three **separate** benchmarks, each with held-out evaluation (HarnessDev: 5 downstream benchmarks, 2,207 instances, hidden tasks) | not stated | **literature** — no public data linked from the project page; ask the authors |
| **GAIA2** ([HF](https://huggingface.co/datasets/meta-agents-research-environments/gaia2), [ARE](https://github.com/facebookresearch/meta-agents-research-environments)) | 2025-09-09 | **`validation` only**, 160 per config × 7 configs; no test split in the dataset or the repo | CC-BY-4.0 | **not an exam** — usable as a distribution-shift probe, or ask Meta for the hidden test |
| **SWE-Gym** ([HF](https://huggingface.co/datasets/SWE-Gym/SWE-Gym), ICML 2025) + SWE-bench Verified as test | 2024-12 | `train` 2,438 only; the held-out side is SWE-bench Verified | MIT | fallback for a cheap code domain |
| **MLE-Dojo** ([arXiv 2505.07782](https://arxiv.org/abs/2505.07782)) | 2025-05 | 200+ Kaggle competitions; the competition's own test is scored by submission | unverified | fallback for a non-terminal domain; heavy container needs |
| **AppWorld** / **τ³-bench** | already here | train 90 / dev 57 / exam 585; `split_tasks.json` train/test | — | in use / in unmerged `#20` |

GAIA2 deserves one more sentence because it is the trap: a validation-only release cannot be both
the selection side and the reported side, and cutting our own test out of it is not the benchmark's
official test. If it is used at all, it is a *probe* (does the harness survive a different
environment?) and must be labelled that way in any table.

## Handoff: VibeTraining-Bench

Taking this on means writing a `data/datasets/` adapter and a `registry/dataset-blockers.json` row,
exactly like every other dataset. What is already established:

* **The protocol is the point.** Fix a resource budget, let the research agent iterate with
  **validation** feedback, freeze, then evaluate the final system **once** on the test side. Test
  isolation, a separate grading container and network restrictions for the research side are part
  of their design, not extras — our receipt's `identity` already records the tree, model and
  endpoint, so the two can be reconciled.
* **Licence MIT**, repo has 2,952 files: `benchmarks/`, `agents/` (run adapters), `LICENSE`.
* **It ships eight benchmark directories**, which is where the overlap risk lives:
  `terminal-bench__terminal-bench-2-1`, `swe-bench__swe-bench-verified`,
  `sierra-research__tau3-bench`, `eigent-ai__toolathlon_gym`,
  `hkust-nlp__toolathlon_verified`, `openthoughts__openthoughts-tblite-val50`, `aime__26`,
  `aime__aime`.

Overlaps to settle before writing code:

1. **`tau3_bench` is our unmerged `#20`.** Do not port a second τ³ adapter; either take theirs as
   the reference implementation or reuse ours, and decide which one the platform measures.
2. **`terminal_bench` already has an adapter here.** Theirs is version **2.1**; ours is pinned to
   its own revision. Pick one revision per table and never mix.
3. **`swe-bench__swe-bench-verified` as an exam is public.** That is allowed, and it must be
   declared: pin the revision, never train on it, and note the model-side contamination risk in the
   table's caption rather than pretending it away.

What the adapter must satisfy — the checklist the platform actually enforces:

- one goal and one artifact per task; a scorable artifact at the end, not a conversation;
- a **deterministic** scorer, runnable offline, that does not need a paid endpoint (a paid judge
  makes the number non-publishable);
- `invalid ≠ 0`: a task the platform could not measure is absent from `per_task`; a task that
  **timed out** is a `0.0` (operator decision 2026-10-23, `INTERFACE.md`);
- every point records which tree it measured (`platform_content_sha`), so a table can never mix
  two platforms;
- `tests/quality/test_<dataset>*.py`, a `CAPABILITY`/`EXERCISES` entry in
  `tools/dataset_classes.py`, and an `aggregation_policies.json` entry with the scopes the module
  actually declares (`run_reward`, plus `corpus_metrics` only if it defines
  `aggregate`/`evaluate_predictions` — see `tests/quality/test_aggregation_policies.py`);
- no user simulator: if a task needs a second party, that is a platform facility question
  (`docs/refusal-policy.md`), not something the adapter may fake.

Open questions to answer with the repo in hand, before any GPU time:

1. Is the **test data** shipped in the repo, or fetched at grading time? If fetched, the exam is
   not reproducible offline and the dataset needs a `provision_status()` that says so.
2. What does one task cost — wall clock, tokens, containers? That decides whether a 100-task dev
   side is affordable at all.
3. Which of the eight benchmarks are self-contained, and which need network or an external service?
4. Does its grading container fit `eval/run`'s harness, or does it need a new runtime kind?
