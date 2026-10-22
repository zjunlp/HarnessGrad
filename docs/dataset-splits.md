# Dataset divisions: declared, or none

The platform runs two sides, `train` and `eval` (INTERFACE.md §2.3), and **an adapter
never invents the division**. It declares the benchmark's own where the benchmark states
one, and declares nothing where it does not. This document is the state of every dataset
in one table, and it is generated from the adapters by
`python3 tools/split_inventory.py --write`; `tests/quality/test_split_inventory.py` fails
if it drifts.

## The three states

| `SPLIT` | `SPLIT_SOURCE` | what it means |
| --- | --- | --- |
| a dict with both sides | `"declared"` | the benchmark publishes both sides; the adapter says which is which, and `OFFICIAL_EVAL` is the scored side's ids |
| `None` | `"none"` | the adapter loads **one** side and declares no division; a run covers every task, and `--split default` (the platform's hash rule) or `--split <file.json>` is how a division is supplied |
| absent | absent | the adapter cannot answer without a payload that is not on this host; `load()` names the root |

`OFFICIAL_EVAL` says what the pool *is*, and it is required of every runnable dataset:

* `None` — nothing in the pool is study material: the pool **is** the benchmark's scored
  split (its test, dev or validation file, or the whole of a release that publishes no
  division). `--side eval --split official` scores the whole pool, with no study material
  in it at all.
* `[]` — the pool is **not** part of the benchmark's evaluation set either (Reasoning
  Gym's generated tasks). `--split official` has nothing to name and is refused.
* the ids — the scored side of a declared division.

## Why the hash third is gone

Every adapter below with `split_source: none` used to fill `SPLIT` with
`data/scaffolds/registry.py::default_split` — a deterministic third of the pool, by task-id
hash. For a benchmark whose table is its test or dev file, that meant:

* a third of an **evaluation** set was relabelled "study material", and the method studied
  it;
* the reported score was the other two thirds, so the number was neither the benchmark's
  nor a held-out estimate of anything it publishes;
* and because the hash is over task ids, adding a task to the pool moved the division.

The operator's decision on 2026-10-24: official division where the benchmark has one, no
division where it does not. `--split default` still gives a run exactly that hash rule, and
the run record says `split_source: default` when it does — it is now an explicit choice
rather than the silent default of a third of the datasets.

## The table

<!-- split-inventory:begin -->

| dataset | state | split_source | tasks | train | eval | OFFICIAL_EVAL |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `agentdojo` | none | none | 949 | - | - | None (the pool is the scored split) |
| `aime` | none | none | 60 | - | - | None (the pool is the scored split) |
| `alfworld` | none | none | 134 | - | - | None (the pool is the scored split) |
| `api_bank` | refused | - | - | - | - | - |
| `appworld` | declared | declared | 675 | 90 | 585 | 585 |
| `bfcl_v3` | none | none | 640 | - | - | None (the pool is the scored split) |
| `bird_minidev` | none | none | 500 | - | - | None (the pool is the scored split) |
| `chessbench_puzzles` | none | none | 10000 | - | - | None (the pool is the scored split) |
| `cooperbench` | not provisioned | - | - | - | - | - |
| `costbench` | not provisioned | - | - | - | - | - |
| `costbench_dynamic` | not provisioned | - | - | - | - | - |
| `ds1000` | none | none | 926 | - | - | None (the pool is the scored split) |
| `fever` | none | none | 15935 | - | - | None (the pool is the scored split) |
| `gaia` | none | none | 165 | - | - | None (the pool is the scored split) |
| `gsm8k` | declared | declared | 8792 | 7473 | 1319 | 1319 |
| `hotpotqa` | none | none | 7405 | - | - | None (the pool is the scored split) |
| `jericho` | none | none | 56 | - | - | None (the pool is the scored split) |
| `kumo` | not provisioned | - | - | - | - | - |
| `livecodebench` | none | none | 1055 | - | - | None (the pool is the scored split) |
| `locomo` | none | absent | 10 | - | - | None (the pool is the scored split) |
| `memoryagentbench` | not provisioned | - | - | - | - | - |
| `musique` | none | none | 2417 | - | - | None (the pool is the scored split) |
| `nestful` | refused | - | - | - | - | - |
| `personamem_v2` | not provisioned | - | - | - | - | - |
| `physicsquestions` | none | none | 53 | - | - | None (the pool is the scored split) |
| `reasoning_gym` | none | none | 1780 | - | - | [] (not an evaluation set) |
| `scienceworld` | none | none | 1744 | - | - | None (the pool is the scored split) |
| `sotuqa` | refused | - | - | - | - | - |
| `sportsunderstanding` | none | none | 1000 | - | - | None (the pool is the scored split) |
| `spreadsheetbench` | none | none | 400 | - | - | None (the pool is the scored split) |
| `strategyqa` | none | none | 2290 | - | - | None (the pool is the scored split) |
| `strongreject` | not provisioned | - | - | - | - | - |
| `tau2_bench` | refused | - | - | - | - | - |
| `tau3_bench` | declared | declared | 375 | 275 | 100 | None |
| `tau_bench` | declared | declared | 634 | 495 | 139 | 139 |
| `terminal_bench` | none | none | 89 | - | - | None (the pool is the scored split) |
| `triviaqa` | declared | declared | 19944 | 2000 | 17944 | 17944 |
| `twowikimultihopqa` | none | none | 12576 | - | - | None (the pool is the scored split) |

Not provisioned on this host, with the root each one named:

* `cooperbench` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/cooperbench/prepared-v1/manifest.json'
* `costbench` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/costbench/prepared-v1/manifest.json'
* `costbench_dynamic` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/costbench/dynamic-prepared-v1/manifest.json'
* `kumo` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/kumo/prepared-v1/manifest.json'
* `memoryagentbench` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/memoryagentbench/prepared/manifest.json'
* `personamem_v2` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/personamem_v2/prepared/manifest.json'
* `strongreject` -- FileNotFoundError: [Errno 2] No such file or directory: '/mnt/20t/xubuqiang/datasets/strongreject/native-v1/upstream-dataset.csv'

<!-- split-inventory:end -->

## What is left

Nothing: every published dataset now either declares the benchmark's division or declares
none. The second group is the majority because most of these adapters load one side of
their benchmark (a test, dev or validation file), and a table that *is* the evaluation set
has no division to declare -- saying otherwise would call a third of an exam "study
material", which is what the hash rule did until 2026-10-24.

Two adapters keep a real division because their benchmark publishes both sides:
`gsm8k`/`triviaqa` (the official train split, a sample of it for TriviaQA) and
`appworld` (train studies, dev selects, test is the exam -- via `HG_APPWORLD_POOL`).
`alfworld` and `scienceworld` declare one **only when** a study file or pool is supplied
(`HG_ALFWORLD_STUDY_IDS`, `HG_SCIENCEWORLD_POOL`); without it they are the same as the
rest.
