# upstream native research datasets

Fixed source: `RUC-NLPIR/upstream@e9c7667945f959d62da27204649a82f94d1b6705`.
`PROVENANCE.json` records unmodified native files and their hashes, including
source-specific prompts, parsers and evaluators. Package initializer projections
are listed as adaptations; no upstream agent, algorithm or runner is imported.
The source archive hash and licenses are recorded beside these files.

## Preparation and source identity

```sh
# A fresh output directory outside the platform. The config is native data
# configuration, not a file containing provider credentials.
python3 tools/prepare_native_research.py hle --config /path/native-config.json \
  --output /path/hle-prepared --judge-model YOUR_DECLARED_MODEL
export HG_HLE_DATA=/path/hle-prepared
```

A local config uses `data_path` (file or native split mapping). Fetching native
HF/GitHub sources requires explicit `--fetch`. The complete source is converted
without `limit` or synthetic hash partitions. A local `data_path` role mapping is
an explicit operator partition, not proof of a native release split; its exact
configuration is retained. Driver task selection remains the platform's job. The manifest binds every private case to
its actual SHA-256 and retains native source-split metadata. `input_origin`
distinguishes a local-declared pool from a pinned-source fetch: a local override
or fork is not authenticated by naming an expected upstream revision. Real-data
provisioning and a separately reviewed official oracle/noop sweep remain gates.

BrowseComp decrypts the pinned official encrypted records. HLE projects only
text-only questions, preserving the prompt and strict response schema. DeepSearchQA
retains native answer-type privacy and set-answer counts/F1. A judge model is
explicit, reflected in the metric identity, and can differ from an official
model; custom OpenAI-compatible profiles do not claim official Gemini parity.

Judge endpoint/keys use the existing `HG_JUDGE_*` configuration. They never enter
actor files/environment. `VERIFY` fixes model, prompt/schema, native token field
and output limit. HLE/BrowseComp keep omitted temperature and
`max_completion_tokens=4096`; LiveMathBench uses its native temperature 0 / 8192,
WideSearch its native 10240. The broker reports finish reason so truncated or
malformed judging is unmeasured. There is no score from actor-supplied verifier
messages, expected labels or user-reported numbers.

Web research uses `python3 web.py call search JSON` / `... fetch JSON`.
These are existing platform provider operations behind the existing private UNIX
tool capability. A native `files/network=none` subject cannot reach a host TCP
web gateway, so it is not given an unusable Docker/bridge address. The selected
provider, keys and SSRF policy stay on the host; tool records are sealed and
provider/runtime failures cannot become normal zero scores.

## Stateful and repeated protocols

- **WorkBench**: native source and initial CSVs are copied into a private world.
  Use native config `_prepared_root` for a separately acquired upstream tree and
  `data_path` for its complete native data directory. No arbitrary source is
  copied to the actor. The actor can `describe/call`; it has no grade/reset API.
  The service records actual actions, seals them after execution, and an independent
  verifier replays them into a fresh native world and calls the unchanged native
  outcome/action scorer. The test inventory binds official source revision
  `49c7dfd00c03d384ec59ea57374f50b766aa5613` and source bytes. Original-source
  release provenance for a production local override needs its own verification.
- **TravelPlanner**: config must include `database_path` with all six official
  database files. The database hash is checked before preparation and scoring.
  Private commonsense/hard constraints and the native converter run after actor
  exit. Unlabelled official test rows are refused. Conversion model/profile is
  explicit; native default is `gpt-4-1106-preview`.
- **WideSearch**: native query/evaluation schema and private gold CSV are retained.
  Install pandas/numpy/dateparser/loguru/datasets and prepare the original gold
  directory. Judge exceptions caught by the upstream evaluator are re-raised as
  measurement failures. Metrics retain the native table definitions.
- **LiveMathBench**: one platform trial is one native solver sample and one judge
  label. The curve is named single-sample-label accuracy. Run the declared number
  of independent trials (native default n=48) and use
  `python3 tools/report_livemathbench.py CURVE --n 48 --k 16` for the unchanged
  hypergeometric G-Pass/mG-Pass formulas. The report refuses missing/invalid labels;
  it never renames HG's pass^k formula as G-Pass.

## Native file and repository execution

Harvey LAB now projects selected public documents, exports only `output/**`, and
uses the official lab-core1.2.0 dual Anthropic/OpenAI file-rubric evaluator and
pandoc through a private host process. Its independent providers use trusted
`HG_JUDGE_HARVEY_*` settings removed from the actor environment. Explicit local or
private provider endpoints are required; public paid defaults are disabled.
See `data/datasets/harvey_lab/README.md` for exact dependencies, source custody,
dual-model identities and native SDK completion checks.

SWE-bench Verified now exposes a selected offline repository through the existing
UNIX tool capability, seals the native patch after actor exit and replays it in a
fresh observed container with the unchanged official SWE parser. Fixed source,
local instance images, baseline sanitization and private test inputs must be
prepared explicitly. See `data/datasets/swe_bench/README.md` for provisioning and
the limits of image provenance and isolation attestation.

Both use fixed `VERIFY.native_benchmark` dispatch rather than a host shell command
chosen by the actor. Private input hashes, selected task/run/trial bindings,
sealed interaction records, and artifact-only export are checked before scoring.
Missing dependencies/data or failed native lifecycle are refused conditionally;
native `grade()` functions that merely consume an actor-authored `*Verifier`
event remain unacceptable substitutes.

See `docs/dataset-reviews/transfer-review.md` for all 20 new entries, aliases,
runtime gaps and the complete control/readiness distinction.
