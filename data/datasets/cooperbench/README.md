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

Preparation recipes cover all **30 groups**, with pinned runner hashes and exact
installation ranges in `recipes.json`:

| Family | Groups | Preparation and offline execution |
| --- | ---: | --- |
| Python (Outlines, DSPy, datasets, LlamaIndex, tiktoken, Click, Jinja, Pillow, dirty-equals) | 24 | Resolve the official dependency block and build backends during preparation. After each submitted patch, reinstall editable code inside a writable venv with `--no-index --no-deps --no-build-isolation`; this preserves native-extension rebuilds and entry-point updates. |
| Go chi | 3 | Preserve `go test`; copy module cache into the bounded workspace and disable module network resolution. |
| React Hook Form | 2 | Preserve cached node_modules and the test command; preinstall pnpm 8 so the runner uses it directly without npx downloading it. |
| Typst/Rust | 1 | Preserve Cargo build/test; relocate writable registry/git caches, retain compiled target output, and set offline Cargo mode. |

The official reset, patch application, test commands and cleanup remain intact.
`/patches` and Harbor's other writable mounts move under `/workspace` to retain
the platform's filesystem limits. `--build-network host` is available **only for
preparation**, for hosts whose download proxy listens on loopback; measurement
always has no network. A stale published image whose runner/base does not match
is refused. Rebuild the pinned task Dockerfile and supply its explicit local ID
with `--source-image` in that case; the prepared identity remains recorded.

Preparation records the measured seed size/inode count. Each runtime receives
2 CPUs and 512 PIDs, at least a 2 GiB/200,000 inode workspace, with bounded
headroom based on that seed. Preparation refuses requirements beyond the reviewed
16 GiB/2 million inode envelope. Memory covers the workspace quota plus 2 GiB
(minimum 4 GiB). Worker and verifier deadlines are 1800 seconds. These resource
settings are explicit adapter settings; a larger environment needs separate
configuration and validation.

Only the released **Click task2956 pair f1-2** has currently passed real Docker
oracle/noop controls. All 30 recipe transformations have source-drift and shell
syntax checks; the other images/toolchains are **not yet validated**. Preparing
an image does not mark it validated. `provision_status()` lists prepared and
unprepared groups without dropping any of the 652 pair identities.

## Controls and validation

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
