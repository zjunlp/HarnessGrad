# LoCoMo native adapter (Harbor protocol)

This port preserves the task text, transcript rendering and scoring of Harbor
`3e9f14a7c1033896cc600d27de69d6948bcb3410`. It uses the official `locomo10.json`
from `snap-research/locomo@3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`;
`specs/source.json` pins source bytes and the Apache-2.0 Harbor reference files.
The reference license is in `tests/fixtures/locomo_harbor/LICENSE`.

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
The manual report uses the same loader. NLTK is loaded lazily for token scoring;
the aggregation-only report uses the standard library and does not need the
execution runtime or NLTK in the platform environment.

## Run locally

Use the platform Python environment from the root README. Set `HG_DATA_LOCOMO`
to the directory that will contain `raw/locomo10.json`, then fetch the data and
explicitly prepare the scorer's separate CPython 3.12 environment:

```sh
python tools/datasets.py --fetch --dataset locomo
python tools/prepare_dataset.py --dataset locomo --runtime execution
python tools/prepare_dataset.py --dataset locomo --runtime execution --check --json
python tools/dataset_lint.py --dataset locomo --json
```

`requirements-score.txt` records the direct NLTK dependency; `requirements.lock`
pins all execution dependencies, including `nltk==3.10.4`. Preparation uses uv
and publishes a verified artifact outside the checkout. `--runtime-root` or
`HG_DATASET_RUNTIME_ROOT` selects a different external store; use the same store
for preparation and execution. The platform does not need NLTK or a corpus.
The historical reference metadata in `specs/source.json` is retained unchanged;
the current dependency identity comes from the execution declaration and lock.

Loading and input rendering remain standard-library operations. Fetching only
downloads the fixed JSON; it does not install dependencies. The execution
`--check` command verifies only the artifact, without reading data or starting a
scorer. Dataset readiness checks both the fixed data and the execution artifact.

The selected verifier binds the exact prepared interpreter and runtime identity,
uses `-I -B`, and rechecks the artifact before launch. Missing or altered artifacts
fail without falling back to PATH or the platform Python. The native `files`
sandbox mounts the runtime read-only; the verifier evidence records the actual
Python, prefix, NLTK version and import origins, and the verdict records the
runtime binding. Actor inputs contain neither the scorer nor private gold.
The verifier loads only the exact rematerialized scorer file, without adding a
task-writable directory to Python's package search path.

The following controls inspect a selected released task. Local controls use the
same bound Python
and check its artifact, but provide no sandbox isolation. `--platform` exercises
the full runner and read-only verifier boundary. No model is needed for controls.

```sh
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --oracle
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --noop
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --oracle --platform
HG_AGENT_BACKEND=mock python data/datasets/locomo/control.py --task-id locomo/conv-26 --noop --platform
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

This command needs the fixed source JSON to check task/question coverage, but
does not rescore answers or import NLTK. It can run with the platform Python.

```sh
python data/datasets/locomo/report.py --task-id locomo/conv-26 --evidence /path/evidence.json --output /path/fresh-report.json
```

This matches the existing dataset-owned manual summary pattern. The standard
batch flow does not call the LoCoMo summary, create `dataset_reports`, or attach
`dataset_report` to curve points.

## Validation

Dataset validation requires a provisioned dataset and an appropriate isolated runtime.
Synthetic tests do not read the released data or call a model. Explicitly prepare
a disposable test runtime first, set `HG_TEST_LOCOMO_EXECUTION_RUNTIME_ROOT` to
its store, then run the native controls and the Harbor differential with a separate test interpreter containing
pytest and the locked NLTK dependencies:

```sh
HG_TEST_LOCOMO_EXECUTION_RUNTIME_ROOT=/path/to/dataset-runtimes python -m pytest -q tests/quality/test_locomo_dataset.py tests/quality/test_locomo_execution_runtime.py tests/quality/test_locomo_harbor_scoring.py
```

Tests never install dependencies. Runtime controls skip if that explicit store is
absent. The full released-data differential additionally requires explicit
`LOCOMO_AUDIT_DATA` pointing at the pinned JSON.
A mock or small control does not establish a full benchmark result, endpoint
compatibility, or production isolation. Preserve execution receipts separately
with their precise source, scorer, split, harness and model identities.
