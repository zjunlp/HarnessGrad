# Independent ChessBench native scorer

The managed `chess==1.11.2` provider is for loading and interactive board state.
It is not the native scoring runtime. Native scoring selects the unchanged
`data/runtimes/chessbench_puzzles-native.json` pin:

- Manifest SHA-256: `1dd47b9a27c0598c89c55c08579e7f85655b821166bc8a8150645e9379479599`.
- 724 file destinations plus the launcher, represented by 718 unique blobs.
- CPython 3.12.16 interpreter: 31,028,464 bytes, SHA-256
  `23bc148d2b48964b621c5561ff2b81c1d9c7b717967a7491efbf12f4304882b5`.

The manifest fixes the interpreter, standard library, shared libraries, chess
source and historical scorer source bytes. Current Python, another patch release,
an installed provider, or a newly captured local runtime cannot replace them.
The old schema-1 Anaconda ledger identifies different interpreter bytes.

## Explicit preparation and read-only checks

The Chess-specific entry point requires only the platform standard library:

```bash
python -B -m tools.prepare_chessbench_native inventory --root /external/native-store
python -B -m tools.prepare_chessbench_native prepare --root /external/native-store \
  --package-archive /retained/independently-trusted-chess-native-package.tar.gz --probe
python -B -m tools.prepare_chessbench_native check --root /external/native-store --probe
```

`inventory` does not create store directories or establish readiness. Exit zero
means that inventory completed; `verified: false` is intentional. `check` verifies
the installed marker and every pinned byte. `--probe` additionally runs the sealed
host-capability/identity probe, without a puzzle or model. It does not establish
released-puzzle scoring coverage. Missing/corrupt stores fail, without provider or
host-Python fallback. Package archives must satisfy the existing strict allowlist
and marker contract; applicable distribution licenses remain the operator's duty.

When retained bytes are available as separate files, provide explicit mappings:

```bash
python -B -m tools.prepare_chessbench_native inventory --root /external/native-store \
  --map /opt/hg-python=/retained/exact-python-tree \
  --map /opt/hg-site-packages=/retained/site-packages \
  --map /scorer=/retained/scorer-tree \
  --map /lib=/retained/lib --map /lib64=/retained/lib64 \
  --launcher /retained/bwrap
```

Use `--file SANDBOX_FILE=/retained/exact/file` for a single-file override. Each
mapping must be absolute and unambiguous. Discovery reads only the paths already
named in the reviewed manifest; it does not traverse a venv, import a candidate,
execute a candidate interpreter, or scan datasets/runs. Size and SHA-256 must both
match. Source candidates are read-only. Discovery reports rejected candidates and
an exact missing-blob ledger, including destination paths and launcher status.

Replace `inventory` with `prepare` using the same mappings only after all bytes
are available. Incomplete discovery exits 2 and does not create a package/store.
Complete discovery is passed as a temporary locator ledger to the existing native
store assembler, which checks every byte again and publishes atomically without
replacement. No download, dependency installation, pin generation or repair is
implicit. General retained capture archives/legacy ledgers remain supported by
`tools/prepare_native_runtime.py assemble --dataset chessbench_puzzles`.

## Current evidence and remaining boundary

The original package has now been recovered: all 718 unique blobs and 724 file
mappings match the unchanged manifest. The earlier 163 missing Python blobs were
recovered from the official CPython 3.12.16 build `20261026`, with one sysconfig
file reconstructed using the official uv installation transform and checked
against its original full-file SHA. No substitute interpreter or pin was accepted.
The [recovery receipt](../../../.github/assets/native-original-recovery-20261107.json)
retains the scope, hashes and original failure/fixture-repair evidence.

For a new Linux installation, obtain the reviewed complete archive and its
checksum/provenance/license sidecars from the maintainer, then import it into an
external store. No public complete-archive URL is registered yet.

```bash
python -B -m tools.prepare_native_runtime import --dataset chessbench_puzzles \
  --root /external/native-store --archive /download/chessbench-native-1dd47b9a.tar.gz
python -B -m tools.prepare_native_runtime verify --dataset chessbench_puzzles \
  --root /external/native-store --probe
```

The actual native execution controls are ready to run against an exact package:

```bash
HG_CHESS_NATIVE_RUNTIME_ROOT=/external/native-store \
python -B -m pytest -q -rs tests/quality/test_chess_native_preparation.py \
  --basetemp=/external/task-owned-test-directory
```

They use only an authored legal opening and fixed complete, wrong, partial and
empty solver trajectories through the ordinary verifier with the original native
pin. A provider-only or absent store makes those four cases explicitly skip;
an existing corrupt package fails. No local recapture or mock native scorer is
used. The corrected complete test file passed 12 cases, including rewards 1/0/0/0
for those four trajectories. Private gold payload is redacted; the original
scorer intentionally retains its public puzzle ID in the trusted raw verdict.
Public export filtering is a separate boundary. No released puzzle pool, neural
engine, real model or benchmark run is involved.
