# upstream native QA datasets

Seven independent HarnessGrad dataset names are supplied: `searchqa`, `nq`, `bbeh`,
`supergpqa`, `math`, `math500`, and `officeqa_pro`. This is an interface port from
upstream commit `e9c7667945f959d62da27204649a82f94d1b6705`; it does not import or
run upstream's experiment runner. Scorer bytes and source paths are recorded in
`PROVENANCE.json`, with the upstream Apache-2.0 license retained.

## Source and metric identity

| Adapter | Pinned source | Scored protocol |
| --- | --- | --- |
| searchqa | kyunghyuncho/search_qa @ 06907e45883b7cae435453b65d598447039fde79 | upstream context question, native search snippets, normalized text EM; F1 retained in evidence |
| nq | google-research-datasets/natural_questions @ e8103d566bef4154c2c12b17c6095ec5275840cc | upstream any-short-answer-or-yes/no subset, full non-HTML document, complete multi-span references; contextual text EM/F1 |
| bbeh | BBEH/bbeh @ 08e07a803851822c04399782ece3c4a07ce419f9 | Google per-answer correctness @ 80d12ca916b7158f22293fcf3144f4d3d854d4be; sample mean |
| supergpqa | m-a-p/SuperGPQA @ 4430d4458112c7d4497fdcf94d7cc223313d6acf | upstream final-letter-v1, sample mean; **not** original free-form/judge or discipline-macro leaderboard |
| math | EleutherAI/hendrycks_math @ 21a5633873b6a120296cce3e2df9d5550074f4a3 | Math-Verify 0.9.0, native last-boxed extraction |
| math500 | HuggingFaceH4/MATH-500 @ 6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be | Same exact mathematical-equivalence grader, MATH-500 source pool |
| officeqa_pro | databricks/officeqa @ 763a8366abf2a3605c381d53586d844dc60fa756 | Unmodified official reward @ 7b9a3c154ef9fb40215bb67934afc43e6799de16, tolerance=0 |

NQ here is explicitly the contextual short-answer subset defined by upstream; it
is not the official NQ long/short answer offset F1 or an answerable-rate claim.
BBEH duplicate question groups cause refusal, matching upstream's default. An
explicit preparation option `--duplicate-questions exclude` implements its
whole-group exclusion policy: every repeated question is removed, including all
contradictory labels. Source JSONL remains complete and digest-bound; a `selection`
receipt records the policy, source/retained/excluded counts and question-ID hashes.
The loader recomputes those fields before filtering, rather than trusting an
arbitrary exclusion list. This creates a filtered experimental pool, not a full
official benchmark score. The pinned 4520-row source excludes 9 repeated rows and
retains 4511 tasks under this explicit policy. Other QA datasets do not accept it.

## Prepare native data

Remote acquisition uses a separate tooling environment with compatible
`datasets`/`huggingface_hub` installations; local Parquet import additionally
needs pyarrow in that tooling interpreter. These acquisition dependencies are
separate from the scoring artifact and are not installed by loading or scoring.
Local JSON, JSONL and CSV imports and all prepared-JSONL loading use only the
standard library. Preparation is the only phase that may fetch source data:

```bash
python3 -m data.scaffolds.native_qa.prepare --dataset searchqa
python3 -m data.scaffolds.native_qa.prepare --dataset bbeh --duplicate-questions exclude
python3 -m data.scaffolds.native_qa.prepare --dataset math500
```

`--root` overrides the destination. Each adapter reads `HG_DATA_<NAME>`, for
example `HG_DATA_SEARCHQA` or `HG_DATA_MATH500`. The root is always outside the platform checkout.
Preparation stores derived JSONL, source repo/revision, native split names, row
counts and SHA-256 per file in `source-manifest.json`. `--local split=/path` is
an explicit native-source import; the receipt labels it local and does not claim
it proves authenticated HuggingFace acquisition. Load verifies every manifest
file before it builds tasks. No branch/latest source, automatic import-time
fetch, model API or proxy scorer is used.

