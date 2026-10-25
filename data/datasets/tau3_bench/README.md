# τ³ text half-duplex integration

This adapter pins the official τ³ release **v1.0.1**, commit
`fc0055dc4e0a316c3f83133267fbd6faaa770992`, in `sierra-research/tau2-bench`.
It retains all **375 base tasks**: airline 50, retail 114, telecom 114 and
banking_knowledge 97. Banking uses BM25; voice/full-duplex is outside this adapter.

The adapter is registered as `tool_use / tool_dialogue` and exercises `context`,
`tools`, `control` and `reliability`. Policies, dialogue and business state require
context selection; business actions require tool calls and decisions based on
user and tool feedback. Reliability coverage comes from the official repeated-trial
`pass^k` protocol described below, not from a single successful dialogue or API call.

The native service runs the official business tools, databases, user-side tools
and `UserSimulator`. The half-duplex bridge derives from Harbor commit
`3e9f14a7c1033896cc600d27de69d6948bcb3410`; see `LICENSE.harbor`.
The actor sees domain policy, business-tool schemas and the `start/say/call/calls/status`
API. Private task scenarios and criteria stay outside its sandbox. There is no
actor reset, configure, seed override or episode upload API.

`VERIFY` replays the platform-sealed episode with the official `EvaluationType.ALL`
scorer and every task's original reward basis. All 113 tasks declaring
`NL_ASSERTION` keep it, including oracle controls. Source code, dependencies and
score scripts are read-only verifier projections; they are unavailable to the actor.
When the candidate exits with a healthy, valid sealed episode but no stop signal,
the adapter maps its explicit `termination_reason: null` to the official
`AGENT_ERROR` termination for scoring. The official early-termination rule returns
zero, which counts as a measured failure in the trial statistics. This mapping is
an adapter policy: the original episode stays unchanged, no message or stop signal
is added, and verifier evidence records the mapping and the incomplete-conversation
reason. Missing or malformed records, provider failures and scorer errors remain
unmeasured; an unfinished episode must explicitly report `failure: null`.
Official max-step/error terminations retain their official zero reward.
Malformed actor transport requests are rejected before entering the runtime and
recorded in `transport.request_errors`. They do not change native step/error counts
or invalidate a later completed episode. Service failures and explicit resource-limit
refusals remain unmeasured.
The first caught runtime exception type is preserved as `failure` in the relay
receipt and private episode; generic deadline/transport labels are fallbacks.
Healthy services report `failure: null`. The receipt remains available after cleanup.
NL judge output must contain one strict boolean decision for each requested assertion;
missing, duplicate, foreign or malformed decisions are unmeasured. This validity
gate prevents the upstream empty-result `all([])` case from becoming a success.

## Preparation

Use Python 3.12 for the isolated dependency runtime and direct runtime tests.
The platform driver may use its own supported Python. Keep the following directory
outside the repository and outside the sandbox's private `/tmp`:

```bash
export HG_TAU3_DATA=/absolute/datasets/tau3_bench
export HG_TAU3_RUNTIME="$HG_TAU3_DATA/runtime"
python3.12 -m venv "$HG_TAU3_RUNTIME"
"$HG_TAU3_RUNTIME/bin/pip" install -r data/datasets/tau3_bench/requirements.lock
"$HG_TAU3_RUNTIME/bin/python" -I data/datasets/tau3_bench/prepare.py \
  --root "$HG_TAU3_DATA" --fetch
```

Alternatively supply `--archive /path/to/source.tar.gz`. Preparation verifies the
archive digest, requires the exact base inventory and writes each case separately.
The runtime bundle omits task sets, gold trajectories and historical model results.
Preparation requires a fresh root.
No model is called during preparation. `PYTHON_DOTENV_DISABLED=1` prevents upstream
imports from reading ambient credential files.

The relay and verifier both launch `HG_TAU3_RUNTIME/bin/python`. Verifier declarations
include read-only `runtime_paths` for that environment and its base Python prefixes,
queried from the selected interpreter once per runtime configuration. Host `PATH`
and the driver's Python do not select the scoring interpreter. The local startup
probe has a 10-second management timeout; it is not an episode or model-call limit.
Missing or non-3.12 runtime installations refuse configuration. At verification,
invalid mounts or runtime failures remain unmeasured; bootstrap checks Python 3.12
before importing pinned dependencies. No system-Python fallback is used.

## Model roles and trials

