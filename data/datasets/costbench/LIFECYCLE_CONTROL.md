# Author-owned native lifecycle controls

`python -m tools.check_costbench_lifecycle` checks the real static service and the dynamic
`cost_change` service without reading released tasks, prepared manifests, released
queries or released search databases. It needs an existing original-pin source
bundle and an explicitly prepared CostBench execution artifact. It never installs
packages, downloads data or calls a model.

The builder runs with the exact admitted service interpreter under `-I -B`,
checks the preparation-source fingerprint, and verifies the unchanged upstream
source pins through bootstrap. Its input is one author-owned location preference,
a one-entry author-owned lookup database, and five original native atomic tools
with author-selected cost 20. The real upstream solver computes this tiny graph's
reference path. The dynamic version uses the real blocker to freeze and apply
one cost-change event; no event or score is mocked. These controls use the required
task-ID namespace but are not added to the released roster or prepared manifests.

For each static/dynamic case, oracle, noop and invalid-tool arms start the original
relay through `tool_relay.start`, call its real Unix socket, submit an answer,
stop it, seal its trusted episode and run the original verifier/scorer in the
native sandbox. The service and verifier bind separate roles of the same exact
artifact. Recorded runtime observations include executable, dependency origins,
`isolated` and read-only mount facts. Process exit and private relay-directory
removal are checked after cleanup. Startup or scoring failures remain unmeasured;
an invalid actor tool call is instead a measured zero.

## Run

Prepare the execution artifact separately using the normal explicit command if
needed. The artifact must live outside `/tmp`: the native verifier uses private
`/tmp` isolation and correctly rejects a runtime hidden by that mount. This is an
existing deployment constraint, not a reason to fall back to platform Python.
For example, choose a task-owned store below the user's cache directory:

```sh
python tools/prepare_dataset.py --dataset costbench --runtime execution \
  --runtime-root "$COSTBENCH_RUNTIME_STORE"
python -m tools.check_costbench_lifecycle \
  --runtime-root "$COSTBENCH_RUNTIME_STORE" \
  --bundle "$PINNED_COSTBENCH_BUNDLE" --output "$FRESH_CONTROL_OUTPUT"
```

`PINNED_COSTBENCH_BUNDLE` must contain the exact files named by the unchanged
`source-files.json`. `FRESH_CONTROL_OUTPUT` must be outside the installed code
tree and must not exist. The control keeps its authored inputs, builder log,
sealed episodes, service receipts, verifier evidence and a combined
`lifecycle-receipt.json`. Existing output is never overwritten. Actual tests are
opt-in and reuse the same offline control:

```sh
HG_COSTBENCH_LIFECYCLE_RUNTIME_ROOT="$COSTBENCH_RUNTIME_STORE" \
HG_COSTBENCH_LIFECYCLE_BUNDLE="$PINNED_COSTBENCH_BUNDLE" \
python -m pytest -q tests/quality/test_costbench_authored_lifecycle.py
```

## Observed scope

The six native arms measured static `1/0/0` and dynamic cost-change `1/0/0`
(oracle/noop/invalid), with clean service stop, process exit and relay-directory
cleanup. The dynamic oracle applied one real intervention and produced two
reference scenarios; both service and scorer reported the selected artifact
interpreter with isolated execution and read-only runtime mounts. A prior attempt
with an artifact under `/tmp` correctly remained unmeasured and its failure log
was retained before explicitly preparing the same dependency lock elsewhere.

This validates real native engine, transport, sealing, scoring and cleanup on
small authored inputs. It does not certify released case preparation or runs,
all travel domains, train compatibility, the other three dynamic modes, actor
privacy attacks, full runner/H0/cache behavior, model planning, or full benchmark
acceptance. No production source, manifest, scorer or dependency pin is changed.
