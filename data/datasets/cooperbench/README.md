# CooperBench fixed-peer integration

Each candidate harness runs twice, in independent observed Docker workspaces,
with one platform-assigned released feature per peer and a platform mailbox. The only submitted
artifact is `agent.patch`. Both patches are merged in a fresh verifier; no solver
sidecar, Redis server, model-specific agent or reference patch is part of SETUP.

The metric is **Harbor merged-pair success**, pinned to Harbor
`3e9f14a7c1033896cc600d27de69d6948bcb3410`: filter test diffs, merge normally then
with union fallback, and require both feature runners to exit zero. Data is pinned to CooperBench
`63b9d44d9f39a02fccf5bf0052db48a917a011fd` (199 features, 30 base tasks, 652 pairs).
Harbor's applicable task fixes are applied by its pinned adapter during
preparation, including the oracle-only combined-patch corrections. DSPy task8563
uses the released CooperBench test patches unchanged: feature 1 expects the dict
return format, and features 2–6 already normalize both formats before checking
the payload. Harbor's redundant testfix is not appended to these patches.
React Hook Form task85's reference patch declares the resolved-defaults type
explicitly and lists the external-loading value in its effect dependencies.
Its type/lint commit hooks, feature tests and candidate scoring remain unchanged.

The train/eval split hashes the **base task**, so related pairs never cross the
split. It is an adapter split, not an official heldout designation; the official
flash subset is development material. All pair IDs remain in the inventory even
when a machine has imported only one runtime. A missing runtime fails explicitly.

Prepared inputs must match scorer manifest `64069b7c5dd126b999070a9e4462d5af82e9752a686e8c59a104d59c7a78d84b`.
If a prepared root has a different manifest, prepare a fresh root. Preserve its
inputs and receipts outside the repository.

## Preparation

Use Python 3.12 and external directories. `HG_COOPERBENCH_DATA` overrides the
repository-adjacent `datasets/cooperbench` default. Clone the two sources, check
out the exact commits above, and keep their relevant tracked and untracked trees
clean. The source archive URL and hash are also declared by `downloads()`.

```sh
export HG_COOPERBENCH_DATA=/your/data/cooperbench
python data/datasets/cooperbench/prepare.py \
  --root "$HG_COOPERBENCH_DATA" --source-root /your/sources/CooperBench \
  --harbor-root /your/sources/harbor
python data/datasets/cooperbench/prepare.py \
  --root "$HG_COOPERBENCH_DATA" --source-root /your/sources/CooperBench \
  --image-group pallets_click_task/task2956 --pull \
  --base-runtime /your/selected-resource-runtime
```

Select workspace byte/inode and memory budgets explicitly before image preparation.
`--base-runtime` reuses the `resources` from a chosen runtime directory; without
mirror assets it reuses only those allocations, not its image or dependencies.
Alternatively, pass `--resources /path/to/resources.json` with a complete JSON
object containing `workspace_bytes`, `workspace_inodes` and `memory_mb`;
`cpus` and `pids` are optional. Workspace bytes must be page aligned. Choose values
for the workload and available machine resources. If both options are provided,
the JSON file selects the entire allocation; fields are not merged with the base.
Missing required resources fail before Docker preparation. Preparation records
the selected file hash and resource values, measures seed usage, and refuses a
seed that cannot fit. Image builds add no CPU/memory throttle.

Image preparation is explicit and per group; it never pulls the full image
collection. It verifies the repository base SHA and official runner bytes,
records the imported image ID, registry digests, complete `pip freeze`, build
recipe and transformed runner hashes, and creates a local immutable image ID.
If the task image's Python lacks `tarfile.data_filter`, import a compatible
Python 3.12 image explicitly and pass `--runtime-python-image sha256:...`.
Preparation copies its runtime to `/opt/hg-python`, checks safe extraction in the
final image, and records the auxiliary image ID, executable, prefix and library
path. The official language toolchain is retained. For Debian 12 task images,
Python `3.12.13-slim-bookworm` is the compatible preparation source; use an exact
local image ID after importing and verifying the registry manifest.

Preparation recipes cover all **30 groups**, with pinned runner hashes and exact
installation ranges in `recipes.json`:

| Family | Groups | Preparation and offline execution |
| --- | ---: | --- |
| Python (Outlines, DSPy, datasets, LlamaIndex, tiktoken, Click, Jinja, Pillow, dirty-equals) | 24 | Resolve the official dependency block and build backends during preparation. After each submitted patch, reinstall editable code inside a writable venv with `--no-index --no-deps --no-build-isolation`; this preserves native-extension rebuilds and entry-point updates. |
| Go chi | 3 | Preserve `go test`; copy module cache into the bounded workspace and disable module network resolution. |
| React Hook Form | 2 | Preserve cached node_modules and the test command; preinstall pnpm 8 so the runner uses it directly without npx downloading it. |
| Typst/Rust | 1 | Preserve Cargo build/test; relocate writable registry/git caches, retain compiled target output, and set offline Cargo mode. |

For tiktoken, preparation loads every public baseline encoding through its own
hash-checking loader. `TIKTOKEN_CACHE_DIR` points both workers and the verifier
to that image-bound cache inside the workspace quota.