Configure explicit `HG_ENV_MODEL/BASE_URL/API_KEY` for the simulator and
`HG_JUDGE_BASE_URL/API_KEY` for NL scoring. The judge model is selected from
`HG_TAU3_JUDGE_MODEL` first, then `HG_JUDGE_MODEL`; blank values mean unset.
There is no implicit provider/model, so tasks requiring NL scoring need one of
those model settings. The resolved model is fixed in the verifier receipt;
configuration snapshots include the override.
The platform broker fixes temperature 0; actor model strategy remains the candidate
harness's. Runtime limits are optional management settings, separate from the official
200-step, 10-tool-error episode protocol. The adapter adds no output-token, request-time,
environment-call, relay-call, frame-size or recording-size cap. Actor and verifier phase
timeouts inherit the driver unless explicitly configured. Private transport clients wait
for the broker or enclosing phase to finish.
The official NL evaluator makes one logical judge call for all assertions; optional HTTP
retries are counted separately and do not create additional logical judgments.

Optional limits use `SETUP / ENV / VERIFY`. Retries default to zero. The broker
records declared limits and actual usage.

The official base seed 300 generates three trial seeds: **626729, 373753, 361454**.
`trial_protocol` requires `trials=3`, `ks=[1,2,3]`; every trial starts a fresh private
state. `tool_relay.model.seed_source='trial'` binds the same seed to user sampling.
Only complete, binary task × trial results publish `C(s,k)/C(n,k)`; missing or
unmeasured trials never shrink the denominator or become zeros. The ordinary
`run_reward` is a separate task mean, not the reliability metric.

The adapter also opts into `trial_evidence={"version": 1}`. Every returned trial,
including an unmeasured trial or a successful trial in an incomplete group, is
archived under `<run-dir>/trial-evidence/`. Each JSON keeps the full verdict and
grading evidence, available Harness trace/stdout/stderr, service receipts and
sealed episode references, bound to run/round/harness/task/trial/seed. The trial
report links each archive by relative path and SHA-256. Relay faults still skip
scoring, but available Harness output is collected before cleanup.

This archive is independent of scoring and method feedback. Incomplete groups
still have no formal `pass^k`; no automatic retries are added. A write failure
stops publication, and resume rejects missing or changed archives. Use a run
output directory outside the Harness's visible work root. See the shared
[archive contract](../../../docs/dataset-runtime-interfaces.md#61-可选的逐次完整证据归档)
for supported execution paths and interruption behavior.

The platform emits `repeated-trials.json`; add task-weighted domain metrics with:

```bash
python tools/tau3_report.py /path/to/repeated-trials.json
```

A report for a selected subset is explicitly labelled; it is not a full-base result.
The default (`--split declared`) uses the pinned upstream train/test IDs for
airline, retail and telecom. Upstream `test` maps to the platform's `eval` side.
Banking publishes no train/test division; its 97 tasks use HarnessGrad's
deterministic hash split, approximately 2:1 train/eval. The first byte of the
full task ID's SHA256 digest selects eval when modulo 3 is zero, train otherwise.
This banking division is an adapter policy, not an official upstream split.

| Domain | Train | Eval | Source |
| --- | ---: | ---: | --- |
| airline | 30 | 20 | Upstream train/test |
| retail | 74 | 40 | Upstream train/test |
| telecom | 74 | 40 | Upstream train/test |
| banking_knowledge | 64 | 33 | Platform hash rule |
| Total | 242 | 133 | |

`specs/task-splits.json` preserves the upstream revision, split-file hashes and
train/test IDs. Preparation checks these against the original files.
Every task belongs to exactly one side.

`--split official --side eval` selects all 375 tasks for upstream's complete
`base` reporting protocol. It includes the 242 training tasks and is therefore
not a held-out evaluation after studying the default training side. An explicit
`--split default` requests the platform's hash division instead of this declaration.
Run records retain the selected split identity; results from different splits
must not be combined.

## Controls and tests

```bash
python data/datasets/tau3_bench/control.py --oracle --platform
python data/datasets/tau3_bench/control.py --noop --platform
python -m pytest tests/quality/test_tau3_bench_dataset.py \
  tests/quality/test_tau3_bench_splits.py \
  tests/quality/test_tau3_bench_official_scorer.py --basetemp=/tmp/tau3-tests
```

Set `TAU3_UPSTREAM` to the extracted pinned release to compare the declared split
against the original `split_tasks.json` files.

The released airline/8 oracle executes real business actions and official scoring.
The negative control starts and stops a conversation without business actions;
it receives official zero. An empty exit without a benchmark stop is tested
separately as a measured zero, alongside unmeasured service and record failures.
Controls use a deterministic local HTTP response through
the official user simulator to validate execution and scoring without inference.

Keep live API receipts and trial records outside the repository.

The adapter uses the shared [`record`, `model`, `judge` and `trial_protocol`
interfaces](../../../docs/dataset-runtime-interfaces.md). The service owns official
business APIs and user simulation; the platform owns model credentials, record
sealing and complete-trial reporting.
