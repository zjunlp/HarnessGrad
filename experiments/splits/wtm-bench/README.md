# WTM-Bench research splits

The [mixed-level study](mixed/study.json) uses 300 distinct tasks from the [pinned Hugging Face full release](https://huggingface.co/datasets/prose-ms/wtm-bench/tree/c565125154df4a197611847d4a3197909f75d9b4). The release provides Levels 1, 3 and 5, ranging from detailed to concise instructions. The study uses exactly one released instruction per underlying task. It excludes all 150 curated evaluation IDs.

| Split | Level 1 | Level 3 | Level 5 | Total |
| --- | ---: | ---: | ---: | ---: |
| Train | 34 | 33 | 33 | 100 |
| Dev | 33 | 34 | 33 | 100 |
| Test | 33 | 33 | 34 | 100 |
| Total | 100 | 100 | 100 | 300 |

The builder ranks eligible tasks by seeded SHA-256 and checks the first 360 workbook pairs for shared bytes, worksheet contents and workbook origin metadata. It assigns whole groups to partitions, retains 100 tasks per partition in seeded order and cycles through the three instruction levels. The frozen [membership manifest](../../../data/datasets/wtm_bench/study-300.json) records each task's level, partition, workbook group and input digests. Run `python experiments/splits/wtm-bench/build.py --study-source /path/to/snapshot --check` with a local snapshot to reproduce the selection. Similar workbook templates can still recur across partitions.

[Prepare the mixed-level collection](../../../data/datasets/wtm_bench/README.md#prepare-and-check) before using these flags.

```bash
export HG_WTM_BENCH_POOL=mixed
# Include on each run
--split experiments/splits/wtm-bench/mixed/study.json
# Use with --side eval --sampling all for checkpoint selection
--tasks "$(paste -sd, experiments/splits/wtm-bench/mixed/dev.txt)"
# Use with --side eval --sampling all for final evaluation
--tasks "$(paste -sd, experiments/splits/wtm-bench/mixed/test.txt)"
```

Report results as **HarnessGrad WTM-Bench mixed-level held-out**, with the partition and the instruction levels used.

## Curated Level 5 study

Use the default `HG_WTM_BENCH_POOL=curated` for this study.

The 150 curated Level 5 tasks from Microsoft's [WTM-Bench release](https://github.com/microsoft/WTM-Bench/tree/83d45ae601f3241ac7246613c67b765e48f93c06) are divided into **50 train, 50 dev and 50 test** tasks for HarnessGrad studies. The full Hugging Face collection includes 2,977 unique tasks, with multiple instruction levels per task. This split covers the 150-workbook Level 5 set.

The generator finds 69 groups, the largest containing 13 tasks. A group connects tasks sharing a starter or expected workbook, including when one task's expected file is another task's starter. It checks exact file hashes, worksheet XML and shared-string hashes, and matching workbook creator and creation timestamps. Whole groups are ordered largest first, with deterministic tie-breaking, and assigned to the smallest partition. This controls for the observed duplicates and repeated source metadata. Similar workbook templates can still recur across partitions.

`study.json` stores the 50 training IDs and the 100 held-out IDs in one split. Use it for all runs. Use dev to choose a checkpoint, then evaluate that checkpoint on test. Report results as **HarnessGrad WTM-Bench Level 5 custom held-out** results and name the partition.

```bash
# Include on all three runs
--split experiments/splits/wtm-bench/study.json
# For checkpoint selection, with --side eval --sampling all
--tasks "$(paste -sd, experiments/splits/wtm-bench/dev.txt)"
# For final evaluation, with --side eval --sampling all
--tasks "$(paste -sd, experiments/splits/wtm-bench/test.txt)"
```

The adapter's `--split official --side eval` route still evaluates all 150. Rebuild the research split from prepared workbooks with `python experiments/splits/wtm-bench/build.py`, or add `--check` to verify the checked-in membership.
