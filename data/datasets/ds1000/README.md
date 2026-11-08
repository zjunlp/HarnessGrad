# DS-1000 scientific verifier runtime

The adapter binds one immutable Python 3.12 scientific artifact under separate
**actor** and **verifier** roles. Platform Python and a harness's own SDK interpreter
need no NumPy, pandas, SciPy, scikit-learn, Matplotlib, seaborn or Torch. `verify_for()` binds the selected interpreter and runtime paths, `-I -B`,
task-scoped scorer inputs and the measured-result contract. The native launcher
re-admits that exact artifact; it does not substitute platform Python.

Lazy `ENV.task_python` gives the solver's shell `python` the same scientific
dependencies through a read-only actor mount. It changes task-tool PATH, not the
harness SDK's Python import path. `ENV` and `VERIFY` enumeration do not prepare or
select an artifact. Selection binds each role explicitly; both roles participate
in `dataset_runtime_identity`. This route requires sandboxed files tasks and does
not fall back to an unsandboxed/platform interpreter. The shared artifact contains
only package dependencies: no private code context, official executor or gold.
Those verifier inputs are restored separately after the actor exits.

## Dependency and variant boundary

`requirements.lock` is the complete 29-distribution Linux x86_64 / CPython 3.12
CPU closure. The main versions are NumPy 1.26.4, pandas 2.2.2, SciPy 1.13.1,
scikit-learn 1.8.0, Matplotlib 3.8.4, seaborn 0.13.2 and **torch 2.5.1+cpu**.
This is a new, explicit environment. It is not the formerly observed host's
CUDA-enabled torch 2.5.1 distribution, the original DS-1000 environment.yml, or a
reconstruction of the environment used by the 2026-10-07 sweep.

The frozen **926 offered / 74 excluded** queue, exclusion table, task construction,
original data/source pins and pinned offline seaborn inputs are retained. No
released row was read or executed to introduce this runtime. The exclusions were
not recomputed. CPU authored controls do not certify that all 926 rows pass their
reference solutions in this environment. TensorFlow, CUDA, xgboost and optional
scientific extras are outside this profile.

`cpu-wheels-cp312-linux-x86_64.json` records the public URLs, sizes and SHA256s of
all 29 wheels used for the first authored validation. Their total compressed size
is 287,334,963 bytes; the CPU Torch wheel is 174,642,530 bytes. No CUDA distributions
are included. This provenance receipt is separate from the managed artifact's
physical interpreter, installed-file inventory and runtime identity.

## Explicit preparation

Choose a private absolute runtime root outside source/interpreter/work trees and
outside `/tmp` or `/var/tmp`, which native sandbox isolation deliberately hides
(for example an absolute path under your `~/.cache/harnessgrad/`). The public
stdlib-only entry point works from a source checkout and an installed wheel:

```sh
# Default: read-only offline check. Missing preparation returns exit 2.
python -m tools.prepare_ds1000_runtime --runtime-root /absolute/ds1000-runtime-store

# Explicit network permission: fetch the 29 public CPU wheels, verify every
# size/SHA256, then perform canonical managed preparation and a canonical check.
python -m tools.prepare_ds1000_runtime --runtime-root /absolute/ds1000-runtime-store \
  --python /absolute/python3.12 --fetch

# Repeat using verified cached wheels only, without network access.
python -m tools.prepare_ds1000_runtime --runtime-root /absolute/ds1000-runtime-store \
  --python /absolute/python3.12 --prepare
```

The default wheelhouse is `RUNTIME_ROOT-wheels`; `--wheelhouse /absolute/cache`
selects another private external directory. The preparation target must be
**CPython 3.12 on Linux x86_64 with glibc >= 2.28** (the exact Pillow wheel
requires manylinux_2_28). Other architectures, musl and older glibc receive an
unsupported-profile diagnostic before any download. `uv` is the same prerequisite used by the canonical
runtime manager. The tool does not install it, alter platform/system packages, or
read dataset records.

