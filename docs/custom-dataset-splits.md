# Choose your own study and held-out tasks

You can use any provisioned benchmark pool for a custom experiment. You do not
need to edit its adapter or follow its official train/test partition. This also
works when the only available source is a validation or test set: explicitly
assign part to study, and hold out the rest from the method.

The resulting score describes **your custom held-out partition**, not the official
benchmark's full evaluation score. Official split declarations remain unchanged.
Here, `train` means tasks whose traces the RSI method can study; `eval` means
tasks used to measure a saved harness without running the method. These are two
separate runs. This feature does not introduce a third model-selection/dev pool.

## Freeze a ratio or a task count

Install the editable CLI with `python3 -m pip install -e .`, or replace
`harnessgrad` below with `python3 -m harnessgrad.cli` from the checkout.
Prepare the selected benchmark's data first, as described in DATASETS.md.

For example, use 20% of an evaluation-only pool as study material:

```bash
harnessgrad split create --dataset math500 --source official-eval \
  --train-ratio 0.2 --seed 42 --output splits/math500-study20.json
harnessgrad split check --dataset math500 --file splits/math500-study20.json
```

`--source official-eval` uses the adapter's declared official evaluation IDs.
For datasets that already contain both official train and eval tasks, this option
excludes their original training rows from the custom experiment. Excluded IDs
are recorded explicitly. `--source all` (the default) repartitions the entire
loaded pool, and also works for datasets without an OFFICIAL_EVAL declaration.
Neither option downloads additional tasks or changes which upstream files an
adapter loads. To select a different upstream pool, prepare/configure the adapter
first, then generate the manifest.

Alternatively request an exact study count:

```bash
harnessgrad split create --dataset math500 --train-count 50 --seed 42 \
  --output splits/math500-study50.json
```

Creation prints the actual train/eval/excluded counts, `split_sha`, file SHA,
and number of official evaluation tasks moved to study. It refuses to overwrite
an existing file. Keep this file with the experiment and reuse it across methods,
seeds and checkpoints. A split seed assigns tasks; a run seed controls that run.
Changing the run seed does not change the frozen split.

## Train, then measure the saved harness

```bash
harnessgrad run --config experiments/quickstart.json \
  --set dataset=math500 --set split=splits/math500-study20.json \
  --set run_id=math500-study --set sampling=random_5

harnessgrad run --config experiments/quickstart.json \
  --set dataset=math500 --set split=splits/math500-study20.json \
  --set side=eval --set sampling=all --set from_run=math500-study \
  --set run_id=math500-heldout

python3 tools/run_summary.py runs/math500-heldout
```

The experiment configuration uses the same `split` string as `driver.py --split`.
Relative paths follow the launch working directory, as other driver paths do.
The existing method/model configuration and benchmark dependencies still apply;
these commands do not select a model or supply a missing native scorer. `echo_base`
in the existing quickstart is a deterministic wiring method, not an RSI model.
Replace it with your chosen method and configure its editor before a real study.

Use `--check-only` with the same arguments to validate the partition without
launching a task. `--prepare-only` saves the frozen protocol to run metadata and
does not call a model. On eval, the checkpoint must use the same custom split as
the training run. Evaluating a custom-trained checkpoint on the whole official
exam could include tasks it already studied; the paired-run check refuses that.

## Small offline example

This uses only the repository's `demo` fixture and mock backend. It verifies the
plumbing and produces no benchmark capability result:

```bash
python3 -m harnessgrad.cli split create --dataset demo --train-count 3 \
  --seed 42 --output /tmp/hg-demo-partition.json
HG_AGENT_BACKEND=mock python3 driver.py --dataset demo --harness loop \
  --split /tmp/hg-demo-partition.json --sampling all --rounds 0 \
  --run-id partition-study --runs-root /tmp/hg-partition-runs \
  --work-root /tmp/hg-partition-work --improver none \
  --method-entrypoint "python3 $PWD/methods/noop/run.py"
HG_AGENT_BACKEND=mock python3 driver.py --dataset demo --harness loop \
  --split /tmp/hg-demo-partition.json --side eval --from-run partition-study \
  --sampling all --rounds 0 --run-id partition-eval \
  --runs-root /tmp/hg-partition-runs --work-root /tmp/hg-partition-work \
  --improver none
```

Use fresh filenames and run IDs when repeating the example.

## Membership, grouping and evidence

The versioned manifest freezes concrete IDs, dataset name, source pool, public
task-content/roster hashes, original official-eval declaration hash, optional
grouping hash, seed and requested ratio/count. It contains no answers, verifiers
or private grouping keys. Membership is independent of input task ordering.
Task IDs, public contents or grouping declarations changing requires explicit
regeneration; it cannot silently turn an old split into a new one.

Adapters can declare `SPLIT_GROUPS` for related tasks, such as attack variants
of one original request. Every declared group stays entirely on one side,
including the excluded side. Ratio allocation chooses the nearest nonempty
prefix of a seeded group ordering by task count; the actual ratio can therefore
differ from the request. Exact counts that do not fit this ordering are refused
with the nearest count. No split can separate a single indivisible group.
Without an adapter grouping declaration, tasks are treated as independent; the
tool cannot infer hidden semantic duplicates or pretraining contamination.

The driver records the parsed manifest itself, original file SHA, counts and
source-reuse label under `run_meta.json:split_protocol`. The curve carries only
identity/counts and a custom-heldout label; the method does not receive a manifest
listing the held-out tasks. The run summary repeats the custom-score label.
`split_sha` continues to identify the train/eval membership as before. Resume
requires the original split-file bytes; moving the file without editing it is
allowed. Source hashes authenticate the recorded inputs, not official release
provenance or permission to train on a particular release.

The legacy `{ "train": [...], "eval": [...] }` file format still works. It must
cover the whole loaded dataset with unique, disjoint sides; it now also receives
a custom-heldout receipt and source-reuse count. The generated versioned format
additionally supports explicit exclusions and drift detection. Existing
`--split declared`, `--split official` and `--split default` keep their behavior.
