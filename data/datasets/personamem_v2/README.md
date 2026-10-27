# PersonaMem-v2

This adapter provides **text-only 32k histories with multiple-choice questions**.
History messages arrive in their original order through a private relay; questions
and options become available only after all history messages are acknowledged.
The harness chooses how to retain, update and retrieve information. Each native
split/persona combination is a separate episode with fresh state.

This delivery protocol exercises retention of evolving information rather than
supplying a complete history and questions in a single prompt. The content includes
preference updates and requests to forget information. No memory algorithm, model,
store format, context window or memory-size limit is imposed. This is a HarnessGrad
sequential-delivery protocol, not a reproduction of the paper's trained MemAgent.
The adapter does not provide 128k, multimodal or open-ended judge evaluation.

Official sources:

- [Dataset and CC-BY-4.0 attribution](https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2)
  at `ed956dea41521fc4499acbc63f966e0fd3c053ba`.
- [Code](https://github.com/bowen-upenn/PersonaMem-v2) at
  `d29d91d016add354e459dfeb0d24af08bc402e2a`.
- [Paper](https://arxiv.org/abs/2512.06688), *PersonaMem-v2: Towards Personalized
  Intelligence via Learning Implicit User Personas and Agentic Memory*, Jiang et al.

The adapter is registered as `memory / supplied_history` and exercises `context`
and `memory`: answers depend on retaining and updating user information and
selecting relevant, current preferences for the model's context.
Interaction is `closed_loop`: acknowledging a history message or submitting an
answer advances the private episode. The fixed `next/ack/submit` sequence does
not require action planning or stopping decisions, so it does not establish
`control` coverage.

## Native divisions

The release contains **25,610 questions** in **1,735 episodes**. The fixed
valid-question evaluation pool retains **25,609 questions and all 1,735 episodes**:

| Native split | Released questions | Excluded questions | Evaluated questions | Episodes |
| --- | ---: | ---: | ---: | ---: |
| train | 18,549 | 1 | 18,548 | 800 |
| val | 2,061 | 0 | 2,061 | 735 |
| benchmark | 5,000 | 0 | 5,000 | 200 |

`NATIVE_SPLITS` exposes the three sets of episode IDs. HarnessGrad's two-sided
`SPLIT` maps **study/train = native train + val**, **eval = native benchmark**.
`OFFICIAL_EVAL` is the benchmark set. The platform train side therefore does not
mean the native training CSV alone. Callers can select native train or val IDs
through existing task selection; there is no additional random/hash division.

These are native question divisions, not strictly disjoint user identities.
Train and val share 735 personas. Persona 78 also occurs in benchmark:
20 train questions, 2 val questions, 6 benchmark questions. Its three episodes have
different IDs and never share runtime state. No rows are silently moved or removed
to repair this upstream overlap. Experiments using these divisions must not claim
that every evaluation persona is unseen during study.

## Preparation

Set `HG_PERSONAMEM_V2_DATA` to the external dataset directory; the default is
`../datasets/personamem_v2` relative to the checkout. `downloads()` pins three
CSV files and the 999 current histories they reference, approximately 394 MB.
Historical copies in the upstream directory are not needed. Corpus and prepared
bytes stay outside the repository.

```sh
export HG_PERSONAMEM_V2_DATA=/absolute/path/to/datasets/personamem_v2
python tools/datasets.py --fetch --dataset personamem_v2
python data/datasets/personamem_v2/prepare.py \
  --raw "$HG_PERSONAMEM_V2_DATA/raw" \
  --output "$HG_PERSONAMEM_V2_DATA/prepared"
```

Preparation uses Python's standard library. It checks every source CSV and each
selected history against `specs/source.json`, retains original split row indices,
and refuses to overwrite existing prepared output. Repeat `--task-id` to prepare
selected complete episodes; the three native CSVs are still checked in full.
Prepared pools must declare the current valid-question metric; incompatible pools
are rejected. Prepare into a fresh directory and preserve previous data and receipts
outside the repository when changing evaluation protocols.
Each history message is one delivery item; no token-based truncation is applied.
Rendering follows MemAgent's `Message N (role): content` format, removing the first
system message containing the explicit persona. Empty text is retained; a source
object lacking a content key is skipped, matching official preprocessing.

The source includes two concrete data irregularities:

- Ten native train rows encode three distractors followed by JSON `null`. The
  adapter parses the JSON and retains the three supplied distractors. This is an
  explicit serialization normalization: the official MemAgent Python-literal
  parser does not handle these rows correctly. Original files and row IDs remain
  pinned; affected IDs appear in prepared metadata as `normalized_json_distractors`.
- Native train row 949 (zero-based; the 950th data record), persona 592, has no
  distractors. Its reference contains only `You can try a minimal script like:`.
  This single question is excluded with reason `missing_mcq_distractors`;
  the other **28 questions** in the same episode remain runnable and scored.
  `specs/source.json::excluded_questions` pins the split, original row index,
  persona and SHA256 of the canonical parsed CSV row. Preparation verifies that
  identity and the missing distractors before excluding the row. Unknown malformed
  questions fail preparation; there is no general-purpose automatic filtering.

The official MemAgent converter retains this row without MCQ options while
leaving `is_mcq=True`. Its rule scorer interprets the reference's initial `Y`
as answer `y`, so this adapter does not use that malformed scoring path.
The exclusion is an explicit HarnessGrad evaluation policy, not an upstream
correction or an exact reproduction of all released training questions.
Original CSV bytes and indices remain unchanged, including the shuffle seeds
of every retained question. Exclusion metadata stays outside actor inputs.

## Runtime and scoring

```sh
./personamem next
./personamem ack --chunk-id chunk:0 --request-id ack-0
./personamem submit --question-id QUESTION_ID \
  --answer-file answer.txt --request-id submit-0
```

`next` writes the complete current text to `personamem-current.txt` and returns
its path, size and SHA256. The next input overwrites that file; successful
acknowledgement removes it. The CLI does not retain a history archive. The harness
must copy, summarize or otherwise retain anything it needs in its own storage
before acknowledging. The relay cannot replay acknowledged history and cannot
provide future history or questions. Successful request IDs are idempotent for identical retries;
answers cannot be revised. The final harness `answer.txt` is not a submission.
On `done`, finish.

The metric is `personamem_v2.text32k.memagent_mcq_accuracy.valid_questions.v1`.
Four options are
shuffled using `random.Random(42 + original_split_row_index)`, matching official
MemAgent preprocessing on valid source rows. Answers use `\boxed{a}` through
`\boxed{d}`. The scorer preserves the official last-boxed extraction and
space/newline/case normalization. It does not use the different option shuffle
and parser in upstream `inference.py`. No model judge or environment model is
required. `specs/scorer.json` pins the official reference and preprocessing
fragment used by the differential tests.
Public question IDs hash only the original query text. Original CSV row indices
and shuffle seeds remain private: exposing them would reveal correct option
positions without using memory.

The relay seals answers through `tool_relay.record`; only the verifier receives
the bound immutable record and selected gold. The scorer is projected read-only.
An incomplete episode is unmeasured; a completed wrong answer scores zero.
Scorer, transport and source failures remain distinct from model mistakes.
There is no adapter-specific deadline, output-token budget, step cap, relay call
cap, answer-size cap or record-size cap. Platform lifecycle and phase settings,
harness policy and provider limits still apply.

Platform reward is the mean over eligible questions within an episode (28 for
train persona 592); the excluded question is not a zero. Platform averaging across
episodes is not the benchmark's question-weighted accuracy. `aggregate(records,
selected_task_ids=...)` reports question-weighted accuracy **per native split**,
with selected/measured coverage. `data/aggregation_policies.json` declares
`run_reward` as an arithmetic mean over measured episodes (`measured_tasks`),
and `corpus_metrics` as an arithmetic mean over eligible selected MCQs (`gold_rows`)
within each native split. The latter counts questions after fixed source exclusions;
missing or incomplete episodes still leave the split score unset.
Verifier evidence and split summaries include
`original_questions`, eligible `expected_questions`, `excluded_question_count`
and the exact `excluded_questions` with reasons. The full native train denominator
is 18,548 eligible questions, not 18,549 released questions. Report this explicitly
as accuracy over the fixed valid-question pool.
Any missing or unmeasured selected episode leaves that split's score unset;
incomplete model answers and runtime failures are never treated as source exclusions.
Mixed metric versions or conflicting exclusion coverage are rejected. The aggregator
does not combine native train/val/benchmark into an invented official total.

## Controls and tests

```sh
python data/datasets/personamem_v2/control.py \
  --task-id personamem-v2-train-p0153-text32k --oracle --platform
python data/datasets/personamem_v2/control.py \
  --task-id personamem-v2-train-p0153-text32k --wrong --platform
python data/datasets/personamem_v2/control.py \
  --task-id personamem-v2-train-p0153-text32k --noop --platform
```

Omit `--platform` to exercise the live relay and isolated verifier without a full
actor run. Oracle scores 1 and completed wrong answers score 0 for valid episodes.
Noop is explicitly unmeasured `incomplete_episode`. `--output` saves the receipt
and trusted record outside the repository; controls make no model calls.

`tests/quality/test_personamem_v2.py` covers official scorer/preprocessing parity,
ordering and retries, delayed questions, source integrity, native split identity,
the fixed exclusion and retained-question denominators, scorer isolation and real sandbox controls. The full
release inventory test requires the external prepared pool.
