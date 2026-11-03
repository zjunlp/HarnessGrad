# SWE-bench Verified native patch route

The source reader and scoring helper are pinned to upstream
`e9c7667945f959d62da27204649a82f94d1b6705`. The official SWE evaluator revision is
`02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`.

This adapter exports a repository patch; `answer.txt` and actor-authored
`resolved` messages cannot grade a task. The actor uses
`python3 swe.py exec "SHELL COMMAND"` to inspect/edit/test `/testbed` in a
persistent offline container. After actor exit the private relay seals its diff
against the declared baseline. A **different** container starts from the original
instance image, resets the baseline, applies the sealed patch and runs the native
private evaluation script. The unchanged official parser supplies resolved status.
Missing runtime, failed cleanup, truncated patch, malformed judgement, and an
applied patch with no parseable native test execution remain unmeasured.

## Provisioning

1. Prepare the complete native source using
   `tools/prepare_native_research.py swe_bench --config CONFIG --output ROOT`
   and set `HG_DATA_SWE_BENCH=ROOT`. Native rows must retain `image`, `eval_script`,
   `log_parser`, baseline and private test/patch fields; this does not invent
   missing deployment metadata. Image assets are refused, matching upstream.
2. In an isolated interpreter install `swebench` from the exact evaluator revision
   and its dependencies, then set `HG_SWE_PYTHON=/absolute/venv/bin/python`.
   Alternatively set `HG_SWE_SOURCE=/absolute/upstream-tree`: the bridge checks
   the complete package file set against
   `data/scaffolds/native_research/SWE_EVALUATOR_SOURCE.json`. This authenticated
   archive route changes acquisition checking only; scoring code is unchanged.
3. Explicitly acquire the required instance images outside measurement. No adapter
   import, load, preparation command below or trial silently pulls an image.
4. Run `python3 tools/prepare_upstream_swe.py --output /absolute/native-runtime.json`.
   This produces a new actor image for each selected case, pruning future Git
   refs/objects and upstream's specified setup/evaluation files. Use `--task-id`
   with native instance IDs to prepare selected cases. Set
   `HG_SWE_BENCH_RUNTIME=/absolute/native-runtime.json`.

The actor image and original verifier image are bound by local immutable image
IDs, case hash and baseline commit. The preparation does **not** authenticate
arbitrary image contents or prove that every possible embedded answer was removed.
Official image provenance and private-material review remain production gates.
The image must contain an absolute Python interpreter (default
`/usr/local/bin/python3`), Git, Bash and its native test environment. Containers use
the existing observed backend: readonly root, uid65534, no external network,
kernel-observed memory/CPU/PID limits and quota-bounded `/testbed`/temporary storage.
Image-specific incompatibilities are explicit provisioning failures.

The public task inventory loads without provisioning unrelated instance images;
runtime checks apply when selected SETUP/VERIFY values are read. Full readiness
still checks the complete prepared pool.

## Observed controls

```sh
HG_SWE_PYTHON=/tmp/hg-swebench-venv/bin/python \
HG_SWE_SOURCE=/tmp/hg-swebench-official-02e7a74f \
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_upstream_swe_native.py \
  --basetemp=/tmp/hg-swe-native-controls-fresh
```

Observed **6 passed**: three real HG sandbox → native actor → sealed patch → fresh
observed verifier → pinned official scorer controls (repair1/wrong0/empty0), plus
missing-test-execution regressions and authenticated source snapshot/cache isolation. The tiny repository and rows are declared
synthetic instrument controls, **not SWE Verified results**. Local offline pytest
wheels and an existing `python:3.12-bookworm` image are test-only prerequisites.
Four separate supervisor controls validate interrupted startup, forced stop,
exact-container ownership and non-overwriting cleanup failures. No model is called.

The native scorer route deliberately records isolation class `unknown`: fresh
containers and observed resource facts do not constitute HG's complete
`pristine-offline` scorer-bundle/image/ABI closure attestation. Do not promote
these controls into a comparable production score or authorize an H0 pilot from
this README alone.
