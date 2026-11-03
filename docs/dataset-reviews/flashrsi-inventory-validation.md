# FlashRSI inventory validation

Comparison: HarnessGrad base `a100139` versus the current datasets worktree;
FlashRSI source `e9c7667945f959d62da27204649a82f94d1b6705`.
Methods worktree: `codex/flashrsi-methods` at `10980be`.
This is a source/identity audit, not a benchmark execution or readiness claim.

## Method

Use `git ls-tree -r --name-only a100139 data/datasets` and
`git show a100139:<path>` for the starting files. Parse only top-level module
assignments with Python `ast`; count modules declaring literal `KIND = "dataset"`.
Do not count supporting directories or infer registration from a benchmark-like
filename. Parse `ImportFrom` registrations in the pinned FlashRSI
`flashrsi/benchmarks/__init__.py` and `flashrsi/algorithms/__init__.py` for its
actual exported benchmark and algorithm IDs.

For preservation checks, parse the base/current `registry/dataset-blockers.json`
with `json.loads`, key `datasets` rows by `dataset`, and compare each old row by
full Python object equality. Compare all other top-level fields independently.
Compare old aggregation-policy objects and old taxonomy dictionary entries in the
same way. Compare the 39 original dataset module texts byte-for-byte through
`git show`, rather than accepting an unchanged count as proof of preservation.

A compact command for the old dataset-object check is:

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
old = json.loads(subprocess.check_output(
    ['git', 'show', 'a100139:registry/dataset-blockers.json'], text=True))
new = json.loads(Path('registry/dataset-blockers.json').read_text())
a = {r['dataset']: r for r in old['datasets']}
b = {r['dataset']: r for r in new['datasets']}
assert all(b.get(name) == row for name, row in a.items())
assert all(new.get(k) == v for k, v in old.items() if k != 'datasets')
print('old rows:', len(a), 'new rows:', len(b))
print('old historical records:', sum(len(r['historical_runs']) for r in a.values()))
PY
```

## Observed dataset inventory

- Pinned FlashRSI exports **30 benchmark IDs**.
- **10** IDs already exist or are explicit aliases: `aime`, `alfworld`,
  `appworld`, `bfcl → bfcl_v3`, `hotpotqa`, `musique`, `tau2 → tau2_bench`,
  `terminal_bench`, `triviaqa`, `twowiki_multihopqa → twowikimultihopqa`.
  Every alias destination exists at `a100139`.
- `tau3_banking` is an already-covered source/domain subset: both projects use
  tau2-bench revision `fc0055dc4e0a316c3f83133267fbd6faaa770992`; the original HG
  375-task roster explicitly includes **97 banking_knowledge tasks**. FlashRSI's
  19 retrieval variants are protocol settings, not 19 additional benchmarks.
- `30 - 10 - 1 = 19` new benchmark IDs. The audit's `datasets` set matches this
  set exactly, with no missing or extra ID:
  `bbeh`, `browsecomp`, `deepsearchqa`, `deepswe`, `harvey_lab`, `hle`, `jobbench`,
  `livemathbench`, `math`, `nq`, `officeqa_pro`, `searchqa`, `spreadsheetbench2`,
  `supergpqa`, `swe_bench`, `swe_bench_pro`, `travelplanner`, `widesearch`, `workbench`.
- FlashRSI's `MATH` class also supports `variant="math500"` with its own pinned
  MATH-500 source. The extra HG `math500` adapter makes **20 new adapter entries**,
  while `new_dataset_count` remains **19**. `FUNCTION_COVERAGE_FAMILY` maps
  `math500` to `math`, so this variant does not manufacture another family.
- HG literal registrations are **39 at base, 59 now**. No original ID disappeared;
  all **39 original dataset modules are byte-for-byte unchanged**.

## Observed preservation checks

- All **39 original registry dataset objects compare equal**, including every
  old blocker, status and historical-run field.
- All **11 historical records** attached to those original rows remain equal;
  the new 20 rows have no fabricated historical runs.
- Top-level `review`, `schema_version` and `shared_blockers` compare equal.
  Production authorization remains `false` in the original review object.
- Every original `data/aggregation_policies.json` entry compares equal.
- Existing literal taxonomy dictionary entries compare equal; changes append the
  new adapter declarations and the `math500 → math` family relationship.

These checks establish preservation of declarations and history. They do not
prove all 59 datasets are provisioned, executable or production-authorized.

## Observed method inventory

Pinned FlashRSI exports **13 algorithm IDs**. The original HG catalogue has
16 method entries of different kinds; only `ahe`, `meta_harness` and `rrsi`
intersect the FlashRSI algorithm IDs. Therefore the native missing set is exactly
**10**, and it equals the actual 10 method IDs added on `10980be`:

`ace`, `adas`, `arbor`, `darwinx`, `evo_harness`, `evoprompt`, `gepa`,
`modular_rsi`, `opro`, `skillopt`.

No native missing method was omitted, and no unrelated new ID was counted.
The original reference entries (`a`, `b`, `echo_base`), controls (`noop`,
`raise_step_cap`), improver (`llm_improver`) and `other` entry (`lightrsi`)
are not counted as missing published FlashRSI algorithms. Existing HG published
methods outside the FlashRSI 13 remain intact.

The new methods declare `kind="published"` to register named method ports, but
their title/note/fidelity profiles identify **FlashRSI-policy-port**, not native
reproduction. Native evaluator callbacks and separate dev capabilities are
explicitly adapted or unavailable; see each `method.json` and
`methods/flashrsi_ports/README.md` in the methods worktree.
