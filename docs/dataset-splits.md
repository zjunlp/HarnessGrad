# Dataset divisions: declared, or none

The platform runs two sides, `train` and `eval` (INTERFACE.md §2.3), and **an adapter
never invents the division**. It declares the benchmark's own where the benchmark states
one, and declares nothing where it does not. This document is the state of every dataset
in one table, and it is generated from the adapters by
`python3 tools/split_inventory.py --write`; `tests/quality/test_split_inventory.py` fails
if it drifts.

Only what an adapter **declares** is in the table. How many task ids a side happens to
hold is *not* a declaration -- `SPLIT` is computed from the pool in most adapters -- so
these cells say `unknown (computed)` and the numbers are printed by
`python3 tools/split_inventory.py --host`, which answers for one machine and is never
written into this file.

## The four states

| `state` | what it means |
| --- | --- |
| `declared` | `SPLIT_SOURCE` is `"declared"`: the benchmark publishes both sides and the adapter says which is which. A run's `--split declared` and `--split official` are then the same division, and `OFFICIAL_EVAL` is the scored side's ids. |
| `none` | the adapter declares no division: `SPLIT_SOURCE` is `"none"`, or a prose source (the authored bundle), or absent with no `SPLIT` at all (`locomo`). A run covers every task, and `--split default` (the platform's hash rule) or `--split <file.json>` is how a division is supplied. |
| `undeclared` | the adapter answers no `SPLIT_SOURCE` and builds `SPLIT` from its payload, so whether it divides its pool is not a fact this file can state and it says so instead of guessing (`--host` prints what the payload resolves to). The nine upstream research adapters are here (`browsecomp`, `deepsearchqa`, `harvey_lab`, `hle`, `livemathbench`, `swe_bench`, `travelplanner`, `workbench`, `widesearch`): their split membership is read from a prepared manifest, so declaring `SPLIT_SOURCE` would be a claim about a payload. The marker is not evidence that the dataset is undivided. |
| `refused` | `REFUSED` is set: this benchmark cannot be measured as released, so its adapter refuses it and `load()` raises. There is no division to report -- `docs/refusal-policy.md` says why for each one. |

## What is not in this file

`docs/dataset-splits.md` is committed and read on machines that are not the one that
rendered it, so a committed cell describes the **repository**. A size does not:

* `train` / `eval` under a declared division are `unknown (computed)`. `SPLIT` is not a
  literal in those adapters -- `data/scaffolds/qa/base.py:QASpec.build()` derives both
  sides from the task records it reads (`gsm8k`, `triviaqa`), `appworld` reads
  `pool.jsonl`, `costbench`/`costbench_dynamic`/`personamem_v2` read a prepared manifest,
  and `tau3_bench` extends its train side with ids from its roster. The number therefore
  answers "how many tasks are on this disk", and moves when a payload is provisioned or
  removed or an `HG_*_POOL` / study-file knob changes. `tau_bench` is the case the tool
  cannot tell apart from those: it computes its sides from a roster **committed** under
  `data/datasets/tau_bench/`, so its 495/139 are reproducible from the repository alone --
  but a computed size *may* be a payload fact, and `--host` prints the number either way.
* the `tasks` count, whether a dataset's payload can be read at all, and the exception it
  raises when it cannot, are host facts too, and they live in `--host` as well.

**`unknown (computed)` and `-` mean "not known here", never a measured zero.**
`unknown (computed)` is a value the adapter derives from its pool, which this file may not
turn into a number; `-` is a question that does not apply -- a refusal reports no division,
and a dataset that declares none has no sides to size. A zero is a score, and a score only
ever appears in a run receipt.

`python3 tools/split_inventory.py --host` prints that other view -- per dataset the task
count, the payload-resolved state, both side sizes and the exact exception -- and
`--write`/`--check` refuse to combine with it. It is a point-in-time answer and it is
expected to go stale: measured 2026-11-02, this checkout's committed file still called
`cooperbench`, `costbench`, `kumo` and `personamem_v2` unprovisioned after all four had
been provisioned, while a fresh clone failed `--check` for every dataset.

The one environment fact a row does depend on is the run configuration the adapter reads
at import: `alfworld` and `scienceworld` declare a division only when a study file or pool
is supplied (`HG_ALFWORLD_STUDY_IDS`, `HG_SCIENCEWORLD_POOL`), and this table is rendered
for the default configuration. Set one of those knobs and `--check` says the file is
stale, which is the honest answer -- the declarations really did change.

## `OFFICIAL_EVAL`: what the pool is

`OFFICIAL_EVAL` says what the pool *is*, and it is required of every runnable dataset:

* `None` -- nothing in the pool is study material: the pool **is** the benchmark's scored
  split (its test, dev or validation file, or the whole of a release that publishes no
  division). `--side eval --split official` scores the whole pool, with no study material
  in it at all.
* `[]` -- the pool is **not** part of the benchmark's evaluation set either (Reasoning
  Gym's generated tasks). `--split official` has nothing to name and is refused.
* the ids -- the scored side of a declared division. Those ids are a size, so the cell is
  `unknown (computed)` here and the count is in `--host`; the single exception is
  `tau3_bench`, which states `None` literally (it reports every task, there is no held-out
  set to name).

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
rather than the silent default of a third of the datasets. `strongreject` used to fill
`SPLIT` with a hash third of its own; since PR 31 it declares no division at all (`none`) and
supplies `SPLIT_GROUPS` instead, so the choice is explicit and the three attack variants of one
original request cannot land on opposite sides -- see the next section.

## Optional groups for related tasks

An adapter may declare private `SPLIT_GROUPS = {task_id: sha256_group_id}`. The
mapping covers every task, and each group id is a lowercase SHA-256 hex digest
of the adapter's stable grouping key. This declaration creates no division and
adds nothing to actor tasks. `--split default` applies the first-byte modulo-3
rule directly to the digest (zero selects eval); it does not hash the digest a
second time. Declared, default, official and supplied JSON divisions must keep
every group on one side. Invalid mappings or groups crossing sides are refused
before task selection or execution. Adapters without `SPLIT_GROUPS` keep the
existing task-id hash rule and validation.

StrongREJECT uses each original request as its grouping key, so its three
attack variants stay together. It declares no native split. On its frozen
150-task Harbor pool, an explicit `--split default` yields 99 train tasks
(33 originals) and 51 eval tasks (17 originals); `--side eval --split official`
uses all 150 tasks.

## The table

<!-- split-inventory:begin -->

| dataset | state | split_source | train | eval | OFFICIAL_EVAL |
| --- | --- | --- | --- | --- | --- |
| `agentdojo` | none | none | - | - | None (the pool is the scored split) |
| `aime` | none | none | - | - | None (the pool is the scored split) |
| `alfworld` | none | none | - | - | None (the pool is the scored split) |
| `appworld` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `authored_example_fix_bug` | none | authored bundle: every task in the bundle is scored | - | - | None (the pool is the scored split) |
| `bbeh` | none | none | - | - | None (the pool is the scored split) |
| `bfcl_v3` | none | none | - | - | None (the pool is the scored split) |
| `bird_minidev` | none | none | - | - | None (the pool is the scored split) |
| `browsecomp` | undeclared | absent | - | - | absent |
| `chessbench_puzzles` | none | none | - | - | None (the pool is the scored split) |
| `cooperbench` | none | none | - | - | None (the pool is the scored split) |
| `costbench` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `costbench_dynamic` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `deepsearchqa` | undeclared | absent | - | - | absent |
| `deepswe` | none | none | - | - | None (the pool is the scored split) |
| `ds1000` | none | none | - | - | None (the pool is the scored split) |
| `fever` | none | none | - | - | None (the pool is the scored split) |
| `gaia` | none | none | - | - | None (the pool is the scored split) |
| `gsm8k` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `harvey_lab` | undeclared | absent | - | - | absent |
| `hle` | undeclared | absent | - | - | absent |
| `hotpotqa` | none | none | - | - | None (the pool is the scored split) |
| `jericho` | none | none | - | - | None (the pool is the scored split) |
| `jobbench` | none | none | - | - | None (the pool is the scored split) |
| `kumo` | none | none | - | - | None (the pool is the scored split) |
| `livecodebench` | none | none | - | - | None (the pool is the scored split) |
| `livemathbench` | undeclared | absent | - | - | absent |
| `locomo` | none | absent | - | - | None (the pool is the scored split) |
| `math` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `math500` | none | none | - | - | None (the pool is the scored split) |
| `memoryagentbench` | none | none | - | - | None (the pool is the scored split) |
| `musique` | none | none | - | - | None (the pool is the scored split) |
| `nq` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `officeqa_pro` | none | none | - | - | None (the pool is the scored split) |
| `personamem_v2` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `physicsquestions` | none | none | - | - | None (the pool is the scored split) |
| `reasoning_gym` | none | none | - | - | [] (not an evaluation set) |
| `scienceworld` | none | none | - | - | None (the pool is the scored split) |
| `searchqa` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `sportsunderstanding` | none | none | - | - | None (the pool is the scored split) |
| `spreadsheetbench` | none | none | - | - | None (the pool is the scored split) |
| `spreadsheetbench2` | none | none | - | - | None (the pool is the scored split) |
| `strategyqa` | none | none | - | - | None (the pool is the scored split) |
| `strongreject` | none | none | - | - | None (the pool is the scored split) |
| `supergpqa` | none | none | - | - | None (the pool is the scored split) |
| `swe_bench` | undeclared | absent | - | - | absent |
| `swe_bench_pro` | none | none | - | - | None (the pool is the scored split) |
| `tau2_bench` | refused | - | - | - | - |
| `tau3_bench` | declared | declared | unknown (computed) | unknown (computed) | None |
| `tau_bench` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `terminal_bench` | none | none | - | - | None (the pool is the scored split) |
| `travelplanner` | undeclared | absent | - | - | absent |
| `triviaqa` | declared | declared | unknown (computed) | unknown (computed) | unknown (computed) |
| `twowikimultihopqa` | none | none | - | - | None (the pool is the scored split) |
| `widesearch` | undeclared | absent | - | - | absent |
| `workbench` | undeclared | absent | - | - | absent |

<!-- split-inventory:end -->

## What is left

Nothing: every published dataset either declares the benchmark's division, declares none,
or is refused. Counted from the table above (59 rows): **11 declared, 35 none, 9 undeclared,
4 refused** -- the numbers in this paragraph are a reading of the table, and the table is the
authority. The `none` group is the majority because most of these adapters load one side of
their benchmark (a test, dev or validation file), and a table that *is* the evaluation set has
no division to declare -- saying otherwise would call a third of an exam "study material",
which is what the hash rule did until 2026-10-24.

The 11 that declare a real division do so because their benchmark publishes both sides:
`gsm8k`/`triviaqa`/`math`/`nq`/`searchqa` (the official train/test files of a QA release, with a
sample of it for TriviaQA), `appworld` (train studies, dev selects, test is the exam -- via
`HG_APPWORLD_POOL`), `tau_bench` and `tau3_bench` (the official retail/telecom train files
against the test files), `costbench`/`costbench_dynamic` and `personamem_v2` (a `train`/`test`
label in the prepared manifest). They compute their sides from the adapter's pool instead of
declaring them literally, which is why their sizes are `unknown (computed)` in the table and
printed by `--host`; `tau_bench`'s pool is committed to the repository (see above).

Four adapters are refused (`api_bank`, `nestful`, `sotuqa`, `tau2_bench`). The 9 `undeclared`
are the upstream research adapters, whose split membership is read from a prepared manifest --
`strongreject` used to be one of them and now declares no division at all plus `SPLIT_GROUPS`
(see the section above). `alfworld` and `scienceworld` declare a division **only when** a study
file or pool is supplied (`HG_ALFWORLD_STUDY_IDS`, `HG_SCIENCEWORLD_POOL`); without it they are
the same as the rest.
