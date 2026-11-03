# Provisioning four small datasets on this machine, 2026-11-02

Whether `bfcl_v3`, `cooperbench`, `costbench` and `costbench_dynamic` can actually be
provisioned **and measured here** — the last unknown before an open-source release.
All work happened in the `hg-t11` worktree (`/tmp/hg-t11`, branch `hg-t11`, base `main`
at `9d79ea0`). No dataset byte was written into the repository, and nothing except this
file is committed.

Machine: Python 3.12.4, Docker Engine 29.2.1 (`docker info` OK), `uv` 0.10.11, host load
average ~17 with 48 logged-in users. No `HG_DATA_*`, `HG_DATA_ROOT`, `HG_COSTBENCH_DATA`
or `HG_COOPERBENCH_DATA` variable was set during any command below, so every adapter took
its root from `data/scaffolds/roots.py` itself.

## Verdicts

| dataset | root (`roots.describe`) | bytes on disk after | `provision_status` | oracle | noop | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `bfcl_v3` | `/mnt/20t/xubuqiang/datasets/bfcl-v3` (`legacy`) | 223 MB (nothing new fetched) | **present: False**, `tasks: 0` | refused | not run | **blocked** |
| `cooperbench` | `/home/xubuqiang/.local/share/harnessgrad/datasets/cooperbench` (`default`) | 58 MB + 162 MB external sources | **present: False**, `0/30 base-task runtimes prepared` | refused | not run | **blocked** |
| `costbench` | `/home/xubuqiang/.local/share/harnessgrad/datasets/costbench` (`default`) | 2.7 GB (+1.1 GB added later by `costbench_dynamic`) | present: True, `tasks: 2293` | **reward 1** | **reward 0** | **usable** |
| `costbench_dynamic` | `/home/xubuqiang/.local/share/harnessgrad/datasets/costbench` (`default`, resolved under the name `costbench`) | 1.1 GB of its own inside the shared root | present: True, `tasks: 9172` | **reward 1** | **reward 0** | **usable** |

Root resolution, verbatim (`roots.describe(<name>, <adapter>.DATASET_ROOT, legacy=<the adapter's literal>)`):

```
bfcl_v3           {'name': 'bfcl_v3', 'root': '/mnt/20t/xubuqiang/datasets/bfcl-v3', 'source': 'legacy', 'exists': True}
cooperbench       {'name': 'cooperbench', 'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/cooperbench', 'source': 'default', 'exists': True}
costbench         {'name': 'costbench', 'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/costbench', 'source': 'default', 'exists': True}
costbench_dynamic {'name': 'costbench', 'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/costbench', 'source': 'default', 'exists': True}
```

Only `bfcl_v3` still resolves through rule 3 (its legacy literal exists); the other three
resolve through rule 4, so `--fetch` created them on a machine that never held them —
which is the open-source case. `costbench_dynamic` intentionally shares CostBench's root
and resolves under the name `costbench`.

---

## `bfcl_v3` — blocked

### 1. Fetch

```
$ python3 tools/datasets.py --fetch --dataset bfcl_v3          # 2026-11-02T13:39:59Z, 21.4 s
== bfcl_v3  (root /mnt/20t/xubuqiang/datasets/bfcl-v3)
  ok   [http] gorilla BFCL-v3 data (tag v1.3): gorilla-v1.3.tar.gz (present, sha256 ok); extracted -> /mnt/20t/xubuqiang/datasets/bfcl-v3
  ok   [pip] pyarrow==23.0.0 (install separately)
```

No new bytes were downloaded: `_src/gorilla-v1.3.tar.gz` was already present at
44,552,456 bytes with the pinned sha256, and the fetch re-extracted it. `du -sh
/mnt/20t/xubuqiang/datasets/bfcl-v3` = **223M**.

### 2. Preparation

There is none to run: the dataset has an adapter only, no `data/datasets/bfcl_v3/`
directory, therefore no `prepare.py`/`prepare_resources.py`. (`ls data/datasets/ |
grep bfcl` → `bfcl_v3.py` alone.)

### 3. Provisioning assertion — **fails**

```
$ python3 -c "import sys; sys.path.insert(0,'.'); from data.scaffolds import registry; print(registry.provision_status('bfcl_v3'))"
{'root': '/mnt/20t/xubuqiang/datasets/bfcl-v3', 'present': False, 'split_dir': '/mnt/20t/xubuqiang/datasets/bfcl-v3/gorilla-1.3/berkeley-function-call-leaderboard/bfcl_eval/data',
 'tarball': '/mnt/20t/xubuqiang/datasets/bfcl-v3/_src/gorilla-v1.3.tar.gz', 'tasks': 0}
```

