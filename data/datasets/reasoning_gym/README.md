# Reasoning Gym runtime

The platform reads the frozen JSONL pool with the standard library. Generation,
local scoring diagnostics and the sandboxed verifier use one prepared CPython
3.12 execution artifact with `reasoning-gym==0.1.25` and its complete lock.
The verifier rechecks the exact artifact, mounts it read-only and records its
identity and the actual package origin. It uses the existing QA answer format
and the upstream `score_answer` method, including partial credit.

## Build the three source-only wheels

Build these wheels separately on the target platform with CPython 3.12. Pycosat
requires a C compiler and the headers for that interpreter. The source SHA-256
values below pin the inputs. Build tools stay in the disposable build environment.
Use a fresh directory outside the checkout and retain the wheelhouse for reuse.

```sh
rg_build=$(mktemp -d)
rg_wheels="$rg_build/wheels"
mkdir "$rg_wheels"
(
  set -eu
  cd "$rg_build"
  curl -fLO https://files.pythonhosted.org/packages/a8/1e/c606ac9bf0277524cd935d6c2bb5b387c3bf401fa8ddbb59b622d6dd50af/cellpylib-2.4.0.tar.gz
  curl -fLO https://files.pythonhosted.org/packages/5b/81/cf8ebf77fc4f06f680ad3ee43d0d01826f6d6054828f1cf3b42d944b82a1/pycosat-0.6.6.tar.gz
  curl -fLO https://files.pythonhosted.org/packages/1e/d1/ed34d12f55d07cc1efb61d74fb2f64f46a705557f5bdd1ef1b810f0e2ec5/zss-1.2.0.tar.gz
  sha256sum --check <<'SHA256'
e23ff7b5f647c23958748727c899b4e57b667c9f18cbc66d7577dbfaab3729ef  cellpylib-2.4.0.tar.gz
a376cfae20b16fcfbef24bf3c047a8a294c35032bb051fa98842c12bbab6f0ff  pycosat-0.6.6.tar.gz
07bb937441929ccb82961f4f7b80fbce9e2b20d0e46ddcbcbc1fcb094f585b50  zss-1.2.0.tar.gz
SHA256
  uv venv --python 3.12 builder
  uv pip install --python builder/bin/python --only-binary :all: setuptools==80.9.0 wheel==0.45.1
  for sdist in cellpylib-2.4.0.tar.gz pycosat-0.6.6.tar.gz zss-1.2.0.tar.gz; do
    uv build --offline --wheel --no-build-isolation --python builder/bin/python --out-dir "$rg_wheels" "$sdist"
  done
  sha256sum "$rg_wheels"/*.whl > wheel-sha256.txt
)
```

## Prepare the runtime and pool

Run these commands from the checkout. Set `HG_DATA_REASONING_GYM` or `HG_DATA_ROOT`
to select external data storage. `HG_DATASET_RUNTIME_ROOT` selects the external
runtime store. Only explicit preparation installs dependencies. The execution
profile accepts wheels, including the local wheelhouse above.

```sh
UV_FIND_LINKS="$rg_wheels" python tools/prepare_dataset.py --dataset reasoning_gym --runtime execution
python data/datasets/reasoning_gym/prepare.py --import-pool /path/to/frozen/pool.jsonl
python data/datasets/reasoning_gym/prepare.py --check
python tools/datasets.py --check --dataset reasoning_gym
```

An existing pool at the selected data root can be checked in place. The frozen
digest is `c0ff4eaef318cd59afcc61472ebe206362fc0211c4b589ed0d53946a569bd177`.
The 89 families, 20 indices per family and file order define the historical task
IDs. The original generation environment could not be recovered and previous
regeneration attempts produced different bytes. Running `prepare.py` without
arguments still generates with the managed runtime, but it only publishes a
byte-identical result. A mismatching result leaves an existing pool intact.
See the historical record in [withdrawn requirements](../../../registry/withdrawn-requirements.md#reasoning_gym--original-generation-process-receipt).

`prepare_dataset.py --runtime execution --check` checks the execution artifact.
`tools/datasets.py --check` checks the artifact and the pool digest. Once both are
ready, use the shared QA control with `--oracle` or `--noop` and an explicit task
ID. Add `--platform` to exercise the actual sandboxed verifier. The dataset
continues to declare a study pool without an official evaluation split.
