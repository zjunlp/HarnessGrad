# LiveCodeBench runtime

The platform uses the standard library to load task metadata and decode the
pinned release shards. Official scoring runs in a prepared CPython 3.12
execution environment. Its only Python dependency is `numpy==1.26.4`, the
version in the pinned upstream revision's `poetry.lock`.

Set `HG_DATA_LIVECODEBENCH` or `HG_DATA_ROOT` to select external data storage
before preparing the dataset. Run these commands from the checkout.

```sh
python tools/datasets.py --fetch --dataset livecodebench
python tools/prepare_dataset.py --dataset livecodebench --runtime execution
python tools/datasets.py --check --dataset livecodebench
python data/datasets/livecodebench/prepare.py
python data/datasets/livecodebench/prepare.py --check
```

Run the data preparation step on the digest-verified shards. Existing
installations can reuse their prepared pool and extracted upstream files.
The pool check prints the SHA-256, which should match
`bbf2bf826856156656568f568e4026f7702f90b5da22a880b733b755a69f4256`.

```sh
python tools/prepare_dataset.py --dataset livecodebench --runtime execution --check
python tools/dataset_lint.py --dataset livecodebench --allow-unprovisioned
```

The sandboxed verifier binds the exact prepared interpreter read-only. It
checks the selected test payload against its pool row and authenticates the
two scorer files from the pinned upstream archive before use. The workdir is
excluded from Python dependency lookup. The official executor still runs the
submitted single-file program in its child process inside the verification
sandbox. The result reports pass@1 and per-test verdicts. Evidence omits
executor diagnostics that can contain private inputs or expected outputs.

Use an explicit covered task for the authored oracle or no-op control. The
`--platform` option exercises the sandboxed route.

```sh
python data/datasets/livecodebench/control.py --oracle --task-id livecodebench/800 --platform
python data/datasets/livecodebench/control.py --noop --task-id livecodebench/800 --platform
```
