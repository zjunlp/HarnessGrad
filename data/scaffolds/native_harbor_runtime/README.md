# Native Harbor benchmark execution

DeepSWE, JobBench and SWE-bench Pro now declare conditional, runnable routes rather
than unconditional unsupported stubs. Selected SETUP/VERIFY access requires a task/source-bound
runtime manifest, immutable images already in Docker, and a prepared Python 3.12+
interpreter with **harbor==0.23.0**. Measurement does not pull or build images.

The acting candidate runs through ordinary HG `files/network=none` sandboxing.
`native.py describe`, `native.py exec 'COMMAND'`, and `native.py finish` operate on
one persistent native task world behind the private tool relay. The model stays
in the outer harness; neither model/judge credentials nor Docker socket enter the
world. The task prompt explicitly requires finish: it quiesces actions and runs
native collect commands before artifact-only export. SIGTERM cleanup bounds
collect to three seconds; a failed collect is unmeasured, never a zero score.

The sealed relay record is consumed only by the platform's fixed
`native_benchmark` dispatch. Fresh Harbor trials replay the captured committed
patch or deliverable files, preserving original task instructions, collect hooks,
phase/network declarations, separate-verifier behavior and scorer code. SWE-bench
Pro runs the pinned author's PatchReplayAgent and _CAPTURE command; a native trial
must produce an actual reward and `official_evaluation_performed` receipt.

## Explicit preparation

1. Prepare native source with the dataset's `prepare.py`; JobBench additionally
   uses the original pinned renderer's `--render` path.
2. Import the exact actor/replay/verifier images with their native tests and
   dependencies. Separate verifier images must already contain their private
   `/tests`; private tests must **not** appear in the actor image.
3. Explicitly prepare the shared dependency component (this is not a dataset):

```sh
python -m data.scaffolds.native_harbor_runtime.prepare_python prepare --runtime-root /external/component-store
python -m data.scaffolds.native_harbor_runtime.prepare_python check --runtime-root /external/component-store
python -m data.scaffolds.native_harbor_runtime.prepare_python probe --runtime-root /external/component-store
export HG_DATASET_RUNTIME_ROOT=/external/component-store
```

The complete exact lock preserves Harbor 0.23.0 and its transitive dependencies.
Preparation rebuilds at its final managed path; it never copies/adopts a host
venv. Check is read-only. Probe admits the exact binding, imports original Harbor
Trial/config classes under that Python, and re-admits without creating a trial,
contacting a judge/model or using Docker. Missing dependencies do not auto-install
on load or evaluation.

4. Select runtime resources and images with:

```sh
python3 data/scaffolds/native_harbor_runtime/prepare_runtime.py \
  --dataset deepswe --data-root /path/to/native \
  --task-id deepswe/TASK \
  --actor-image sha256:... --replay-actor-image sha256:... \
  --verifier-image sha256:... \
  --manifest /path/to/native/native-runtime.json --workdir /app \
  --workspace-bytes 1073741824 --workspace-inodes 262144 --memory-mb 8192 \
  --build-actor-derivative
```

`HG_DEEPSWE_RUNTIME`, `HG_JOBBENCH_RUNTIME`, or `HG_SWE_BENCH_PRO_RUNTIME` can
select that manifest; otherwise it is `native-runtime.json` under the source
collection root. SWE Pro also requires `--pro-tooling /path/to/v2/tooling` from
its pinned source; both tooling files are checked against known SHA-256 values.
Each selected source task must be represented; missing tasks are not omitted.

Native actor images need Python 3.12 at `/usr/local/bin/python3`. For no-network
worlds, the admitted writable workspace and `/logs` symlink derivative are
created explicitly; `observed.Runtime` proves CPU/memory/PID/network/tmpfs before
an actor command executes. Its image must be free of private `/tests`.

## JobBench public network and judge

JobBench's original actor permits public research. This is preserved by a
separate explicit **native-public** Docker profile, never by silently changing
its network to none and never by weakening observed-v1 globally. Set a nonempty
operator reason in `HG_NATIVE_HARBOR_ALLOW_PUBLIC` to prepare/run this profile.
It records real Docker network/resource/cleanup observations but makes no HG
filesystem quota, egress-closed or production H0 claim. The default remains
unprovisioned until this choice is explicit. No large benchmark or paid judge
is launched by readiness.

