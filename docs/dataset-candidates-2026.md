# Dataset candidates for harness learning — checked 2026-10-23

Read this before onboarding a new benchmark. Everything here was verified by fetching the source
that is named; anything not fetched is marked **unverified** rather than assumed.

The protocol this repository measures is **fit on a train side, select on a dev side, report once on
an exam side**, with one goal and one artifact per task, a deterministic scorer, and no second
party. A candidate is usable only if it can be run that way. "Has a test set" is necessary and not
sufficient — a public test set is still usable as an exam (we never train on it) as long as the
revision is pinned and the model-side contamination risk is declared.

**The one property that decides everything else: does a method move the *harness* while the weights
stay fixed?** If a benchmark lets the agent fine-tune the task model, then a score difference cannot
be attributed to the harness, and the number is not harness evidence however good the benchmark is.

## What is new, and what it is good for

| candidate | released | splits, as published | licence | our verdict |
| --- | --- | --- | --- | --- |
| **VibeTraining-Bench** ([arXiv 2610.26342](https://arxiv.org/abs/2610.26342), [repo](https://github.com/D2I-ai/VibeTraining-Bench)) | 2026-10-19, repo pushed 2026-10-22 | **validation feedback → freeze → test, by protocol**; test isolated in a separate grading container | MIT | **protocol reference only** — it improves the *model* as well as the harness (see below) |
| **HarnessDev / ASPIRE / S³Gym** ([09](https://arxiv.org/abs/2609.01437), [11](https://arxiv.org/abs/2608.31111), [00](https://arxiv.org/abs/2608.31100)) | 2026-08-31 / 09-01 | three **separate** benchmarks, each with held-out evaluation (HarnessDev: 5 downstream benchmarks, 2,207 instances, hidden tasks) | not stated | **the right kind of target, no public data** — HarnessDev's unit is a runnable harness; ask the authors |
| **GAIA2** ([HF](https://huggingface.co/datasets/meta-agents-research-environments/gaia2), [ARE](https://github.com/facebookresearch/meta-agents-research-environments)) | 2025-09-09 | **`validation` only**, 160 per config × 7 configs; no test split in the dataset or the repo | CC-BY-4.0 | **not an exam** — usable as a distribution-shift probe, or ask Meta for the hidden test |
| **SWE-Gym** ([HF](https://huggingface.co/datasets/SWE-Gym/SWE-Gym), ICML 2025) + SWE-bench Verified as test | 2024-12 | `train` 2,438 only; the held-out side is SWE-bench Verified | MIT | usable as a **harness-only** code domain — fit the scaffold on SWE-Gym, exam on Verified, never fine-tune |
| **MLE-Dojo** ([arXiv 2505.07782](https://arxiv.org/abs/2505.07782)) | 2025-05 | 200+ Kaggle competitions; the competition's own test is scored by submission | unverified | fallback for a non-terminal domain; heavy container needs |
| **AppWorld** / **τ³-bench** / **`terminal_bench`** | already here | train 90 / dev 57 / exam 585; `split_tasks.json` train/test; no train side | — | in use / in unmerged `#20` / exam-only |

GAIA2 deserves one more sentence because it is the trap: a validation-only release cannot be both
the selection side and the reported side, and cutting our own test out of it is not the benchmark's
official test. If it is used at all, it is a *probe* (does the harness survive a different
environment?) and must be labelled that way in any table.

## Why VibeTraining-Bench is not an exam here

A labmate's reading was right and this file's first version was wrong, so the evidence matters: the
repository's top level contains **`training/`**, `containers/trainer.Dockerfile` and
`scripts/prepare/python/aime_sft_pipeline/` (which ships `convert_cot_to_slime_sft.py`); 62 paths
mention `train` and 15 mention `sft`, while five mention `harness` — and those five are
`benchmarks/*/task_context/harness.md`, i.e. a document handed to the agent. The abstract says it
plainly: the agent "jointly optimize[s] training data, the trainer, and the inference harness". The
README is even sharper about the *design goal*: it measures "coordinated data, trainer, and harness
improvement **rather than isolated tuning**", and a formal run builds a trainer image plus SGLang and
converts the checkpoint through Megatron -- only a "no-training smoke" skips training. So the
benchmark does not merely *permit* weight changes; it rewards them.

So it is a **model-training** benchmark. The agent under test may fine-tune the task model, and then
a score difference is not attributable to the harness. Measuring it here would produce a number
nobody can attribute — the same class of error as an unreproducible released metric, which this
platform refuses rather than resamples.

What is worth taking from it is the **measurement discipline**, and that belongs in a citation
rather than in an adapter: a fixed resource budget, validation feedback only, freeze, then **one**
evaluation on an isolated test side, with research and grading containers separated and the research
side off the network. If we adopt anything, it is that shape applied to our own harness-only suites.

## If a harness-only benchmark is wanted as an exam

- **HarnessDev** is the right *kind* of target: its unit is a runnable harness, evaluated on
  held-out benchmarks. It has no public data, so step one is asking the authors.
- **`tau3_bench`** (unmerged `#20`) ships a real train/test split and is harness-only.
- **AppWorld** is already the main harness-only study with train/dev/exam.
- **`terminal_bench`** is harness-only and has no train side: an exam, not a study.
- **SWE-Gym → SWE-bench Verified** works if the scaffold only is edited; that constraint has to be
  enforced by the run, not assumed.

## Checklist for a new adapter (what the platform enforces)

- one goal and one artifact per task; a scorable artifact at the end, not a conversation;
- a **deterministic** scorer, runnable offline, with no paid endpoint (a paid judge makes the number
  non-publishable);
- `invalid ≠ 0`: a task the platform could not measure is absent from `per_task`; a task that
  **timed out** is a `0.0` (operator decision 2026-10-23, `INTERFACE.md`);
- every point records which tree it measured (`platform_content_sha`), so a table cannot mix two
  platforms;
- `tests/quality/test_<dataset>*.py`, a `CAPABILITY`/`EXERCISES` entry in `tools/dataset_classes.py`,
  and an `aggregation_policies.json` entry carrying exactly the scopes the module declares
  (`run_reward`, plus `corpus_metrics` only if it defines `aggregate`/`evaluate_predictions` — see
  `tests/quality/test_aggregation_policies.py`);
- no user simulator: a task that needs a second party is a platform-facility question
  (`docs/refusal-policy.md`), never something the adapter fakes.

## Open questions, before any GPU time

1. Is the **test data** shipped, or fetched at grading time? If fetched, the exam is not
   reproducible offline and the adapter needs a `provision_status()` that says so.
2. What does one task cost — wall clock, tokens, containers? That decides whether a 100-task dev
   side is affordable at all.
3. Which suites are self-contained, and which need network or an external service?
4. Does the grading container fit `eval/run`'s harness, or does it need a new runtime kind?
5. For a harness-only claim: is the method forbidden from writing to model weights or training
   configs? If not, the number is not harness evidence.
