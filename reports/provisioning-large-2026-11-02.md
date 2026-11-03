# Provisioning four large datasets — 2026-11-02

Verification that `kumo`, `memoryagentbench`, `personamem_v2` and `strongreject` can be
provisioned and measured on this host, the last unknown before an open-source release.

- Worktree: `/tmp/hg-t12`, branch `hg-t12`, base commit `9d79ea0` (identical to `main` at
  the start of the run). Python 3.12.4, CST.
- No `HG_DATA_*` / `HG_DATA_ROOT` variable was set. For all four datasets
  `data/scaffolds/roots.py` therefore took **rule 4 (default)**:
  `~/.local/share/harnessgrad/datasets/<name>`. Rule 1 (`HG_DATA_<NAME>`) and rule 2
  (`HG_DATA_ROOT`) were unset, and rule 3 (`legacy`) exists only when its path exists —
  `/mnt/20t/xubuqiang/datasets/<name>` does not exist for any of the four (verified with
  `roots.describe()`, see each section).
- No dataset bytes were written into the repository. The worktree stayed at 36 MB; every
  byte below is under `$HOME/.local/share/harnessgrad/datasets/`.

## Verdict

| dataset | bytes on disk (`du -sh` of resolved root) | resolved root (rule) | oracle | no-op | verdict |
| --- | --- | --- | --- | --- | --- |
| `kumo` | 236M | `~/.local/share/harnessgrad/datasets/kumo` (rule 4, default) | 1.0 | 0.0 | **usable** |
| `memoryagentbench` | 77M (7 files; no prepared pool) | `~/.local/share/harnessgrad/datasets/memoryagentbench` (rule 4, default) | not reached | not reached | **blocked** |
| `personamem_v2` | 713M (raw 378M + prepared 335M) | `~/.local/share/harnessgrad/datasets/personamem_v2` (rule 4, default) | 1.0 | `null` (`incomplete_episode`); `--wrong` = 0.0 | **usable** |
| `strongreject` | 36K (8/155 files) | `~/.local/share/harnessgrad/datasets/strongreject/native-v1` (rule 4, default) | not reached | not reached | **blocked** |

### Every blocker, with its exact error text

1. **`memoryagentbench` — the pinned NLTK archive is on a blocked host.**
   `tools/datasets.py --fetch` fetches the six Hugging Face files and the o200k encoding,
   then fails on the punkt_tab ZIP:

   ```
   FAIL [http] MemoryAgentBench English sentence tables archive: punkt_tab.zip: curl exited 35: curl: (35) Recv failure: 连接被对方重置
   ```

   The raise site is `tools/datasets.py:235` (`RuntimeError(f"curl exited {code}")`),
   returned as a failure at `tools/datasets.py:301`. Without those four English tables
   `prepare_resources.py` refuses:

   ```
   ValueError: missing or corrupt resources: resources/nltk/tokenizers/punkt_tab/english/abbrev_types.txt, resources/nltk/tokenizers/punkt_tab/english/collocations.tab, resources/nltk/tokenizers/punkt_tab/english/ortho_context.tab, resources/nltk/tokenizers/punkt_tab/english/sent_starters.txt
   ```

   (`data/datasets/memoryagentbench/prepare_resources.py:91`.) The sanctioned restore,
   `--download`, hits the same host at `prepare_resources.py:50`:

   ```
   urllib.error.URLError: <urlopen error [Errno 104] Connection reset by peer>
   ```

2. **`memoryagentbench` — pinned preparation dependencies are not installed.**
   Even after the raw files landed, `prepare.py` stops at its dependency gate
   (`data/datasets/memoryagentbench/lib/inputs.py:45`, reached from
   `prepare.py:109` → `lib/inputs.py:60`):

   ```
   ValueError: dependency_version_mismatch:nltk
   ```

   `specs/chunk-policy.json` pins `nltk 3.10.3`, `regex 2026.9.29`, `tiktoken 0.14.0`
   (`requirements-prepare.txt` asks for `nltk==3.10.4`); this environment has
   `nltk 3.9.2`, `regex 2023.10.3`, `tiktoken 0.7.0`.

