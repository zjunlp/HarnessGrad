# τ³ text half-duplex integration

This adapter pins the official τ³ release **v1.0.1**, commit
`fc0055dc4e0a316c3f83133267fbd6faaa770992`, in `sierra-research/tau2-bench`.
It retains all **375 base tasks**: airline 50, retail 114, telecom 114 and
banking_knowledge 97. Banking uses BM25; voice/full-duplex is outside this adapter.
The existing refused `tau2_bench` v0.1 adapter is unchanged.

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
Missing stop signals, provider failures and scorer errors remain unmeasured.
Official max-step/error terminations retain their official zero reward.
NL judge output must contain one strict boolean decision for each requested assertion;
missing, duplicate, foreign or malformed decisions are unmeasured. This validity
gate prevents the upstream empty-result `all([])` case from becoming a success.

## Preparation

Use Python 3.12 for the driver and the isolated dependency runtime. Keep the
following directory outside the repository:

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
A fresh root is required; preparation does not overwrite previous evidence.
No model is called during preparation. `PYTHON_DOTENV_DISABLED=1` prevents upstream
imports from reading ambient credential files.

## Model roles and trials

Configure explicit `HG_ENV_MODEL/BASE_URL/API_KEY` for the simulator and
`HG_JUDGE_MODEL/BASE_URL/API_KEY` for NL scoring. There is no implicit provider/model.
The platform broker fixes temperature 0, at most 4096 output tokens per call and
60 seconds per call; the user has at most 200 calls, and the NL judge one.
This bounded local profile is recorded and does not claim parity with Harbor's
GPT-5.2 reasoning-effort profile. Actor model strategy remains the candidate harness's.

The official base seed 300 generates three trial seeds: **626729, 373753, 361454**.
`trial_protocol` requires `trials=3`, `ks=[1,2,3]`; every trial starts a fresh private
state. `tool_relay.model.seed_source='trial'` binds the same seed to user sampling.
Only complete, binary task × trial results publish `C(s,k)/C(n,k)`; missing or
unmeasured trials never shrink the denominator or become zeros. The ordinary
`run_reward` is a separate task mean, not the reliability metric.

The platform emits `repeated-trials.json`; add task-weighted domain metrics with:

```bash
python tools/tau3_report.py /path/to/repeated-trials.json
```

A report for a selected subset is explicitly labelled; it is not a full-base result.
The default study/eval split is HarnessGrad's hash split, not an official split.

## Controls and verification limits

```bash
python data/datasets/tau3_bench/control.py --oracle --platform
python data/datasets/tau3_bench/control.py --noop --platform
python -m pytest tests/quality/test_tau3_bench_dataset.py \
  tests/quality/test_tau3_bench_official_scorer.py --basetemp=/tmp/tau3-tests
```

The released airline/8 oracle executes real business actions and official scoring.
The negative control starts and stops a conversation without business actions;
it receives official zero. An empty exit without a benchmark stop is tested
separately as unmeasured. Controls use a deterministic local HTTP response through
the official user simulator to validate execution and scoring without inference.
They are not model-performance evidence, a completed 375-task evaluation, or
proof that every released gold trajectory passes the model-based criteria.
A bounded live API control on 2026-10-18 covered the preselected train tasks
`airline/8` and `retail/24`, one trial each, using Ling-3.1-flash for the user and
NL judge. Three requests succeeded (1,409 input / 977 output tokens), with no retries
or cleanup errors. Official scores were 1 and 0; the latter remained a valid
negative because the fixed control did not complete the task's conversation.
This validates `run_one` with real model services. It is not a candidate-harness
pilot through the driver, a full-base evaluation, or a complete pass^k result.
