# BFCL fabricated differential and source evidence

This packet exercises retained AST function slices with fabricated inputs. It is
not an official BFCL scorer entrypoint, a benchmark measurement, source approval,
expression containment or production admission. The retained upstream BinOp
decoder still executes the in-memory canary. `UNCONTAINED_STOP`,
`source_gate_satisfied: false` and `official_scorer_executed: false` remain in the
observations.

## Storage migration review (2026-11-03)

This storage integration was reviewed against `df9e5108` and is now rebased
onto `d9233ca7`. The review covers explicit storage configuration and trusted
scorer imports. The reviewed changes are:

- `data/datasets/bfcl_v3.py:97` uses the portable root resolver instead of the
  legacy host path. The ASTs of `bare_name`, `gold_names`, `parse_prediction` and
  `score` are identical to the pinned baseline, ignoring source positions.
  Their current definitions begin at `data/datasets/bfcl_v3.py:176`,
  `data/datasets/bfcl_v3.py:193`, `data/datasets/bfcl_v3.py:208` and
  `data/datasets/bfcl_v3.py:233`.
- `data/scaffolds/qa/base.py:388` launches Python with `-I` before startup.
  `data/scaffolds/qa/base.py:417` supplies the three trusted `harnessgrad` helper
  files. No candidate `.env` is declared as a trusted input.
- `data/scaffolds/qa/verify/check.py:44` refuses non-isolated script execution
  before importing dependencies. The fixed source map at
  `data/scaffolds/qa/verify/check.py:66` imports only explicitly restored package
  files, with empty package search paths and no candidate directory in sys.path.
  `data/scaffolds/qa/verify/check.py:60` compiles source directly, ignoring
  candidate bytecode caches. The selected adapter is added at
  `data/scaffolds/qa/verify/check.py:287` only after dataset-name validation.
  `scorer_paths()` still surrounds the entire scoring call. Existing scoring
  dispatch expressions and review conclusions are retained at their new lines.
- `harnessgrad/paths.py:26` supplies the explicit scoring context. It bypasses
  environment/configuration and checkout discovery, uses a trusted source file
  as a non-directory corpus prefix, and restores the previous context on exit.
  This was separately verified by real-sandbox QA controls with candidate `.env`
  and forged worktree `.git` files; these are synthetic isolation controls.

The inventory now includes the four previously unlisted storage helpers copied
by the verifier: `data/scaffolds/roots.py`, `harnessgrad/__init__.py`,
`harnessgrad/paths.py` and `harnessgrad/env_file.py`. The scope comprises **66
source files**, and `sinks.jsonl` and `boundary-review.jsonl` contain **43 paired
sites**, including the two trusted-source loader sites. The lazy `getattr` at
`harnessgrad/__init__.py:46` is restricted by the
three literal runner-export names; the fabricated BFCL slices do not exercise
that runner path, so its control state stays `UNKNOWN`.

The storage policy now requires an explicit shared or per-dataset root. An
unconfigured `DatasetPath` is only a declaration: joining it does not choose a
directory, while filesystem conversion/access raises an actionable configuration
error. Its method delegation adds one reviewed `getattr` site. Staged scoring
uses ordinary inert paths inside `scorer_paths()` and never resolves host storage.
Root diagnostics delegate to the same resolver, with no legacy or user-directory
fallback. These changes do not alter BFCL scoring functions or acquired sources.

The original 19 fabricated differential cases and nine boundary controls are
replayed against the refreshed source pins. Their observations must match the
retained packet, including the upstream uncontained-expression finding. Existing
acquired source snapshots, cases, observations and historical registry proposals
are preserved.

The current pins include the QA module's documentation edit and the
`HG_REVIEWED_ENVIRONMENT_ONLY` setting. With that setting enabled, storage
configuration comes from the process environment and skips `.env` discovery.
The launcher is responsible for selecting those variables. The trusted scoring
context still takes precedence. The 43 reviewed call sites are unchanged apart
from the line of the `getattr` in `DatasetPath.__getattr__`.

## Validation

Run from the repository root:

```sh
python -m pytest tests/quality/test_bfclcontain_differential.py \
  tests/quality/test_dataset_roots.py::test_the_bfclcontain_inventory_row_for_the_migrated_adapter_matches_disk
python tests/fixtures/hg_bfclcontain/control.py
```

`control.inventory()` reproduces the inventory and sink frontier. The tests keep
exact file-size/hash, sink, review and observation assertions, and also require
every BFCL verifier Python input to be inventoried. `load_slice()` still checks
the retained whole-file hash before compiling any selected node. The tests also
retain the recorded source-gate refusal and absence of official scorer execution.
