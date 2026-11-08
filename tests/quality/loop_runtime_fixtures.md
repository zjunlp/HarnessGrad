# Genuine Loop SDK controls

These integration tests use the shipped, complete Loop wheel lock, including the
real `openai==2.21.0` SDK. They never create replacement SDK wheels or install
packages in the platform interpreter. Model responses are authored local controls.

Select an external uv cache containing the genuine locked wheels explicitly:

```bash
export UV_CACHE_DIR=/absolute/owned/uv-cache
export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never
# Optional: reuse an already prepared, fully revalidated managed runtime.
export HG_HARNESS_RUNTIME_ROOT=/absolute/owned/harness-runtimes
pytest -q tests/quality/test_loop_managed_runtime.py \
  tests/quality/test_trials_and_unmeasured.py::test_a_changed_tree_is_not_a_cache_hit \
  --basetemp=/tmp/loop-sdk-controls
```

Alternatively set `HG_HARNESS_WHEELHOUSE` to an external directory of original
wheel files. A uv cache is **not** a wheelhouse: do not use it as `--find-links`.
With a wheelhouse and no configured `UV_CACHE_DIR`, the fixture uses a fresh owned
cache. Without either source it fails explicitly rather than silently downloading
or skipping SDK execution. A supplied runtime store is reused only through the
production artifact admission protocol; fixture cleanup removes only its own root.

To prepare and inspect a genuine runtime explicitly, using the same production
wheel-only builder and complete transitive-lock checks:

```bash
python tools/prepare_loop_test_runtime.py \
  --cache-dir /absolute/owned/uv-cache --store /absolute/owned/harness-runtimes
# Optional: --wheelhouse /absolute/original-wheels
# Only an explicit --allow-downloads permits public package acquisition.
```

Preparation defaults to offline, disables Python downloads, and never runs Loop
or a model. Tests always remain offline. No SDK is added to the platform lock.

Native controls do not require Docker. Select these node IDs when validating only
the native path:

- `test_empty_platform_can_prepare_and_check_loop_without_pip_or_sdk`
- `test_real_loop_sdk_shell_and_private_scorer[files]`
- `test_tasks_reuse_readonly_dependencies_and_start_with_private_state[files]`
- `test_shipped_loop_h0_and_snapshot_resume`

Container controls retain their existing real-daemon/image requirements. Native
cases acquire that fixture only when an exec placement actually needs it.

The isolation probe uses a genuine, pip-free private platform venv with an
importable non-secret canary and a private file. Direct `run_one` controls really
start the trusted driver through that venv. The actor must not see its private
prefix, package or file; the actor's separate locked SDK and base stdlib remain
usable. This avoids incorrectly treating an Anaconda base prefix as a private
platform venv. Loopback controls clear ambient proxy variables so local synthetic
traffic cannot inherit an operator's unrelated SOCKS configuration.