Three of the four files `provision_status()` requires are present and non-empty:

```
OK   .../bfcl_eval/data/BFCL_v3_simple.json             280474
OK   .../bfcl_eval/data/BFCL_v3_irrelevance.json        160120
OK   .../bfcl_eval/data/possible_answer/BFCL_v3_simple.json  60809
MISS selected-index.v1.json
```

`selected-index.v1.json` is neither in the pinned tarball nor produced by anything in the
repository: a grep for `repo_tag` / `selected-index` over the tree finds only the adapter
constant (`data/datasets/bfcl_v3.py:112`), prose in
`docs/adapter-integration-journal.md:571`, and a **synthetic** fixture built inside
`tests/quality/test_bfcl_selected.py:42`. A filesystem search for `selected-index*.json`
under `/mnt/20t/xubuqiang`, `/mnt/8t/xubuqiang` and `/home/xubuqiang` (depth 6) found none.

### 4. Control arm — refused

```
$ python3 data/scaffolds/qa/control.py --dataset bfcl_v3 --oracle
  File "/tmp/hg-t11/data/datasets/bfcl_v3.py", line 584, in control_task
    return _selected_records(["simple/0" if task_id is None else task_id])[0]
  File "/tmp/hg-t11/data/datasets/bfcl_v3.py", line 382, in _selected_records
    raise ValueError("bfcl-v3: selected index is unavailable or unreadable") from exc
ValueError: bfcl-v3: selected index is unavailable or unreadable
```

(exit 1; the underlying cause in the chained traceback is
`FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/bfcl-v3/selected-index.v1.json'`.)
`--noop` was not run: the failure is in record construction, before either arm.

### Verdict

**blocked: `ValueError: bfcl-v3: selected index is unavailable or unreadable` (`data/datasets/bfcl_v3.py:382`) — the required `selected-index.v1.json` is not in the pinned tarball and no committed tool produces it.**

---

## `cooperbench` — blocked

### 1. Fetch

```
$ python3 tools/datasets.py --fetch --dataset cooperbench      # 2026-11-02T13:40:32Z
== cooperbench  (root /home/xubuqiang/.local/share/harnessgrad/datasets/cooperbench)
  ok   [http] CooperBench pinned source and released feature pairs: source.tar.gz (sha256 ok)
```

`source.tar.gz` = 1,693,222 bytes (pinned sha256 verified). `du -sh` of the root right
after the fetch = **1.7M**.

### 2. Preparation — pair inputs succeed, image runtimes do not

`prepare.py --help` showed the preparation is split: pair inputs (needs a **git checkout**
of the pinned source and of Harbor) and then one **explicit Docker image per group**.
`prepare.py:97-99` runs `git -C <source> rev-parse HEAD` and `git -C <harbor> rev-parse
HEAD`, so the tarball alone is not enough — the two pinned checkouts had to be created
outside the repository:

```
$ git init -q /tmp/hg-t11-sources/CooperBench && git -C ... fetch --depth 1 origin 63b9d44d9f39a02fccf5bf0052db48a917a011fd
63b9d44d9f39a02fccf5bf0052db48a917a011fd            # 14M
$ git init -q /tmp/hg-t11-sources/harbor     && git -C ... fetch --depth 1 --filter=blob:none origin 3e9f14a7c1033896cc600d27de69d6948bcb3410
3e9f14a7c1033896cc600d27de69d6948bcb3410            # 148M
sha256 adapters/cooperbench/src/cooperbench/adapter.py                 dc6adef8c5a04bab... == HARBOR_ADAPTER_SHA256
sha256 adapters/cooperbench/.../task-template/tests/test.sh            4c410f101b130a35... == HARBOR_SCORER_SHA256
```

Pair preparation then succeeded and reproduced the pinned manifest exactly:

```
$ python3 data/datasets/cooperbench/prepare.py \
    --root /home/xubuqiang/.local/share/harnessgrad/datasets/cooperbench \
    --source-root /tmp/hg-t11-sources/CooperBench \
    --harbor-root /tmp/hg-t11-sources/harbor                    # 2026-11-02T13:42:51Z, 1.5 s
{"tasks": 652, "groups": 30, "manifest_sha256": "64069b7c5dd126b999070a9e4462d5af82e9752a686e8c59a104d59c7a78d84b"}
```

`64069b7c…d84b` is exactly `MANIFEST_SHA256` in `data/datasets/cooperbench.py:32`, so the
652 released pairs are provisioned correctly. `du -sh` of the root after this step = **58M**
(57M of it `prepared-v1/`).

