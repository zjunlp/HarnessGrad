# τ²-bench v0.1.0 — prepared payload and trusted dual-control runtime

## Pin, pool and metric

The source is `sierra-research/tau2-bench`, tag **v0.1.0**, archive SHA256
`ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377`.
Its `tasks.json` pool contains **278 tasks**: airline 50, retail 114, telecom 114.
This adapter declares no train/test split (`SPLIT_SOURCE = "none"`, `SPLIT = None`);
`OFFICIAL_EVAL = None` means the released pool is the scored set, not that every run
measures every task. Later releases change pool selection and upstream warns that
results below 1.0.1 are not comparable with those at or above it. τ³ is separate.
The metric is **`tau2.v0.1.0.ALL.binary`**.

## Privacy, runtime and scoring

`relay.py` runs the pinned release's `UserSimulator` and domain tools in a private
trusted Python 3.13 process. The actor gets `cli/tau2.py`, policy and assistant tool
schemas, not the scenario, gold actions or evaluation criteria. The prepared host
`public.json` also contains `user_tools`; the actor projection strips that field
without changing the digest-pinned prepared file. User tools belong to the simulator,
not the assistant. Released USER actions occur in **94 telecom tasks**, none in the
other domains.

**The official v0.1.0 orchestrator supports multi-tool batches.** Use
`python3 tau2.py calls '[{"id":"a","name":"TOOL","arguments":{}}]'` for an
assistant batch; the complete envelope is validated before mutation. Participant emission and environment execution consume
separate steps, and a boundary can terminate before pending calls execute. The
prepared limits are `max_steps=200`, `max_errors=10`, but upstream `num_errors` is
never incremented in this release: this is **not an enforced ten-tool-error budget**.
The port preserves that behavior and reports observed errors as diagnostic
`tool_error_count`.

The verifier binds the sealed episode to the trusted private task identity and runs
`evaluate_simulation(..., EvaluationType.ALL)`. Source criteria, reward bases and
default bases are never altered to make a control pass. Missing health evidence,
runtime failure, unfinished episodes and malformed judge output are unmeasured,
not ordinary zero scores. Official early-limit termination retains its official
zero and records skipped judge work explicitly.

ALL performs NL evaluation whenever assertions exist, even if the reward basis
excludes `NL_ASSERTION`: **58 tasks** declare a judge (50 airline, 8 retail).
Every performed evaluation requires complete assertion coverage, including exact
multiplicity; missing, duplicate or unexpected results are refused. An empty
`results` list is not valid coverage for a nonempty assertion set. The judge pin is
bound before evaluation because the upstream default is an import-time constant.

## Preparation and readiness

The platform controller uses only the standard library and can run under Python
3.12 from either the source checkout or an installed wheel. It resolves an already
prepared managed execution artifact, then starts its Python 3.13 worker with
`-I -B`. Normal preparation and check-only never install dependencies.

```bash
export HG_DATA_ROOT="$HOME/hg-data"
export HG_DATASET_RUNTIME_ROOT="$HOME/.cache/harnessgrad/dataset-runtimes"
TAU2_ROOT="$HG_DATA_ROOT/tau2-bench"
TAU2_SOURCE="$HG_DATA_ROOT/sources/tau2-bench-0.1.0"

# Separate explicit action when the execution artifact has not been prepared.
python -m tools.prepare_tau2_bench --prepare-runtime

# Read-only inspection; --source is unnecessary.
python -m tools.prepare_tau2_bench --check-only --target "$TAU2_ROOT"

# Fresh generation using the separately pinned extracted source.
python -m tools.prepare_tau2_bench --source "$TAU2_SOURCE" \
    --target "$HG_DATA_ROOT/tau2-bench-fresh"

# Validate existing bytes and prove regeneration; never repair in place.
python -m tools.prepare_tau2_bench --source "$TAU2_SOURCE" \
    --target "$TAU2_ROOT" --reuse-bundle
```