3. **`strongreject` — all 155 source files are on the same blocked host.**
   One full pass: 147 of 155 FAIL, 8 already present (`ELAPSED=184.06s`):

   ```
   FAIL [http] StrongREJECT tasks/strongreject_disinformation_and_deception_0004_pap_logical_appeal.json: strongreject_disinformation_and_deception_0004_pap_logical_appeal.json: curl exited 35: curl: (35) Recv failure: 连接被对方重置
   ```

   (`tools/datasets.py:235` / `:301`.) The dataset's own `prepare.py --fetch` fails at
   `data/datasets/strongreject/prepare.py:29`:

   ```
   urllib.error.URLError: <urlopen error [Errno 104] Connection reset by peer>
   ```

   `provision_status()` additionally reports `judge_configured: false` for the model judge
   the control arm needs (`gpt-4o-mini-2024-07-18`); that arm was never reached because the
   bytes are missing.

## Network reality on this host

Measured at the start of the run (`curl -sS -o /dev/null -w '%{http_code}'`):

| URL | result |
| --- | --- |
| `https://hf-mirror.com` | 200 |
| `https://huggingface.co` | 000 (unreachable) |
| `https://github.com` | 200 |
| `https://raw.githubusercontent.com` | 000 — `curl: (35) Recv failure: 连接被对方重置` (all 5 A records, IPv4 and IPv6) |
| `https://openaipublic.blob.core.windows.net` | 200 |

`raw.githubusercontent.com` is the single common cause of the memoryagentbench and
strongreject blockers. It is not fully down: repeated passes of the strongreject fetch
landed 6 then 2 more files, and a 20-request probe of one URL immediately afterwards
returned 0/20 — i.e. **intermittent resets**, not a clean refusal. On a host with normal
egress both datasets' first fetch should succeed unchanged.

## `kumo` — usable

### Commands

```bash
python3 tools/datasets.py --fetch --dataset kumo
python3 data/datasets/kumo/prepare.py --fetch --root "$HOME/.local/share/harnessgrad/datasets/kumo"
python3 -c "import sys; sys.path.insert(0,'.'); from data.scaffolds import registry; print(registry.provision_status('kumo'))"
python3 data/datasets/kumo/control.py --oracle
python3 data/datasets/kumo/control.py --noop
du -sh "$HOME/.local/share/harnessgrad/datasets/kumo"
```

### Step 1 — declarative fetch fails on the blocked host

```
== kumo  (root /home/xubuqiang/.local/share/harnessgrad/datasets/kumo)
  FAIL [http] KUMO pinned sparse source and released games: .gitignore: curl exited 35: curl: (35) Recv failure: 连接被对方重置
Command exited with non-zero status 1
ELAPSED=16.87s
```

The adapter's own `downloads()` note says *"Prefer prepare.py --fetch for batched sparse
Git download"*, so step 2 is the dataset's sanctioned path, not a workaround.

### Step 2 — the dataset's own preparation succeeds

The failed step 1 created `<root>/upstream/` (empty) before its first transfer failed.
`prepare.py:147` skips the clone when `(root / "upstream").exists()`, so the first
invocation read a `.gitignore` that did not exist:

```
FileNotFoundError: [Errno 2] No such file or directory: '/home/xubuqiang/.local/share/harnessgrad/datasets/kumo/upstream/.gitignore'
Command exited with non-zero status 1
ELAPSED=0.05s
```

That directory held **0 files** (checked with `find … -type f | wc -l` = 0); it was
removed and the same command was re-run:

```bash
python3 data/datasets/kumo/prepare.py --fetch --root "$HOME/.local/share/harnessgrad/datasets/kumo"
```

```
正克隆到 '/home/xubuqiang/.local/share/harnessgrad/datasets/kumo/upstream'...
来自 https://github.com/linhaowei1/kumo
 * branch            576748764f7d72634654d0784944a485eca9047c -> FETCH_HEAD
HEAD 目前位于 5767487 Update README.md
{"eligible": 5290, "excluded": 10, "manifest_sha256": "05e20d7a9e7e8cff348f77e36cfb0d2842b549c344086e71fe478458b3cd05fe", "manifest": "/home/xubuqiang/.local/share/harnessgrad/datasets/kumo/prepared-v1/manifest.json"}
ELAPSED=12.07s
exit=0
```

The manifest digest equals the adapter's `MANIFEST_SHA256`, and `5290/10` matches the
adapter's declared inventory (5,300 source tasks, ten excluded).

### Step 3 — provisioning assert

```
{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/kumo', 'present': True, 'tasks': 5290, 'source_tasks': 5300, 'excluded': 10, 'detail': '0 missing prepared files'}
```

