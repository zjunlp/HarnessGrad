# Registry discovery order is observable

Current-layout note: this is a historical audit of the named baseline. After
Stages C/D, shared paths map to `data/scaffolds/` and adapter/asset paths map to
`data/datasets/`; baseline spellings below are retained as audit evidence.
The split report records the current verification.


Audit of checkout `b9ed453`, answering `docs/data-split-plan.md` section 7.
No files were moved and the split plan was not edited.

**Verdict: the sequence returned by `data.registry.published()` is externally
observable.** Several consumers preserve it when printing or serializing lists.
The filesystem's *raw enumeration order* is not observable through those lists:
the registry sorts paths **before importing**, rather than sorting a completed
output list. A move that preserves the names, successful imports and this sorted
sequence preserves these outputs; a move that changes the returned sequence does
not. Sorting JSON object keys does not sort JSON arrays.

## Discovery, exactly

`data/registry.py:43-60`, `published()`:

```text
Path(__file__).resolve().parent.glob("*.py")   # direct siblings, not recursive
  -> sorted(paths)                           # path/name order in one directory
  -> skip stems "registry" and "__init__"
  -> kind_of(stem)
       -> importlib.import_module(f"data.{name}")
       -> module.KIND, default "fixture"
       -> unrecognized KIND becomes "fixture"
  -> append stem only when KIND == "dataset"
       (any Exception from kind_of silently omits that candidate)
  -> tuple(out)                              # no subsequent shuffle or sort
```

There is no explicit discovery list, recursive search, default dataset selection,
or task loading in `published()` itself. Importing candidates does execute their
module initialization; a subsequent call repeats the scan but Python normally
caches imported modules. Registry loading functions import a dataset **by its
supplied name**, independently of discovery.

## Chains to observable outputs

Every chain in this table begins at the discovery call above. These are the
production consumers found outside the excluded paths.

| Consumer and chain from `published()` | Observable dependency |
| --- | --- |
| `tools/datasets.py:78`, `datasets_with_downloads()` iterates names, imports each named module, appends `(name, downloads)` when `downloads` is callable; `main():406-470` retains that order, including when filtering `--dataset` | Default / `--list` text blocks and JSON array order; `--check` text rows and JSON `datasets` array; `--fetch` dataset execution/log block order. Per-dataset entry order is the adapter's own order. |
| Same rows -> `tools/datasets.py:334`, `render_index()` loops rows -> `main()` `--write` writes `DOWNLOADS.md` | Written dataset section order. The printed total `len(rows)` is independent of a permutation of the same rows. |
| `tools/dataset_lint.py:422`, `published_datasets()` converts the tuple to a list -> `main()` uses it unless explicit `--dataset` arguments are supplied -> results comprehension -> print loop / JSON list | Default lint execution order, text result blocks and JSON result array order. Explicit dataset arguments use argument order instead. The aggregate exit status is independent of result order. |
| `tools/dataset_classes.py:546`, `classify_all()` builds rows in registry order -> `select()` copies/filters without sorting -> `main()` prints JSON or `_table()` | Taxonomy JSON array, text dataset rows, and dataset-name lists in the closed-loop / single-shot / function / adversarial summaries. Numeric counts and group membership do not change under permutation. Demand/function group ordering has its own rules. |
| `tools/serve_ui.py:216`, `scan_options()` iterates registry names -> imports and loads each -> appends dataset dictionaries (silently omits failures) -> return `datasets` -> `Handler` `/api/options` at line 1656 -> JSON response | Operations API `datasets` array order. A dictionary's `count` is `len(tasks)` and its `sample` uses that adapter's `tasks[0]`; discovery order changes their positions, not their values. Startup at line 1708 prints `len(opts['datasets'])`, which is unchanged by a permutation with the same successful loads. |

Two further consumers explicitly erase registry order:

* `tools/task_difficulty.py:222`: `set(registry.published())` -> membership filter
  over state datasets -> `sorted(...)` -> printed summaries / per-dataset JSON
  writes. An explicit `--dataset` instead makes a singleton list. Registry order
  does not determine output or write order here.
* `tools/test_scope.py:67`: `sorted(registry.published())` -> `scope_map()` sets
  -> `select()` returns sorted test filenames -> `tools/run_tests.py:219-285`
  selection and printed plan. Discovery order does not choose a dataset or test
  here. This audit did not execute the runner or any additional test files.

The registry docstring mentions the Launch form/read API as consumers, but the
current SPA uses a separate path. `web/server/launch.py:218`, `dataset_names()`
AST-reads a zero-argument top-level `load()` declaration in `data/*.py` and sorts
names; `build_launch_options():298` -> `web/server/serve.py:152`
`GET /api/launch/options` -> `web/src/lib/api.ts:477` ->
`web/src/pages/launch.tsx:73,136` maps the array to choices. It does **not** call
`registry.published()`, and its membership rule differs from `KIND`. The fixture
builder `web/scripts/build_fixtures.py:409` also globs and sorts independently.
Those discovery roots must also be updated during a move, but their output order
is not evidence that registry order is hidden.

## Default choices, task order, splits and records

These chains do **not** start at `published()`:

```text
driver.py:255 --dataset (default "demo")
  -> registry.load_split(args.dataset) (:451)
       -> load(name) -> import data.<name> -> module.load()
       -> that same module's SPLIT -> validate_split()
  -> optional --split default / official / file (:466-502)
  -> side_ids from split[side], or adapter task order (:519)
  -> explicit --tasks filter / diagnostic or pilot selection (:525-548)
  -> selected tasks, not the registry catalog
```