For Outlines task1706, its public model loaders need Hub metadata as well as
weights. The [fixed HF dependency mirror](hf_mirror/README.md) supplies both from
verified immutable assets over container-local HTTP. Prepare those assets with
`python data/datasets/cooperbench/hf_mirror/prepare.py --root /your/hf-assets`,
then derive a runtime into a fresh data root containing the same prepared inputs:

```sh
python data/datasets/cooperbench/prepare.py \
  --root /your/fresh/cooperbench --source-root /your/sources/CooperBench \
  --image-group dottxt_ai_outlines_task/task1706 \
  --base-runtime /your/base/cooperbench/runtimes/dottxt_ai_outlines_task-task1706 \
  --hf-mirror-assets /your/hf-assets
```

For LlamaIndex task18813, the [fixed HTTPS asset mirror](https_asset_mirror/README.md)
supplies the original W3C PDF bytes through a local TLS proxy. The unmodified
clients retain certificate and hostname verification against a dedicated local CA;
this reproduces the resource, not W3C's public certificate chain. Prepare assets
with `python data/datasets/cooperbench/https_asset_mirror/prepare.py --root /your/w3c-assets`,
then use the same derivation command with `--image-group llama_index_task/task18813`,
its prepared base runtime, and `--https-asset-mirror-assets /your/w3c-assets` in
place of the HF option. Only the exact fixed public URL is served; the service
cannot forward requests or access the internet.

Derivation preserves the base image, dependency freeze and preparation identity;
it adds a read-only mirror layer and selects its isolated interpreter wrapper.
The known public cache payload must fit the selected workspace allocation;
cache metadata and compilation headroom must also be budgeted by the operator. Scoring requires
matching mirror health before and after the official runner and a zero verifier
exit status; service failure stays unmeasured even if a reward file remains.

The official reset, patch application, test commands and cleanup remain intact.
`/patches` and Harbor's other writable mounts move under `/workspace` to retain
the platform's filesystem limits. Native compilation scratch uses
`/workspace/.test-tmp` within this quota. `--build-network host` is available
**only for preparation**, for hosts whose download proxy listens on loopback; measurement
always has no network. A stale published image whose runner/base does not match
is refused. Rebuild the pinned task Dockerfile and supply its explicit local ID
with `--source-image` in that case; the prepared identity remains recorded.

Runtime allocations come from the prepared metadata. PIDs are uncapped unless set
in metadata. Harbor's pinned task template supplies the default
2 CPUs; the adapter declares 1800-second worker and verifier deadlines.
Per-project test commands retain their upstream timeouts.
DSPy task8587 runs its local LiteLLM fixture with `NUM_WORKERS=1`, the server's
native worker-count setting. One asynchronous server process avoids redundant
worker imports under the allocated CPUs; the official 10-second readiness check,
test cases and 300-second runner timeout remain intact.

The adapter explicitly sets mailbox limits, `artifact_max_bytes`,
`model_route` limits and verifier `result_max_bytes` to null. Optional finite
bounds can be declared through the [shared runtime interfaces](../../../docs/dataset-runtime-interfaces.md).
`/tmp`, `/dev` and `/dev/shm` each use the selected workspace byte/inode
allocation; total tmpfs usage also remains subject to the selected memory limit.
Network isolation, observed resource enforcement, sender/peer binding, artifact identity
checks and mirror lifecycle health checks remain mandatory. Mirror readiness and
HTTP health timeouts are service lifecycle checks, not additional scoring budgets.
The public HF asset preparation tool accepts optional `--max-total-bytes`; it
has no implicit total download ceiling. Exact source sizes/hashes, metadata
framing checks and finite transport recovery remain enforced.

`provision_status()` reports prepared and unprepared groups without dropping
any of the 652 pair identities.

## Controls and tests

Run oracle/noop controls for the selected prepared image and task pair. Tests
cover recipe source drift and shell syntax, patch filtering, scoring and worker
isolation. Controls exercise the selected environment and scorer; they do not
measure candidate coding quality. Keep control receipts and API diagnostics
outside the repository.

```sh
python data/datasets/cooperbench/control.py --oracle --platform \
  --task-id cooperbench/pallets_click_task/task2956/f1-2 --output /your/results/oracle.json
python data/datasets/cooperbench/control.py --noop --platform \
  --task-id cooperbench/pallets_click_task/task2956/f1-2 --output /your/results/noop.json
python -m pytest tests/quality/test_cooperbench_dataset.py \
  tests/quality/test_cooperbench_harbor_scoring.py --basetemp=/tmp/hg-cooperbench-tests
```

Omitting `--platform` runs the same observed container verifier directly, without
workers. Oracle controls give both peers the released combined patch; these are
reachability controls, never a candidate solver. Platform controls also exchange
messages and check UID, private-input absence and model-credential absence.
`--output` preserves the complete receipt and its temporary control artifact
directory for review; ordinary controls remove their own temporary directory.

Missing patches and failing feature tests follow Harbor's zero-score semantics.
Scorer startup, provenance, timeout or runtime failures are unmeasured. Candidate
repositories, process state and arbitrary files never enter the verifier;
selected tests enter only after both worker containers stop.

`vendor/harbor-test.sh` is the unmodified pinned Harbor scoring source (Apache-2.0,
see `vendor/LICENSE`). It is retained for byte-level drift and Git-based scoring
controls; runtime preparation uses Harbor's task-specific copy, including the pinned task fixes.