`roots.describe('kumo', …, legacy='/mnt/20t/xubuqiang/datasets/kumo')` →
`{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/kumo', 'source': 'default', 'exists': True}`.

The declarative check agrees after preparation:

```
$ python3 tools/datasets.py --check --dataset kumo
ok   kumo (5290 task(s) loadable)
  ok  [http] KUMO pinned sparse source and released games @ 5767487… -- …/kumo/upstream (5846 file(s), sha256 ok)
```

### Step 4 — control arms

```bash
$ python3 data/datasets/kumo/control.py --oracle
{"task_id": "kumo/easy/AlgorithmEnv/0", "oracle": true, "platform": false, "score": 1.0, "detail": "{\n \"measured\": true,\n \"returncode\": 0,\n \"reward\": 1.0,\n \"stderr\": \"\",\n \"why\": null\n}"}
reward 1
ELAPSED=0.89s

$ python3 data/datasets/kumo/control.py --noop
{"task_id": "kumo/easy/AlgorithmEnv/0", "oracle": false, "platform": false, "score": 0.0, "detail": "{\n \"measured\": true,\n \"returncode\": 0,\n \"reward\": 0.0,\n \"stderr\": \"\",\n \"why\": null\n}"}
reward 0
ELAPSED=0.89s
```

Oracle 1.0, no-op 0.0, both measured — the arm passes.

### Step 5 — bytes and resolution

`du -sh "$HOME/.local/share/harnessgrad/datasets/kumo"` → `236M`
(`upstream` 154M, `prepared-v1` 81M, `official-audit.json` 1.6M).
Resolved root: `~/.local/share/harnessgrad/datasets/kumo`, rule **4 (default)**.

### Verdict

**usable.** Both control arms behave, the manifest identity is verified, and the bytes are
outside the repository. Note for release notes: on this host the *declarative*
`tools/datasets.py --fetch` cannot fetch kumo (blocked `raw.githubusercontent.com`), and
its failed attempt leaves an empty `upstream/` that defeats `prepare.py --fetch`'s
`exists()` guard until removed.

## `memoryagentbench` — blocked

### Commands

```bash
python3 tools/datasets.py --fetch --dataset memoryagentbench
python3 data/datasets/memoryagentbench/prepare_resources.py --resources "$R/resources"
python3 data/datasets/memoryagentbench/prepare_resources.py --resources "$R/resources" --download
python3 data/datasets/memoryagentbench/prepare.py --raw "$R/raw" --resources "$R/resources" --output "$R/prepared"
python3 -c "import sys; sys.path.insert(0,'.'); from data.scaffolds import registry; print(registry.provision_status('memoryagentbench'))"
```

with `R="$HOME/.local/share/harnessgrad/datasets/memoryagentbench"`.

### Step 1 — fetch: 6 HF files and the o200k encoding land; punkt_tab does not

```
== memoryagentbench  (root /home/xubuqiang/.local/share/harnessgrad/datasets/memoryagentbench)
  ok   [hf-dataset] MemoryAgentBench fixed data: README.md (sha256 ok); data/Accurate_Retrieval-00000-of-00001.parquet (sha256 ok); data/Conflict_Resolution-00000-of-00001.parquet (sha256 ok); data/Long_Range_Understanding-00000-of-00001.parquet (sha256 ok); data/Test_Time_Learning-00000-of-00001.parquet (sha256 ok); entity2id.json (sha256 ok)
  FAIL [http] MemoryAgentBench English sentence tables archive: punkt_tab.zip: curl exited 35: curl: (35) Recv failure: 连接被对方重置
  ok   [http] MemoryAgentBench o200k_base encoding: fb374d419588a4632f3f557e76b4b70aebbca790 (sha256 ok)
Command exited with non-zero status 1
ELAPSED=26.75s
```

`du -sh` → `77M`, 7 files (6 `raw/` files + `resources/tiktoken/<hash>`). No Hugging Face
token was required; the `hf-dataset` entry was not gated.

### Step 2a — resources refuse offline, and cannot be restored online

