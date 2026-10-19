# CooperBench fixed-peer integration

Each candidate harness runs twice, in independent observed Docker workspaces,
with one released feature per peer and a platform mailbox. The only submitted
artifact is `agent.patch`. Both patches are merged in a fresh verifier; no solver
sidecar, Redis server, model-specific agent or reference patch is part of SETUP.

The metric is **Harbor merged-pair success**, pinned to Harbor
`3e9f14a7c1033896cc600d27de69d6948bcb3410`: filter test diffs, merge normally then
with union fallback, and require both feature runners to exit zero. This is not
the subsequently changed upstream evaluator. Data is pinned to CooperBench
`63b9d44d9f39a02fccf5bf0052db48a917a011fd` (199 features, 30 base tasks, 652 pairs).
Harbor's four existing task fixes are applied by its pinned adapter during
preparation, including the oracle-only combined-patch corrections.

The train/eval split hashes the **base task**, so related pairs never cross the
split. It is an adapter split, not an official heldout designation; the official
flash subset is development material. All pair IDs remain in the inventory even
when a machine has imported only one runtime. A missing runtime fails explicitly.

Prepared inputs must use the corrected scorer manifest `3accea49615abcc01f65d366e5717fe27d39626bd59e3f8c331297fc60d5b072`.
Roots prepared with the earlier mount relocation must be prepared again in a
fresh directory. Preserve their inputs and receipts; copy verified runtime
metadata to reuse the same immutable images without rebuilding them.

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
  --image-group pallets_click_task/task2956 --pull
```

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
  --base-runtime /your/previous/cooperbench/runtimes/dottxt_ai_outlines_task-task1706 \
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
The cache byte count enters the same workspace/memory formula. Scoring requires
matching mirror health before and after the official runner and a zero verifier
exit status; service failure stays unmeasured even if a reward file remains.

The official reset, patch application, test commands and cleanup remain intact.
`/patches` and Harbor's other writable mounts move under `/workspace` to retain
the platform's filesystem limits. Native compilation scratch also stays inside
this quota, under `/workspace/.test-tmp`, rather than the small system `/tmp`. `--build-network host` is available **only for
preparation**, for hosts whose download proxy listens on loopback; measurement
always has no network. A stale published image whose runner/base does not match
is refused. Rebuild the pinned task Dockerfile and supply its explicit local ID
with `--source-image` in that case; the prepared identity remains recorded.

Preparation records the measured seed size/inode count. Each runtime receives
2 CPUs and 512 PIDs, at least a 2 GiB/200,000 inode workspace, with bounded
headroom based on that seed. Preparation refuses requirements beyond the reviewed
16 GiB/2 million inode envelope, except the pinned Typst recipe: its measured
6.63 GiB seed needs a 20.89 GiB workspace, within an explicit 24 GiB envelope. Memory covers the workspace quota plus 2 GiB
(minimum 4 GiB). Worker and verifier deadlines are 1800 seconds. These resource
settings are explicit adapter settings; a larger environment needs separate
configuration and validation.

All 30 recipe transformations have source-drift and shell syntax checks.
`provision_status()` reports prepared and unprepared groups without dropping any
of the 652 pair identities. Preparation alone does not establish validation.

A small Click communication check also passed with Ling-3.1-flash: two model
requests, mailbox exchange and patch submission through a temporary fixed
HTTP-to-HTTPS bridge. The provider key stayed on the host and workers received
temporary tokens. This checks the API/communication path, not model coding
performance.

## Controls and validation

Real Docker oracle/noop controls passed (1/0) for the representative **f1-2 pair
in 25 of 30 groups**, including both candidate workers, private-input isolation,
artifact submission, official scoring and cleanup:

| Project | Passed task IDs |
| --- | --- |
| Click | 2068, 2800, 2956 |
| Jinja | 1465, 1559, 1621 |
| Go chi | 26, 27, 56 |
| Pillow | 25, 68, 290 |
| dirty-equals | 43 |
| React Hook Form | 153 |
| Typst | 6554 |
| tiktoken | 0 |
| DSPy | 8394, 8635 |
| Hugging Face datasets | 3997, 6252 |
| LlamaIndex | 17070, 17244, 18813 (fixed W3C PDF mirror) |
| Outlines | 1371; 1706 with the fixed public HF mirror |

The remaining five groups have explicit limits:

- React Hook Form 85 has an **upstream oracle/merge blocker**: its released patch
  fails the original Husky/TypeScript pre-commit check, producing an empty merge.
- DSPy 8563's pinned Harbor test-fix append conflicts with its second test patch;
  the conflict also reproduces in the original image.
- DSPy 8587's local LiteLLM service misses the official 10-second readiness check
  at the official 2 CPUs. Follow-up profiling found millisecond DNS failures;
  removing the optional remote cost-map lookup still missed the deadline. Its
  timeout and resource limit remain unchanged.
- Outlines 1655's image imports. A fresh attempt passed the earlier metadata
  request failure but exhausted its 1,200-second preparation budget while
  downloading the original locked dependencies; no scoring control ran.
- Datasets 7309 retains a verified partial image download after a bounded attempt;
  no runtime or scoring control is available yet.

These controls cover environment reachability, not all 652 pairs or candidate
coding quality. Earlier receipts remain preserved. The corrected scorer's
migration checked all 652 input identities and all 30 distinct oracle patch
contents: only scorer mount relocation changed, and official filtered oracle
patches remained identical. Fresh Click oracle/noop and an adversarial
`tests/helpers.py` patch control verify the corrected filter end to end.

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

This integration exercises fixed-peer cooperation. It does not claim that the
candidate dynamically decomposes work or spawns/delegates to subagents: the
platform assigns the two official feature roles. The strict delegation gap
therefore remains.

`vendor/harbor-test.sh` is the unmodified pinned Harbor scoring source (Apache-2.0,
see `vendor/LICENSE`). It is retained for byte-level drift and Git-based scoring
controls; runtime preparation uses Harbor's task-specific corrected copy.
