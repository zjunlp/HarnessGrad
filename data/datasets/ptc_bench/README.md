# PTC-Bench

The adapter loads all 240 tasks from the official [I-PTC release](https://github.com/HuanzhiMao/I-PTC) at commit `fa4462e5ad1ea3cde2011aaf6396de5d76da4489`. The fixed archive is 1,335,401 bytes and is verified before preparation. Code and data are Apache-2.0. The source revision includes post-paper scorer corrections, so its measurements should name this revision.

| Upstream evaluation partition | Tasks | Task ID prefix |
|---|---:|---|
| Main | 100 | `ptc_bench/main/` |
| Error recovery | 70 | `ptc_bench/error_recovery/` |
| Multi-turn | 70 | `ptc_bench/multi_turn/` |

The official release has no training partition. Use `--split official --side eval` with an explicit partition task list when reporting a partition score. The project research division in `experiments/splits/ptc-bench` carries a shared study split and separate dev and test task lists. Related official task IDs stay on the same side of a research split.

## Prepare and verify admission

Configure `HG_DATA_PTC_BENCH` (or the collection-wide `HG_DATA_ROOT`) and a dataset execution runtime store outside the checkout and its sandbox-hidden paths. Run from the HarnessGrad root.

```bash
python tools/datasets.py --fetch --dataset ptc_bench
python data/datasets/ptc_bench/prepare.py
python tools/prepare_dataset.py --dataset ptc_bench --runtime execution --python /path/to/python3.12
python tools/datasets.py --check --dataset ptc_bench
python tools/dataset_lint.py --dataset ptc_bench
```

Preparation preserves the original order of JSON state objects, which some tools use to break ties. The catalogue and bundle have independent content pins. Only Python 3.12 and its standard library are used by the simulator and official task verifiers.

## Interaction and scoring

The actor sees `ptc.py` and the local transport client. It uses `status`, `tools`, `call` and `answer` over the trusted relay. A Python script running in the actor's sandbox can import `ptc` and call the same API surface. The relay accepts only methods named in the verified documentation and retains the state across calls and user turns. Simulated failures return as ordinary tool results. An answer to the first user turn releases the second one on multi-turn tasks.

The platform seals the simulator state and submissions outside the actor. The final response can also be submitted as a plain `answer.txt` after the final user turn has been released. A response already submitted through the relay takes precedence. Official task `passed` is the binary reward, with the partial score recorded separately as evidence. A broken runtime or untrusted state record leaves the task unmeasured. The adapter uses its own no-network runner and the upstream simulator and verifiers. Provider tools, self-evolving documentation and the upstream Python-cell executor do not participate in this measurement.

## Offline controls

```bash
python data/datasets/ptc_bench/control.py --oracle --platform
python data/datasets/ptc_bench/control.py --noop --platform
```

These commands run one authored positive and one do-nothing control per partition. The positive controls cover a real state change with an answer, an injected failure and recovery, and a genuine second user turn on a persistent simulator. They use the pinned release without a model endpoint. The release provides answer dictionaries on some tasks and has no complete executable oracle corpus. In a separate official-verifier preflight, all 240 do-nothing cases returned `passed=false`, including cases with a nonzero partial score.
