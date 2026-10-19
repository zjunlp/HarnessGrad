# MemoryAgentBench

The native adapter offers the complete fixed release: **146 episodes, 3,671
questions, 22 sources**. Local primary metrics cover 41 episodes / 3,271 questions;
the two model-judge paths cover the remaining 105 episodes / 400 questions. Each task receives
material chunks in order, then answers questions in order. Answers are final once
accepted. The harness chooses its own storage, retrieval and context strategy.

Data revision: `ai-hyz/MemoryAgentBench@7ea066982b140a19337e17e60d45d4076e042faf`.
Official input and scoring reference:
`HUST-AI-HYZ/MemoryAgentBench@538026089d1a8a8eff05121d0db89b388f360eba`.
The fixed source hashes, question templates, tokenizer resources and ordered
chunk fingerprints are in `specs/`. All corpus bytes stay outside the repository.

The adapter currently uses the existing `KIND = "fixture"` publication switch
while the taxonomy's minimum-members rule for the first memory-demand dataset
is under review. This is a real released benchmark, not synthetic task data.
Explicit `registry.load("memoryagentbench")`, `run_one` and the controls work;
the chooser and corpus-level driver aggregation are not published yet. Formal
registration must add aggregation policy, control/inventory/blocker entries and
`memory / supplied_history` with `context, memory` exercises together, after the
taxonomy policy decision. No minimum-members exception is introduced here.

## Preparation

Set `HG_MAB_DATA` to the external dataset directory; by default it is
`../datasets/memoryagentbench` relative to the checkout. `HG_MAB_PREPARED` can
independently select a prepared pool (default `$HG_MAB_DATA/prepared`). Imports do
not read the corpus. The four upstream splits are capability partitions; the
adapter declares no study/evaluation split. Supply an explicit experiment split
when one is needed.

Install `requirements-prepare.txt` in a separate environment. These dependencies
are needed for preparation and official differential tests; the relay and primary
verifier use only Python's standard library. `downloads()` pins the raw parquet,
metadata, movie mapping and tokenizer resources; fetch with the platform download
command, then use:

```sh
python data/datasets/memoryagentbench/prepare_resources.py \
  --resources "$HG_MAB_DATA/resources" \
  --archive "$HG_MAB_DATA/resources/downloads/punkt_tab.zip"
python data/datasets/memoryagentbench/prepare.py \
  --raw "$HG_MAB_DATA/raw" --resources "$HG_MAB_DATA/resources" \
  --output "$HG_MAB_DATA/prepared"
```

Preparation refuses an existing output, verifies all raw and resource hashes,
and checks every prepared chunk/query sequence against the frozen official
comparison. Repeat `--task-id` to prepare only selected episodes. Every prepared
episode is included in the runnable pool; `excluded_tasks()` returns an empty list.
The two judge sources require explicit provider configuration at scoring time.

## Runtime and scoring

`SETUP` exposes only `mab` and the shared transport client. The selected episode
is private to the host relay; neither future inputs nor gold are staged in the
actor directory. Use:

```sh
./mab next
./mab ack --chunk-id chunk:0 --request-id ack-0
./mab submit --question-id QUESTION_ID --answer-file answer.txt --request-id submit-0
```

`next` writes the complete current text to a local file and returns its path,
size and hash. Acknowledge each material chunk before the next is released.
Reuse the same request ID for an identical retry. The actor cannot call seal,
reset, judge, or select another episode. The final harness `answer.txt` does not
submit question answers.

The relay seals its state on termination through `tool_relay.record`; the
platform adds task/run/trial/input binding and mounts it read-only for the
verifier. `VERIFY.inputs` carries only selected gold, the public movie mapping
and a read-only immutable scorer directory. Actor-created Python packages cannot
shadow the scorer. `result_contract` preserves incomplete episodes and scorer or
transport failures as unmeasured. A genuinely idle control is incomplete;
completed wrong or empty answers are scored by the official formulas. Empty
recommendations may score nonzero under upstream nearest-movie matching.

