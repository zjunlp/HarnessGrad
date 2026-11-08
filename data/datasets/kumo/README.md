# KUMO native adapter

Coverage: **5,290 of 5,300** released standard cases (5,040 easy, 250 hard).
Source: `linhaowei1/kumo@576748764f7d72634654d0784944a485eca9047c`.
The data root is `<HG_DATA_ROOT>/kumo`; `HG_DATA_KUMO` can override it.
Set `HG_DATA_ROOT` or `HG_DATA_KUMO` before accessing data; no directory is selected implicitly.
See [dataset storage](../../../README.md#dataset-storage) for `.env` configuration.
Data, private answers, books and audit artifacts stay outside this repository.

The adapter is registered as `reasoning / planning_and_search` and exercises
`context`, `tools`, `control` and `verification`. The harness organizes the
knowledge book and accumulated observations, chooses queries and uses their
results to decide what to query next or when to submit. Public rules and observed
outcomes let it check a candidate for contradictions before submission. This
candidate-consistency check supports `verification` coverage; the presence of
the platform's final scorer alone does not.

## Provisioning

```sh
python3 -m tools.prepare_kumo --fetch
python3 tools/datasets.py --check --dataset kumo
python3 tools/dataset_lint.py --dataset kumo
```

The module entrypoint works from an installed distribution. The existing checkout
entrypoint `python3 data/datasets/kumo/prepare.py` remains supported.

`--fetch` uses a pinned sparse Git checkout, then audits every candidate twice and
creates `prepared-v1`. With an existing checkout, omit `--fetch`. Preparation refuses
to overwrite an existing prepared directory. Source blobs are checked against Git;
public/private files are bound to a pinned manifest. The adapter verifies public
bytes; the KUMO relay and verifier each check the selected private case.
`source-files.json` also supports the generic downloader's per-file SHA256 checks.
No extra Python dependencies or model calls are needed for preparation/scoring.

## Metric and protocol

The metric is `kumo.official.consistency_accuracy.v1`: upstream
`env/engine.py::Environment.evaluate_answer` is vendored verbatim with its license.
It accepts a candidate iff its rules are consistent with **all** released observations,
including inclusive numeric intervals. No judge model is used.

The pinned standard suite contains 5,255 cases with one accepted candidate,
35 with multiple accepted candidates, and 10 with none. The adapter retains
the 35 ambiguous cases under the official predicate. The 10 excluded easy cases are:

- FungalEnv/36
- MythicalCreatureEnv/9, /14
- QuantumEnv/3, /15, /36
- SorcererEnv/0, /5, /7, /40

Exclusion is recomputed from the official predicate, not the handwritten list.
Per-row evidence and source digests are in `official-audit.json`.
MedicalOOD's easy cases are included. Its extra hard scenario is outside the
standard 5,300-case suite: the source file is truncated and lacks corresponding books.

Protocol `kumo.query50.v1` follows Harbor's limit of **50 accepted action queries**,
including repeated actions. Invalid actions do not spend that budget. This differs
from upstream VanillaAgent's limit of 50 model turns; scores are not directly
comparable across those protocols. The adapter declares no extra transport
call/frame/deadline cap. Explicit limits in `SETUP.tool_relay` remain enforced;
exhaustion is an unmeasured transport failure. Resource policy is separate from
`kumo.query50.v1`: `ENV` declares Harbor's 900-second actor and 120-second verifier
deadlines. Local `show` and `submit` commands do not consume service calls.
The adapter declares a deterministic hash split using the platform rule (3,515 train / 1,775 eval). This is an adapter policy,
not an official upstream split; `OFFICIAL_EVAL=None`.

## Execution and isolation

The actor gets the selected public book, candidate/action lists and `kumo.py`:
`show`, `do`, `stats`, `submit`. A private host-side Unix relay answers actions.
Public materials omit the upstream VanillaAgent system prompt. The harness
chooses its prompts, query order, batching, reasoning and completion procedure.
The verifier reads only the final `answer.txt`, which must contain one candidate
verbatim. `submit` is an optional file-writing helper; it does not terminate the
harness or prevent the harness from subsequently overwriting the answer.
A lock preserves the episode query budget across reconnects. No private answer,
rules or unqueried observation table is mounted in the actor sandbox.
The relay reads only the selected private case.

After actor exit, the command verifier scores the final answer against the
selected private case using the official predicate. The adapter enables neither
`tool_relay.record` nor `tool_relay.model`; scoring does not read interaction logs.
Relay startup/transport failures are unmeasured. The runner stops the relay process
group, removes its staging directory and records transport and cleanup results in
`runtime_services.tool_relay`. See the
[shared runtime interface](../../../docs/dataset-runtime-interfaces.md).

Run with the native filesystem sandbox (the default); privacy is not promised
for an explicitly unsandboxed run. Network is inherited to support a configured
HTTP or HTTPS model endpoint. Egress restrictions, filesystem quotas and prevention
of online answer lookup are not enforced by this adapter.

## Validation

```sh
python3 data/datasets/kumo/control.py --oracle
python3 data/datasets/kumo/control.py --noop
python3 data/datasets/kumo/control.py --oracle --platform
python3 data/datasets/kumo/control.py --noop --platform
python3 tools/run_tests.py --dataset kumo --with-registry --jobs 8
```

Controls use the mock backend and require scores 1/0. Tests cover vendored scorer
identity/differential behavior, full provisioned audit, cross-connection budgets,
private data isolation, scoring independent of forged recordings, and source mismatch
rejection. Shared runtime controls separately cover ordinary relay lifecycle,
cleanup receipts and adapters that do not request interaction-record sealing.

The platform oracle uses `oracle_harness/` to make one relay query and submit the
released answer through `kumo.py`. Without `--platform`, controls run the relay
and verifier directly through the command-control helpers.

Real model runs use the platform runner/driver and base harness. Keep API receipts
and diagnostic artifacts outside the repository.

## Dependency and resource boundary

The prepared execution route has no third-party Python dependency: loading uses
`hashlib/json/pathlib`, the public `kumo.py` client uses the shared stdlib socket
transport, the host relay loads the selected authenticated private case, and the
isolated verifier reconstructs rules for the verbatim pinned `vendor/engine.py`.
There is no environment model, judge model, endpoint or credential requirement.
The original upstream agents/LLM clients are not this adapter's actor or scorer.
Native platform runs still require Linux bubblewrap, the platform's supported
Python and an admissible interpreter location outside hidden sandbox trees.
Control scripts preserve the invoking virtual environment in `PATH` for the
actor's Python commands. A package-free virtual environment is a validation
fixture, not a new required dataset runtime.

Preparation is a separate source boundary: `--fetch` needs Git, HTTPS and the
pinned upstream repository. Checking an existing Git checkout also invokes Git;
the downloaded hash-inventory layout checks all source bytes without Git.
`prepare.py` imports upstream `env.load_env` and audits source environments;
the bundled stdlib engine alone does **not** establish closure of those upstream
imports. In the November 2026 offline audit, attempts to retrieve the fixed source
from raw.githubusercontent.com failed at TLS, so complete pinned-source
preparation was **not measured**. The original revision, source inventory,
manifest digest, scorer and license remain unchanged.

`test_stdlib_venv_platform_controls` uses authored prepared cases and an empty
`venv --without-pip` to run the real bubblewrap runner, actor CLI, private relay
and verifier: oracle = 1, noop = 0. KUMO scores an empty answer as an incorrect
answer. Other dedicated tests cover private/public digest changes, private actor
mount isolation, query budgets and relay cleanup. The authored case replaces the
release roster only in that test; it does not validate the full 5,290-task pool,
source preparation, or a model-based harness. Oracle answer material is an
explicit control-only actor input; ordinary setup never stages it.

Each `load()` and readiness check clears the manifest/roster caches once before
reading the current manifest and checking its original pinned SHA256. A changed
manifest is refused even in an already-running console process. Factories then
reuse the validated selection snapshot, with public/private case bytes checked
against its digests when selected. Replacing prepared files during an active run
remains unsupported; a subsequent selection authenticates current bytes. Storage
configuration resolved at adapter import remains fixed for that process, so
changing the configured root still requires restarting the host.

### Selected-engine failure audit

The additional authored-case checks in `test_kumo_dataset.py` distinguish
three boundaries beyond ordinary oracle/noop execution. Missing selected private
state prevents the real relay from starting and removes its temporary state.
After an actor has queried the live relay and written the correct candidate,
killing the test-owned relay process at shutdown invalidates the run and cleans
up its directory; the answer cannot restore a measured result. Direct isolated
`python -I -S` execution of the actual projected scorer scores correct/wrong
candidates as 1/0, then removes a deliberately stale reward when private state is
missing and exits 3 with unmeasured error evidence.

The actor also attempts to replace the checker, scoring adapter, vendored engine
and private input with local forgeries; a wrong answer still scores 0 against the
authenticated source. This adapter restores verifier inputs after the actor exits;
it does not declare the scorer's own copied files as immutable mounts. Host relay
state stays outside the actor mount, and only requested observations cross the
stdlib socket transport. The execution import path is `Game` plus shared
`Relay`/socket transport, then `scoring.environment` plus the pinned `Environment`;
these use stdlib modules and local platform code, with no judge/provider imports.
Native isolation still relies on bubblewrap, the OS and the selected Python
installation. These selected synthetic checks do not close the unmeasured
upstream `env.load_env` preparation dependency boundary described above.
