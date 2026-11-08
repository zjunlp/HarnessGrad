# MiniSWE

The adapter runs the actual mini-SWE-agent 2.4.6 CLI with LiteLLM 1.75.9. Its
native prompt, planning loop, trajectory and submission sentinel stay upstream's.
Complete locks provide immutable dependencies separately from platform Python.

```bash
# Native files tasks; CPython 3.12, uv and bubblewrap must already exist.
python tools/configure_harness.py --harness base_harness/miniswe --fetch
python tools/configure_harness.py --harness base_harness/miniswe --list --json

# Task images must already be imported locally; preparation never pulls images.
python tools/configure_harness.py --harness base_harness/miniswe --image LOCAL_IMAGE --fetch
python tools/configure_harness.py --harness base_harness/miniswe --dataset DATASET --fetch
```

Omit `--fetch` for preparation from cached wheels. Ordinary candidate preparation
is offline; `--list`, cache admission and resume install nothing. Use
`--runtime-root` / `HG_HARNESS_RUNTIME_ROOT` for an external dedicated store.
The platform needs neither pip nor mini-SWE-agent, LiteLLM, Arrow or their SDKs.
`requirements.txt` retains the two high-level pins; `requirements.lock` is the
complete native CPython 3.12 closure. Exec additionally declares locks for CPython
3.10, 3.11 and 3.13. Unsupported versions and incompatible wheels fail explicitly.

Both native `files` and ordinary container `exec` are supported, including mounted
task directories and container-state tasks. An image supplying Python gets an
image-owned, read-only actor venv and a copy of its exact executable. Its standard
and system libraries remain image-owned. An image explicitly without Python uses
the exact bound native artifact after compatibility checks inside that image.
Broken discovery never triggers a fallback. This fallback supplies the actor;
it does not add Python to task shell commands.

The adapter and the mini CLI use the same bound interpreter with `-I -B`. The
CLI loads the distribution's own `mini.yaml` from that read-only artifact, so a
task's cwd or Python configuration cannot replace its package or configuration.
An inherited directory descriptor addresses this file without exposing runtime
directory names (including `=`) to mini's configuration-override parser.
Container task commands retain the image's PATH, Python import configuration and
existing venv. Native files tasks use the managed interpreter path. Mini's private
HOME, configuration, XDG directories and scratch are recreated for every task;
ambient mini/LiteLLM settings and other model roles' credentials are removed.

The locks include Arrow through mini's declared `datasets` dependency. That is
actor-owned Arrow, independently bound from any dataset provider's environment.
Actor dependencies are absent from the platform and are not mounted into the
scorer or committed container-state snapshot. Candidate source, locks, image and
interpreter identities and actual dependency contents bind H0, caches, checkpoints
and resume. Historical bindings do not select newer runtime publications.

Preparation runs trusted installers and image interpreters without candidate
source or package startup. Existing backend admission/resource policies apply.
Observed/cooperative workers, external runtime paths and unsandboxed managed
actors remain unsupported. Image/ABI coverage and synthetic controls do not
establish benchmark scores, live-model connectivity or operating-system closure.
Unopted historical Harness copies retain their legacy overlay behavior.

## Offline native integration controls

Prepare the **actual 73-distribution native lock**, including mini-SWE-agent
2.4.6 and LiteLLM 1.75.9, in a dedicated external store. Use a matching MiniSWE
cache; Loop's smaller SDK cache is not this closure. Preparation may fetch public
locked wheels only when `--fetch` is explicit:

```bash
UV_CACHE_DIR=/absolute/miniswe-uv-cache \
  python tools/configure_harness.py --harness base_harness/miniswe \
  --runtime-root /absolute/miniswe-store --fetch
```

For an offline repeat, omit `--fetch`; append `--list --json` to check the prepared
runtime without installation. A uv cache is not a wheelhouse: use
`HG_HARNESS_WHEELHOUSE` only for a directory of original wheel files.

The following authored controls use the real mini CLI and LiteLLM with the mock
backend, not a package double, model endpoint or released task. Set
`HG_TEST_MINISWE_RUNTIME_ROOT` to explicitly revalidate the prepared store; an
invalid provided store fails instead of becoming a dependency skip. Keep pytest's
temporary directory outside the checkout:

```bash
HG_TEST_MINISWE_RUNTIME_ROOT=/absolute/miniswe-store \
UV_CACHE_DIR=/absolute/miniswe-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
  python -m pytest -q \
  'tests/quality/test_miniswe_managed_runtime.py::test_native_cli_uses_bound_runtime_and_keeps_task_python[files-mount]' \
  tests/quality/test_miniswe_managed_runtime.py::test_empty_platform_can_check_prepared_miniswe_without_pip_or_sdks \
  tests/quality/test_miniswe_managed_runtime.py::test_shipped_h0_cache_snapshot_eval_and_resume \
  --basetemp=/tmp/harnessgrad-miniswe-native-tests
```

The actor/driver controls run the platform from a private venv with no pip,
pytest or component SDKs. A canary package and file are visible to that platform
but absent from the managed actor. The common base interpreter/standard library
may remain mounted; its prefix is not a private-platform isolation probe.
Synthetic loopback mock traffic discards ambient proxy settings. These native
controls do not certify the separate container/image routes or benchmark quality.