Runtime budgets come from the explicit run resource policy described below.
Native mount isolation keeps relay state private; actor networking uses
the platform default and this adapter does not claim egress isolation.

Local primary metrics are official `exact_match`, `substring_exact_match`, and
`recsys_recall@5`. ROUGE and other diagnostics remain available through
`lib/scoring.py::local_metrics`, but are not needed to publish these primary
columns. The standard-library edit-distance implementation is differentially
checked against upstream `editdistance.eval`. The verifier fixes
`PYTHONHASHSEED=0` because upstream breaks equal-distance movie ties using set
iteration; compare scores only under the same tie-breaking protocol.

### Model judges

Both paths use the existing verifier-only `VERIFY.judge` capability. No shared
platform interface changes are needed. Gold and judge metadata remain private;
only the scorer receives the judge socket, after the actor has finished.

| Source | Coverage | Primary score | Default judge | Calls per question |
| --- | --- | --- | --- | --- |
| `infbench_sum_eng_shots2` | 100 episodes / 100 questions | `gpt-4-f1`: fluency × F1(recall, precision) | `gpt-4o-2024-05-13` | 3 |
| literal `longmemeval_s*` | 5 episodes / 300 questions | `Accuracy` | `gpt-4o` | 1 |

`specs/judge-prompts.json` freezes the official book-summary prompts and all
LongMemEval question-type/abstention prompts. Summary sampling is temperature
0.1, top-p 0.9, seed 42, maximum 4,096 output tokens; QA uses temperature 0 and
maximum 10 output tokens. The successful scoring call budget is exactly three
times the selected question count for summary, once for QA. Model fallbacks are
not used. Retry and timeout policy comes from the run configuration; retries
default to zero.

Configure `HG_JUDGE_BASE_URL` and `HG_JUDGE_API_KEY` for the judge service.
Model selection is `HG_MAB_JUDGE_MODEL` > `HG_JUDGE_MODEL` > the source's official
default. Blank optional settings mean unset. Set only `HG_JUDGE_MODEL` to share
one model across datasets, or use `HG_MAB_JUDGE_MODEL` to override it here; the two
values need not match. With neither set, summary and QA retain their different
official models. The endpoint must serve whichever model is selected.
Model and output overrides are declared in the unified configuration layer and
appear in `--show-config`/configuration snapshots; the final per-source profile
is frozen in the verifier and its evidence. A nondefault model changes
the recorded judge profile/identity; such scores do not establish equivalence to
the official default-model results. If a provider needs more output tokens,
`HG_MAB_JUDGE_MAX_OUTPUT_TOKENS` explicitly selects a positive integer;
it applies to both sources and changes the profile/identity. It takes precedence
over `HG_EVAL_LIMITS` judge `max_output_tokens`. With neither override, the
official output limits remain. There is no automatic budget increase. Model,
output budget, sampling, prompt/source hashes and
response-admission rules enter the profile. The platform separately records the
endpoint hash, request/response hashes, returned model and token usage. Source
reports reject mixed judge profiles, including per-question mismatches.

The official valid-response prompts, parsing and formulas are preserved. Stricter
failure admission prevents empty/malformed decisions, nonbinary fluency and
out-of-range counts from becoming ordinary scores. QA requires a yes/no word
before applying the official `yes` substring rule. Provider or parsing failure
makes the entire episode unmeasured; the adapter does not reproduce upstream
batch code that silently drops failed summary items from the denominator.
An incomplete episode issues no judge requests.

Platform reward is the question mean **within an episode**, then a mean over
measured episodes. `aggregate()` / `python -m data.datasets.memoryagentbench.report`
produce question-weighted means **per source** with the full selected denominator
and explicit missing/unmeasured coverage. They do not publish incomplete source
scores or invent a benchmark-wide official total. Pass raw verifier evidence
JSON files to `report --evidence`, and use `--task-id` for the exact selected pool.
Use the runner's `recordings_root` to retain sealed records after cleanup.