```bash
$ python3 data/datasets/memoryagentbench/prepare_resources.py --resources "$R/resources"
…
ValueError: missing or corrupt resources: resources/nltk/tokenizers/punkt_tab/english/abbrev_types.txt, resources/nltk/tokenizers/punkt_tab/english/collocations.tab, resources/nltk/tokenizers/punkt_tab/english/ortho_context.tab, resources/nltk/tokenizers/punkt_tab/english/sent_starters.txt
# exit 1 (prepare_resources.py:91)

$ python3 data/datasets/memoryagentbench/prepare_resources.py --resources "$R/resources" --download
…
urllib.error.URLError: <urlopen error [Errno 104] Connection reset by peer>
# exit 1 (prepare_resources.py:50, the pinned nltk_archive_url on raw.githubusercontent.com)
```

The only local `punkt_tab.zip` (`~/.local/share/nltk_data` installed copy) is **not**
usable and was not substituted: its digest is
`a1630314ee16bc3021e95bfa4aa490031b89ec8d4de8ce43613bc5266b1af573`, while
`specs/resources.lock.json` pins `e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`.

### Step 2b — the preparation then stops on its dependency gate

```bash
$ python3 data/datasets/memoryagentbench/prepare.py --raw "$R/raw" --resources "$R/resources" --output "$R/prepared"
Traceback (most recent call last):
  File "…/data/datasets/memoryagentbench/prepare.py", line 109, in prepare
    sentence_tokenizer, encoding = initialize(resources)
  File "…/data/datasets/memoryagentbench/lib/inputs.py", line 60, in initialize
    lock = validate_resources(resources)
  File "…/data/datasets/memoryagentbench/lib/inputs.py", line 45, in validate_resources
    raise ValueError("dependency_version_mismatch:" + package)
ValueError: dependency_version_mismatch:nltk
```

Pins vs installed: `nltk` 3.10.3 (`requirements-prepare.txt` says 3.10.4) vs **3.9.2**;
`regex` 2026.9.29 vs **2023.10.3**; `tiktoken` 0.14.0 vs **0.7.0**. This gate is
independent of the archive: fixing the host would still leave this `pip install` need.

### Step 3 — provisioning assert (fails, as expected)

```
{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/memoryagentbench', 'prepared': '/home/xubuqiang/.local/share/harnessgrad/datasets/memoryagentbench/prepared', 'present': False, 'detail': "[Errno 2] No such file or directory: '/home/xubuqiang/.local/share/harnessgrad/datasets/memoryagentbench/prepared/manifest.json'"}
```

### Step 4 — control arms

Not reached: the dataset's own `control.py --oracle/--noop` loads the prepared pool first,
and the pool cannot be built. Per the task's stop-at-first-broken-step rule, no control
was run and no score is claimed.

### Step 5 — bytes and resolution

`du -sh` → `77M` (7 files). Resolved root:
`~/.local/share/harnessgrad/datasets/memoryagentbench`, rule **4 (default)**
(`roots.describe` source `default`, `exists: True`).

### Verdict

**blocked.** Primary: `curl: (35) Recv failure: 连接被对方重置` for the pinned
`punkt_tab.zip` (`tools/datasets.py:235`, surfaced by `tools/datasets.py:301`;
`prepare_resources.py:91/50`). Secondary: `ValueError: dependency_version_mismatch:nltk`
(`lib/inputs.py:45`) — needs `nltk==3.10.4`, `regex==2026.9.29`, `tiktoken==0.14.0`
(Python packages, not data).
**needs:** stable egress to `raw.githubusercontent.com` **and**
`pip install nltk==3.10.4 regex==2026.9.29 tiktoken==0.14.0`.

## `personamem_v2` — usable

### Commands

```bash
python3 tools/datasets.py --fetch --dataset personamem_v2          # repeated; see Step 1
python3 data/datasets/personamem_v2/prepare.py --raw "$R/raw" --output "$R/prepared"
python3 -c "import sys; sys.path.insert(0,'.'); from data.scaffolds import registry; print(registry.provision_status('personamem_v2'))"
python3 data/datasets/personamem_v2/control.py --oracle --task-id personamem-v2-train-p0153-text32k
python3 data/datasets/personamem_v2/control.py --noop   --task-id personamem-v2-train-p0153-text32k
python3 data/datasets/personamem_v2/control.py --wrong  --task-id personamem-v2-train-p0153-text32k
du -sh "$HOME/.local/share/harnessgrad/datasets/personamem_v2"
```

with `R="$HOME/.local/share/harnessgrad/datasets/personamem_v2"`.

### Step 1 — fetch: 1,002 files, five passes forced by hf-mirror 429s

The single `hf-dataset` entry names 1,002 files (393,775,124 pinned bytes). `--fetch`
aborts the whole entry on the first failed file, and hf-mirror returned `429` part-way
through:

