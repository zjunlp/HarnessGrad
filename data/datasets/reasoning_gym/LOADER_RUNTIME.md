# Reasoning Gym execution runtime

The loader reads a materialized pool with the standard library. Prepare the
separate scoring and generation runtime once with:

```
python tools/prepare_dataset.py --dataset reasoning_gym --runtime execution
python tools/prepare_dataset.py --dataset reasoning_gym --runtime execution --check
```

The execution lock preserves the adapter's existing `reasoning-gym==0.1.25`
pin and fixes its CPython 3.12 dependency closure resolved from distribution
metadata. It is a new execution contract, not evidence of historical package
versions. Some dependencies are source distributions; preparation may build
them in the dataset runtime manager. The artifact identity covers the venv and
base executable, not the complete host OS.

To provision the existing pinned pool from a previously retained snapshot, set
`HG_DATA_REASONING_GYM` to a new, nonexistent data directory and run:

```
python -m tools.prepare_reasoning_gym --import-pool /absolute/path/to/pool.jsonl
python -m tools.prepare_reasoning_gym --check
```

The import copies only a regular file whose SHA-256 matches the existing pool
pin and writes a local import receipt. Linux `renameat2(RENAME_NOREPLACE)`
publishes the directory atomically and refuses any existing destination, including
an empty directory created concurrently. It does not claim that the current runtime
generated those bytes, alter the original file or overwrite an existing pool.
The loader also checks the pool digest before reading its records.
Matching a digest establishes byte identity, not independent authenticity of the
snapshot's origin. Public distribution, licensing and availability of snapshots
remain separate prerequisites; no pool is shipped with this package.

`python -m tools.prepare_reasoning_gym --generate` is a diagnostic operation,
not the recommended way to provision the historical pool. It runs inside the
admitted runtime in a private working directory, but does not itself provide
network or operating-system isolation; some bundled generators execute code.
It refuses to overwrite an existing pool or receipt. A generated pool whose
digest differs from the existing pin is retained for diagnosis and is not
published as ready; a matching result gets a `pool.runtime.json` receipt with
`operation=generate` and the actual execution artifact. Some generators depend
on hash-randomized sets or global randomness, so package version, seed and family
size alone do not guarantee the historical bytes. `--check` only inspects an
existing pool. Scoring invokes the
library's own `score_answer` with the original seed and family size. Missing
dependencies, malformed gold and scorer errors are unmeasured, not zero.

An operator who already has an authorized snapshot endpoint may instead use:

```
python -m tools.prepare_reasoning_gym --snapshot-url 'https://operator.example/pool.jsonl'
```

The example is a placeholder, not a public download location. No default snapshot
URL is provided. This operation needs only the Python standard library and does
not prepare the scoring runtime. It downloads into a private temporary attempt,
reuses the atomic no-replace local importer, which checks the existing SHA-256
while copying the exact downloaded file. Any existing destination, including an empty
directory, is refused. Failed downloads leave no ready pool or success receipt.

HTTP and HTTPS are supported; HTTPS retains certificate and hostname verification.
Redirects are refused and only HTTP 200 is accepted. The download worker
inherits only standard `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` (including lowercase
forms) and explicit `SSL_CERT_FILE` / `SSL_CERT_DIR` network settings. Standard
`urllib` proxy selection and TLS verification remain active; model/API credentials
are not inherited. Proxy credentials and CA paths are not written to receipts or
public errors. Set `NO_PROXY` explicitly when an endpoint should be reached directly. Downloads are limited to 8 MiB, 15 seconds per socket operation, and
a hard 60-second total deadline enforced by the parent process. The download
worker never publishes a pool; only the parent can import a completed attempt.
Interrupted declared lengths, invalid lengths and digest mismatches fail closed.
URLs must be ASCII (percent-encode other characters), without userinfo or fragments.
Query strings may carry endpoint tokens; neither the path nor query is printed
or written to the receipt. The receipt retains only scheme, host and explicit
port as `source_origin`, plus the actual bytes and digest. Avoid putting secrets
in the host; command arguments may also be visible to other local processes.

An operator-supplied endpoint is not evidence of official publication or permission
to distribute the pool. Public snapshot availability and licensing of all family
content remain unverified; this command does not grant distribution rights.

The [source/license audit](SOURCE_LICENSE_AUDIT.md) records the actual 0.1.25
Apache-2.0 package declaration, selected third-party license statements (including
ACRE's GPL-3.0 header), and bounded public-source checks. Those checks found no
verified public endpoint for this exact pool; they do not establish distribution
rights for all family content. Its JSON receipt preserves evidence hashes and
separates local byte custody from provenance and authorization.