Only `--fetch` may contact the manifest's public HTTPS sources. Interrupted or
hash/size-rejected downloads retain their `.part-*` bytes and are never published
as usable wheels. A replaced invalid cache entry is retained as `.rejected-*`.
Valid repeated preparations reuse verified bytes and the managed artifact. The
tool writes a local PEP 503 index, removes ambient alternate indexes/credentials
for preparation, enables `UV_OFFLINE=1`, and invokes canonical
`dataset_runtime.prepare_execution` followed by `check_execution`. A JSON receipt
reports actual downloaded bytes, per-wheel reuse, manifest digest and runtime ID.
Default `--check` does not create a store, wheelhouse, index, lock or receipt.

The lower-level `tools/prepare_dataset.py --dataset ds1000 --runtime execution`
interface remains available. Its default PyPI index does not contain this exact
CPU Torch variant, so first-time users should use the complete helper above.
For a run, explicitly set `HG_DATASET_RUNTIME_ROOT` to the prepared root.

## Custody and failure behavior

The source SHA256 of official `execution.py` is
`fab0beb2a5c3300f4540c2225ffc013881e5770fed388ad20d4620ae040cd311`
(xlang-ai/DS-1000 commit `b39aab71`). It is checked before runtime selection and
again when binding its bytes together with `context.json` and the offline CSV
inventory. The check consumes authenticated private copies. Gold reference code
still travels only through the explicit oracle route, never actor SETUP.

Both check and child require isolated, bytecode-free startup. The input helper
and official executor are compiled directly from selected source bytes; task
packages, cached bytecode and symlink aliases cannot supply those modules.
Scientific imports are checked in the bound interpreter before the unchanged
upstream `code_context + code=repr(candidate) + test_execution(code)` construction,
including conditional `test_string(code)`. The upstream 120-second budget and
disabled reliability guard are unchanged. The verifier's outer budget is 180 s.

Missing scientific dependencies, invalid private-input bindings, broken executor
startup, malformed result transport and timeouts produce `measured: false`, a
nonzero exit and no reward file. Ordinary failed assertions remain measured zero.
Scientific preflight validates the declared seven-library CPU environment. After
a successful preflight, official failed results remain measured zero, including a
candidate importing a nonexistent module. Result text never determines whether a
failure is environmental. Any additional trusted-context dependency needs an
explicit profile declaration/preflight before it can be classified as unavailable.
Arbitrary context imports are not extracted or executed separately: context may
create modules or configure import paths before importing them. Only the profile
modules are preflighted, preserving the official program's ordering and effects.

## Authored checks, without benchmark execution

Prepare the real profile and provide only the original pinned `execution.py`:

```sh
HG_DS1000_AUTHORED_RUNTIME_ROOT=/absolute/ds1000-runtime-store \
HG_DS1000_OFFICIAL_EXECUTION_SOURCE=/absolute/DS-1000-b39aab71/execution.py \
python -m pytest -q --basetemp=/absolute/external-test-tmp \
  tests/quality/test_ds1000_execution_runtime.py \
  tests/quality/test_ds1000_actor_runtime.py
```

The controls use synthetic prompt/context/candidate/CSV bytes. A fresh platform
venv with none of the seven scientific packages resolves/admit-binds the real
artifact and runs authored NumPy, pandas, SciPy, scikit-learn, plotting, seaborn
and CPU Torch operations through the original official executor. An additional
stdlib-only authored harness starts a real shell with actor NumPy/CPU Torch, tries
a solution and submits `solution.py`; positive and incorrect controls then use
the bound official verifier. Actor probes verify the dependency mount is read-only
and private context/executor/scorer inputs and artifact manifests are not visible.
These actor controls create and remove an owned cold venv outside `/tmp`, following
the native sandbox's interpreter visibility rules. They cover
positive and ordinary-negative results, conditional string checks, private-source
mutation, missing dependencies, wrong interpreter, package/module/bytecode shadow
attempts and helper/executor symlink substitution. Without explicit runtime/source
configuration, the real-stack controls skip with preparation instructions; static
profile and historical-pin checks do not require data or dependencies.

The existing `test_ds1000_official_scorer.py` contains released-row controls and
historical sweep checks. It was **not run** for this change. Running that suite or
revising its historical coverage assertions requires separate authorization.