| pass | elapsed | result | files on disk |
| --- | --- | --- | --- |
| 1 | 17.80s | `429` on `benchmark/text/benchmark.csv` | 0 |
| 2 | 238.02s | `429` on `…/persona313.json` | 242 |
| 3 | 245.56s | `429` on `…/persona572.json` | 528 |
| 4 | 258.26s | `429` on `…/persona833.json` | 818 |
| 5 | 175.13s | `exit=0` | 1,002 |

```
== personamem_v2  (root /home/xubuqiang/.local/share/harnessgrad/datasets/personamem_v2)
  ok   [hf-dataset] PersonaMem-v2 text-32k CSVs and referenced histories: benchmark/text/benchmark.csv (present, sha256 ok); benchmark/text/train.csv (present, sha256 ok); … (all 1,002 files)
```

Re-running is safe because present files are sha256-verified and skipped, so each pass
resumes. Total download wall time 1,024.8 s (**17.1 min**), under the 20-minute cap; the
extra time is 429 backoff, not dataset size. A direct ranged `GET` of the URL that had
just returned 429 came back `HTTP 206` with the expected CSV header, confirming the 429s
were rate limiting rather than a missing or gated object. No HF token was requested.

### Step 2 — preparation succeeds

```bash
$ python3 data/datasets/personamem_v2/prepare.py --raw "$R/raw" --output "$R/prepared"
{"episodes": 1735, "questions": 25609, "original_questions": 25610, "excluded_questions": 1, "native_episodes": {"benchmark": 200, "train": 800, "val": 735}, "output": "/home/xubuqiang/.local/share/harnessgrad/datasets/personamem_v2/prepared", "manifest_sha256": "f12632c0c43b87fd525c04ae6b5281dce15333347059d9c5debae4dcd7dce697"}
ELAPSED=11.49s
exit=0
```

### Step 3 — provisioning assert

```
{'present': True, 'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/personamem_v2', 'tasks': 1735, 'questions': 25609, 'original_questions': 25610, 'excluded_questions': 1}
```

`roots.describe('personamem_v2', …, legacy='/mnt/20t/xubuqiang/datasets/personamem_v2')` →
`source: 'default'`. The declarative check agrees:

```
$ python3 tools/datasets.py --check --dataset personamem_v2
ok   personamem_v2 (1735 task(s) loadable)
  ok  [hf-dataset] PersonaMem-v2 text-32k CSVs and referenced histories @ ed956dea… -- …/personamem_v2/raw (1002 file(s), sha256 ok)
```

### Step 4 — control arms

```bash
$ python3 data/datasets/personamem_v2/control.py --oracle --task-id personamem-v2-train-p0153-text32k
{"task_id": "personamem-v2-train-p0153-text32k", "mode": "oracle", "platform": false, "score": 1.0, "reason": null}
ELAPSED=1.05s

$ python3 data/datasets/personamem_v2/control.py --wrong --task-id personamem-v2-train-p0153-text32k
{"task_id": "personamem-v2-train-p0153-text32k", "mode": "wrong", "platform": false, "score": 0.0, "reason": null}
ELAPSED=1.12s

$ python3 data/datasets/personamem_v2/control.py --noop --task-id personamem-v2-train-p0153-text32k
{"task_id": "personamem-v2-train-p0153-text32k", "mode": "noop", "platform": false, "score": null, "reason": "incomplete_episode"}
ELAPSED=1.04s
```

The oracle reaches 1.0 and the completed-but-wrong arm scores a measured 0.0, so the
verifier discriminates. The no-op is **not a measured zero**: it returns `score: null`
with `reason: "incomplete_episode"`, which is the dataset's own pass condition
(`control.py` exits 0 only for that pair). That is the honest reading of "the check cannot
be reached by doing nothing", but it does not literally match the task's "reward 0 on the
no-op" wording; the `--wrong` arm supplies the measured 0 that a no-op cannot.

### Step 5 — bytes and resolution

`du -sh` → **713M** (`raw` 378M + `prepared` 335M). Resolved root:
`~/.local/share/harnessgrad/datasets/personamem_v2`, rule **4 (default)**.

### Verdict

**usable.** Oracle 1.0; wrong 0.0 measured; no-op correctly unmeasured rather than a
fabricated zero. Provisioning caveat for release notes: the mirror's per-file `429`
handling makes a 1,002-file `hf-dataset` entry need repeated `--fetch` passes; the tool
resumes correctly, but there is no built-in backoff.