`registry.default_split():167` hashes each **task ID**, preserves input order
within each side, and never discovers datasets. `official_split():208` uses the
named module's `OFFICIAL_EVAL` (or all of that module's task IDs when it is
`None`). `split_sha():144` sorts task IDs before hashing. `load_tasks():708` and
`load_envs():550` also load by name without calling `published()`.

The three allowed adapters that import the registry use this task-level rule:
`data/bird_minidev.py:310` passes `_ids()` to `default_split()`;
`data/livecodebench.py:289` passes task IDs from `_INDEXED`;
`data/gaia.py:247` lazily passes IDs from its own rows. None of these calls uses
the catalog sequence. Importing the registry is not itself a discovery call.

The operations task picker is also name-based:
`tools/serve_ui.py:1049 task_options(dataset)` -> registry `load`, `load_split`,
`load_envs` -> list comprehension over that dataset's tasks. Its `start_job():296`
defaults to literal `"demo"`; the SPA initializes its dataset to `"demo"`
(`web/src/pages/launch.tsx:44`). Neither selects `published()[0]`.
`data/command_control.py:63 pick()` can select the first eval task or first task
of a supplied module; that is adapter task/split order, not catalog order.

Record and receipt inputs visible in the driver are:

```text
named dataset + resolved split + side_ids/eligible_ids
  -> driver.py:592 diagnostic_policy.build_receipt(
       eligible=eligible_ids, selected=side_ids, context=policy_evidence["context"])
  -> :598 write_receipt(staged_plan.json / diagnostic_receipt.json)

args.dataset + resolved split + selected IDs
  -> driver.py:954 _write_run_meta(dataset=..., split=..., split_source=...,
       split_sha=..., diagnostic_policy={selected_task_ids: ..., ...})
  -> harnessgrad/records.py:402 -> run_meta.json

selected task_ids -> driver.py:1154 sampling dictionary -> curve/state records
```

No discovered catalog tuple is passed into these record/receipt calls. This
establishes no direct registry-order dependency in their inputs, rather than a
claim about every implementation detail of the excluded receipt machinery.
`tools/import_env.py:226`, `tools/configure_harness.py:86`,
`tools/task_sensitivity.py:294`, `tools/validate_method.py:68`, and
`harnessgrad/environments.py` likewise use named loaders or registry constants,
not `published()`, so they add no catalog-order chain. The legacy domain adapter
at `legacy/adapters/harnessgrad_domain/adapter.py:107,117` calls `load` and
`load_split` with its configured dataset (default `"probe_set"`); it also does
not discover a default from the catalog.

## Regression evidence and limits

`tests/quality/test_registry_discovery_order.py` checks a literal 28-name catalog
expectation against the real registry scan, using both the actual glob iteration
and its reverse. It statically reads allowed literal `KIND` declarations instead
of importing adapters. The excluded adapter's name is an explicit catalog
assumption from the existing inventory; its source and assets are never opened.
A synthetic import test pins sorted import order, fixture filtering, skipped
infrastructure names and omission of import failures. End-to-end discovery to
`--list` text/JSON and default lint JSON tests use synthetic download/lint work
and check the same literal ordered expectation. No downloads or benchmark runs
are performed. These are order tests, not dataset-readiness or H0 authorization.

Validation ran only these individual files with bytecode writes disabled:

```text
python -m pytest -q tests/quality/test_registry_discovery_order.py \
  --basetemp=/mnt/20t/xubuqiang/.hg-pytest/t2/registry-discovery-order
6 passed in 0.32s

python -m pytest -q tests/quality/test_new_dataset.py \
  --basetemp=/mnt/20t/xubuqiang/.hg-pytest/t2/new-dataset
28 passed in 10.96s
```

For the existing generator file, a temporary `sitecustomize.py` installed an
import finder rejecting `data.agentdojo` before resolution and an audit hook
rejecting opens of its source/assets. The guard was inherited by subprocesses;
the parent reported three blocked discovery imports. The temporary guard was
removed after execution. Discovery omitted the adapter under its normal
exception handling. This checks the generator's promises with the explicit
scope restriction, not unrestricted discovery availability. An initial run of
that same file had 27 passes and one fixture-setup error because the requested
basetemp parent did not exist. Creating the parent and rerunning resolved it.

What remains undetermined:

* The excluded adapter's declaration, initialization behavior and runtime
  availability were not independently verified. An early broad `rg` search put
  include globs after exclusion globs, accidentally re-including excluded paths
  in the search. This violated the read restriction; it was disclosed and the
  search pattern corrected. The evidence chains above use the allowed sources;
  the new tests never open or import that adapter, and the existing generator
  file ran with the guard described above. No excluded file was edited.
* No implementation under `eval/` was deliberately audited or changed. Receipt input chains
  stop at the driver call boundary; the requested existing generator tests can
  exercise the platform normally.
* Arbitrary import-time side effects, dependencies on warm module caches,
  environment changes, or failing imports can change availability. Sorting
  removes glob-order variation; it is not proof that imports are side-effect
  free. Actual full-catalog imports were deliberately avoided.
* External repositories, dynamically constructed consumers and deployed clients
  are outside this tree audit. They may use the returned tuple directly, whose
  order is already a public Python observation.
* A future move can also change paths, hashes or import identity independently
  of catalog order. This finding only closes the discovery-order question.
