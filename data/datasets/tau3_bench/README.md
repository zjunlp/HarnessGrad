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
The adapter explicitly enables `model_failure_policy` version 1 with
`invalidate_on_http_status=[429, *range(500, 600)]` independently of evidence archival.
A platform-observed agent-model inference HTTP 429 or any 5xx makes the trial
unmeasured even if the Harness wraps or hides the error, retries successfully,
exits zero or obtains a successful official reward. This is an adapter validity
policy: service outages introduce interference into this task evaluation.
It does not assert that the outage caused the Harness to terminate. A confirmed
response status remains evidence if its body or another connection is incomplete;
an error string alone or a non-inference response does not qualify.
The original official reward, sealed episode and available Harness output remain
in the existing evidence archive; the invalid trial has no formal score and its
`pass^k` group remains incomplete. Other 4xx, including request/context errors, retain
existing rules. Unrecognized traffic without a confirmed status does not establish
an HTTP service fault. The policy is part of the resume configuration; resuming
requires the same policy.
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

Use the standard-library platform controller from either the checkout or an
installed wheel. It resolves an existing managed Python 3.12 execution artifact;
normal generation and read-only checking never install dependencies.

```bash
export HG_DATA_TAU3_BENCH=/absolute/datasets/tau3_bench
export HG_DATASET_RUNTIME_ROOT=/absolute/cache/dataset-runtimes

# Separate explicit installation action, only when needed.
python -m tools.prepare_tau3_bench --prepare-runtime

# Fresh generation; --archive can replace the explicit source download.
python -m tools.prepare_tau3_bench --root "$HG_DATA_TAU3_BENCH" --fetch
python -m tools.prepare_tau3_bench --root "$HG_DATA_TAU3_BENCH" --check-only
```

The selected interpreter runs an isolated bytecode-free worker. Before provider
imports, the worker admits the exact binding and `sys.executable`; worker and
parent re-admit after completion. A fresh payload is generated in sibling staging
storage and atomically published only after those checks. No-replace publication
refuses even a concurrent empty target. `--reuse-bundle` only validates an existing
payload and never repairs/replaces it. `--runtime`, if supplied, must name the
selected managed artifact. No external-venv or platform-Python fallback is used.

The pinned archive digest, native split validation, bundle digest and final task
manifest/case digests remain authoritative. Prepared bundles omit task sets,
gold trajectories and historical model results. Check-only reads existing bytes
without starting a model or task episode. Worker environments exclude model/API
credentials, active venv/PYTHONPATH and provider configuration; dotenv is disabled.
Host libraries and base stdlib remain compatibility inputs, not a sealed OS.

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
output directory outside the Harness's visible work root and runtime/input mounts.
The final ordered sandbox mounts are checked before every actor launch, including
aliases and projections that could expose the archive at another path. See the shared
[archive contract](../../../docs/dataset-runtime-interfaces.md#62-可选的逐次完整证据归档)
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

## Managed execution artifact

Service and scorer declarations now require the separately prepared execution
profile, built at its final path from the existing exact `requirements.lock`:

```bash
python tools/prepare_dataset.py --dataset tau3_bench --runtime execution
```

Preparation is explicit; loading and readiness never install dependencies. Both
trusted launch roles carry the artifact identity and revalidate its bytes before
launch. Discovery is not cached by the adapter. Existing external venvs are not
copied or silently adopted. `HG_DATA_TAU3_BENCH_RUNTIME`, if set, must name the selected
managed artifact; unset the old override when migrating. The legacy `RUNTIME`
constant remains available to direct fixture utilities, but does not select the
production service, scorer or preparation controller. Use the formal
`python -m tools.prepare_tau3_bench` entry point for preparation.

The artifact covers the venv and base executable. Host shared libraries and the
base standard library are compatibility inputs, not an immutable operating system.
Pinned bundle source and private/public case digests remain separate checks;
runtime identity contains no private task or answer bytes. Readiness does not
establish model endpoint health or an episode score.


Preparation source admission also commits the finite set of owned controller,
prepare, adapter and bootstrap/runtime helper `.py` files. Those helpers are
compiled directly from their source bytes at explicit owned paths; timestamp-valid
`.pyc` files and import-cache/cwd helper substitutions are not used. The worker
checks that source identity before/after loading and execution, and the parent
checks it again before publication. Upstream bundle/source/task pins are unchanged.
Source drift fails closed and retains the sibling staging directory with the
expected source inventory, source snapshots and minimal worker-attempt metadata;
an existing payload and its provenance are never rewritten by check-only.

Each dependency/provider worker uses a temporary private HOME, XDG directories,
TMP and cwd. Relative CLI source/target/archive arguments are normalized against
the controller's original cwd before launch. Explicit source fetching stays in
the standard-library controller with its existing authentication/proxy settings;
workers receive the archive path and still enforce the original archive digest.


Once a fresh preparation attempt has been created, every failure preserves its
private staging directory: source snapshots, partial payload, worker attempt and
raw stdout/stderr diagnostics. `failed.json` contains only the exception type and
phase; the public error gives that safe summary and the private stage path, never
raw task/error text. Only successful publication removes staging. A late target
collision leaves the concurrent target and all historical receipts unchanged.
Reuse and read-only checks create no attempt/output directories.