## Controls and tests

```sh
python data/datasets/memoryagentbench/control.py --oracle
python data/datasets/memoryagentbench/control.py --wrong
python data/datasets/memoryagentbench/control.py --noop
# Repeat each with --platform for the actual actor/verifier namespaces.
python tools/dataset_lint.py --dataset memoryagentbench
```

The default control uses a released Conflict Resolution episode. Oracle scores
1; completed wrong answers score 0; noop reports `incomplete_episode` with no
score. `--output` preserves a receipt and, for platform controls, the sealed
record outside the temporary work directory. These default local-source controls
make zero model calls. Selecting a judge-source task calls its configured judge
for oracle/wrong/empty controls; noop still makes no requests. Reference-answer
controls test scoring plumbing and do not guarantee a model judge will award 1.
The CLI checks the expected oracle=1/wrong=0 values and retains any discrepancy
in the receipt; it never adjusts the returned score.

`tests/quality/test_memoryagentbench_*.py` covers input preparation, official
scorer differential tests, ordering and retries, selected-input privacy, trusted
record binding, source aggregation and real sandbox controls. Set `MAB_UPSTREAM`
to the pinned official checkout and `NLTK_DATA` to the locked English sentence
resources to enable the official comparisons. Missing reference source skips
those comparisons; it does not establish equivalence. No real-model diagnostic
or benchmark performance claim follows from these controls.

A bounded API connectivity smoke also exercised the normal SDK and `./mab`
client in the actual sandbox: it retained both full material chunks of
`conflict_resolution-r0000` and submitted the first four of its 100 questions.
The trusted record and verifier correctly reported `incomplete_episode` with no
score. This fixed diagnostic selection is not an official training split or a
completed benchmark episode. This actor-side smoke is separate from judge validation; complete-episode
scoring is covered by released-data controls.
Keep smoke harnesses, API receipts and sealed records outside the repository.

The two judge paths were also checked with bounded released-data API controls:
one complete summary episode (reference/wrong, six calls) and one selected QA
question (reference/wrong). With explicit `Ling-3.1-flash`, summary scored 1/0;
QA returned no scoring text at the default 10-token limit, correctly remaining
unmeasured. A separately declared 512-token QA diagnostic scored 1/0 in two more
calls. Total: ten API calls, no full evaluation. These validate transport and
scoring, not judge equivalence or benchmark performance. Full 60-question QA
protocol/aggregation is tested with a local HTTP stub; all 146 prepared episodes
and 400 judge questions passed inventory/metadata validation without API calls.

### Run resource limits

`HG_EVAL_LIMITS` supplies global area settings and per-dataset overrides under
`datasets.memoryagentbench`. Supported areas used here are `task`
(`agent_timeout_s`, `verify_timeout_s`), `relay` (`max_calls`, `deadline_s`,
`max_line_bytes`), `record` (`max_bytes`), `submission` (`max_bytes`), and `judge`
(`max_calls`, `max_output_tokens`, `timeout_s`, `max_retries`, `max_attempts`,
`response_max_bytes`, `max_line_bytes`). Without an explicit setting,
this adapter adds no task deadline, relay call/frame cap, recording cap, answer
size cap, or judge request deadline. Task execution still follows the driver's
phase policy. Official scoring output defaults and successful call structure
remain as described above. An explicit judge `max_calls` can tighten the
official successful-call upper bound; a larger value or null does not relax it.

Explicit answer limits are enforced by the private relay and bound into the
trusted service input; the actor cannot change them. Answers and request IDs
have no dataset-specific size ceiling. UTF-8 decoding, ordered access,
idempotent request IDs, private records and complete-episode scoring remain
required. A resource limit or service failure produces an unmeasured result,
not an ordinary zero score. The official 4,096-token material chunking policy
is unchanged.
