# WTM-Bench

[Workbook Time Machine](https://arxiv.org/abs/2608.07873) evaluates agents that edit a real starter workbook from a natural-language request. The curated evaluation uses all 150 Level 5 tasks from the [Microsoft release](https://github.com/microsoft/WTM-Bench/tree/83d45ae601f3241ac7246613c67b765e48f93c06). The optional [100/100/100 research split](../../../experiments/splits/wtm-bench/README.md) selects 300 additional tasks from the [Hugging Face release](https://huggingface.co/datasets/prose-ms/wtm-bench/tree/c565125154df4a197611847d4a3197909f75d9b4), with one instruction variant per task across Levels 1, 3 and 5.

Actors receive the released user request and workbook preview together with `input.xlsx`. They can edit the workbook with the task-owned Python and openpyxl and submit `output.xlsx`. The verifier checks the pinned starter and reference workbooks and grades a private copy of the submission. Reference files and the scorer are private to verification.

## Prepare and check

Set `HG_DATA_WTM_BENCH` or `HG_DATA_ROOT` and use **Python 3.12** for the task execution runtime. Source preparation requires PyArrow. Loading prepared data uses the standard library.

```bash
python tools/datasets.py --fetch --dataset wtm_bench
python data/datasets/wtm_bench/prepare.py
python tools/prepare_dataset.py --dataset wtm_bench --runtime execution --python 3.12
python tools/datasets.py --check --dataset wtm_bench
python tools/dataset_lint.py --dataset wtm_bench
```

Preparation pins the archive, Level 5 parquet, complete task catalogue, scorer source and each workbook. The official scorer ships under the MIT license in Microsoft's source archive. The curated benchmark data is distributed under CDLA-Permissive-2.0 as stated in the dataset card.

After preparing the curated release, prepare the mixed-level collection with Git and PyArrow.

```bash
python data/datasets/wtm_bench/prepare.py --study
```

The command fetches the pinned full parquet and the selected workbook pairs. Set `HF_ENDPOINT` to use a Hugging Face mirror, or pass `--study-source /path/to/snapshot` for a local copy of the same revision. Set `HG_WTM_BENCH_POOL=mixed` and use the [mixed-level split files](../../../experiments/splits/wtm-bench/README.md) for training and held-out evaluation.

For the curated evaluation, use `HG_WTM_BENCH_POOL=curated` (the default) and `--split official --side eval`. This command checks data and sandbox readiness for a single task without invoking a model.

```bash
python driver.py --dataset wtm_bench --split official --side eval \
  --tasks wtm_bench/Backward_EnronFuse.566 --harness-path examples/bash-harness \
  --agent-backend mock --rounds 0 --check-only
```

## Scoring and controls

HarnessGrad reports `wtm_bench.workbook_cell_grade`. The pinned official `scorer_v2.py` computes it in `values_only` mode. As in the released runner, the value is rounded to four decimals and can include checks for charts, pivots, conditional formatting, tables and sheets. The upstream overall `score` also includes a tool-execution bonus of up to 0.05. The upstream execution bonus is outside this adapter's metric. A missing submission or a file rejected as non-XLSX scores zero. A failed trusted verification is unmeasured.

```bash
python tools/wtm_bench_control.py --oracle
python tools/wtm_bench_control.py --noop
python tools/wtm_bench_control.py --missing
python tools/wtm_bench_control.py --oracle --case 2183
```

In the offline sweep, all 150 unchanged workbooks scored 0. At the released four-decimal precision, 148 of 150 references scored 1. The reference charts in tasks `2183` and `2284` have no recognized series-data references. The official similarity function gives 0.20 of the 0.40 series-reference component when both descriptors have no references, so submitting each reference workbook scores 0.8. The adapter retains both tasks and their original scores.