The worker admits the exact role binding and executable before importing provider
code, re-admits after generation/checking, and returns only a management receipt.
The parent re-admits after completion before publishing a staged fresh payload.
Atomic no-replace publication refuses even a concurrent empty target directory.
Failure or package drift leaves the existing payload untouched. Partial/corrupt
targets are refused; provision a fresh target separately. `--runtime`, if supplied,
must name the selected managed artifact; no external-venv fallback is performed.
Model/endpoint credentials and active Python configuration are not inherited by
the worker. Host shared libraries/base stdlib remain compatibility inputs.

The payload contains `manifest.json`, paired `cases/<domain>/<id>/{public,private}.json`,
`bundle/src`, and `bundle/data` without task sets or trajectories. Both case halves
and the bundle have independent expected pins. Fresh generation uses the source-only
bundle receipt `344dd2017a2e273a9378c232784c5bb47642af80298ba68847851a7f33953419`.
The legacy receipt `82546c17152d18f67331e71852ba528ab2a2434dc401cab9949dc007b86be189`
is still validated: it contains the same 107 source/domain/LICENSE file hashes plus
66 historical bytecode-cache entries. Shared receipts are not rewritten; runtime
imports disable bytecode writes and avoid those cached inputs.

Production `provision_status()` uses a check-only subprocess: pinned bytes, Python
3.13 identity/prefix, dependency discovery and installed-version metadata. It does
not import providers, contact models, or establish binary-import/endpoint/model health.

## Deterministic controls and evidence boundary (2026-11-04)

The default three tasks are assistant-only, state-changing, have no communication
phrase requirement, and exclude NL from the reward basis. The corrected deterministic
judge fixture returns **one false result per expected assertion**, not empty
`results` exploiting `all([])`. Its verdicts do not carry the selected controls'
reward and are not a measurement of NL judging quality.

`--dual` instead selects one released telecom task: the official UserSimulator emits
USER `toggle_data`, receives the real tool response and continues; only the oracle
assistant then calls `refuel_data`. USER gold is never injected into the assistant.

```bash
export HG_DATA_TAU2_BENCH_RUNTIME="$TAU2_RUNTIME"
# Evidence directories must be new; omit --dual for the default three tasks.
"$TAU2_RUNTIME/bin/python" -B data/datasets/tau2_bench/control.py \
    --dual --oracle --platform --evidence-dir ./receipts/tau2-dual-oracle-new
"$TAU2_RUNTIME/bin/python" -B data/datasets/tau2_bench/control.py \
    --dual --noop --platform --evidence-dir ./receipts/tau2-dual-noop-new
```

The parent integrated-tree sandbox controls measured oracle/noop **1/0** on the
USER-toggle task and **[1,1,1]/[0,0,0]** on the default three tasks. All eight
executions recorded sandbox enforcement and complete relay cleanup with no fixture
errors. Both airline arms recorded complete **4/4** performed NL assertion coverage,
excluded from the reward basis. The controls used production verifier declarations,
without control-only runtime mount overrides. This is bounded deterministic reachability
evidence, not coverage of all 94 USER-action tasks, model behavior, or H0/pilot authorization.

Focused regression coverage lives in `tests/quality/test_tau2_bench_*.py`: inventory,
preparation, runtime, official scorer, integrity/limits and dual-control tests.
Real-payload checks may explicitly skip when storage/runtime is unavailable; a skip
is not execution evidence.

## Managed execution artifact

Service and scorer declarations now require the separately prepared execution
profile, built at its final path from `execution-requirements.lock`. This is the
existing exact lock with only `tau2==0.0.1` omitted: that package is supplied by
the separately digest-pinned source bundle and is not available from the index:

```bash
python tools/prepare_dataset.py --dataset tau2_bench --runtime execution
```

Preparation is explicit; loading and readiness never install dependencies. Both
trusted launch roles carry the artifact identity and revalidate its bytes before
launch. Discovery is not cached by the adapter. Existing external venvs are not
copied or silently adopted. `HG_DATA_TAU2_BENCH_RUNTIME`, if set, must name the selected
managed artifact; unset the old override when migrating. The legacy `RUNTIME`
constant remains available to direct fixture utilities, but does not select the
production service, scorer or preparation controller. Use the formal
`python -m tools.prepare_tau2_bench` entry point for preparation.

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
