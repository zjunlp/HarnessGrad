# KUMO native adapter

Coverage: **5,290 of 5,300** released standard cases (5,040 easy, 250 hard).
Source: `linhaowei1/kumo@576748764f7d72634654d0784944a485eca9047c`.
Downloaded sparse source: 5,846 files, 127,892,007 bytes; no historical model results.
The default data root is the workspace sibling `datasets/kumo`; override with `HG_KUMO_DATA`.
Data, private answers, books and audit artifacts stay outside this repository.

## Provisioning

```sh
python3 data/datasets/kumo/prepare.py --fetch
python3 tools/datasets.py --check --dataset kumo
python3 tools/dataset_lint.py --dataset kumo
```

`--fetch` uses a pinned sparse Git checkout, then audits every candidate twice and
creates `prepared-v1`. With an existing checkout, omit `--fetch`. Preparation refuses
to overwrite an existing prepared directory. Source blobs are checked against Git;
public/private files are bound to a pinned manifest. The adapter verifies public
bytes; the KUMO relay and verifier each check the selected private case.
`source-files.json` also supports the generic downloader's per-file SHA256 checks.
No extra Python dependencies or model calls are needed for preparation/scoring.
Older prepared materials include the VanillaAgent prompt. Preserve the old
`prepared-v1` directory and rerun preparation to obtain the current public files
and manifest; private cases, task selection and scoring remain unchanged.

## Metric and protocol

The metric is `kumo.official.consistency_accuracy.v1`: upstream
`env/engine.py::Environment.evaluate_answer` is vendored verbatim with its license.
It accepts a candidate iff its rules are consistent with **all** released observations,
including inclusive numeric intervals. No judge model is used.

This differs from Harbor's exact gold-string score. The full repeated audit found
5,255 uniquely accepted golds, 35 cases with multiple accepted candidates, and 10
cases for which no candidate is consistent. The 35 ambiguous cases are retained
with the official predicate. The 10 excluded easy cases are:

- FungalEnv/36
- MythicalCreatureEnv/9, /14
- QuantumEnv/3, /15, /36
- SorcererEnv/0, /5, /7, /40

Exclusion is recomputed from the official predicate, not the handwritten list.
Per-row evidence and source digests are in `official-audit.json`.
MedicalOOD is outside the standard 5,300-case suite (its extra file is truncated
and lacks corresponding books).

Protocol `kumo.query50.v1` follows Harbor's limit of **50 accepted action queries**,
including repeated actions. Invalid actions do not spend that budget. This differs
from upstream VanillaAgent's limit of 50 model turns; scores are not directly
comparable across those protocols. The adapter declares no extra transport
call/frame/deadline cap. Explicit limits in `SETUP.tool_relay` remain enforced;
exhaustion is an unmeasured transport failure.
Local `show` and `submit` commands
do not consume service calls. The adapter declares a deterministic hash split
using the existing platform rule (3,515 train / 1,775 eval). This is an adapter policy,
not an official upstream split; `OFFICIAL_EVAL` remains unset. The diagnostic selects
only train. Other datasets' split handling is unchanged.

## Execution and isolation

The actor gets the selected public book, candidate/action lists and `kumo.py`:
`show`, `do`, `stats`, `submit`. A private host-side Unix relay answers actions.
Public materials omit the upstream VanillaAgent system prompt. The harness
chooses its prompts, query order, batching, reasoning and completion procedure.
The verifier reads only the final `answer.txt`, which must contain one candidate
verbatim. `submit` is an optional file-writing helper; it does not terminate the
harness or prevent the harness from subsequently overwriting the answer.
The shared transport limits total calls, and a lock preserves the episode budget
across reconnects. No private answer, rules or unqueried observation table is mounted
in the actor sandbox. Only the selected private case is read by the relay.

The relay enforces the 50-query budget in host memory. After actor exit, the
standard command verifier scores only the final answer against the pinned private
case using the official predicate. It neither consumes nor trusts actor-produced
query recordings. No trusted episode is exported through a new runner field.
Relay startup/transport failures use the shared unmeasured path. The current shared
runner stops the relay process group, removes its staging directory and returns
transport and cleanup evidence in `runtime_services.tool_relay`. Cleanup errors
are recorded. KUMO does not enable the optional `record` or `model` declarations;
answer scoring and the query budget remain independent of sealed interaction records.
See [the shared runtime interface](../../../docs/dataset-runtime-interfaces.md).

Run with the native filesystem sandbox (the default); privacy is not promised
for an explicitly unsandboxed run. Network is inherited to support a configured
HTTP or HTTPS model endpoint. Egress restrictions, filesystem quotas and prevention
of online answer lookup are not claimed. This is an integration adapter, not a
production benchmark admission.

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

The control entry point and its scripted `oracle_harness/` live beside the adapter's
CLI and verifier, following ALFWorld/Jericho. The platform oracle makes one real
relay query and submits the staged released answer through `kumo.py`; no shared
oracle source is rewritten. The local arm reuses the shared command-control helpers.

There is no KUMO-specific API diagnostic command. Real model runs use the existing
platform runner/driver and base harness. Historical diagnostic artifacts are retained
outside the repository; they are integration evidence, not aggregate benchmark results.

## Recorded real API checks

Two user-authorized checks on the preselected train task `CookingEnv/42` are
retained outside Git. The 2026-10-17 check at `b8899205` completed with score 0,
12 model calls and three action queries. After the dataset-local controls revision,
the 2026-10-18 `Ling-3.1-flash` check submitted `Stewing`, received official score 1,
and completed seven model calls and six queries. The earlier incomplete attempt
is retained separately. These are historical single-task integration checks under
their recorded revisions, not aggregate benchmark results or fresh runs of the
current shared lifecycle implementation.

KUMO and the shared runtime interfaces are now merged upstream. The temporary
shared-runtime rollback used during the earlier review is no longer the current
implementation. Control entry points remain dataset-local; no KUMO-specific API
diagnostic tool or repository-local validation logs are required.

Resource policy is separate from `kumo.query50.v1`: the task inherits Harbor's
900-second actor and 120-second verifier deadlines in `ENV`. The former 600/30-second deadlines and 256-call ceiling are removed.
Private case isolation, accepted-query counting and final-answer scoring are unchanged.