## MATH and MATH500 scoring environment

Both adapters share the explicitly prepared `math` execution artifact, using
CPython 3.12 and the complete `data/datasets/math/requirements.lock` with
`math-verify==0.9.0`. Prepare it from the platform environment:

```sh
python tools/prepare_dataset.py --dataset math --runtime execution
python tools/prepare_dataset.py --dataset math --runtime execution --check --json
```

The same two commands cover MATH500; no second scorer environment is needed.
Preparation uses uv. `HG_DATASET_RUNTIME_ROOT` or `--runtime-root` selects the
external store; retain that selection during execution. Use a physical store
outside the checkout, `/tmp` and task/work storage so the sandbox can mount it.
`--check` only verifies the artifact, without fetching data or running a scorer.
Dataset readiness additionally verifies the digest-bound native source.
`tools/datasets.py --check` checks declared downloaded bytes and packages in
that shared artifact, rather than querying platform-installed packages.

The platform does not need Math-Verify or SymPy. Loading and source parsing do
not inspect platform packages; selecting a verifier requires the prepared
artifact. Each verifier records an exact `python_runtime` binding and launches
with `-I -B`. Launch admission verifies that artifact again and mounts its venv
and physical base interpreter read-only, rather than mounting the platform venv.
H0, cache and resume identities include the selected binding. Missing, modified
or incompatible artifacts fail without choosing another Python or a string-match
scorer. Other native QA datasets retain their existing interpreter paths.

The pinned `math_grade.py` and last-boxed mathematical-equivalence rules remain
unchanged. A separate process still bounds each Math-Verify call to 15 seconds;
native parse errors and timeouts are unmeasured. Evidence records the actual
inner grading interpreter, prefix, isolated/read-only state, package versions and
origins; the verdict records the bound runtime identity. Restored scorer source
is loaded without accepting task-local packages or cached bytecode.

Direct `score()` calls and `native_qa/control.py` use the same selected artifact
in a bounded diagnostic worker, with private temporary state and a minimal
environment. These direct diagnostics do not provide sandbox isolation. The
regular `files` verifier is the read-only sandbox route.

Synthetic tests never install dependencies or fetch released questions. Prepare
a disposable artifact first and use a separate pytest interpreter, which does
not need Math-Verify:

```sh
HG_TEST_MATH_EXECUTION_RUNTIME_ROOT=/path/to/disposable-store python -m pytest -q tests/quality/test_native_qa_adapters.py tests/quality/test_math_execution_runtime.py
```

Math execution tests skip when that explicit test store is absent. Remote data
acquisition dependencies remain an operator-provided tooling environment; they
are not part of the Math-Verify scoring lock.

## OfficeQA-Pro public corpus

OfficeQA-Pro separately requires `HG_DATA_OFFICEQA_PRO_CORPUS` or `<root>/corpus`.
Supply the entire public corpus directory, with only ordinary txt/md/pdf/json
files. Do **not** put the evaluation CSV, answer-key metadata or source_docs
answer pointers inside it. The dataset stages only that public corpus, under
`corpus/`. Corpus acquisition remains an operator prerequisite and must be
reviewed separately from table provisioning.

## Splits, privacy and validation scope

Native `train` remains study, native validation/test remain eval. The exact
source split remains in each task ID. Test-only datasets declare no artificial
train/eval partition; `OFFICIAL_EVAL=None` states the whole pool is evaluation.
OfficeQA's source CSV is unassigned upstream; this port treats its complete
question pool as evaluation and documents that decision, rather than trusting
HuggingFace's inferred `train` label. `math500` also has no invented training set.

`task.json` contains only a public question and delivery hint. Gold inputs,
checks and scorer modules are materialized only after actor exit; they are never
in SETUP. The checker validates trusted input digest, emits a bounded [0,1]
result with metric identity, and redacts reference content from evidence.

Unit tests use authored native-shaped fixtures to verify the transport and
scoring rules; such fixtures prove no benchmark capability or production
readiness. Existing dataset admission/blocker gates continue to apply.
