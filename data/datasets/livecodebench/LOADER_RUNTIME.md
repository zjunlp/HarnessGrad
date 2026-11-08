# LiveCodeBench runtime boundaries

The metadata loader uses the Python standard library and reads the pool only
when tasks are requested. The adapter's upstream
pass@k helper functions import NumPy only when called; task discovery and staging
do not need it. The independent verifier still executes the pinned upstream
`testing_util.py` and `pass_k_utils.py` using a separately prepared managed
execution runtime. Prepare it explicitly before selecting verifier tasks:

```bash
python -m tools.prepare_dataset --dataset livecodebench --runtime execution
python -m tools.prepare_dataset --dataset livecodebench --runtime execution --check
```

Preparation can download the declared wheel. The separate `--check` command
inspects the prepared artifact without installation.

At upstream commit `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`, the two
executor/aggregator files import NumPy, while `uv.lock` pins 2.2.5 and
`poetry.lock` pins 1.26.4. This project selects NumPy 2.2.5 from `uv.lock` on
CPython 3.12 for its new verifier contract; it does not assert that either lock
was used for historical official measurements. The upstream archive SHA-256 is
`56c7c42aee979def86b69547ecd6d5a877a0e4940af8923bf4325ca0defecbaf`.
The selected upstream `uv.lock` SHA-256 is
`d89499528b844282d0ba0db3b312f09353a9888458393becfe3f1111d322fbcc`.
No scorer or official metric has been replaced by this change. Runtime identity
captures the selected artifact, not all system libraries or the host OS.

Selecting a verifier also checks the executor and aggregator against file hashes
derived from that pinned archive. Their authenticated private copies and the
selected test payload enter the scorer; they are absent from actor setup.
The selected test payload must also match the digest in its pool row. Scoring
evidence records the interpreter and the loaded NumPy origin when present, along
with per-test verdicts. Executor diagnostics can contain private test content
and are kept out of the evidence.

Missing or altered scorer sources or test payloads are refused. A failed scorer process is an
unmeasured attempt, while an ordinary wrong solution keeps the official zero.