## `strongreject` — blocked

### Commands

```bash
python3 tools/datasets.py --fetch --dataset strongreject
python3 data/datasets/strongreject/prepare.py --fetch
python3 -c "import sys; sys.path.insert(0,'.'); from data.scaffolds import registry; print(registry.provision_status('strongreject'))"
du -sh "$HOME/.local/share/harnessgrad/datasets/strongreject"
```

### Step 1 — fetch: 147/155 fail, 8 land (intermittent, not a refusal)

```
== strongreject  (root /home/xubuqiang/.local/share/harnessgrad/datasets/strongreject/native-v1)
  FAIL [http] StrongREJECT tasks/strongreject_disinformation_and_deception_0004_pap_logical_appeal.json: strongreject_disinformation_and_deception_0004_pap_logical_appeal.json: curl exited 35: curl: (35) Recv failure: 连接被对方重置
  …
  ok   [http] StrongREJECT tasks/strongreject_disinformation_and_deception_0009_prefix_injection.json: strongreject_disinformation_and_deception_0009_prefix_injection.json (sha256 ok)
  ok   [http] StrongREJECT tasks/strongreject_disinformation_and_deception_0047_pap_logical_appeal.json: strongreject_disinformation_and_deception_0047_pap_logical_appeal.json (present, sha256 ok)
  …
  FAIL [http] StrongREJECT upstream-LICENSE: upstream-LICENSE: curl exited 35: curl: (35) Recv failure: 连接被对方重置
Command exited with non-zero status 1
ELAPSED=184.06s
```

Counts on that full pass: `FAIL=147 OK=8` (155 entries). After two passes the root holds
**8 files / 36K**. A 20-request probe of one pinned URL immediately afterwards returned
`ok=0 fail=20`, so the eight successes were a brief window, not a usable transfer path.

### Step 2 — the dataset's own preparation fails identically

```bash
$ python3 data/datasets/strongreject/prepare.py --fetch
Traceback (most recent call last):
  …
  File "/tmp/hg-t12/data/datasets/strongreject/prepare.py", line 29, in prepare
    with urllib.request.urlopen(entry["url"], timeout=60) as response:
  …
urllib.error.URLError: <urlopen error [Errno 104] Connection reset by peer>
# exit 1, ELAPSED=0.20s
```

### Step 3 — provisioning assert (fails, as expected)

```
{'root': '/home/xubuqiang/.local/share/harnessgrad/datasets/strongreject/native-v1', 'present': False, 'judge_configured': False, 'judge_model': 'gpt-4o-mini-2024-07-18', 'metric': 'strongreject.rubric.defense.gpt-4o-mini-2024-07-18.v1', 'admission': 'Real judge oracle sweep and measured diagnostic required before experiments', 'detail': "[Errno 2] No such file or directory: '/home/xubuqiang/.local/share/harnessgrad/datasets/strongreject/native-v1/upstream-dataset.csv'"}
```

### Step 4 — control arms

Not reached: `rows()` hashes `upstream-dataset.csv` first, which is absent, and the
dataset's control is an **explicit live rubric** control (`--task-id` required) with no
mock judge or fallback. `judge_configured: false` means a judge endpoint/credential is a
second, independent need even once the bytes are present.

### Step 5 — bytes and resolution

`du -sh` → `36K` (8 files). Resolved root:
`~/.local/share/harnessgrad/datasets/strongreject/native-v1`, rule **4 (default)**
(`roots.describe` source `default`, `exists: True`).

### Verdict

**blocked.** `curl: (35) Recv failure: 连接被对方重置` on 147/155 pinned sources
(`tools/datasets.py:235` / `:301`); `prepare.py --fetch` →
`urllib.error.URLError: <urlopen error [Errno 104] Connection reset by peer>`
(`prepare.py:29`).
**needs:** stable egress to `raw.githubusercontent.com` (the eight files that landed show
the host is intermittent, not permanently gone), plus a configured judge
(`judge_configured: false`, `gpt-4o-mini-2024-07-18`) before any control arm can run.

## Cross-cutting findings

1. **One blocked host explains two blocked datasets.** Every failing URL in
   memoryagentbench and strongreject is `raw.githubusercontent.com`; `hf-mirror.com`
   (Hugging Face data) and `github.com` (git clone) both work. The platform's default
   `--endpoint https://hf-mirror.com` is the right choice on this host.
