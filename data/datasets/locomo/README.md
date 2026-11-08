# LoCoMo native adapter (Harbor protocol)

This port preserves the task text, transcript rendering and scoring of Harbor
`3e9f14a7c1033896cc600d27de69d6948bcb3410`. It uses the official `locomo10.json`
from `snap-research/locomo@3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`;
`specs/source.json` pins source bytes and the Apache-2.0 Harbor reference files.
The reference license ships alongside this adapter as [LICENSE.harbor](LICENSE.harbor).

There are 10 tasks (complete conversations), 1,986 questions, all five categories.
Every task supplies `conversation.md` and the full question list immediately.
The harness writes `answers.json`, mapping string question indices to short answers.
Only file paths change from Harbor's instruction. The private answer key and
category/evidence fields enter `VERIFY.inputs` after the actor exits. The verifier
runs with isolated Python imports and writes a scalar reward plus per-question
prediction/score evidence. This is static long-context evidence, not a staged
memory episode. No dataset-specific harness, Docker or service is needed.

The score is Harbor's full-precision token F1 (categories 1–4) and refusal/option
check (category 5); no model judge or downloaded NLTK corpus is used. Category 1
uses comma-separated multi-answer matching; category 3 uses the first semicolon
answer. Category 5 option placement is deterministic. Malformed/missing answer
JSON produces measured zero, as in Harbor. A failed verifier/provider execution
is unmeasured and must never be converted into a zero.

**Metric identity:** `locomo.harbor.question_micro_mean.v1`. Across conversations,
use `report.py`: it weights every question equally and suppresses the complete-set
aggregate if any selected task is unmeasured/missing. The platform's generic curve
instead averages measured conversation rewards. These differ for unequal question
counts. This port does not claim bitwise identity with every original LoCoMo
inference script or paper score. No study/eval split is declared here.

## Helper loading

The adapter follows ScienceWorld's file-path loading pattern for private helper
files (`importlib.util.spec_from_file_location`). `locomo.py` remains an ordinary
module; no `__path__` override or package-style private imports are needed.
The manual report uses the same loader. The original platform
import-boundary test is unchanged; dataset inventory additions remain.

## Run locally

The platform loader and report aggregation use the standard library. Prepare the
scorer's pinned dependencies in a separate execution runtime:

```sh
python -m tools.prepare_dataset --dataset locomo --runtime execution
python -m tools.prepare_dataset --dataset locomo --runtime execution --check
```

Preparation may download declared packages; `--check` installs nothing. Scoring
uses the admitted runtime interpreter and private verifier inputs, without
installing NLTK into platform Python. See [execution runtime](EXECUTION_RUNTIME.md)
for the dependency and provenance boundary.
Set `HG_DATA_LOCOMO` to the directory containing `raw/locomo10.json`.

```sh
python tools/dataset_lint.py --dataset locomo --json
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --oracle
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --noop
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --oracle --platform
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --noop --platform
LOCOMO_AUDIT_DATA="$HG_DATA_LOCOMO/raw/locomo10.json" python -m pytest -q tests/quality/test_locomo_dataset.py tests/quality/test_locomo_harbor_scoring.py
```

## Results and manual aggregation

The standard driver saves task scores in `curve.jsonl` and detailed verifier
records in `verdicts/round-N/<task-id>.json`. LoCoMo retains each question's score,
category, scoring method and prediction excerpt under `evidence.per_question`.
The platform's `score` averages conversation-task rewards.

To obtain Harbor's question-weighted score, invoke `report.py` separately using
verifier evidence objects and the complete list of selected task IDs. A saved
verdict wraps the evidence under its `evidence` key; extract that object first.
With repeated trials, choose the matching trial's evidence from `trials` and
summarize each trial separately. Do not pass a whole verdict or mix duplicate
conversation records into one report. Missing selected evidence suppresses the
complete-set aggregate.

```sh
python data/datasets/locomo/report.py --task-id locomo/conv-26 --evidence /path/evidence.json --output /path/fresh-report.json
```

This matches the existing dataset-owned manual summary pattern. The standard
batch flow does not call the LoCoMo summary, create `dataset_reports`, or attach
`dataset_report` to curve points.

## Validation

Dataset validation requires a provisioned dataset and an appropriate isolated runtime.
Tests can exercise synthetic scoring and aggregation without model access. A mock or
small control does not establish a full benchmark result, endpoint compatibility, or
production isolation. Preserve any execution receipts separately with their precise
source, scorer, split, harness and model identities.