Only the host-side verifier reads `HG_JUDGE_HARBOR_BASE_URL` (an explicit local
HTTP loopback provider), `HG_JUDGE_HARBOR_MODEL`, and optional
`HG_JUDGE_HARBOR_API_KEY`. These use the existing HG_JUDGE credential-removal
boundary. Native defaults never fall back to xAI or another paid provider.
A custom local judge is a custom-model measurement and must be reported as such,
not the default official grok-4.3 leaderboard identity.

## Validation and remaining provisioning

The test suite uses tiny local Python images, native separate task definitions,
actual Docker actor/tool export and fresh native Harbor verifier containers. It
checks reward 1/0, actor-private tests absence, complete case/artifact binding, distinct
native verifier containers and confirmed cleanup. No paid model is called.
This proves the execution path, not source-wide image/dependency availability.
The full real datasets/images still need per-task preparation and true oracle/
noop diagnostics before a production H0 or pilot. Native public profiles and
Harbor writable layers are deliberately not mislabeled as quota-attested HG
pristine-offline scoring.

DeepSWE replay refreshes the Git index stat cache after image extraction before
applying the sealed committed diff. A failed replay is unmeasured, never ordinary
zero. The normal full HG sandbox → relay → fresh Harbor verifier controls cover
both the correct committed patch and the unchanged baseline.

Native tool-command timeout aborts the world as unmeasured. In particular, the
original Pro timeout/partial-patch capture path still needs safe actor quiescence
and bounded capture support in this transport; it is not claimed by these controls.


## Harbor dependency identity and migration

Each task entry now requires `harbor_runtime`, an exact canonical verifier-role
binding, and the matching `harbor_python` executable. Old path/version-only entries
are refused with explicit re-preparation instructions; readiness never fabricates
an identity for an existing venv. The optional `--harbor-python` argument may only
name the selected managed artifact, never a different interpreter or host fallback.

The verifier declaration exposes that binding through the existing
`python_runtime`/`runtime_paths` contract. Generic measurement/cache/resume admission
therefore revalidates the artifact instead of trusting a static case digest.
The private inline case carries the same complete binding. Native verification
checks it before artifact handling, immediately before starting the selected child,
inside the child before Harbor imports, and after child completion. Every child
receipt must return the same dependency identity. Harbor source/module bytes,
transitive package files, venv configuration and base executable are inventoried
by the canonical manager; paths and a version string alone are insufficient.

This binding belongs only to the trusted verifier. Actor environment bindings,
public/no-network choices, immutable image selection, private test projection,
collect/replay and judge responsibilities are unchanged. Base stdlib/system
libraries remain compatibility inputs as documented by the canonical manager;
this is not a sealed OS image or benchmark-lifecycle acceptance.


DeepSWE, JobBench and SWE-bench Pro declare the same literal
`MANAGED_EXECUTION_RUNTIME = 'native_harbor'`. Standard execution preparation and
H0 discovery therefore select this component before actor/model work; for example,
`python tools/prepare_dataset.py --dataset deepswe --runtime execution --check`.
The canonical native-dispatch validator still requires the fixed trusted entry,
isolated startup, exact binding and matching runtime paths. It does not permit an
arbitrary host command or an actor-side verifier environment. Old direct specs
without that binding are refused before cache/resume as well as fresh execution.

## JobBench local judge gateway

The native verifier uses the existing `ModelGateway` contract (`split`,
`ModelGateway(...).start()`, `port` and `path`), not a compatibility `publish` or
`url_for` shim. Its listener binds only the IPv4 gateway returned by the selected
Docker bridge metadata; it no longer binds `0.0.0.0` or falls back to unrelated
host interfaces. The upstream remains explicit HTTP on a literal loopback IP.
The original base path is preserved. URL userinfo/query/fragment are refused so
credentials cannot be silently embedded in or dropped from routing metadata.
The verifier's `finally` stops its owned gateway on success, refusal, timeout and
child error. This repair does not add a model-attempt or zero-cost claim: gateway
connections and bytes are transport diagnostics, not judge usage.
