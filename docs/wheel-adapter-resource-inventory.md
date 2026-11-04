# Wheel adapter resource inventory

This is a reviewed starting inventory, not a claim that all registered datasets
run from a wheel. An installed Python module or a catalog row does not prove its
actor CLI, scorer inputs, upstream data or runtime closure were packaged. Check
`docs/clean-install-validation.md` for the offline mock-demo evidence and build
from a clean reviewed source export: namespace discovery can include untracked
Python modules from a dirty source tree.

| Component | Checkout resources referenced | Wheel declaration | Verified scope |
| --- | --- | --- | --- |
| GSM8K shared QA scaffold | `data/scaffolds/qa/cli/qa`, `prediction.schema.json`, Python verifier and scaffold modules, GSM8K adapter | Exact package-data entries for the extensionless CLI and schema; Python modules via namespace discovery | Offline installed-wheel synthetic row: actor `qa submit`, re-materialized verifier inputs and the adapter's decimal-aware scorer reach reward 1. No released rows, pyarrow, model or network used. |
| Codex task Harness | `agent.py`, `codex_runtime.py`, `harness.json`, `README.md` | Python modules via namespace discovery; exact JSON and README package-data entries | Declared components are checked by the installed-wheel resource contract. Codex itself remains an external installation: prepare local bindings and validate the chosen task images separately. |
| ALFWorld / ScienceWorld / Jericho / Bird / Chess native runtimes | `data/runtimes/*-native.json` and individually pinned native artifacts | Not included | Not wheel-ready. Their manifests include digest-bound runtime sources, targets and environment. Do not rewrite paths or repin to simulate portability. |
| AppWorld / ALFWorld / Bird native CLIs | Dataset-specific extensionless CLIs and supporting assets | Not included by the GSM8K entries | Not validated from wheel; use a reviewed checkout and provisioned dataset/runtime. |
| Kumo, CostBench, MemoryAgentBench and native research adapters | Source inventories, locked resources and/or native provenance under adapter directories | Not generally included | Catalog visibility does not mean installed execution support. See `docs/clean-install-validation.md`. |

## Maintain the inventory

For any adapter being added to installed support, enumerate the actual `SETUP`,
`VERIFY` and runtime resource references; add only exact, reviewed package-data
entries rather than globs; verify the installed wheel in a fresh environment
outside the source tree with synthetic data or a legitimate separately approved
control. Assert the resource bytes and required executable behavior, not just that
imports or `--help` succeed. Never package released private cases, operator
credentials or unreviewed runtime caches. Record what was skipped, and retain
licenses and immutable provenance. If the scorer itself uses an adapter-specific
metric rather than an upstream metric, state its real identity.

The test `tests/quality/test_clean_install_contract.py` checks the GSM8K path
above and declared bundled-harness components, including exclusion of synthetic
untracked non-Python data. It also demonstrates the known remaining untracked
Python module discovery risk. Neither this file nor that test authorizes a real
benchmark or proves all external dependencies are installed.