Image preparation is where it stops. One group, the README's own example, with an explicit
allocation (`{"workspace_bytes": 2147483648, "workspace_inodes": 200000, "memory_mb": 4096}`):

```
$ python3 data/datasets/cooperbench/prepare.py --root .../cooperbench \
    --source-root /tmp/hg-t11-sources/CooperBench \
    --image-group pallets_click_task/task2956 --pull \
    --resources /tmp/hg-t11-resources.json                      # 2026-11-02T13:47:01Z
task2956: Pulling from akhatua/cooperbench-pallets-click
8bd2cf7d3f6a: Pulling fs layer
... 10 layers "Pulling fs layer"
bd20f4652b5a: Download complete          # 8 min in: zero layers completed, image never registered
```

Killed after 8 minutes with no progress; a clean retry was bounded and also failed:

```
$ time timeout 900 docker pull akhatua/cooperbench-pallets-click:task2956   # 2026-11-02T14:00:35Z
real 15m0.007s
exit=124                                   # 0 of 10 layers ever reached "Pull complete"
```

The registry path is the problem, not this repo: `docker pull busybox:latest` finished in
4.3 s and `docker pull alpine:3.19` (3.5 MB) in 30.1 s, while the client-side registry
endpoint is unreachable outright —

```
$ docker buildx imagetools inspect akhatua/cooperbench-pallets-click:task2956
ERROR: failed to do request: Head "https://registry-1.docker.io/v2/akhatua/cooperbench-pallets-click/manifests/task2956":
       dial tcp 118.193.240.37:443: i/o timeout
```

(`docker manifest inspect` fails the same way: `failed to configure transport: error pinging
v2 registry: … Client.Timeout exceeded while awaiting headers`.) `provision_status()` needs a
`runtime.json` for **all 30** group images before it reports present, i.e. 30 full task
images from a registry path that delivered no layer in 15 minutes. Per the 2 GB instruction
the remaining 29 pulls were not started.

```
$ python3 -c "... print(registry.provision_status('cooperbench'))"
{'root': '.../cooperbench', 'present': False, 'tasks': 652, 'prepared_groups': [],
 'unprepared_groups': [ ...30 group ids... ],
 'detail': '0/30 base-task runtimes prepared; all pair identities retained'}      # step 3 FAILS
```

### 4. Control arm — refused

`control.py` needs a prepared runtime image for the pair's group:

```
$ python3 data/datasets/cooperbench/control.py --oracle \
    --task-id cooperbench/pallets_click_task/task2956/f1-2
  File "/tmp/hg-t11/data/datasets/cooperbench.py", line 81, in runtime
    config = json.loads((folder / 'runtime.json').read_text())
FileNotFoundError: [Errno 2] No such file or directory:
  '/home/xubuqiang/.local/share/harnessgrad/datasets/cooperbench/runtimes/pallets_click_task-task2956/runtime.json'
```

(exit 1, reached through `control.py:20 → VERIFY[...] → cooperbench.py:169 → :139 → :81`.)
Neither arm can run without a prepared image, so neither reward was measured.

### Verdict

**blocked: `FileNotFoundError: [Errno 2] No such file or directory: '.../runtimes/pallets_click_task-task2956/runtime.json'` (`data/datasets/cooperbench.py:81`) because per-group image preparation never finishes — `docker pull akhatua/cooperbench-pallets-click:task2956` delivered 0 of 10 layers in 15m0s (exit 124) and all 30 group images are required.**

---

## `costbench` — usable

### 1. Fetch

```
$ python3 tools/datasets.py --fetch --dataset costbench         # 2026-11-02T13:40:54Z
== costbench  (root /home/xubuqiang/.local/share/harnessgrad/datasets/costbench)
  ok   [http] CostBench official static tasks and Python environment source: source.tar.gz (sha256 ok)
```

`source.tar.gz` = 2,512,094 bytes; `du -sh` of the root = **2.5M**.

### 2. Preparation

`prepare.py` runs the full gold audit and wants the isolated 3.12 interpreter
(`--install` creates it with `uv` from `requirements.lock`). The first invocation stalled —
environmentally, not in the dataset — inside `uv pip install`, for 7m11s, holding
`~/.cache/uv/.lock` while a second session on this machine was running its own
`uv pip install`; it was killed after 7m11s (`subprocess.CalledProcessError … died with
<Signals.SIGTERM: 15>`). Re-running the same install with a private cache completed
immediately, after which preparation ran:

