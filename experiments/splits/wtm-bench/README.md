# WTM-Bench research split

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
