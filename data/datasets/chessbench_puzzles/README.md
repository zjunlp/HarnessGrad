# ChessBench Puzzles environments

The platform imports only the standard library. CSV loading, FEN/UCI decoding and
stepwise board transitions use the explicitly prepared CPython 3.12 QA provider
with `chess==1.11.2`. The adapter is private provider/scorer code; platform callers
use the registry, QA control or runner instead of importing it directly.

```sh
python tools/datasets.py --fetch --dataset chessbench_puzzles
python tools/prepare_dataset.py --dataset chessbench_puzzles
python tools/prepare_dataset.py --dataset chessbench_puzzles --check --json
```

Configure `HG_DATA_CHESSBENCH_PUZZLES` or `HG_DATA_ROOT` before fetching or loading.
`HG_DATASET_RUNTIME_ROOT` selects an external provider store; its default is the
user cache. Fetch uses standard-library HTTP/CSV tools and installs no packages.
Only explicit preparation uses uv. Inspection and loading never install anything
or fall back to platform-installed chess. The provider check verifies dependencies
and loads data; its scope does not claim the native scorer is prepared.

Offline strict replay retains the reviewed sealed native scorer, including its
independent interpreter, chess package and original source bytes. Its manifest
and admission pin are unchanged. Import a matching reviewed archive into an
external native store, or verify an already installed package:

```sh
python tools/prepare_native_runtime.py import --dataset chessbench_puzzles --archive /path/reviewed-chess-package.tar --probe
python tools/prepare_native_runtime.py verify --dataset chessbench_puzzles --probe
```

`HG_DATA_NATIVE_RUNTIMES` or `HG_DATA_ROOT/native-runtimes` selects that store.
The ABI/capability probe launches no task. Missing blobs or incompatible host
capabilities are refused; a fresh capture never replaces a reviewed pin.
Local QA controls use the selected provider interpreter as diagnostics;
`--platform` exercises the reviewed native scorer and sandbox.

The existing interactive `chessbench_puzzles.stepwise.v1` protocol remains
distinct from offline strict sequence scoring. The platform owns the relay,
trusted submission history and receipt-based scoring. Board transitions run in
short-lived provider processes against inline verifier-owned gold, replaying only
the platform's own history. They read no CSV. Only the existing five public board
fields reach the actor; gold, future moves and runtime references stay private.

The runner binds `VERIFY.provider_runtime` before cache lookup, re-admits that
exact manifest on every board request, and includes it in dataset runtime
identity, H0/cache/resume checks and the private receipt. A changed discovery
index cannot move a running session. Missing or modified artifacts fail before
cache reuse or actor launch. The controller remains classified as `in-process`:
provider dependency separation does not turn receipt scoring into native or
pristine verification. Offline native admission is independent of this protocol.

The wheel includes the provider declaration/bridge and the unchanged reviewed
native manifest. Native payload blobs remain in the external store. Synthetic
acceptance covers a chess-free platform, installed wheel, selected-row reading,
oracle/noop controls, private stepwise transport and runtime/cache refusal.
Tests require an explicitly prepared disposable provider store via
`HG_TEST_CHESS_PROVIDER_RUNTIME_ROOT`; sealed tests additionally need
`HG_DATA_NATIVE_RUNTIMES`. Unit tests of the private adapter use a separate test
interpreter containing chess. No synthetic result establishes released accuracy.
