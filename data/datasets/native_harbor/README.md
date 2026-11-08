# Harbor trusted-verifier dependency component

This is an internal execution dependency recipe, not a 57th dataset adapter.
It preserves Harbor **0.23.0** and a complete 90-package exact lock. It is not the
actor environment, native task preparation, or benchmark/trial acceptance.

```sh
python -m data.scaffolds.native_harbor_runtime.prepare_python prepare --runtime-root /external/component-store
python -m data.scaffolds.native_harbor_runtime.prepare_python check --runtime-root /external/component-store
python -m data.scaffolds.native_harbor_runtime.prepare_python probe --runtime-root /external/component-store
```

Only `prepare` installs dependencies into the task-owned canonical store. `check`
is read-only; `probe` loads original Harbor Trial/config modules under the exact
admitted interpreter but never creates/runs a Trial, model or Docker workload.
Use a store outside the checkout and private `/tmp` sandbox trees.

Old `harbor_python` path/version-only task manifests are refused. Reprepare this
component, set `HG_DATASET_RUNTIME_ROOT`, then rerun the explicit native task
runtime preparation to record `harbor_runtime`. No identity is fabricated for an
old venv. The binding belongs only to the trusted verifier; actor/public/private
and network responsibilities remain unchanged. Host stdlib/system libraries
remain compatibility inputs, not a sealed OS image.

See the [native runtime preparation and migration guide](../../scaffolds/native_harbor_runtime/README.md).
