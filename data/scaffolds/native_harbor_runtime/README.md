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
3. Select runtime resources and images with:

```sh
python3 data/scaffolds/native_harbor_runtime/prepare_runtime.py \
  --dataset deepswe --data-root /path/to/native \
  --task-id deepswe/TASK \
  --actor-image sha256:... --replay-actor-image sha256:... \
  --verifier-image sha256:... --harbor-python /path/to/harbor/bin/python \
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