```
$ UV_CACHE_DIR=/tmp/hg-t11-uvcache uv pip install \
    --python .../costbench/runtime/bin/python -r data/datasets/costbench/requirements.lock
Resolved 23 packages in 2.33s … + numpy==2.2.6 …                        # exit 0

$ .../costbench/runtime/bin/python data/datasets/costbench/prepare.py \
    --root /home/xubuqiang/.local/share/harnessgrad/datasets/costbench   # 2026-11-02T13:49:35Z, 4m55.8s, exit 0
{"revision": "6424069f3f773ade076bcedbed302ed47cfb1837", …
 "counts": {"train": 1912, "test": 381}, "total": 2293, "passed": 2293, "tcr_passed": 381}
```

Every one of the 2293 official gold episodes passed (train 1912/1912, test 381/381).
`du -sh` after preparation = **2.7G**: `search-spaces/` 2.3G (generated by the audit),
runtime venv 97M, `prepared-v1/` 267M, extracted source 18M, archive 2.4M.

### 3. Provisioning assertion — passes

```
$ python3 -c "... print(registry.provision_status('costbench'))"
{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/costbench', 'present': True,
 'tasks': 2293, 'detail': 'static sequence5; train compatibility is declared separately'}
```

### 4. Control arms

The dataset's own control (the generic `data/scaffolds/qa/control.py` does not work for
this adapter — see "Code changes needed"):

```
$ .../costbench/runtime/bin/python data/datasets/costbench/control.py --oracle    # 13:54:54Z, 2.9 s
… "official": {"metric": "costbench.static.sequence5.per_query_path_exact_match.v1", "primary_mean": 1.0, …},
  "scorer_input": {"task_id": "costbench/test/Query00001", "sha256": "cec987b2…", "verified": true}}}}}
reward 1

$ .../costbench/runtime/bin/python data/datasets/costbench/control.py --noop      # 13:55:02Z, 1.6 s
… "scorer_input": {"task_id": "costbench/test/Query00001", "sha256": "cec987b2…", "verified": true}}}}}
reward 0
```

Both exit 0. `oracle` reaches reward 1 through the released official scorer and `noop`
reaches reward 0, so the arm can both pass and fail.

### Verdict

**usable** — provisioned (`present: True`, 2293 tasks), oracle reward 1, noop reward 0.

---

## `costbench_dynamic` — usable

### 1. Fetch

```
$ python3 tools/datasets.py --fetch --dataset costbench_dynamic   # 2026-11-02T13:41:12Z
== costbench_dynamic  (root /home/xubuqiang/.local/share/harnessgrad/datasets/costbench)
  ok   [http] CostBench dynamic sequence5: four one-event protocols: source.tar.gz (present, sha256 ok)
```

No new bytes: like the adapter, the entry shares CostBench's `source.tar.gz` (already
fetched one step earlier). The root's `du -sh` at that moment was still **2.5M**.

### 2. Preparation

It requires the static preparation above, and it adds its own four one-event protocols:

```
$ .../costbench/runtime/bin/python data/datasets/costbench_dynamic/prepare.py \
    --root /home/xubuqiang/.local/share/harnessgrad/datasets/costbench   # 13:55:30Z, 21m8s, exit 0
{"total": 9172, "passed": 9172, "manifest_sha256": "a424801c3d156bfc9527534e715e40ed02cd812889ae0d1c711c9d8c792e4c14"}
```

`a424801c…4c14` is exactly `MANIFEST_SHA256` in `data/datasets/costbench_dynamic.py:25`.
All 9172 audit cases passed (4 modes × (1912 train + 381 test)). `dynamic-prepared-v1/` is
**1.1G**; the shared root grew to **3.8G** in total.

### 3. Provisioning assertion — passes

```
$ python3 -c "... print(registry.provision_status('costbench_dynamic'))"
{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/costbench', 'present': True,
 'tasks': 9172, 'detail': 'dynamic sequence5; four one-event modes; training compatibility declared separately'}
```

### 4. Control arms

```
$ .../costbench/runtime/bin/python data/datasets/costbench_dynamic/control.py --oracle   # 14:17:14Z, 2.9 s
… "scorer_input": {"task_id": "costbench_dynamic/cost_change/test/Query00001",
                   "sha256": "e5ec3b6f…", "verified": true}}}}}
reward 1

$ .../costbench/runtime/bin/python data/datasets/costbench_dynamic/control.py --noop
reward 0
```

### Verdict

**usable** — provisioned (`present: True`, 9172 tasks), oracle reward 1, noop reward 0.

---

## `tools/datasets_doc.py --write` / `--check`

