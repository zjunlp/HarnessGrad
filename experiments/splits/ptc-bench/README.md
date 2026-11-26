# PTC-Bench research split

The 240 tasks in PTC-Bench commit [`fa4462e5`](https://github.com/HuanzhiMao/I-PTC/commit/fa4462e5ad1ea3cde2011aaf6396de5d76da4489)
are partitioned into three 80-task sets for HarnessGrad studies. Report scores
as custom held-out results and name the partition.

| Partition | Main | Error recovery | Multi-turn | Total |
| --- | ---: | ---: | ---: | ---: |
| Train | 34 | 23 | 23 | 80 |
| Dev | 33 | 24 | 23 | 80 |
| Test | 33 | 23 | 24 | 80 |

Variants sharing an upstream ID stem stay together. The pinned corpus contains
50 three-variant families, one two-variant family, and 88 singleton families.
The generator checks that identical normalized user messages also stay within
one family. Families are stratified by variant counts, then assigned by a
seeded hash order. Shared simulation fixtures and API domains can still recur
across partitions.

Google Maps occurs in only two task families, assigned to train and dev with
this seed. The other nine base API domains appear in all three partitions.

`study.json` contains train membership and the 160 held-out IDs in one
train/eval file. Use that same file for training, checkpoint selection, and
final evaluation. Use `--side eval --sampling all` and the relevant selector
to measure exactly 80 tasks. Use dev to select a checkpoint before measuring
that checkpoint on test.

    --split experiments/splits/ptc-bench/study.json
    --tasks "$(paste -sd, experiments/splits/ptc-bench/dev.txt)"
    --tasks "$(paste -sd, experiments/splits/ptc-bench/test.txt)"

The last two flags are alternatives for separate eval runs. On train, omit
`--tasks` to use the entire 80-task study set with `--sampling all`.

Check the checked-in files against the downloaded upstream corpus with
`python experiments/splits/ptc-bench/build.py --data-dir /path/to/I-PTC/ptc_bench/data --check`.
