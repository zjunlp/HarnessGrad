# Bring your own RSI method

A **harness** solves one task. A **method** edits a copy of a harness and returns
the candidate; HarnessGrad measures it. Your optimizer can use its own model,
archive, prompt search or code editing logic. It must not call the benchmark
evaluator or use the held-out results to select edits.

## Start with a working process boundary

```bash
python3 tools/validate_method.py examples/method-template --harness base_harness/loop
cp -R examples/method-template /tmp/my-rsi-method
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --method-entrypoint "python3 /tmp/my-rsi-method/run.py" \
  --run-id my-method-smoke --fail-on-incomplete
python3 tools/run_summary.py runs/my-method-smoke
```

The unchanged template is a zero-cost control, so it ends early with no edit.
Replace `propose(candidate, study)` in `run.py` with your edit rule and return
whether it changed the candidate. Use fresh run IDs for subsequent attempts.
No model, benchmark download or optimizer dependency is required for this smoke.
Configure the solver model and your optimizer separately before using a
[real research recipe](rsi-experiments.md).

The platform sends one JSON request on stdin. Important fields are:

| Field | Use |
| --- | --- |
| `base_harness` | pristine candidate source, including its staged study channel |
| `workspace` | private writable directory for candidate copies and method files |
| `trajectory_out` | write the candidate sequence here |
| `round_index` | next platform measurement round |
| `epochs_total`, `epochs_measured` | total and already measured improvement states; budget is their difference |
| `improver` | optional resolved editor tool; you can also implement your own editor |

<<<<<<< HEAD
Read the study channel the platform stages beside the candidate (the `base_harness` entry in
the request names the tree): `_harnessgrad/round.json`, `traces/`, `tasks/`, `history/` and
`states/` hold the evidence available to the method. The candidate's own task
execution does not receive that optimizer channel. Custom-split held-out task IDs
and the private manifest are not included in method feedback.
=======
The request supplies absolute `base_harness`, `workspace`, and `trajectory_out`
paths, task IDs, and `platform_api_version`. Validate the API with `require_api`.
Read staged round evidence from `<base_harness>/_harnessgrad/`, including
`round.json`, `tasks/`, `traces/`, `history/`, `states/`, and `SKILL.md`. The base is
read-only; put edits and candidate directories under `workspace`. The platform
hides its private tree from the method.
>>>>>>> 174ba19 (fix: align first-run checks and measurement reuse)

Write a trajectory with one or more `steps`, each naming an absolute
`harness_dir`, and emit only one JSON response on stdout. Send diagnostics to
stderr or the method log files in the writable workspace. An error is an error;
do not return the unchanged base as a successful optimized candidate when your
provider or optimizer crashed. Report your actual generation cost; the template's
zero is correct only while it calls no model.

The platform validates each candidate, runs it, and returns its measured study
history on subsequent calls. It owns scoring, held-out evaluation and receipts.
Use `--fail-on-incomplete` in batch scripts so optimizer failures cannot turn an
H0-only curve into a successful job. A method that deliberately finishes early
with a valid no-change response is reported as `early_stop`.

## From a smoke to an experiment

1. Select and prepare a benchmark from the research recipes.
2. Freeze a custom study/held-out manifest once.
3. Point `--method-entrypoint` at your method, using the same initial harness and
   manifest as the baselines. Configure any private optimizer model in your
   method process, separately from `HG_AGENT_*`.
4. Train on the study side, then measure a fixed checkpoint on the held-out side.
5. Export the run summary and matrix. Check completion, invalid/unmeasured tasks,
   dataset/split identity and cost alongside scores.

For the complete versioned protocol, helper functions and multi-candidate
selection fields, read [methods/protocol.py](../methods/protocol.py) and
[INTERFACE.md §4](../INTERFACE.md#4-the-method-interface).