2. **A failed `--fetch` leaves a destination directory behind.** `_download` calls
   `target.parent.mkdir(parents=True, exist_ok=True)` before the transfer, so a failure on
   the first file of an entry leaves an empty `dst` directory. For kumo that directory is
   `upstream/`, and `prepare.py:147` treats it as a completed checkout
   (`if args.fetch and not (root / "upstream").exists()`). Evidence: the 0.05 s
   `FileNotFoundError … .gitignore` above, cleared by removing a zero-file directory.
   The same shape would hit any dataset whose `prepare.py` uses existence as a
   checkout guard.
3. **`memoryagentbench` has a dependency gate behind the network gate.** `nltk`,
   `regex` and `tiktoken` are all pinned to versions this environment does not have
   (`lib/inputs.py:45`). This is `needs:` a pip install, not a data defect.
4. **No substitution was made.** The one local `punkt_tab.zip` was rejected on digest
   rather than used; no smaller file, regenerated attack or proxy classifier replaced a
   pinned byte; no score was invented where a control could not run.
5. **hf-mirror rate-limits many-file downloads.** `personamem_v2`'s single `hf-dataset`
   entry is 1,002 files; the mirror returns `429` mid-entry and `--fetch` aborts the whole
   entry on the first failure. Present files are sha256-skipped on a re-run, so progress
   is resumable, but each 429 costs one pass. See the PersonaMem section for the exact
   counts.

## DATASETS.md regeneration (not committed)

```bash
$ python3 tools/datasets_doc.py --write
wrote DATASETS.md (39 dataset(s))
$ python3 tools/datasets_doc.py --check
DATASETS.md matches the adapters          # exit 0
```

`git diff --stat DATASETS.md`: `1 file changed, 4 insertions(+), 4 deletions(-)`.

Every changed **table cell** is in the `provisioned here` column:

| row | before | after | cause |
| --- | --- | --- | --- |
| `kumo` | `no` | `yes` | provisioned by this run |
| `personamem_v2` | `no` | `yes` | provisioned by this run |
| `costbench` | `no` | `yes` | **not this run** — a 3.1 GB `~/.local/share/harnessgrad/datasets/costbench` tree (2,293 tasks) with mtime `2026-11-02 21:55`, i.e. it appeared while these four were being provisioned, consistent with a concurrent task writing the shared default root |

Diff **outside** the `provisioned here` column: exactly one line — the derived
"At a glance" summary, `28 provisioned here, 8 not` → `31 provisioned here, 5 not`.
No other column (`official metric`, `known deviation`, `oracle source`, `control arm`,
`metric test(s)`, `refused`) changed. `DATASETS.md` is left modified in the worktree and
**not committed**, as the task requires.

Note for whoever merges: the `provisioned here` column is a fact about the *host*, so the
`costbench` flip will follow whichever machine regenerates the file. It is not a code or
declaration change.

## Repository state

- Report file: `reports/provisioning-large-2026-11-02.md` (the only committed file).
- Commit: the single commit on `hg-t12` that adds this file, parent `9d79ea0`. Its hash is
  reported in the hand-off message; in this worktree it is `git rev-parse HEAD` (a commit
  cannot contain its own hash).
- No dataset bytes in the worktree; no adapter, `DOWNLOADS.md`, `tools/*` or test change.

## Addendum (merged 2026-11-02, after this report was written)

The `provisioned here` column this report's diff refers to was replaced by a `provision`
column (commit `11826e6`) before the report was merged, and the note above ("a fact about the
*host*, so the `costbench` flip will follow whichever machine regenerates the file") is
exactly why: `python3 tools/datasets_doc.py --check` no longer reads the data root at all, and
the host answer is printed by `--host` instead of committed. The verdicts are unchanged:
`kumo` and `personamem_v2` are usable (oracle 1.0, no-op 0.0 — PersonaMem's no-op answer is
`incomplete_episode`, its own pass condition, not a measured zero), `memoryagentbench` and
`strongreject` are blocked on `raw.githubusercontent.com` (reset on this host) and, for
`memoryagentbench`, on a pinned NLTK set (`nltk 3.10.3` / `regex 2026.9.29` / `tiktoken
0.14.0`) that is installable but not installed here. `HG_GH_RAW_MIRROR` (commit `a5f6b37`)
lifts the first blocker wherever a GitHub-raw proxy is available.
