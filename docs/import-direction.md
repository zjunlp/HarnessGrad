# Import direction

The automated guard is
[`tests/quality/test_import_direction.py`](../tests/quality/test_import_direction.py).
Run it with the quality suite or with Python alone:

```sh
python -m pytest -q tests/quality/test_import_direction.py
python tests/quality/test_import_direction.py
```

## Observed graph

The initial AST audit at `6c539d8` scanned all 221 Python modules under `data/`,
`eval/`, `harnessgrad/`, `tools/`, and `driver.py`, including package initializers,
nested functions, conditional imports, and adapter support scripts. Reproduce the
full **module -> imported modules** graph from the current source with:

```sh
python tests/quality/test_import_direction.py --graph
```

This command reads source without importing production modules, loading datasets,
starting services, or contacting model endpoints. It includes modules with no
imports. Relative imports are resolved against the importing package; imported
attributes point to their containing module, while known imported child modules
point to the child. This is a static graph of Python import statements; dynamic
imports, generated code, and runtime `sys.path` changes are outside its scope.

The distinct cross-layer module edges at that revision are summarized below.
`harnessgrad` here excludes its separately listed config/runtime packages.

| Importing layer | Imported layer (number of distinct module edges) |
| --- | --- |
| `data` | `eval` (11) |
| `eval` | `harnessgrad.config` (3), `tools` (4) |
| `harnessgrad` | `data` (1), `eval` (17), `harnessgrad.config` (4), `harnessgrad.runtime` (5), `tools` (1) |
| `harnessgrad.config` | None of the other listed layers |
| `harnessgrad.runtime` | None of the other listed layers |
| `tools` | `data` (10), `driver` (1), `eval` (14), `harnessgrad` (2), `harnessgrad.config` (1) |
| `driver` | `data` (1), `eval` (9), `harnessgrad` (8), `harnessgrad.config` (1), `tools` (3) |

The wider graph therefore has cycles today. The guard protects the boundaries
below; it does not assert that all of `harnessgrad/` is below `eval/`.

## Eval concern packages

The structural refactor from `9c928ed` first scanned the real import graph using
`ast`, printing every internal eval edge and every external importer edge. That
whole-tree scan covered 437 Python files and found 211 distinct edges targeting
`eval`, including 50 internal edges. The full outgoing production eval graph
before the move (including dependencies outside eval) is preserved in the
[audit report](../reports/eval-package-refactor-e1.md).

The graph supports seven concern packages: `eval.run`, `eval.containers`,
`eval.admission`, `eval.service`, `eval.observe`, `eval.policy`, and
`eval.verification`. In particular, observation modules consume `phases`,
`harness_runtime` consumes `container`, and `stepwise_transport` is a transport
leaf. Verification has its own package because it composes containers, services
and runtime policy rather than defining admission evidence. The package names
`containers` and `service` avoid collisions with legacy module names, allowing
identity-preserving aliases instead of state-copying package facades.

This is a concern map, not a claim of an acyclic package graph. Existing
cross-concern imports are retained. Every production importer now targets a
canonical implementation. The 32 documented old-path aliases add compatibility
edges only; package `__init__.py` files have no implementation imports. The
[architecture map](architecture.md#compatibility-import-paths) lists every shim.
The exact eleven data exceptions and three config edges below retain their
original justifications and now name the moved implementations. The guard also
checks that canonical production modules never import the compatibility paths.

## Enforced boundaries

- `harnessgrad/config/` and `harnessgrad/runtime/` cannot import `eval`, `data`,
  `tools`, or `driver`, including their submodules.
- `eval/` can import config only through the three exact module edges below.
  Additional config submodule dependencies need separate review.
- Production Python modules anywhere under `data/`, `eval/`, and `harnessgrad/`
  cannot import `legacy` or any of its submodules. The initial audit found none.
- Adapters may import `data.scaffolds.*` and their own private assets; imports of other adapters or other `data.*` modules are forbidden. The guard covers `data.datasets.*`, including private support scripts; top-level adapter shims are forbidden.
- `data/` cannot import `eval/`, except for the eleven existing edges explicitly
  recorded below. Support/control scripts are scanned too.

The permitted config edges are:

| File | Imported module | Reason |
| --- | --- | --- |
| `eval/containers/harness_runtime.py` | `harnessgrad.config` | Resolved runtime overlay configuration |
| `eval/containers/sandbox.py` | `harnessgrad.config` | Resolved sandbox configuration |
| `eval/service/services.py` | `harnessgrad.config` | Resolved service configuration |

## Existing exceptions

The `data -> eval` invariant was already violated at the audited revision. These
are documented layering debt, preserved to keep this change focused on the guard.
The structural refactor rewires those same dependencies to canonical paths.
Every exception is keyed by rule, exact file,
and exact imported module in `EXCEPTIONS`, with a reason:

| File | Imported module | Reason |
| --- | --- | --- |
| `data/datasets/alfworld/control.py` | `eval.run.runner` | Platform control validates adapter wiring through `run_one` |
| `data/datasets/appworld/control.py` | `eval.run.runner` | Platform control invokes the AppWorld verifier through `run_one` |
| `data/datasets/appworld.py` | `eval.policy.appworld_reporting` | Adapter and reporting share `make_manifest`; extraction needs a separate refactor |
| `data/scaffolds/command_control.py` | `eval.run.runner` | Shared command control runs platform verification |
| `data/datasets/jericho/control.py` | `eval.policy.runtime_policy` | Local control stages runtime and applies platform timeout |
| `data/datasets/jericho/control.py` | `eval.containers.sandbox` | Local control executes the game CLI in the platform sandbox |
| `data/datasets/jericho/control.py` | `eval.verification.verifier` | Local control interprets measured evidence through the platform verifier |
| `data/datasets/jericho/control.py` | `eval.run.runner` | Platform control invokes the Jericho verifier through `run_one` |
| `data/scaffolds/qa/control.py` | `eval.run.runner` | Shared QA control validates adapter wiring through `run_one` |
| `data/datasets/scienceworld/control.py` | `eval.run.runner` | Platform control invokes the ScienceWorld verifier through `run_one` |
| `data/datasets/spreadsheetbench/control.py` | `eval.run.runner` | Platform control invokes the spreadsheet verifier through `run_one` |

New permissions require a focused diff explaining the exact dependency and its
reason. Empty reasons and stale permissions fail the guard, including removed
config edges: remove the corresponding permission when removing an import.
Failures identify the importing file, line, import statement, rule, and resolved
module edge. Exceptions never authorize a whole directory or another imported
module from the same file.
