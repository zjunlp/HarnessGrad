# AppWorld data preparation

Loading an already materialized, digest-matching pool uses the platform's standard
library. Missing data is a refusal; catalog inspection does not implicitly decrypt
the bundle or install packages.

From either a source checkout or an installed wheel, select an external data root,
download the declared release, prepare the decryption dependencies, then explicitly
materialize the data:

```bash
export HG_DATA_ROOT="$HOME/.local/share/harnessgrad/datasets"
python -m tools.datasets --fetch --dataset appworld
python -m tools.prepare_dataset --dataset appworld --runtime execution
python -m tools.prepare_dataset --dataset appworld --runtime execution --check
python -m tools.prepare_appworld
python -m tools.datasets --check --dataset appworld
```

The runtime preparation step may download declared packages into its external
store. Decryption uses that admitted interpreter, without model credentials.
The data materializer checks the encrypted bundle and generated pool against their
existing release pins. It validates extracted official files before reuse and
refuses missing, changed or symlinked official paths, preserving existing contents.
Unrelated extra files are retained. A new tree is checked in a private staging
directory and published without replacing an existing directory; this publication
currently requires Linux `renameat2`. Unsupported systems fail explicitly.

Each successful preparation retains a receipt under `preparation-receipts/`, with
the bundle, pool, decryption-worker and runtime identities. Extracted-tree verification
covers official archive members; retained additional files are not authenticated.
This is data preparation
evidence. It does not certify the actor/service/scorer containers, replace their
existing pins, or establish a benchmark result. Real execution still needs the
declared images and its own configuration and acceptance.
