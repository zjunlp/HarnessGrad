# Preparing pinned native scorer packages on Linux

The five reviewed manifests are inventories, not runtime distributions. A fresh
checkout does not include their interpreter, standard library, shared libraries,
launcher, SDKs or other blobs. `verify` checks an installed package; it does not
capture the host Python, download substitutes or change a pin.

Start with a read-only inventory (no store directories are created):

```sh
python tools/prepare_native_runtime.py inventory --dataset bird_minidev --root /external/native-runtimes
```

The JSON names each unavailable SHA256, exact size, sandbox targets, and whether
it supplies the launcher. `verified: false` is intentional: an inventory is not
admission or a host capability result. A corrupt or linked member is an error,
not an absent blob. `required_bytes` counts unique blobs before compression.

Obtain an independently trusted package archive from its operator, preserving
its provenance and applicable component licenses, then import and probe:

```sh
python tools/prepare_native_runtime.py import --dataset bird_minidev --root /external/native-runtimes --archive /supplied/bird-package.tar.gz --probe
python tools/prepare_native_runtime.py verify --dataset bird_minidev --root /external/native-runtimes --probe
```

The target identity is selected by the checkout's independent `NATIVE_PINS`.
An explicit reviewed manifest requires both `--manifest` and `--sha256`.
Imports require the exact marker and allowlisted regular-file blobs, with no
links, duplicate or extra members. Verification and host capability probing do
not run benchmark tasks. Host probing can still refuse a fully populated store
when Linux namespace/bubblewrap capabilities are unavailable.

For an operator who retained a legacy schema 1 source ledger or capture archive:

```sh
python tools/prepare_native_runtime.py assemble --dataset bird_minidev --root /external/native-runtimes --source-manifest /capture/legacy.json --source-map /old/runtime=/retained/runtime --archive /capture/retained.tar.gz --probe
```

Assembly treats ledgers as byte locators and archives as byte candidates. A
ledger may supply shared bytes for another dataset, but every accepted blob
must match the selected target's exact SHA256 and size. Relative ledger paths
use `--source-root` (default: this checkout); parent traversal is refused.
Relocations apply the longest explicit old prefix first. Archive member names
are never used as extraction destinations. Missing bytes are returned in JSON;
partial packages are not published. Existing packages and output archives are
never replaced. Repair of an existing corrupt package is an explicit operator
action. Assembly does not turn an arbitrary source release into a pinned binary.

The current five pins share a 31,028,464-byte Python interpreter with SHA256
`23bc148d2b48964b621c5561ff2b81c1d9c7b717967a7491efbf12f4304882b5`.
The older schema 1 ledger before the portable migration identifies a different
30,652,904-byte interpreter. It cannot establish current runtime readiness.
No public download URL or verified redistribution license bundle is registered
by this preparation tool. Do not describe a clean external install as ready
until exact bytes are available and both verification and probing succeed.
Synthetic package tests exercise the mechanics; they are not recovery evidence
for any of the five reviewed closures.
