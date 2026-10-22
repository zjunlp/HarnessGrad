# B5 public provenance acquisition

This is a **candidate evidence bundle, not independent approval**. No source-gate
receipt or approval pin was issued. Both datasets retain external blockers.
The earlier tasks 195, 227, 196 and 225 remain unchanged and are reused by path
and hash in `inventory.json`. `starting-blockers.json` preserves the original
owned registry entries, including verbatim `observed` and `next_action` text.

Every HTTP response body, including HTTP errors, is retained under `responses/`
with request URL/method/headers, UTC times, status, response headers, byte count
and SHA-256 in a separate receipt. Transport failures have no response/body hash;
their error receipts are hashed instead. Redirects were disabled. `SHA256SUMS`
covers the whole bundle except itself. Its test pin establishes local integrity,
not independent origin, historical custody, reviewer identity or authorization.

## bfcl_v3: release-index-custody

The upstream tag currently resolves to the previously observed immutable commit
`ea13468e4423454d0c213704fb87cf7cb3990433`. The retained release explicitly says V3.
The signed commit bytes and all 93 tree objects reconstruct to their Git SHA-1
identities, rooting original source paths at tree
`b34aa53a73491f42dc69b8b18c92448d7c311f50`. GitHub reports a verified signature;
this work does not independently verify its PGP key or signature.

The archive request was **HEAD only**. The server identifies
`gorilla-1.3.tar.gz`, HTTP 200, and an ETag. No archive body or benchmark material
was downloaded or opened. The empty HEAD body digest is NOT the archive digest;
the ETag is NOT evidence for the declared archive SHA-256
`57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe`.
All `expected_archive_member` values are candidate paths, not verified membership.

**Satisfied by acquisition:** public tag/release/commit identity and a verified
Git commit/tree/path/blob integrity chain for retained original files.
**Independent custodian/reviewer required:** historical tag-to-archive linkage,
archive digest and original member path/size/hash custody; metadata-only
release/index/span/split bindings; independent code-only evaluator review and
approval. No selected index was authenticated or dataset storage accessed.

## bfcl_v3: simple-ast-contract

Tasks 195/227 already supplied eight unique original files. This bundle adds
42 original Python files without rewriting any earlier candidate. The three
previously missing direct checker imports (`model_config.py`, Java converter,
JS converter), Java/JS parsers, category/column/default-prompt/eval constants,
model style, utility code, multi-turn import support, Qwen/base OSS handlers and
27 other model-handler files are now acquired and Git-blob verified. Paths,
SHA-256, Git SHA-1, sizes, expected archive members and static import edges for
all 50 unique files are in `inventory.json`. No upstream Python was imported.

`model_config.py` imports the full handler catalogue unconditionally. Twenty-three
remaining handler body requests returned HTTP 403 (rate-limit bodies retained).
Their exact module names are `bfcl_v3.unacquired_imports` in the inventory;
there are no guessed source bytes or stubs. Package initializer identities are
present in tree metadata, but initializer response bodies were not acquired.
Dynamic multi-turn function-module loading is recorded and was not traversed.
Third-party package/native grammar/runtime locks and independent license review
remain outstanding. Static inspection found source templates/docstrings rather
than task-row files; this is not an independent code-only closure decision.

Candidate entrypoints, all in original `bfcl_eval/` paths:

- `model_handler/utils.py:695`, `default_decode_ast_prompting`; parser core
  `ast_parse:241`. Python expression resolution includes `eval` calls and needs
  runtime review before use.
- `eval_checker/ast_eval/ast_checker.py:30`, `ast_checker` (six arguments).
- `eval_checker/eval_runner.py:246`, `ast_file_runner`; `evaluate_task:410`.
- `model_handler/local_inference/qwen.py:5`, `QwenHandler`, inherits
  `OSSHandler.decode_ast` from `local_inference/base_oss_handler.py:55`, which
  calls the default prompting decoder (candidate handler provenance only; no
  production/model-route selection is approved).

**Satisfied by acquisition:** the named missing checker helpers and broad
parser/checker/dispatch/helper source provenance, original license/spec reuse,
and commit-tree linkage for the old and new bytes.
**Still required:** remaining original import bodies/initializers, a reviewed
complete source and dependency graph, independent archive/member/license custody,
independent reviewer identity and a separately delivered approved exact receipt
pin. Only then may the explicit source gate pass and fabricated full-call
contract tests be derived. This acquisition does not pass that gate, declare
full evaluator provenance independently reviewed, or authorize production H0.

## triviaqa: hub-export-scorer-linkage

