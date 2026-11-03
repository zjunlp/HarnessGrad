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

## Prepare, without changing runtime behavior

Run from the repository root, with a compatible `datasets`/`huggingface_hub`
installation. Preparation is the only phase that may use the network:

```bash
python3 -m data.scaffolds.native_qa.prepare --dataset searchqa
python3 -m data.scaffolds.native_qa.prepare --dataset bbeh --duplicate-questions exclude
python3 -m data.scaffolds.native_qa.prepare --dataset math500
```

`--root` overrides the destination. Each adapter reads `HG_<NAME>_ROOT`, for
example `HG_DATA_SEARCHQA`. The root is always outside the platform checkout.
Preparation stores derived JSONL, source repo/revision, native split names, row
counts and SHA-256 per file in `source-manifest.json`. `--local split=/path` is
an explicit native-source import; the receipt labels it local and does not claim
it proves authenticated HuggingFace acquisition. Load verifies every manifest
file before it builds tasks. No branch/latest source, automatic import-time
fetch, model API or proxy scorer is used.

Math grading requires `math-verify==0.9.0` in the trusted verifier's interpreter.
Without it load refuses with `ConfigMissing`; there is no string-match fallback.
A native parse error/timeout is unmeasured and has no score.
For `math` and `math500`, the verifier uses the current absolute `sys.executable`
and declares both `sys.prefix` and `sys.base_prefix` through existing read-only
`VERIFY.runtime_paths`, so grading uses the same interpreter checked at load.
Provision isolated Math-Verify virtual environments **outside `/tmp`**; prefixes
under that hidden sandbox tree refuse with `ConfigMissing`. The adapter checks
and uses the current platform interpreter for this dependency. This does not
relax sandbox isolation or add an automatic dependency install.

Sandbox plan 6 mounts the active venv and its base interpreter as distinct
read-only runtime trees, preserving symlink-venv dependencies during the driver
startup probe and task/scorer execution. Use an environment such as
`~/.venvs/harnessgrad`, outside `/tmp`, the platform checkout and task/work
storage. Hidden or writable runtime prefixes are refused before launch; do not
disable sandboxing to bypass that guard. Provision Math-Verify in the environment
used to launch the driver.

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
