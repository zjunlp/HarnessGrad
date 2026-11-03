# swe_bench_pro: native source and execution contract

This port inventories the original task tree pinned in `SOURCE.json`, without
importing upstream or Harbor into the measurement runtime. It does not pretend
that terminal snapshot verification is the native separate/replay protocol.

```sh
python3 data/datasets/swe_bench_pro/prepare.py --root /path/to/swe_bench_pro
HG_DATA_SWE_BENCH_PRO=/path/to/swe_bench_pro python3 -c \
  'from data.datasets import swe_bench_pro as d; print(d.provision_status())'
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
- V2's official patch capture, fresh sandbox patch replay and official regrade
  using `v2/tooling/patch_replay.py` / `locked_claude_code.py`;
- no-network actor/model allowlist, <=3000-second native actor budget, exact
  V2 task images and scorer identity;
- captured partial patches remain eligible for official regrade; actor chat/exit
  status cannot substitute for `official_evaluation_performed`. Native tool-command
  timeout currently aborts the world as unmeasured: safe timeout quiescence and
  partial-patch capture are a remaining limitation of this HG transport.