The original evaluator fixture already pins the unchanged original scorer at
`ca43b5820b107f3970cf4b7d67f7db7a98117b79` and Apache-2.0 license; component
synthetic differentials are retained. Task 225 supplied a 2021 historical loader,
not evidence that it was used for the requested export. These facts are reused.

Direct `huggingface.co` requests failed with network unreachable; a public DNS
route retry retained hostname TLS verification and failed with connection reset.
The public **hf-mirror.com mirror**, under its own TLS identity, returned revision,
root tree, config/schema/split metadata, `rc.nocontext` file identities, predecessor
source and conversion PR events. All mirror URLs, bodies and hashes are retained.
Mirror-supplied metadata has not been independently authenticated against Hub
Git commit bytes or direct official transport. Metadata acquisition via a mirror
is progress, not an independent Hub-origin attestation.

The metadata reports export commit
`0f7faf33a3908546c6fd5b73a660e0f8ff173c2f`, “Convert dataset to Parquet (#8)”,
2024-01-05. Its root tree has no loader. The config maps validation to
`rc.nocontext/validation-*`; its card declares 17,944 validation examples.
The exact validation LFS declaration is 7,335,321 bytes, SHA-256
`48a5005c0eb4f8a4ae5b9868644297fd5bf1e694aeb3dc9c8ab958cac0d5b201`,
pointer blob `fbe4369287ccf5e4fbd8d148d817b0c1ca7bf71e`.
This is a metadata declaration, NOT a locally measured payload hash or row count.
LFS pointer identities and the selected directory tree reconstruct without
fetching payloads.

PR #8's recorded first conversion commit is
`6baa09772e6ddf7e7b5d629ea64f1a8c1df21c3c`, parent
`d63a76f01c8226449f76b437dd512cf8b17adf5d`. The next commit
`279d93a6e999c93dc992e1a705389e350ba7f706` adds `rc.nocontext`; its file metadata
matches the export. Later PR events delete the script and data folder and mark
it merged. The same loader blob `a9a6932c23d378b9e5628ecec1f0509496806d77`
is declared at the predecessor, initial conversion and pre-deletion revisions.
Its original source is retained as `triviaqa/trivia_qa.py`, SHA-256
`b80c2307a37cfb261b66b7c73157de5495ae18add3f91f5f7d62f86620edc137`.
It differs from task 225's historical loader (notably relative ZIP inputs and
streaming parsing). Its original Apache-2.0 header is intact; the original
Apache text already retained by task 225 is referenced by digest. No standalone
LICENSE is declared in either Hub root listing. The export card's dataset-license
label is `unknown`; source licensing is not a dataset-license certification.

The predecessor metadata also identifies `data/triviaqa-rc.zip` with declared LFS
SHA-256 `f5c28a667a3cace893b0dafef8293891dd26b03144a53fc045d6db90aa0ea734`.
No ZIP or Parquet bytes were requested. This pins a declared potential input,
not proof of which input was consumed by the conversion job.

Static findings in the acquired predecessor loader (unchanged source):

- Lines 77–95, 101–151 and 199–226: `rc.nocontext` selects Web plus Wikipedia;
  validation selects `web-dev.json` and `wikipedia-dev.json` within the RC ZIP.
- Lines 234–257: normalized aliases are copied and stripped; `HumanAnswers`
  are omitted. The exported declared answer schema likewise has no human-answer
  field. Absence/redundancy in original data is not established by omission.
- Lines 259–261 and 301–329: documents are emptied for no-context, domain and
  verified flags are omitted, and no original verified-evaluation filtering is
  implemented. Source filenames/declared input identity are not actual-row
  membership observations.
- Lines 314–329: keys are filename plus question ID, without explicit cross-source
  deduplication. Framework/writer duplicate handling is not established by this
  loader. Original scorer uses Wikipedia question keys or Web question/document
  keys and a requested-key denominator, with missing predictions contributing
  zero. Export row means and the original scorer domain cannot be equated from
  this metadata.

**Satisfied by acquisition:** mirror-supplied exact-revision commit/tree/config/
split/file declarations, immutable predecessor code/hash/license header, and a
conversion PR history associating that predecessor with the Parquet transition.
**Independent origin/custody and conversion attestation still required:**
corroborate the mirror against authoritative Hub commit/tree provenance; obtain
an immutable build/export receipt identifying the loader/converter actually
executed, its code revision, library/runtime/writer versions and input hashes.
The PR contains no such execution receipt, so the predecessor is not declared the
actual converter. A reviewer must decide preservation/absence/redundancy of human
answers, source/verified selection, duplicates, scorer-key mapping and denominator/
aggregation equivalence, or explicitly limit the metric claim. Preserve the v2
metric identity and all historical receipts. No production authorization follows.
