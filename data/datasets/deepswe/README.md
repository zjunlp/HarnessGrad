# deepswe: native source and execution contract

This port inventories the original task tree pinned in `SOURCE.json`, without
importing upstream or Harbor into the measurement runtime. It does not pretend
that terminal snapshot verification is the native separate/replay protocol.

```sh
python3 data/datasets/deepswe/prepare.py --root /path/to/deepswe
HG_DEEPSWE_DATA=/path/to/deepswe python3 -c \
  'from data.datasets import deepswe as d; print(d.provision_status())'
```

Preparation is explicit; importing/loading never downloads or installs anything.
The manifest records the source revision and each downloaded file SHA-256.
Preparation refuses links and pre-existing conflicting files. Native task content,
including `task.toml`, original instructions, images, private tests, collect hooks,
resource/network policies and separate environment declarations remains intact.
`inventory()` returns all selected source tasks and their declarations/digests;
public actor tasks only contain the native instruction and a namespaced ID.

`load()` inventories public tasks without requiring unselected runtimes. Selected `SETUP` / `VERIFY` access validates the task/source-bound native runtime manifest before actor/model work. It uses a private native-world relay and fresh Harbor verifier replay, with missing dependencies/images reported as ConfigMissing rather than scored zero. See [shared runtime preparation](../../scaffolds/native_harbor_runtime/README.md).

Native execution requirements preserved by the route:
- v1.1 separate verifier image plus native `verifier.collect` commands exporting
  committed binary diffs from the correct base revision;
- native actor and verifier no-network with only the platform model route;
- native verifier build dependencies, task-image digests and real oracle/noop
  checks. A same-container `/tests/test.sh` is not an equivalent protocol.
