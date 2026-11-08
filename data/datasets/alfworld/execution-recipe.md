ALFWorld trusted interactive service
===================================

Discovery, task filtering, game stripping, the actor CLI and action-log parsing
use the standard library. Third-party imports occur only in `gym_env.build()` in
the trusted service or the independently sealed native verifier. Task setup binds
a prepared execution artifact with role `service`; a missing or changed artifact
is an error, without host interpreter fallback or implicit installation.

Prepare explicitly (an external runtime store is required):

```
UV_NATIVE_TLS=1 UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
python tools/prepare_dataset.py --dataset alfworld --runtime execution \
  --runtime-root /absolute/external/store --python /absolute/python3.12
python tools/prepare_dataset.py --dataset alfworld --runtime execution \
  --runtime-root /absolute/external/store --check
```

Set `HG_DATASET_RUNTIME_ROOT` to that store for task execution. Preparation records
and freezes the resolved distributions and file inventory. Check and launch
admission verify the same artifact without downloads. Select a dedicated CPython
installation when preparing, because its base standard library is a compatibility
input in the service filesystem view.

`execution-requirements.in` retains the adapter's original ALFWorld 0.4.2 and
TextWorld 1.7.0 declarations and requests TextWorld's PDDL engine. It also keeps
NumPy 1.26.4, Jericho 3.3.1 and TatSu 5.8.3: selected package source files match
the original native scorer hashes byte for byte, with distribution versions read
without importing those packages. `native-source-version-evidence.json` records
these limited identity checks; it does not claim a complete original inventory. The exact pins
in `execution-requirements.txt` are a **new service dependency resolution**, made
on 2026-11-07 with uv using the public Tsinghua index for CPython 3.12/Linux. They
are not a reconstruction of the historical scorer environment. The existing
`data/runtimes/alfworld-native.json`, its hashes and the native scoring algorithm
are unchanged and continue to define verification.

Only three source files are mounted for the service: relay.py, lib/gym_env.py and
the platform tool_transport.py. The runner separately stages the selected stripped
game in private service state. Neither its directory nor neighboring task inputs
are actor files. The actor receives the stdlib CLI/library and socket route only.

Validation: the empty-site (`-I -S`) loader import and missing-artifact control run
without third-party dependencies. The opt-in focused test uses a genuinely prepared
managed artifact and native bubblewrap to run an authored fake engine through the
real relay and stdlib CLI, checks action logging and the winning reply, and checks
a private neighboring file is absent from the service view. This transport control
is not a benchmark run or a native scorer fidelity result. To run it:

```
HG_ALFWORLD_SERVICE_TEST_ROOT=/absolute/external/store \
python -m pytest -q tests/quality/test_alfworld_managed_runtime.py \
  --basetemp=/tmp/alfworld-service-tests
```

The legacy `control.py --oracle` / `--noop` and `--platform` spellings now all
use the normal runner with the complete managed relay declaration and original
sealed native verifier. `--budget` is carried consistently to service, actor CLI
and replay; missing scores or relay failures remain unmeasured. The older helper
API also starts the admitted service through the runner's relay lifecycle and
scores through the native verifier; it never imports the engine in platform Python.