```
$ python3 tools/datasets_doc.py --write
wrote DATASETS.md (39 dataset(s))
$ python3 tools/datasets_doc.py --check
DATASETS.md matches the adapters                                   # exit 0
```

The diff is confined to the `provisioned here` column plus the sentence that summarises it
(`1 file changed, 5 insertions(+), 5 deletions(-)`; `git diff --numstat` = `5 5`):

```diff
-**At a glance.** 39 published datasets: 28 provisioned here, 8 not, 3 unknown; …
+**At a glance.** 39 published datasets: 32 provisioned here, 4 not, 3 unknown; …
-| `costbench` | … | no | released | no  | …
+| `costbench` | … | no | released | yes | …
-| `costbench_dynamic` | … | no | released | no  | …
+| `costbench_dynamic` | … | no | released | yes | …
-| `kumo` | … | no | released | no  | …
+| `kumo` | … | no | released | yes | …
-| `personamem_v2` | … | no | released | no  | …
+| `personamem_v2` | … | no | released | yes | …
```

`costbench` and `costbench_dynamic` are this work; `kumo` and `personamem_v2` are other
sessions provisioning concurrently on the same machine (`DATASETS.md` is generated from live
`provision_status()`), which is also why `--check` on the committed file now says
`DATASETS.md is stale; run tools/datasets_doc.py --write`. Per the instruction, the file was
**not committed**: it was restored with `git checkout -- DATASETS.md` and the worktree is
clean apart from this report. `bfcl_v3` and `cooperbench` stay `no` in that column.

## Code changes these datasets need (not written)

1. **`bfcl_v3` cannot ever report present.** `provision_status()` and `_selected_records()`
   require `selected-index.v1.json`, but nothing in the repository downloads or builds it —
   the only producer anywhere is a synthetic pytest fixture
   (`tests/quality/test_bfcl_selected.py:42`). Needed: a committed builder/prepare step that
   derives the index from the pinned tarball (bound to `repo_tag` + `tarball_sha256`, as
   `data/datasets/bfcl_v3.py:384-395` already validates) and is recorded in `downloads()`,
   otherwise `tools/datasets.py --check/--fetch` is green while the dataset stays unusable.
2. **The generic QA control does not understand the CostBench adapters.**
   `python3 data/scaffolds/qa/control.py --dataset costbench --oracle` dies with
   `KeyError: None` at `data/datasets/costbench.py:75` (`control_task(None)`), and with
   `--task-id` it dies with `KeyError: 'gold'` at `data/scaffolds/qa/control.py:100`
   (the record carries `goal`/`inputs`, not `gold`). Those adapters ship their own
   `control.py`, which works; `qa/control.py` should delegate to it or refuse explicitly
   instead of raising `KeyError`.
3. **`cooperbench` provisioning depends on two unpinned-by-`downloads()` git checkouts.**
   `prepare.py:97-99` requires a CooperBench checkout at `63b9d44…` and a **Harbor** checkout
   at `3e9f14a…`; `downloads()` pins only the CooperBench tarball, and no Harbor URL is
   recorded anywhere in the tree (`docs/harbor-dataset-handover.md` only tells a reader to
   clone "harbor"). Needed: a pinned Harbor source entry (or a checked-in fetch step), plus a
   decision about the 30 registry images that provisioning depends on.

## Environment notes that are not dataset defects

- `uv` is cache-lock sensitive: a second session's `uv pip install` in the same
  `~/.cache/uv` stalled this one for minutes. `UV_CACHE_DIR=<private>` removed it.
- `docker.io` is not reachable client-side from this host (resolved to
  `118.193.240.37:443`, `i/o timeout`); the daemon can still pull small `library/*` images.
- Docker already held 132.7 GB of images before this work; `/` was 94 % full (113 GB free →
  106 GB free after the two CostBench preparations). Nothing was deleted. The largest single
  download executed here was CostBench's 2.5 MB archive; the ~3.8 GB CostBench root is
  generated by its gold audit, not downloaded.

## Addendum (merged 2026-11-02, after this report was written)

The `provisioned here` column this report refers to was replaced by a `provision` column
(commit `11826e6`) *before* these findings were merged, and for the reason this run
demonstrates: a cell derived from `provision_status()` describes the machine, so it changed
under the report as bytes landed. `python3 tools/datasets_doc.py --check` is now
host-independent by construction, and the host answer moved behind `--host`. Nothing in this
report's verdicts depends on the column: `costbench` and `costbench_dynamic` are `usable`
because the oracle scores 1 and the no-op scores 0, `bfcl_v3` and `cooperbench` are `blocked`
because a required artifact no command produces (the selected index) or no reachable registry
serves (30 images).
