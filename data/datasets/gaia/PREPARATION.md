GAIA preparation uses the preparation-only `native_qa` execution profile for PyArrow.
It is a shared dependency component, not another dataset. The platform controller
uses the standard library, and launches an admitted artifact with `-I -B`, without
model/API or Hugging Face credentials. Only the explicit fetch controller reads
HF_TOKEN (or the existing documented Hugging Face token fallback).

Explicit setup and read-only inspection:

```
python -m tools.prepare_gaia --prepare-runtime --runtime-root /path/to/runtime-store
python -m tools.prepare_gaia --check-runtime --runtime-root /path/to/runtime-store
python -m tools.prepare_gaia --runtime-root /path/to/runtime-store
python -m tools.prepare_gaia --check
```

The module entry point works from an installed wheel. The source-checkout
`python data/datasets/gaia/prepare.py` entry point remains compatible.

Setup installs dependencies only; the third command explicitly fetches gated data.
Neither check command installs dependencies or fetches source data. Missing or
invalid artifacts fail before acquisition; there is no platform-package fallback.
Parquet digests recorded from downloaded bytes are passed to the worker, which
hashes and parses the same in-memory bytes. The worker admits the exact supplied
binding against its own executable before importing PyArrow. After success the
controller re-admits that same binding and checks the worker source digest before
consuming any conversion output. Artifact/source drift leaves the attempt
unpublished. Worker diagnostics are not forwarded,
to avoid exposing private source rows or credentials.

Preparation requires a fresh pool/attachments namespace. Complete and failed
attempts remain under `gaia-preparation-attempt-*`; the published attachments
symlink references the winning attempt, so retain that directory. Existing source,
scorer files, pools and receipts are not overwritten. Pool publication refuses a
competing writer, and rolls back only its own attachments link on failure. Original
release/revision, pool schema, validation selection and scoring pins are unchanged.

Authored controls (no real gated source, models or services):

```
python -m pytest -q tests/quality/test_gaia_preparation_runtime.py
HG_DATASET_RUNTIME_ROOT=/path/to/runtime-store /usr/bin/python3 tests/quality/gaia_preparation_managed_control.py
```

The second command exercises actual PyArrow conversion of authored Parquet,
local fake HTTP downloads, attachment custody, placeholder exclusion, digest
mismatch rejection and preservation of an existing pool. It requires an already
prepared artifact and never installs one. These controls do not attest the gated
GAIA release or a full benchmark run.
